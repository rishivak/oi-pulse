"""Timeframe bucketing and aggregation primitives for Phase 2 analytics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Sequence


class Timeframe(str, Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"
    THIRTY_MINUTE = "30m"
    ONE_HOUR = "1h"


_TIMEFRAME_TO_MINUTES: dict[Timeframe, int] = {
    Timeframe.ONE_MINUTE: 1,
    Timeframe.FIVE_MINUTE: 5,
    Timeframe.FIFTEEN_MINUTE: 15,
    Timeframe.THIRTY_MINUTE: 30,
    Timeframe.ONE_HOUR: 60,
}


@dataclass(slots=True)
class MarketPoint:
    ts: datetime
    ltp: float | None
    oi: int | None
    volume: int | None


@dataclass(slots=True)
class AggregatedBucket:
    bucket_start: datetime
    bucket_end: datetime
    open_ltp: float | None
    close_ltp: float | None
    ltp_change: float | None
    open_oi: int | None
    close_oi: int | None
    oi_change: int | None
    oi_high: int | None
    oi_low: int | None
    volume: int | None
    points: int


def timeframe_minutes(timeframe: Timeframe | str) -> int:
    tf = Timeframe(timeframe)
    return _TIMEFRAME_TO_MINUTES[tf]


def bucket_start(ts: datetime, timeframe: Timeframe | str) -> datetime:
    """Return UTC bucket boundary for a timestamp and timeframe."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_utc = ts.astimezone(timezone.utc)

    mins = timeframe_minutes(timeframe)
    total_min = ts_utc.hour * 60 + ts_utc.minute
    floored = (total_min // mins) * mins

    return ts_utc.replace(
        hour=floored // 60,
        minute=floored % 60,
        second=0,
        microsecond=0,
    )


def bucket_end(start: datetime, timeframe: Timeframe | str) -> datetime:
    return start + timedelta(minutes=timeframe_minutes(timeframe))


def aggregate_points(points: Sequence[MarketPoint], timeframe: Timeframe | str) -> AggregatedBucket | None:
    """
    Aggregate ordered points into a single timeframe bucket.

    The caller must provide points that belong to one bucket; this function
    remains pure and deterministic for testability.
    """
    if not points:
        return None

    sorted_points = sorted(points, key=lambda p: p.ts)
    start = bucket_start(sorted_points[0].ts, timeframe)
    end = bucket_end(start, timeframe)

    first_ltp = next((p.ltp for p in sorted_points if p.ltp is not None), None)
    last_ltp = next((p.ltp for p in reversed(sorted_points) if p.ltp is not None), None)
    ltp_change = round(last_ltp - first_ltp, 2) if first_ltp is not None and last_ltp is not None else None

    first_oi = next((p.oi for p in sorted_points if p.oi is not None), None)
    last_oi = next((p.oi for p in reversed(sorted_points) if p.oi is not None), None)
    oi_change = (last_oi - first_oi) if first_oi is not None and last_oi is not None else None

    oi_values = [p.oi for p in sorted_points if p.oi is not None]
    vol_values = [p.volume for p in sorted_points if p.volume is not None]

    return AggregatedBucket(
        bucket_start=start,
        bucket_end=end,
        open_ltp=first_ltp,
        close_ltp=last_ltp,
        ltp_change=ltp_change,
        open_oi=first_oi,
        close_oi=last_oi,
        oi_change=oi_change,
        oi_high=max(oi_values) if oi_values else None,
        oi_low=min(oi_values) if oi_values else None,
        volume=max(vol_values) if vol_values else None,
        points=len(sorted_points),
    )
