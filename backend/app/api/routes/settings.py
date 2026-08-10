"""User settings and preferences endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.db.models.user import UserPreference

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PreferenceUpdate(BaseModel):
    default_underlying: str | None = None
    default_interval_min: int | None = None
    default_expiry_type: str | None = None
    theme: str | None = None
    strike_window: int | None = None


@router.get("")
async def get_settings(user: CurrentUser, db: DB):
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()
    if prefs is None:
        prefs = UserPreference(user_id=user.id)
        db.add(prefs)
        await db.commit()
    return {
        "default_underlying": prefs.default_underlying,
        "default_interval_min": prefs.default_interval_min,
        "default_expiry_type": prefs.default_expiry_type,
        "theme": prefs.theme,
        "strike_window": prefs.strike_window,
    }


@router.patch("")
async def update_settings(body: PreferenceUpdate, user: CurrentUser, db: DB):
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()
    if prefs is None:
        prefs = UserPreference(user_id=user.id)
        db.add(prefs)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(prefs, field, value)

    await db.commit()
    return {"status": "updated"}
