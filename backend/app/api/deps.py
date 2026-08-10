"""Shared API dependencies (FastAPI Depends)."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import unsign_session_id
from app.db.engine import get_db
from app.db.models.user import UpstoxAccount, User
from app.core.security import decrypt_token

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


async def get_upstox_token(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Return a plaintext Upstox access token for the authenticated user."""
    result = await db.execute(
        select(UpstoxAccount).where(
            UpstoxAccount.user_id == user.id,
            UpstoxAccount.is_active == True,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Upstox account not connected. Please complete OAuth.",
        )
    return decrypt_token(account.access_token_enc)


CurrentUser = Annotated[User, Depends(get_current_user)]
UpstoxToken = Annotated[str, Depends(get_upstox_token)]
DB = Annotated[AsyncSession, Depends(get_db)]
