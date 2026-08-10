"""Upstox REST API client.

All requests are made from the backend — the access token is NEVER sent to the
browser.  The client accepts a plaintext access token at call time; callers are
responsible for decrypting it from storage before passing it here.

Retry logic: up to 3 attempts with exponential back-off + jitter.
Token expiry (401): raises UpstoxAuthError so callers can trigger re-auth.
Rate limit (429): backs off and retries within the attempt budget.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import date
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.upstox.schemas import (
    UpstoxOptionChainResponse,
    UpstoxProfileResponse,
    UpstoxTokenResponse,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class UpstoxError(Exception):
    """Generic Upstox API error."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class UpstoxAuthError(UpstoxError):
    """Access token expired or revoked — user must re-authenticate."""


class UpstoxRateLimitError(UpstoxError):
    """Rate limit hit — should back off."""


def _build_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


async def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute an HTTP request with automatic retry and structured error handling."""
    last_exc: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(_MAX_RETRIES):
            try:
                response = await client.request(
                    method, url, headers=headers, params=params, json=json
                )

                if response.status_code == 401:
                    raise UpstoxAuthError("Access token expired or revoked", 401)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 5))
                    logger.warning("Upstox rate limit hit; waiting %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    raise UpstoxError(f"Upstox server error {response.status_code}", response.status_code)

                response.raise_for_status()
                return response.json()

            except UpstoxAuthError:
                raise  # don't retry auth errors
            except (httpx.TimeoutException, httpx.NetworkError, UpstoxError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        "Upstox request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, exc,
                    )
                    await asyncio.sleep(delay)

    raise UpstoxError(f"All {_MAX_RETRIES} attempts failed: {last_exc}") from last_exc


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def build_auth_url(state: str) -> str:
    """Construct the Upstox OAuth2 authorisation redirect URL."""
    settings = get_settings()
    params = {
        "client_id": settings.upstox_client_id,
        "redirect_uri": settings.upstox_redirect_uri,
        "response_type": "code",
        "state": state,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{settings.upstox_auth_base}/dialog?{qs}"


async def exchange_code_for_token(code: str) -> UpstoxTokenResponse:
    """Exchange an authorisation code for an access token."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{settings.upstox_auth_base}/token",
            data={
                "code": code,
                "client_id": settings.upstox_client_id,
                "client_secret": settings.upstox_client_secret,
                "redirect_uri": settings.upstox_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise UpstoxAuthError(
                f"Token exchange failed: {response.status_code} {response.text}"
            )
        return UpstoxTokenResponse.model_validate(response.json())


# ── Data endpoints ────────────────────────────────────────────────────────────

async def get_profile(access_token: str) -> UpstoxProfileResponse:
    settings = get_settings()
    data = await _request_with_retry(
        "GET",
        f"{settings.upstox_api_base}/user/profile",
        headers=_build_headers(access_token),
    )
    return UpstoxProfileResponse.model_validate(data)


async def get_option_chain(
    access_token: str,
    instrument_key: str,
    expiry_date: date,
) -> UpstoxOptionChainResponse:
    """
    Fetch the full option chain for *instrument_key* at *expiry_date*.

    instrument_key examples:
        "NSE_INDEX|Nifty 50"   → NIFTY
        "NSE_INDEX|Nifty Bank" → BANKNIFTY
        "BSE_INDEX|SENSEX"     → SENSEX
    """
    settings = get_settings()
    data = await _request_with_retry(
        "GET",
        f"{settings.upstox_api_base}/option/chain",
        headers=_build_headers(access_token),
        params={
            "instrument_key": instrument_key,
            "expiry_date": expiry_date.isoformat(),
        },
    )
    return UpstoxOptionChainResponse.model_validate(data)


async def get_option_expiries(
    access_token: str,
    instrument_key: str,
) -> list[str]:
    """Return sorted list of expiry date strings for an underlying."""
    settings = get_settings()
    data = await _request_with_retry(
        "GET",
        f"{settings.upstox_api_base}/option/contract",
        headers=_build_headers(access_token),
        params={"instrument_key": instrument_key},
    )
    # Upstox returns {"status":"success","data":["2024-08-29","2024-09-26",...]}
    return sorted(data.get("data", []))
