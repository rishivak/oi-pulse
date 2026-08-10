"""Snapshot persistence with idempotent upserts and Redis distributed lock.

The write sequence for each collection cycle:
  1. Acquire Redis lock for the (user, underlying, expiry, interval, bucket_ts) key.
  2. Fetch previous snapshot for this interval to compute OI deltas.
  3. Upsert OISnapshot (ON CONFLICT DO UPDATE).
  4. Upsert OIStrikeSnapshot rows in a single bulk operation.
  5. Write an OutboxEvent row in the same transaction.
  6. Commit.
  7. Release lock.

The ON CONFLICT strategy means a retry after partial failure is safe.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

# Convenience alias so excluded columns read cleanly
_exc = pg_insert

from app.core.config import get_settings
from app.db.models.instrument import OptionExpiry
from app.db.models.market_data import MarketDataEvent, OITimeBar
from app.db.models.operational import OutboxEvent
from app.db.models.snapshot import OISnapshot, OIStrikeSnapshot
from app.integrations.upstox.schemas import UpstoxOptionChainRow
from app.services.analytics_service import StrikeInput, classify_oi_interpretation, compute_snapshot_analytics

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 120   # lock auto-expires if the process crashes


def _interval_to_timeframe(interval_min: int) -> str | None:
    if interval_min == 1:
        return "1m"
    if interval_min == 5:
        return "5m"
    if interval_min == 15:
        return "15m"
    if interval_min == 30:
        return "30m"
    if interval_min == 60:
        return "1h"
    return None


def _lock_key(user_id: int, underlying: str, expiry_id: int, interval_min: int, bucket_ts: datetime) -> str:
    ts_str = bucket_ts.strftime("%Y%m%dT%H%M")
    return f"lock:snapshot:{user_id}:{underlying}:{expiry_id}:{interval_min}:{ts_str}"


async def _acquire_lock(redis: aioredis.Redis, key: str) -> bool:
    """SET NX with TTL — returns True if lock acquired."""
    return await redis.set(key, "1", nx=True, ex=_LOCK_TTL_SECONDS)


async def _release_lock(redis: aioredis.Redis, key: str) -> None:
    await redis.delete(key)


# ── Expiry helpers ────────────────────────────────────────────────────────────

async def get_or_create_expiry(
    session: AsyncSession,
    underlying: str,
    expiry_date: date,
) -> OptionExpiry:
    result = await session.execute(
        select(OptionExpiry).where(
            OptionExpiry.underlying == underlying,
            OptionExpiry.expiry_date == expiry_date,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = OptionExpiry(underlying=underlying, expiry_date=expiry_date)
        session.add(row)
        await session.flush()
    return row


# ── Snapshot upsert ───────────────────────────────────────────────────────────

async def save_snapshot(
    session: AsyncSession,
    user_id: int,
    underlying: str,
    expiry_date: date,
    interval_min: int,
    bucket_ts: datetime,
    chain_rows: Sequence[UpstoxOptionChainRow],
    spot_price: float | None,
) -> OISnapshot:
    """
    Idempotent snapshot write.  Acquires a Redis lock before writing to guard
    against concurrent workers for the same bucket.
    """
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    expiry = await get_or_create_expiry(session, underlying, expiry_date)
    lock_key = _lock_key(user_id, underlying, expiry.id, interval_min, bucket_ts)

    acquired = await _acquire_lock(redis, lock_key)
    if not acquired:
        # Another worker is writing this exact bucket — skip to avoid races
        logger.info("Lock held for %s; skipping duplicate write", lock_key)
        result = await session.execute(
            select(OISnapshot).where(
                OISnapshot.user_id == user_id,
                OISnapshot.underlying == underlying,
                OISnapshot.expiry_id == expiry.id,
                OISnapshot.interval_min == interval_min,
                OISnapshot.bucket_ts == bucket_ts,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            await redis.aclose()
            return existing
        # Lock was briefly held by a now-dead process; proceed after small delay
        import asyncio
        await asyncio.sleep(1)

    try:
        # Fetch previous snapshot for this interval (for delta computation)
        prev_result = await session.execute(
            select(OISnapshot)
            .where(
                OISnapshot.user_id == user_id,
                OISnapshot.underlying == underlying,
                OISnapshot.expiry_id == expiry.id,
                OISnapshot.interval_min == interval_min,
                OISnapshot.bucket_ts < bucket_ts,
            )
            .order_by(OISnapshot.bucket_ts.desc())
            .limit(1)
        )
        prev_snapshot = prev_result.scalar_one_or_none()

        # Build prev-OI lookup: strike → (call_prev_oi, put_prev_oi, call_prev_ltp, put_prev_ltp)
        prev_oi_map: dict[float, tuple[int | None, int | None, float | None, float | None]] = {}
        if prev_snapshot:
            prev_strikes_result = await session.execute(
                select(OIStrikeSnapshot).where(OIStrikeSnapshot.snapshot_id == prev_snapshot.id)
            )
            for ps in prev_strikes_result.scalars():
                prev_oi_map[float(ps.strike)] = (ps.call_oi, ps.put_oi, ps.call_ltp, ps.put_ltp)

        # Build analytics inputs
        strike_inputs = [
            StrikeInput(
                strike=row.strike_price,
                call_oi=row.call_options.market_data.oi if row.call_options and row.call_options.market_data else None,
                put_oi=row.put_options.market_data.oi if row.put_options and row.put_options.market_data else None,
                call_ltp=row.call_options.market_data.ltp if row.call_options and row.call_options.market_data else None,
                put_ltp=row.put_options.market_data.ltp if row.put_options and row.put_options.market_data else None,
                call_volume=row.call_options.market_data.volume if row.call_options and row.call_options.market_data else None,
                put_volume=row.put_options.market_data.volume if row.put_options and row.put_options.market_data else None,
                call_iv=row.call_options.option_greeks.iv if row.call_options and row.call_options.option_greeks else None,
                put_iv=row.put_options.option_greeks.iv if row.put_options and row.put_options.option_greeks else None,
                call_prev_oi=prev_oi_map.get(row.strike_price, (None, None, None, None))[0],
                put_prev_oi=prev_oi_map.get(row.strike_price, (None, None, None, None))[1],
                call_prev_ltp=prev_oi_map.get(row.strike_price, (None, None, None, None))[2],
                put_prev_ltp=prev_oi_map.get(row.strike_price, (None, None, None, None))[3],
            )
            for row in chain_rows
        ]

        analytics = compute_snapshot_analytics(strike_inputs)

        # Upsert OISnapshot
        snap_stmt = (
            pg_insert(OISnapshot)
            .values(
                user_id=user_id,
                underlying=underlying,
                expiry_id=expiry.id,
                interval_min=interval_min,
                bucket_ts=bucket_ts,
                spot_price=spot_price,
                total_call_oi=analytics.total_call_oi,
                total_put_oi=analytics.total_put_oi,
                pcr=analytics.pcr,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                constraint="uq_snapshot",
                set_={
                    "spot_price": spot_price,
                    "total_call_oi": analytics.total_call_oi,
                    "total_put_oi": analytics.total_put_oi,
                    "pcr": analytics.pcr,
                },
            )
            .returning(OISnapshot.id)
        )
        snap_result = await session.execute(snap_stmt)
        snapshot_id = snap_result.scalar_one()

        # Upsert all strike rows in bulk
        if strike_inputs:
            strike_values = [
                {
                    "snapshot_id": snapshot_id,
                    "strike": si.strike,
                    "call_oi": si.call_oi,
                    "put_oi": si.put_oi,
                    "call_ltp": si.call_ltp,
                    "put_ltp": si.put_ltp,
                    "call_volume": si.call_volume,
                    "put_volume": si.put_volume,
                    "call_iv": si.call_iv,
                    "put_iv": si.put_iv,
                    "call_prev_oi": si.call_prev_oi,
                    "put_prev_oi": si.put_prev_oi,
                    "call_oi_change": sa_delta.call_oi_change,
                    "put_oi_change": sa_delta.put_oi_change,
                }
                for si, sa_delta in zip(strike_inputs, analytics.strikes)
            ]
            strike_stmt = (
                pg_insert(OIStrikeSnapshot)
                .values(strike_values)
                .on_conflict_do_update(
                    constraint="uq_strike",
                    set_={
                        "call_oi": pg_insert(OIStrikeSnapshot).excluded.call_oi,
                        "put_oi": pg_insert(OIStrikeSnapshot).excluded.put_oi,
                        "call_ltp": pg_insert(OIStrikeSnapshot).excluded.call_ltp,
                        "put_ltp": pg_insert(OIStrikeSnapshot).excluded.put_ltp,
                        "call_oi_change": pg_insert(OIStrikeSnapshot).excluded.call_oi_change,
                        "put_oi_change": pg_insert(OIStrikeSnapshot).excluded.put_oi_change,
                    },
                )
            )
            await session.execute(strike_stmt)

        # Phase 2 additive persistence: normalized events + timeframe bars.
        timeframe = _interval_to_timeframe(interval_min)
        if timeframe:
            bar_end_ts = bucket_ts + timedelta(minutes=interval_min)
            event_rows: list[dict] = []
            bar_rows: list[dict] = []

            for si, chain_row in zip(strike_inputs, chain_rows):
                if chain_row.call_options and chain_row.call_options.instrument_key:
                    event_rows.append(
                        {
                            "event_ts": bucket_ts,
                            "instrument_type": "OPTIONS",
                            "instrument_key": chain_row.call_options.instrument_key,
                            "trading_symbol": chain_row.call_options.instrument_key,
                            "underlying": underlying,
                            "expiry_date": expiry_date,
                            "strike": si.strike,
                            "option_type": "CE",
                            "ltp": si.call_ltp,
                            "oi": si.call_oi,
                            "volume": si.call_volume,
                            "source": "REST",
                            "created_at": datetime.now(timezone.utc),
                        }
                    )
                    call_oi_change = (si.call_oi or 0) - (si.call_prev_oi or 0)
                    call_ltp_change = (
                        round((si.call_ltp or 0) - (si.call_prev_ltp or 0), 4)
                        if si.call_ltp is not None and si.call_prev_ltp is not None
                        else None
                    )
                    bar_rows.append(
                        {
                            "instrument_type": "OPTIONS",
                            "instrument_key": chain_row.call_options.instrument_key,
                            "trading_symbol": chain_row.call_options.instrument_key,
                            "underlying": underlying,
                            "expiry_date": expiry_date,
                            "strike": si.strike,
                            "option_type": "CE",
                            "timeframe": timeframe,
                            "bucket_start_ts": bucket_ts,
                            "bucket_end_ts": bar_end_ts,
                            "open_ltp": si.call_ltp,
                            "close_ltp": si.call_ltp,
                            "ltp_change": call_ltp_change,
                            "open_oi": si.call_prev_oi,
                            "close_oi": si.call_oi,
                            "oi_change": call_oi_change,
                            "oi_high": si.call_oi,
                            "oi_low": si.call_oi,
                            "volume": si.call_volume,
                            "interpretation": classify_oi_interpretation(call_ltp_change, call_oi_change).value,
                            "points_count": 1,
                            "source": "DERIVED",
                            "created_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )

                if chain_row.put_options and chain_row.put_options.instrument_key:
                    event_rows.append(
                        {
                            "event_ts": bucket_ts,
                            "instrument_type": "OPTIONS",
                            "instrument_key": chain_row.put_options.instrument_key,
                            "trading_symbol": chain_row.put_options.instrument_key,
                            "underlying": underlying,
                            "expiry_date": expiry_date,
                            "strike": si.strike,
                            "option_type": "PE",
                            "ltp": si.put_ltp,
                            "oi": si.put_oi,
                            "volume": si.put_volume,
                            "source": "REST",
                            "created_at": datetime.now(timezone.utc),
                        }
                    )
                    put_oi_change = (si.put_oi or 0) - (si.put_prev_oi or 0)
                    put_ltp_change = (
                        round((si.put_ltp or 0) - (si.put_prev_ltp or 0), 4)
                        if si.put_ltp is not None and si.put_prev_ltp is not None
                        else None
                    )
                    bar_rows.append(
                        {
                            "instrument_type": "OPTIONS",
                            "instrument_key": chain_row.put_options.instrument_key,
                            "trading_symbol": chain_row.put_options.instrument_key,
                            "underlying": underlying,
                            "expiry_date": expiry_date,
                            "strike": si.strike,
                            "option_type": "PE",
                            "timeframe": timeframe,
                            "bucket_start_ts": bucket_ts,
                            "bucket_end_ts": bar_end_ts,
                            "open_ltp": si.put_ltp,
                            "close_ltp": si.put_ltp,
                            "ltp_change": put_ltp_change,
                            "open_oi": si.put_prev_oi,
                            "close_oi": si.put_oi,
                            "oi_change": put_oi_change,
                            "oi_high": si.put_oi,
                            "oi_low": si.put_oi,
                            "volume": si.put_volume,
                            "interpretation": classify_oi_interpretation(put_ltp_change, put_oi_change).value,
                            "points_count": 1,
                            "source": "DERIVED",
                            "created_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )

            if event_rows:
                await session.execute(pg_insert(MarketDataEvent).values(event_rows))

            if bar_rows:
                bars_stmt = (
                    pg_insert(OITimeBar)
                    .values(bar_rows)
                    .on_conflict_do_update(
                        constraint="uq_oi_time_bar",
                        set_={
                            "trading_symbol": pg_insert(OITimeBar).excluded.trading_symbol,
                            "open_ltp": pg_insert(OITimeBar).excluded.open_ltp,
                            "close_ltp": pg_insert(OITimeBar).excluded.close_ltp,
                            "ltp_change": pg_insert(OITimeBar).excluded.ltp_change,
                            "open_oi": pg_insert(OITimeBar).excluded.open_oi,
                            "close_oi": pg_insert(OITimeBar).excluded.close_oi,
                            "oi_change": pg_insert(OITimeBar).excluded.oi_change,
                            "oi_high": pg_insert(OITimeBar).excluded.oi_high,
                            "oi_low": pg_insert(OITimeBar).excluded.oi_low,
                            "volume": pg_insert(OITimeBar).excluded.volume,
                            "interpretation": pg_insert(OITimeBar).excluded.interpretation,
                            "points_count": pg_insert(OITimeBar).excluded.points_count,
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                )
                await session.execute(bars_stmt)

        # Write outbox event in the same transaction (transactional outbox pattern)
        outbox = OutboxEvent(
            user_id=user_id,
            event_type="snapshot_created",
            payload={
                "snapshot_id": snapshot_id,
                "underlying": underlying,
                "expiry_date": expiry_date.isoformat(),
                "bucket_ts": bucket_ts.isoformat(),
                "interval_min": interval_min,
                "spot_price": spot_price,
                "total_call_oi": analytics.total_call_oi,
                "total_put_oi": analytics.total_put_oi,
                "pcr": float(analytics.pcr) if analytics.pcr else None,
                "net_oi_change": analytics.net_oi_change,
            },
        )
        session.add(outbox)
        await session.commit()

        logger.info(
            "Snapshot saved: user=%d underlying=%s expiry=%s interval=%dm bucket=%s",
            user_id, underlying, expiry_date, interval_min, bucket_ts.isoformat(),
        )

        # Eagerly refresh to get the full ORM object
        result = await session.execute(
            select(OISnapshot).where(OISnapshot.id == snapshot_id)
        )
        return result.scalar_one()

    finally:
        await _release_lock(redis, lock_key)
        await redis.aclose()
