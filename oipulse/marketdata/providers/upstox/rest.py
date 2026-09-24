"""Upstox REST client.

`docs/design/06-UPSTOX_INTEGRATION.md` §2, §4, §9.

REST is **not** the live path. It is used for discovery, expiry refresh, chain snapshots
(the cross-sectional coherence anchor), historical OHLC, historical daily OI,
reconciliation and out-of-band gap recovery. The legacy system polled the chain every
1-30 minutes and called it real-time; v2 polls on a slower cadence purely as an anchor
and takes live movement from WebSocket.

NOT EXECUTABLE IN THE DEVELOPMENT SANDBOX: requires `httpx` and network access to
api.upstox.com. See the external verification checklist in the Phase 2 report.

Access tokens never leave the backend and never appear in logs or exceptions.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from oipulse.core.clock import Clock
from oipulse.core.errors import OIPulseError
from oipulse.marketdata.ratelimit import EndpointClass, RateLimitGovernor
from oipulse.observability.logging import get_logger
from oipulse.observability.metrics import (
    METRICS,
    RATE_LIMIT_EVENTS,
    REST_ERRORS,
    REST_REQUEST_DURATION,
)

log = get_logger(__name__)

__all__ = ["UpstoxAuthError", "UpstoxRateLimited", "UpstoxRestClient", "UpstoxRestError"]

_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.0


class UpstoxRestError(OIPulseError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UpstoxAuthError(UpstoxRestError):
    """401. Never retried — the collector is suspended and re-auth requested."""


class UpstoxRateLimited(UpstoxRestError):
    """429. Honours Retry-After and feeds the delay back into the governor."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"rate limited; retry after {retry_after}", 429)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class UpstoxRestConfig:
    api_base: str = "https://api.upstox.com/v2"
    timeout_seconds: float = 15.0


class UpstoxRestClient:
    """Thin transport. Normalization lives in `normalize.py`, not here.

    Keeping parsing out of the client is what lets the normalizer be tested against
    fixtures with no network at all.
    """

    def __init__(
        self,
        access_token: str,
        clock: Clock,
        governor: RateLimitGovernor,
        config: UpstoxRestConfig | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._token = access_token
        self._clock = clock
        self._governor = governor
        self._config = config or UpstoxRestConfig()
        # Injectable so tests can supply a fixture-backed transport.
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx  # imported lazily: absent in the offline sandbox

            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client

    async def _request(
        self, endpoint: EndpointClass, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Acquire budget, issue, classify, retry. Budget exhaustion queues, never fails."""
        url = f"{self._config.api_base}{path}"

        for attempt in range(_MAX_ATTEMPTS):
            decision = self._governor.acquire(endpoint)
            while not decision.granted:
                METRICS.inc(RATE_LIMIT_EVENTS, {"endpoint": endpoint.value})
                await asyncio.sleep(max(decision.wait.total_seconds(), 0.01))
                decision = self._governor.acquire(endpoint)

            client = await self._ensure_client()
            started = self._clock.now()
            try:
                response = await client.get(url, headers=self._headers(), params=params)
            except Exception as exc:
                METRICS.inc(REST_ERRORS, {"endpoint": endpoint.value, "status": "network"})
                if attempt == _MAX_ATTEMPTS - 1:
                    raise UpstoxRestError(f"network failure after {_MAX_ATTEMPTS}") from exc
                await asyncio.sleep(_BASE_DELAY * (2**attempt) + random.uniform(0, 0.4))
                continue
            finally:
                METRICS.observe(
                    REST_REQUEST_DURATION,
                    (self._clock.now() - started).total_seconds(),
                    {"endpoint": endpoint.value},
                )

            status = response.status_code
            if status == 401:
                METRICS.inc(REST_ERRORS, {"endpoint": endpoint.value, "status": "401"})
                # Not retried: retrying an expired token burns budget and fills logs,
                # which is exactly the legacy behaviour being corrected.
                raise UpstoxAuthError("access token expired or revoked", 401)

            if status == 429:
                retry_after = timedelta(seconds=float(response.headers.get("Retry-After", 5)))
                # Penalise the whole endpoint class: the limit belongs to the account.
                self._governor.penalise(endpoint, retry_after)
                METRICS.inc(REST_ERRORS, {"endpoint": endpoint.value, "status": "429"})
                if attempt == _MAX_ATTEMPTS - 1:
                    raise UpstoxRateLimited(retry_after)
                await asyncio.sleep(retry_after.total_seconds())
                continue

            if status >= 500:
                METRICS.inc(REST_ERRORS, {"endpoint": endpoint.value, "status": str(status)})
                if attempt == _MAX_ATTEMPTS - 1:
                    raise UpstoxRestError(f"server error {status}", status)
                await asyncio.sleep(_BASE_DELAY * (2**attempt) + random.uniform(0, 0.4))
                continue

            if status >= 400:
                METRICS.inc(REST_ERRORS, {"endpoint": endpoint.value, "status": str(status)})
                raise UpstoxRestError(f"client error {status}", status)

            # Narrowed, not cast: `.json()` is typed `Any`, and a non-object body
            # would otherwise flow into the parsers as though it were a response
            # envelope and fail much later with a confusing shape error.
            body = response.json()
            if not isinstance(body, dict):
                raise UpstoxRestError(
                    f"expected a JSON object from {path}, got {type(body).__name__}",
                    status,
                )
            return body

        raise UpstoxRestError(f"exhausted {_MAX_ATTEMPTS} attempts for {path}")

    # ------------------------------------------------------------------ endpoints

    async def get_json(self, path: str) -> dict[str, Any]:
        """Authenticated GET returning the raw JSON body.

        The V3 feed-authorize endpoint is not a market-data endpoint and needs no
        normalization, so it is served here rather than given a bespoke client. Budget
        is accounted against DISCOVERY: authorize is a low-rate control call, and
        leaving it unaccounted would let it contend with option-chain polling
        invisibly.
        """
        return await self._request(EndpointClass.DISCOVERY, path)

    async def get_option_contracts(self, instrument_key: str) -> dict[str, Any]:
        """Expiry and contract discovery. Cached daily by the caller (`06` §4)."""
        return await self._request(
            EndpointClass.DISCOVERY, "/option/contract", {"instrument_key": instrument_key}
        )

    async def get_option_chain(self, instrument_key: str, expiry: date) -> dict[str, Any]:
        return await self._request(
            EndpointClass.OPTION_CHAIN,
            "/option/chain",
            {"instrument_key": instrument_key, "expiry_date": expiry.isoformat()},
        )

    async def get_quotes(self, instrument_keys: Sequence[str]) -> dict[str, Any]:
        return await self._request(
            EndpointClass.QUOTE,
            "/market-quote/quotes",
            {"instrument_key": ",".join(instrument_keys)},
        )

    async def get_historical_candles(
        self, instrument_key: str, interval: str, frm: date, to: date
    ) -> dict[str, Any]:
        return await self._request(
            EndpointClass.HISTORICAL,
            f"/historical-candle/{instrument_key}/{interval}/{to.isoformat()}/{frm.isoformat()}",
        )

    async def get_historical_oi(
        self, instrument_key: str, expiry: date, on: date
    ) -> dict[str, Any]:
        """Date-granular OI across strikes.

        The one historical endpoint that meaningfully predates live collection. Its
        result is normalized to `HistoricalDailyOI`, never to a quote (AD-26).
        """
        return await self._request(
            EndpointClass.HISTORICAL,
            "/option/open-interest",
            {
                "instrument_key": instrument_key,
                "expiry_date": expiry.isoformat(),
                "date": on.isoformat(),
            },
        )

    async def aclose(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()
