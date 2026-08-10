"""OAuth 2.0 authentication routes.

Flow:
  GET  /api/auth/login      → redirect to Upstox auth dialog
  GET  /api/auth/callback   → exchange code, save tokens, set session cookie
  POST /api/auth/logout     → clear session cookie
  GET  /api/auth/me         → current user info
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DB, CurrentUser
from app.core.security import (
    decrypt_token,
    encrypt_token,
    generate_oauth_state,
    generate_session_id,
    sign_session_id,
    utc_now,
)
from app.db.models.operational import AuditLog
from app.db.models.user import OAuthState, UpstoxAccount, User, UserPreference
from app.integrations.upstox.client import (
    UpstoxAuthError,
    build_auth_url,
    exchange_code_for_token,
    get_profile,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_OAUTH_STATE_TTL = timedelta(minutes=10)


def _post_auth_redirect_url(settings) -> str:
    callback_path = "/api/auth/callback"
    if settings.upstox_redirect_uri.endswith(callback_path):
        return settings.upstox_redirect_uri[: -len(callback_path)] + "/"
    return "/"


def _set_session_cookie(response: Response, user_id: int, settings) -> None:
    session_id = f"user:{user_id}"
    signed = sign_session_id(session_id)
    response.set_cookie(
        key="session_id",
        value=signed,
        max_age=settings.session_ttl_seconds,
        secure=settings.is_production,
        httponly=True,
        samesite="lax",
    )


@router.get("/login")
async def login(request: Request, db: DB):
    """Initiate Upstox OAuth — redirect user to Upstox auth dialog."""
    from app.core.config import get_settings
    settings = get_settings()

    state = generate_oauth_state()
    expires = utc_now() + _OAUTH_STATE_TTL

    # Persist state in DB (fallback) and Redis TTL is handled by the expiry field
    db.add(OAuthState(
        state_token=state,
        session_key=request.client.host or "unknown",
        expires_at=expires,
    ))
    await db.commit()

    auth_url = build_auth_url(state)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback")
async def oauth_callback(
    request: Request,
    response: Response,
    db: DB,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Upstox OAuth callback — exchange code for token, create session."""
    from app.core.config import get_settings
    settings = get_settings()

    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    # Validate state (CSRF guard)
    result = await db.execute(
        select(OAuthState).where(
            OAuthState.state_token == state,
            OAuthState.expires_at > utc_now(),
            OAuthState.used_at.is_(None),
        )
    )
    state_row = result.scalar_one_or_none()
    if not state_row:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # One-time consumption
    state_row.used_at = utc_now()
    await db.flush()

    # Exchange code for access token
    try:
        token_resp = await exchange_code_for_token(code)
    except UpstoxAuthError as exc:
        logger.warning("Token exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="Token exchange failed")

    # Fetch user profile to get the upstox_user_id
    profile_resp = await get_profile(token_resp.access_token)
    upstox_user_id = profile_resp.data.get("user_id", "")
    email = profile_resp.data.get("email", f"{upstox_user_id}@upstox.user")

    # Upsert local user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(email=email, display_name=profile_resp.data.get("user_name", ""))
        db.add(user)
        await db.flush()
        db.add(UserPreference(user_id=user.id))

    # Upsert Upstox account with encrypted token
    enc_token = encrypt_token(token_resp.access_token)
    enc_refresh = encrypt_token(token_resp.extended_token) if token_resp.extended_token else None

    acc_result = await db.execute(select(UpstoxAccount).where(UpstoxAccount.user_id == user.id))
    account = acc_result.scalar_one_or_none()

    expires_at = utc_now() + timedelta(seconds=token_resp.expires_in) if token_resp.expires_in else None

    if account is None:
        account = UpstoxAccount(
            user_id=user.id,
            upstox_user_id=upstox_user_id,
            access_token_enc=enc_token,
            refresh_token_enc=enc_refresh,
            token_expires_at=expires_at,
        )
        db.add(account)
    else:
        account.access_token_enc = enc_token
        account.refresh_token_enc = enc_refresh
        account.token_expires_at = expires_at
        account.is_active = True

    # Audit log — no token values
    db.add(AuditLog(
        user_id=user.id,
        action="oauth_login",
        resource_type="upstox_account",
        ip_address=request.client.host,
        status="success",
        details={"upstox_user_id": upstox_user_id},
    ))

    await db.commit()

    redirect_response = RedirectResponse(
        url=_post_auth_redirect_url(settings),
        status_code=302,
    )
    _set_session_cookie(redirect_response, user.id, settings)
    return redirect_response


@router.post("/logout")
async def logout(response: Response, user: CurrentUser, db: DB, request: Request):
    db.add(AuditLog(
        user_id=user.id,
        action="logout",
        ip_address=request.client.host,
        status="success",
    ))
    await db.commit()
    response.delete_cookie("session_id")
    return {"status": "ok"}


@router.get("/me")
async def me(user: CurrentUser):
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
    }
