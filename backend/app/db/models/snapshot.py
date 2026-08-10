from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OISnapshot(Base):
    """
    One row per (user, underlying, expiry, interval, bucket_ts).

    bucket_ts is the floor-rounded timestamp for the collection interval —
    e.g., 09:35:00 for a 5-minute bucket starting at 09:35.
    The unique constraint enforces idempotency at the DB level.
    """
    __tablename__ = "oi_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "underlying", "expiry_id", "interval_min", "bucket_ts",
            name="uq_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    underlying: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_id: Mapped[int] = mapped_column(Integer, ForeignKey("option_expiries.id"), nullable=False)
    interval_min: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spot_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    total_call_oi: Mapped[int | None] = mapped_column(BigInteger)
    total_put_oi: Mapped[int | None] = mapped_column(BigInteger)
    pcr: Mapped[float | None] = mapped_column(Numeric(10, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    strikes: Mapped[list[OIStrikeSnapshot]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class OIStrikeSnapshot(Base):
    """Per-strike OI data for a parent OISnapshot row."""
    __tablename__ = "oi_strike_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "strike", name="uq_strike"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("oi_snapshots.id", ondelete="CASCADE"), nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Current snapshot values
    call_oi: Mapped[int | None] = mapped_column(BigInteger)
    put_oi: Mapped[int | None] = mapped_column(BigInteger)
    call_ltp: Mapped[float | None] = mapped_column(Numeric(10, 2))
    put_ltp: Mapped[float | None] = mapped_column(Numeric(10, 2))
    call_volume: Mapped[int | None] = mapped_column(BigInteger)
    put_volume: Mapped[int | None] = mapped_column(BigInteger)
    call_iv: Mapped[float | None] = mapped_column(Numeric(8, 4))
    put_iv: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # Previous snapshot OI (copied from prior row for fast delta reads)
    call_prev_oi: Mapped[int | None] = mapped_column(BigInteger)
    put_prev_oi: Mapped[int | None] = mapped_column(BigInteger)

    # Pre-computed deltas stored at write time
    call_oi_change: Mapped[int | None] = mapped_column(BigInteger)
    put_oi_change: Mapped[int | None] = mapped_column(BigInteger)

    snapshot: Mapped[OISnapshot] = relationship(back_populates="strikes")


class MarketTick(Base):
    """Rolling LTP ticks for the underlying index — not a full tick store."""
    __tablename__ = "market_ticks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(50), nullable=False)
    ltp: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    tick_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
