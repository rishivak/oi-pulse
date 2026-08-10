"""Shared API dependencies (FastAPI Depends)."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token, unsign_session_id
from app.db.engine import get_db
from app.db.models.user import UpstoxAccount, User

logger = logging.getLogger(__name__)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the session cookie, returning the authenticated User."""
    signed = request.cookies.get("session_id")
    if not signed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    session_id = unsign_session_id(signed)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    # Session key format: "user:{user_id}"
    if not session_id.startswith("user:"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    try:
        user_id = int(session_id.split(":", 1)[1])
    except (ValueError, IndexError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def _load_active_upstox_account(
    user: User,
    db: AsyncSession,
) -> UpstoxAccount | None:
    result = await db.execute(
        select(UpstoxAccount).where(
            UpstoxAccount.user_id == user.id,
            UpstoxAccount.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def get_upstox_token(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Return a plaintext Upstox access token for the authenticated user."""
    account = await _load_active_upstox_account(user, db)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Upstox account not connected. Please complete OAuth.",
        )
    try:
        return decrypt_token(account.access_token_enc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Upstox token could not be decrypted. Please re-connect Upstox.",
        ) from exc


async def get_optional_upstox_token(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str | None:
    """Return a plaintext Upstox token when available; otherwise None (stored-data mode)."""
    account = await _load_active_upstox_account(user, db)
    if account is None:
        return None
    try:
        return decrypt_token(account.access_token_enc)
    except ValueError:
        logger.warning("Failed to decrypt Upstox token for user %s", user.id)
        return None


async def has_live_market_access(
    user: User,
    db: AsyncSession,
) -> bool:
    """True when an active Upstox account exists and its token decrypts."""
    account = await _load_active_upstox_account(user, db)
    if account is None:
        return False
    try:
        decrypt_token(account.access_token_enc)
        return True
    except ValueError:
        return False


CurrentUser = Annotated[User, Depends(get_current_user)]
UpstoxToken = Annotated[str, Depends(get_upstox_token)]
OptionalUpstoxToken = Annotated[str | None, Depends(get_optional_upstox_token)]
DB = Annotated[AsyncSession, Depends(get_db)]
