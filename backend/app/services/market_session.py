"""Market session calendar for NSE/BSE India.

The collector uses this to:
  - Skip data fetches when the market is closed.
  - Emit collector_status_changed events when session transitions occur.
  - Support catch-up mode: detect missed buckets after a restart.

Trading session: 09:15 – 15:30 IST, Mon–Fri, excluding NSE holidays.

Holidays are seeded as a static list for the current year. For production,
fetch the NSE holiday list from the Upstox Market Holidays endpoint or an
open dataset and refresh it at the start of each trading year.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE market hours
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# NSE holidays 2026 (incomplete placeholder — update with official NSE calendar)
_NSE_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi (indicative)
    date(2026, 4, 3),    # Good Friday (indicative)
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 4),   # Diwali Laxmi Puja (indicative)
    date(2026, 12, 25),  # Christmas
}


def _ist_now() -> datetime:
    return datetime.now(IST)


def is_market_open(dt: datetime | None = None) -> bool:
    """Return True if *dt* (IST) falls within the NSE cash session."""
    dt_ist = (dt or _ist_now()).astimezone(IST)
    if dt_ist.weekday() >= 5:  # Saturday/Sunday
        return False
    today = dt_ist.date()
    if today in _NSE_HOLIDAYS_2026:
        return False
    t = dt_ist.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def get_bucket_timestamp(dt: datetime, interval_min: int) -> datetime:
    """
    Floor *dt* to the nearest *interval_min* boundary in IST, then return
    in UTC.  This is the canonical bucket_ts stored in oi_snapshots.

    Example: 09:34:45 IST with interval_min=5 → 09:30:00 IST → UTC.
    """
    dt_ist = dt.astimezone(IST)
    total_min = dt_ist.hour * 60 + dt_ist.minute
    bucketed_min = (total_min // interval_min) * interval_min
    bucketed = dt_ist.replace(
        hour=bucketed_min // 60,
        minute=bucketed_min % 60,
        second=0,
        microsecond=0,
    )
    return bucketed.astimezone(timezone.utc)


def get_trading_buckets_for_day(day: date, interval_min: int) -> list[datetime]:
    """Return all bucket timestamps for a trading day at *interval_min* spacing."""
    buckets: list[datetime] = []
    start = IST.localize(datetime.combine(day, MARKET_OPEN))
    end = IST.localize(datetime.combine(day, MARKET_CLOSE))
    current = start
    while current <= end:
        buckets.append(current.astimezone(timezone.utc))
        current += timedelta(minutes=interval_min)
    return buckets


def missed_buckets(
    last_collected: datetime | None,
    interval_min: int,
    now: datetime | None = None,
) -> list[datetime]:
    """
    Return UTC bucket timestamps that should have been collected between
    *last_collected* and *now* but weren't — for restart catch-up.
    Only includes buckets within market hours.
    """
    now = now or datetime.now(timezone.utc)
    if last_collected is None:
        return []

    current_bucket = get_bucket_timestamp(now, interval_min)
    last_bucket = get_bucket_timestamp(last_collected, interval_min)

    missed: list[datetime] = []
    probe = last_bucket + timedelta(minutes=interval_min)
    while probe < current_bucket:
        probe_ist = probe.astimezone(IST)
        if is_market_open(probe_ist):
            missed.append(probe)
        probe += timedelta(minutes=interval_min)
    return missed
