"""Canonical bucketing — one flooring rule for the whole system.

The legacy codebase carried two incompatible implementations: one floored in IST and
converted to UTC, the other floored in UTC directly. They agreed for 1m/5m/15m/30m (which
divide evenly into the 05:30 offset) and disagreed at 1h. That class of bug silently
corrupts backtests, so v2 has exactly one authority.

Buckets are **session-anchored**: they align to the session open, not to the wall clock.
A 15-minute bucket in a session opening at 09:15 starts at 09:15, 09:30, 09:45 — not at
09:00. Anchoring to the wall clock would put the open in the middle of a bucket and make
the first bar of every day a partial one.

The session calendar itself is Phase 2. This module takes `session_open` as an argument
and hardcodes no dates — the legacy hardcoded holiday list, with entries its own comment
marked "indicative", is the failure mode being designed out.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from oipulse.core.clock import ensure_utc

__all__ = ["Timeframe", "bucket_end", "bucket_index", "bucket_start"]


class Timeframe(StrEnum):
    ONE_MINUTE = "1m"
    THREE_MINUTE = "3m"
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"
    THIRTY_MINUTE = "30m"
    ONE_HOUR = "1h"

    @property
    def minutes(self) -> int:
        return _MINUTES[self]


_MINUTES: dict[Timeframe, int] = {
    Timeframe.ONE_MINUTE: 1,
    Timeframe.THREE_MINUTE: 3,
    Timeframe.FIVE_MINUTE: 5,
    Timeframe.FIFTEEN_MINUTE: 15,
    Timeframe.THIRTY_MINUTE: 30,
    Timeframe.ONE_HOUR: 60,
}


def bucket_index(at: datetime, timeframe: Timeframe, session_open: datetime) -> int:
    """Return the zero-based bucket ordinal of *at* within the session.

    Negative for instants before the open, which is meaningful during pre-open.
    """
    at_utc = ensure_utc(at)
    open_utc = ensure_utc(session_open)
    elapsed = (at_utc - open_utc).total_seconds() / 60.0
    return int(elapsed // timeframe.minutes)


def bucket_start(at: datetime, timeframe: Timeframe, session_open: datetime) -> datetime:
    """Floor *at* to its session-anchored bucket boundary, in UTC."""
    open_utc = ensure_utc(session_open)
    idx = bucket_index(at, timeframe, open_utc)
    return open_utc + timedelta(minutes=idx * timeframe.minutes)


def bucket_end(at: datetime, timeframe: Timeframe, session_open: datetime) -> datetime:
    """Exclusive upper bound of the bucket containing *at*."""
    return bucket_start(at, timeframe, session_open) + timedelta(minutes=timeframe.minutes)
