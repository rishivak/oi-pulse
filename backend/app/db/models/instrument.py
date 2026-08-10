from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Instrument(Base):
    """Master reference for index instruments used as underlyings."""
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Upstox canonical key e.g. "NSE_INDEX|Nifty 50"
    instrument_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(String(50), nullable=False)   # NIFTY, BANKNIFTY, SENSEX
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)             # NSE_INDEX, BSE_INDEX
    name: Mapped[str | None] = mapped_column(String(200))
    lot_size: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class OptionExpiry(Base):
    """Tracks active option expiry dates per underlying."""
    __tablename__ = "option_expiries"
    __table_args__ = (UniqueConstraint("underlying", "expiry_date", name="uq_expiry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
