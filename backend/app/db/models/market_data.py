from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketDataEvent(Base):
    """
    Normalized raw/latest market data events for futures/options instruments.

    This is additive for Phase 2 and does not replace Phase 1 snapshot tables.
    """

    __tablename__ = "market_data_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False)  # FUTURES | OPTIONS
    instrument_key: Mapped[str] = mapped_column(String(120), nullable=False)
    trading_symbol: Mapped[str | None] = mapped_column(String(120))

    underlying: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Numeric(12, 2))
    option_type: Mapped[str | None] = mapped_column(String(2))  # CE | PE

    ltp: Mapped[float | None] = mapped_column(Numeric(14, 4))
    oi: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)

    source: Mapped[str] = mapped_column(String(20), default="REST", nullable=False)  # REST | WS
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class OITimeBar(Base):
    """Aggregated timeframe bars derived from market_data_events."""

    __tablename__ = "oi_time_bars"
    __table_args__ = (
        UniqueConstraint(
            "instrument_key",
            "timeframe",
            "bucket_start_ts",
            name="uq_oi_time_bar",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False)  # FUTURES | OPTIONS
    instrument_key: Mapped[str] = mapped_column(String(120), nullable=False)
    trading_symbol: Mapped[str | None] = mapped_column(String(120))

    underlying: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Numeric(12, 2))
    option_type: Mapped[str | None] = mapped_column(String(2))

    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)  # 1m|5m|15m|30m|1h
    bucket_start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open_ltp: Mapped[float | None] = mapped_column(Numeric(14, 4))
    close_ltp: Mapped[float | None] = mapped_column(Numeric(14, 4))
    ltp_change: Mapped[float | None] = mapped_column(Numeric(14, 4))

    open_oi: Mapped[int | None] = mapped_column(BigInteger)
    close_oi: Mapped[int | None] = mapped_column(BigInteger)
    oi_change: Mapped[int | None] = mapped_column(BigInteger)
    oi_high: Mapped[int | None] = mapped_column(BigInteger)
    oi_low: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)

    interpretation: Mapped[str | None] = mapped_column(String(40))
    points_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    source: Mapped[str] = mapped_column(String(20), default="DERIVED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
