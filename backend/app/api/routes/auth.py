"""OAuth 2.0 authentication routes.

Flow:
  GET  /api/auth/login            → redirect to Upstox auth dialog
  GET  /api/auth/callback         → exchange code, save tokens, set session cookie
  POST /api/auth/offline-session  → local session from stored snapshots (no live Upstox)
  POST /api/auth/logout           → clear session cookie
  GET  /api/auth/me               → current user info + live vs stored access flags
"""
from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, select

from app.api.deps import DB, CurrentUser, has_live_market_access
from app.core.security import (
    encrypt_token,
    generate_oauth_state,
    sign_session_id,
    utc_now,
)
from app.db.models.operational import AuditLog
from app.db.models.snapshot import OISnapshot
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
_AUTH_ERROR_MESSAGES = {
    "oauth_denied": "OAuth access was denied. Please try again.",
    "invalid_request": "Authentication request was incomplete. Please try again.",
    "invalid_state": "Authentication session expired. Please try again.",
    "upstox_inactive_segments": "No active Upstox segments are enabled for this account. Reactivate them in Upstox app or web, then try again.",
    "token_exchange_failed": "Token exchange failed. Please try again.",
}


def _post_auth_redirect_url(settings) -> str:
    callback_path = "/api/auth/callback"
    if settings.upstox_redirect_uri.endswith(callback_path):
        return settings.upstox_redirect_uri[: -len(callback_path)] + "/"
    return "/"


def _login_error_redirect(settings, error_code: str) -> RedirectResponse:
    base_url = _post_auth_redirect_url(settings).rstrip("/")
    target = f"{base_url}/login?auth_error={quote(error_code)}"
    return RedirectResponse(url=target, status_code=302)


def _map_upstox_auth_error(exc: UpstoxAuthError) -> str:
    message = str(exc)
    if "UDAPI100058" in message:
        return "upstox_inactive_segments"
    return "token_exchange_failed"


def _set_session_cookie(response: Response, user_id: int, settings, request: Request | None = None) -> None:
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
    # Also issue and set v2 session for /terminal
    try:
        from datetime import datetime, timezone, timedelta
        from oipulse.api.security import issue_session, set_session_cookies
        from oipulse.identity.permissions import ALL_PERMISSIONS

        store = getattr(request.app.state, "v2_session_store", None) if request else None
        record = issue_session(
            user_id=str(user_id),
            permissions=ALL_PERMISSIONS,
            at=datetime.now(timezone.utc),
            ttl=timedelta(seconds=settings.session_ttl_seconds),
        )
        if store is not None:
            store.put(record)
        set_session_cookies(
            response,
            record,
            secure=settings.is_production,
            ttl=timedelta(seconds=settings.session_ttl_seconds),
        )
    except Exception as exc:
        logger.warning("Could not issue v2 session cookies: %s", exc)



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
        logger.info("OAuth callback returned provider error: %s", error)
        return _login_error_redirect(settings, "oauth_denied")
    if not code or not state:
        return _login_error_redirect(settings, "invalid_request")

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
        return _login_error_redirect(settings, "invalid_state")

    # One-time consumption
    state_row.used_at = utc_now()
    await db.flush()

    # Exchange code for access token
    try:
        token_resp = await exchange_code_for_token(code)
    except UpstoxAuthError as exc:
        logger.warning("Token exchange failed: %s", exc, exc_info=True)
        return _login_error_redirect(settings, _map_upstox_auth_error(exc))

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
    _set_session_cookie(redirect_response, user.id, settings, request=request)
    return redirect_response


@router.post("/offline-session")
async def offline_session(request: Request, response: Response, db: DB):
    """Issue a local session for browsing stored snapshots without live Upstox access.

    Resolution order (single-user local terminal):
      1. User who owns the most recent OI snapshot
      2. Most recently updated active user
      3. 404 if neither exists
    """
    from app.core.config import get_settings

    settings = get_settings()

    snap_result = await db.execute(
        select(OISnapshot.user_id)
        .order_by(desc(OISnapshot.created_at))
        .limit(1)
    )
    user_id = snap_result.scalar_one_or_none()

    user: User | None = None
    if user_id is not None:
        user_result = await db.execute(
            select(User).where(User.id == user_id, User.is_active == True)
        )
        user = user_result.scalar_one_or_none()

    if user is None:
        user_result = await db.execute(
            select(User)
            .where(User.is_active == True)
            .order_by(desc(User.updated_at))
            .limit(1)
        )
        user = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No stored data yet — connect Upstox once during market hours.",
        )

    db.add(AuditLog(
        user_id=user.id,
        action="offline_login",
        resource_type="session",
        ip_address=request.client.host,
        status="success",
        details={"access_mode": "stored"},
    ))
    await db.commit()

    _set_session_cookie(response, user.id, settings, request=request)
    live = await has_live_market_access(user, db)
    return {
        "status": "ok",
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "live_market_access": live,
        "access_mode": "live" if live else "stored",
        "upstox_connected": live,
    }


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
    response.delete_cookie("oipulse_session")
    response.delete_cookie("oipulse_csrf")
    return {"status": "ok"}


@router.get("/me")
async def me(user: CurrentUser, db: DB):
    live = await has_live_market_access(user, db)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "live_market_access": live,
        "access_mode": "live" if live else "stored",
        "upstox_connected": live,
    }
