"""OI data endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.api.deps import DB, CurrentUser, OptionalUpstoxToken
from app.core.config import UNDERLYING_INSTRUMENT_KEYS
from app.db.models.instrument import OptionExpiry
from app.db.models.market_data import OITimeBar
from app.db.models.snapshot import OISnapshot, OIStrikeSnapshot
from app.integrations.upstox.client import UpstoxError, get_option_expiries
from app.services.analytics_service import classify_oi_interpretation, safe_change_pcr, safe_pcr
from app.services.timeframe_service import MarketPoint, Timeframe, aggregate_points, bucket_start

router = APIRouter(prefix="/api/oi", tags=["oi"])


@router.get("/timeframes")
async def supported_timeframes(_: CurrentUser):
    return {
        "default": Timeframe.THIRTY_MINUTE.value,
        "values": [
            Timeframe.ONE_MINUTE.value,
            Timeframe.FIVE_MINUTE.value,
            Timeframe.FIFTEEN_MINUTE.value,
            Timeframe.THIRTY_MINUTE.value,
            Timeframe.ONE_HOUR.value,
        ],
    }


@router.get("/underlyings")
async def list_underlyings(_: CurrentUser):
    return {"underlyings": list(UNDERLYING_INSTRUMENT_KEYS.keys())}


async def _stored_expiry_strings(
    db: DB,
    user: CurrentUser,
    underlying: str,
) -> list[str]:
    """Return expiry dates available offline from option_expiries and user snapshots."""
    underlying_upper = underlying.upper()

    expiry_result = await db.execute(
        select(OptionExpiry.expiry_date)
        .where(OptionExpiry.underlying == underlying_upper)
        .order_by(OptionExpiry.expiry_date)
    )
    dates = {row[0] for row in expiry_result.all()}

    snap_result = await db.execute(
        select(OptionExpiry.expiry_date)
        .join(OISnapshot, OISnapshot.expiry_id == OptionExpiry.id)
        .where(
            OISnapshot.user_id == user.id,
            OISnapshot.underlying == underlying_upper,
        )
        .distinct()
    )
    dates.update(row[0] for row in snap_result.all())

    return [d.isoformat() for d in sorted(dates)]


@router.get("/expiries")
async def list_expiries(
    underlying: str,
    user: CurrentUser,
    token: OptionalUpstoxToken,
    db: DB,
):
    instrument_key = UNDERLYING_INSTRUMENT_KEYS.get(underlying.upper())
    if not instrument_key:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown underlying: {underlying}")

    if token:
        try:
            expiry_strings = await get_option_expiries(token, instrument_key)

            # Sync to DB when live Upstox is available
            for es in expiry_strings:
                expiry_date = date.fromisoformat(es)
                result = await db.execute(
                    select(OptionExpiry).where(
                        OptionExpiry.underlying == underlying.upper(),
                        OptionExpiry.expiry_date == expiry_date,
                    )
                )
                if result.scalar_one_or_none() is None:
                    db.add(OptionExpiry(underlying=underlying.upper(), expiry_date=expiry_date))
            await db.commit()

            return {"underlying": underlying.upper(), "expiries": expiry_strings}
        except UpstoxError:
            pass  # fall through to stored expiries

    stored = await _stored_expiry_strings(db, user, underlying)
    return {"underlying": underlying.upper(), "expiries": stored}


@router.get("/latest")
async def latest_snapshot(
    underlying: str,
    expiry_date: date,
    interval_min: int = Query(default=5, ge=1),
    user: CurrentUser = None,
    db: DB = None,
):
    result = await db.execute(
        select(OISnapshot)
        .options(selectinload(OISnapshot.strikes))
        .where(
            OISnapshot.user_id == user.id,
            OISnapshot.underlying == underlying.upper(),
            OISnapshot.interval_min == interval_min,
        )
        .order_by(desc(OISnapshot.bucket_ts))
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        return {"snapshot": None}
    return _serialize_snapshot(snapshot)


@router.get("/history")
async def oi_history(
    underlying: str,
    expiry_date: date,
    interval_min: int = Query(default=5, ge=1),
    limit: int = Query(default=50, le=500),
    user: CurrentUser = None,
    db: DB = None,
):
    expiry_result = await db.execute(
        select(OptionExpiry).where(
            OptionExpiry.underlying == underlying.upper(),
            OptionExpiry.expiry_date == expiry_date,
        )
    )
    expiry = expiry_result.scalar_one_or_none()
    if expiry is None:
        return {"snapshots": []}

    result = await db.execute(
        select(OISnapshot)
        .where(
            OISnapshot.user_id == user.id,
            OISnapshot.underlying == underlying.upper(),
            OISnapshot.expiry_id == expiry.id,
            OISnapshot.interval_min == interval_min,
        )
        .order_by(desc(OISnapshot.bucket_ts))
        .limit(limit)
    )
    snapshots = list(result.scalars())
    return {"snapshots": [_serialize_snapshot_summary(s) for s in reversed(snapshots)]}


@router.get("/trending")
async def trending_oi(
    underlying: str,
    expiry_date: date,
    interval_min: int = Query(default=5, ge=1),
    limit: int = Query(default=20, le=100),
    user: CurrentUser = None,
    db: DB = None,
):
    """Return ordered list of snapshots for the Trending OI table."""
    expiry_result = await db.execute(
        select(OptionExpiry).where(
            OptionExpiry.underlying == underlying.upper(),
            OptionExpiry.expiry_date == expiry_date,
        )
    )
    expiry = expiry_result.scalar_one_or_none()
    if expiry is None:
        return {"rows": []}

    result = await db.execute(
        select(OISnapshot)
        .where(
            OISnapshot.user_id == user.id,
            OISnapshot.underlying == underlying.upper(),
            OISnapshot.expiry_id == expiry.id,
            OISnapshot.interval_min == interval_min,
        )
        .order_by(desc(OISnapshot.bucket_ts))
        .limit(limit)
    )
    snapshots = list(result.scalars())

    rows = []
    for i, snap in enumerate(snapshots):
        prev = snapshots[i + 1] if i + 1 < len(snapshots) else None
        rows.append({
            "bucket_ts": snap.bucket_ts.isoformat(),
            "spot_price": float(snap.spot_price) if snap.spot_price else None,
            "total_call_oi": snap.total_call_oi,
            "total_put_oi": snap.total_put_oi,
            "pcr": float(snap.pcr) if snap.pcr else None,
            "call_oi_change": (snap.total_call_oi - prev.total_call_oi) if prev and snap.total_call_oi and prev.total_call_oi else None,
            "put_oi_change": (snap.total_put_oi - prev.total_put_oi) if prev and snap.total_put_oi and prev.total_put_oi else None,
        })

    return {"underlying": underlying.upper(), "interval_min": interval_min, "rows": list(reversed(rows))}


@router.get("/strikes")
async def strike_oi(
    underlying: str,
    expiry_date: date,
    interval_min: int = Query(default=5, ge=1),
    atm_range: int = Query(default=10, ge=0),  # ±N strikes around ATM; 0 = all
    user: CurrentUser = None,
    db: DB = None,
):
    expiry_result = await db.execute(
        select(OptionExpiry).where(
            OptionExpiry.underlying == underlying.upper(),
            OptionExpiry.expiry_date == expiry_date,
        )
    )
    expiry = expiry_result.scalar_one_or_none()
    if expiry is None:
        return {"strikes": []}

    snap_result = await db.execute(
        select(OISnapshot)
        .options(selectinload(OISnapshot.strikes))
        .where(
            OISnapshot.user_id == user.id,
            OISnapshot.underlying == underlying.upper(),
            OISnapshot.expiry_id == expiry.id,
            OISnapshot.interval_min == interval_min,
        )
        .order_by(desc(OISnapshot.bucket_ts))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    if snapshot is None:
        return {"strikes": [], "snapshot_id": None}

    strikes = sorted(snapshot.strikes, key=lambda s: float(s.strike))

    if atm_range > 0 and snapshot.spot_price:
        spot = float(snapshot.spot_price)
        # Find ATM strike (nearest to spot)
        atm = min(strikes, key=lambda s: abs(float(s.strike) - spot))
        atm_idx = strikes.index(atm)
        strikes = strikes[max(0, atm_idx - atm_range): atm_idx + atm_range + 1]

    return {
        "snapshot_id": snapshot.id,
        "bucket_ts": snapshot.bucket_ts.isoformat(),
        "spot_price": float(snapshot.spot_price) if snapshot.spot_price else None,
        "strikes": [_serialize_strike(s) for s in strikes],
    }


@router.get("/heatmap")
async def oi_heatmap(
    underlying: str,
    expiry_date: date,
    interval_min: int = Query(default=5, ge=1),
    user: CurrentUser = None,
    db: DB = None,
):
    """Return strike/OI-change data shaped for the heatmap component."""
    strikes_resp = await strike_oi(
        underlying=underlying,
        expiry_date=expiry_date,
        interval_min=interval_min,
        atm_range=0,
        user=user,
        db=db,
    )
    heatmap_rows = [
        {
            "strike": s["strike"],
            "call_oi_change": s["call_oi_change"],
            "put_oi_change": s["put_oi_change"],
        }
        for s in strikes_resp.get("strikes", [])
    ]
    return {
        "spot_price": strikes_resp.get("spot_price"),
        "bucket_ts": strikes_resp.get("bucket_ts"),
        "rows": heatmap_rows,
    }


@router.get("/history-bars")
async def oi_history_bars(
    underlying: str,
    expiry_date: date,
    timeframe: str = Query(default=Timeframe.THIRTY_MINUTE.value),
    for_date: date | None = None,
    limit: int = Query(default=300, le=2000),
    user: CurrentUser = None,
    db: DB = None,
):
    """
    Phase 2 additive endpoint.

    Returns timeframe-aggregated OI/LTP bars and interpretation from existing
    snapshot history without changing underlying collection frequency.
    """
    tf = Timeframe(timeframe)

    expiry_result = await db.execute(
        select(OptionExpiry).where(
            OptionExpiry.underlying == underlying.upper(),
            OptionExpiry.expiry_date == expiry_date,
        )
    )
    expiry = expiry_result.scalar_one_or_none()
    if expiry is None:
        return {"underlying": underlying.upper(), "timeframe": tf.value, "rows": []}

    # Pull recent snapshots for this contract across intervals. We normalize into
    # points, then bucket them by requested timeframe.
    result = await db.execute(
        select(OISnapshot)
        .where(
            OISnapshot.user_id == user.id,
            OISnapshot.underlying == underlying.upper(),
            OISnapshot.expiry_id == expiry.id,
        )
        .order_by(desc(OISnapshot.bucket_ts))
        .limit(limit)
    )
    snapshots = list(reversed(list(result.scalars())))

    if for_date is not None:
        snapshots = [
            s
            for s in snapshots
            if s.bucket_ts.astimezone().date() == for_date
        ]

    points: list[MarketPoint] = []
    for s in snapshots:
        total_oi = None
        if s.total_call_oi is not None and s.total_put_oi is not None:
            total_oi = s.total_call_oi + s.total_put_oi
        points.append(
            MarketPoint(
                ts=s.bucket_ts,
                ltp=float(s.spot_price) if s.spot_price is not None else None,
                oi=total_oi,
                volume=None,
            )
        )

    grouped: dict = {}
    for p in points:
        b_start = bucket_start(p.ts, tf)
        grouped.setdefault(b_start, []).append(p)

    rows = []
    prev_close_oi: int | None = None
    for b_start in sorted(grouped.keys()):
        agg = aggregate_points(grouped[b_start], tf)
        if agg is None:
            continue

        call_oi = None
        put_oi = None
        # Preserve current phase-1 aggregate series where available
        matching = [s for s in snapshots if bucket_start(s.bucket_ts, tf) == b_start]
        if matching:
            call_oi = matching[-1].total_call_oi
            put_oi = matching[-1].total_put_oi

        call_oi_change = None
        put_oi_change = None
        if len(matching) >= 2:
            curr = matching[-1]
            prev = matching[-2]
            if curr.total_call_oi is not None and prev.total_call_oi is not None:
                call_oi_change = curr.total_call_oi - prev.total_call_oi
            if curr.total_put_oi is not None and prev.total_put_oi is not None:
                put_oi_change = curr.total_put_oi - prev.total_put_oi

        # Fallback where only combined OI is available
        if agg.close_oi is not None and prev_close_oi is not None and agg.oi_change is None:
            agg_oi_change = agg.close_oi - prev_close_oi
        else:
            agg_oi_change = agg.oi_change
        prev_close_oi = agg.close_oi

        rows.append(
            {
                "bucket_start": agg.bucket_start.isoformat(),
                "bucket_end": agg.bucket_end.isoformat(),
                "open_ltp": agg.open_ltp,
                "close_ltp": agg.close_ltp,
                "ltp_change": agg.ltp_change,
                "open_oi": agg.open_oi,
                "close_oi": agg.close_oi,
                "oi_change": agg_oi_change,
                "oi_high": agg.oi_high,
                "oi_low": agg.oi_low,
                "volume": agg.volume,
                "interpretation": classify_oi_interpretation(agg.ltp_change, agg_oi_change).value,
                "total_call_oi": call_oi,
                "total_put_oi": put_oi,
                "call_oi_change": call_oi_change,
                "put_oi_change": put_oi_change,
                "pcr": safe_pcr(put_oi=put_oi, call_oi=call_oi),
                "oi_change_pcr": safe_change_pcr(
                    put_oi_change=put_oi_change,
                    call_oi_change=call_oi_change,
                ),
            }
        )

    return {
        "underlying": underlying.upper(),
        "expiry_date": expiry_date.isoformat(),
        "timeframe": tf.value,
        "rows": rows,
    }


@router.get("/futures")
async def futures_oi_analytics(
    underlying: str,
    timeframe: str = Query(default=Timeframe.THIRTY_MINUTE.value),
    expiry_date: date | None = None,
    limit: int = Query(default=200, le=1000),
    user: CurrentUser = None,
    db: DB = None,
):
    """Phase 2 futures OI analytics view sourced from normalized bars."""
    tf = Timeframe(timeframe)

    query = (
        select(OITimeBar)
        .where(
            OITimeBar.instrument_type == "FUTURES",
            OITimeBar.underlying == underlying.upper(),
            OITimeBar.timeframe == tf.value,
        )
        .order_by(desc(OITimeBar.bucket_start_ts))
        .limit(limit)
    )
    if expiry_date is not None:
        query = query.where(OITimeBar.expiry_date == expiry_date)

    result = await db.execute(query)
    bars = list(reversed(list(result.scalars())))

    return {
        "underlying": underlying.upper(),
        "timeframe": tf.value,
        "rows": [
            {
                "instrument_key": b.instrument_key,
                "trading_symbol": b.trading_symbol,
                "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                "bucket_start": b.bucket_start_ts.isoformat(),
                "bucket_end": b.bucket_end_ts.isoformat(),
                "ltp": float(b.close_ltp) if b.close_ltp is not None else None,
                "ltp_change": float(b.ltp_change) if b.ltp_change is not None else None,
                "oi": b.close_oi,
                "oi_change": b.oi_change,
                "volume": b.volume,
                "interpretation": b.interpretation,
            }
            for b in bars
        ],
    }


@router.get("/options/summary")
async def options_oi_summary(
    underlying: str,
    expiry_date: date,
    timeframe: str = Query(default=Timeframe.THIRTY_MINUTE.value),
    bucket_ts: datetime | None = None,
    user: CurrentUser = None,
    db: DB = None,
):
    """Phase 2 options aggregate summary: Call/Put OI, delta OI, PCR and change PCR."""
    tf = Timeframe(timeframe)

    query = (
        select(OITimeBar)
        .where(
            OITimeBar.instrument_type == "OPTIONS",
            OITimeBar.underlying == underlying.upper(),
            OITimeBar.expiry_date == expiry_date,
            OITimeBar.timeframe == tf.value,
            OITimeBar.option_type.in_(["CE", "PE"]),
        )
    )

    if bucket_ts is not None:
        bucket = bucket_start(bucket_ts, tf)
        query = query.where(OITimeBar.bucket_start_ts == bucket)
    else:
        latest_result = await db.execute(
            select(OITimeBar.bucket_start_ts)
            .where(
                OITimeBar.instrument_type == "OPTIONS",
                OITimeBar.underlying == underlying.upper(),
                OITimeBar.expiry_date == expiry_date,
                OITimeBar.timeframe == tf.value,
            )
            .order_by(desc(OITimeBar.bucket_start_ts))
            .limit(1)
        )
        latest_bucket = latest_result.scalar_one_or_none()
        if latest_bucket is None:
            return {
                "underlying": underlying.upper(),
                "expiry_date": expiry_date.isoformat(),
                "timeframe": tf.value,
                "bucket_start": None,
                "summary": None,
            }
        query = query.where(OITimeBar.bucket_start_ts == latest_bucket)

    result = await db.execute(query)
    rows = list(result.scalars())
    if not rows:
        return {
            "underlying": underlying.upper(),
            "expiry_date": expiry_date.isoformat(),
            "timeframe": tf.value,
            "bucket_start": None,
            "summary": None,
        }

    call_rows = [r for r in rows if r.option_type == "CE"]
    put_rows = [r for r in rows if r.option_type == "PE"]

    total_call_oi = sum((r.close_oi or 0) for r in call_rows)
    total_put_oi = sum((r.close_oi or 0) for r in put_rows)
    call_oi_change = sum((r.oi_change or 0) for r in call_rows)
    put_oi_change = sum((r.oi_change or 0) for r in put_rows)

    first_bucket = min(rows, key=lambda r: r.bucket_start_ts)
    return {
        "underlying": underlying.upper(),
        "expiry_date": expiry_date.isoformat(),
        "timeframe": tf.value,
        "bucket_start": first_bucket.bucket_start_ts.isoformat(),
        "summary": {
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "call_oi_change": call_oi_change,
            "put_oi_change": put_oi_change,
            "pcr": safe_pcr(put_oi=total_put_oi, call_oi=total_call_oi),
            "oi_change_pcr": safe_change_pcr(
                put_oi_change=put_oi_change,
                call_oi_change=call_oi_change,
            ),
        },
    }


# ── Serializers ───────────────────────────────────────────────────────────────

def _serialize_snapshot(snap: OISnapshot) -> dict:
    return {
        "id": snap.id,
        "underlying": snap.underlying,
        "bucket_ts": snap.bucket_ts.isoformat(),
        "interval_min": snap.interval_min,
        "spot_price": float(snap.spot_price) if snap.spot_price else None,
        "total_call_oi": snap.total_call_oi,
        "total_put_oi": snap.total_put_oi,
        "pcr": float(snap.pcr) if snap.pcr else None,
        "strikes": [_serialize_strike(s) for s in sorted(snap.strikes, key=lambda x: float(x.strike))],
    }


def _serialize_snapshot_summary(snap: OISnapshot) -> dict:
    return {
        "id": snap.id,
        "bucket_ts": snap.bucket_ts.isoformat(),
        "spot_price": float(snap.spot_price) if snap.spot_price else None,
        "total_call_oi": snap.total_call_oi,
        "total_put_oi": snap.total_put_oi,
        "pcr": float(snap.pcr) if snap.pcr else None,
    }


def _serialize_strike(s: OIStrikeSnapshot) -> dict:
    return {
        "strike": float(s.strike),
        "call_oi": s.call_oi,
        "put_oi": s.put_oi,
        "call_ltp": float(s.call_ltp) if s.call_ltp else None,
        "put_ltp": float(s.put_ltp) if s.put_ltp else None,
        "call_volume": s.call_volume,
        "put_volume": s.put_volume,
        "call_iv": float(s.call_iv) if s.call_iv else None,
        "put_iv": float(s.put_iv) if s.put_iv else None,
        "call_oi_change": s.call_oi_change,
        "put_oi_change": s.put_oi_change,
    }
