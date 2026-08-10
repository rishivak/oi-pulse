from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    upstox_account: Mapped[UpstoxAccount | None] = relationship(back_populates="user", uselist=False)
    preferences: Mapped[UserPreference | None] = relationship(back_populates="user", uselist=False)


class UpstoxAccount(Base):
    __tablename__ = "upstox_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    upstox_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # Tokens stored as encrypted bytes — never logged or returned in API responses
    access_token_enc: Mapped[bytes] = mapped_column(nullable=False)
    access_token_iv: Mapped[bytes | None] = mapped_column(nullable=True)   # reserved for future manual GCM
    refresh_token_enc: Mapped[bytes | None] = mapped_column(nullable=True)
    refresh_token_iv: Mapped[bytes | None] = mapped_column(nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="upstox_account")


class OAuthState(Base):
    """Short-lived OAuth CSRF state tokens stored in DB (Redis preferred; DB fallback)."""
    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    state_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_key: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    default_underlying: Mapped[str] = mapped_column(String(50), default="NIFTY", nullable=False)
    default_interval_min: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    default_expiry_type: Mapped[str] = mapped_column(String(20), default="current", nullable=False)
    theme: Mapped[str] = mapped_column(String(20), default="dark", nullable=False)
    strike_window: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="preferences")
