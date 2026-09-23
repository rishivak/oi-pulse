"""The time authority: every timestamp in the system originates here.

`docs/design/00-OVERVIEW.md` §5 and `10-REPLAY.md` §3 require that no module reads the
wall clock directly. This is what makes replay and backtest determinism achievable rather
than hoped for: swapping the `Clock` implementation is sufficient to move the whole system
into replay, with no code path able to observe real time behind the harness's back.

`tools/check_clock_access.py` enforces the rule at CI time. This module is the single
authorised call site for `datetime.now()`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

__all__ = [
    "Clock",
    "FrozenClock",
    "ReplayClock",
    "SystemClock",
    "ensure_utc",
    "utc",
]


def utc(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> datetime:
    """Construct a timezone-aware UTC datetime.

    Provided so that callers never reach for a naive `datetime(...)`, which is the usual
    way an accidental local-time assumption enters a codebase.
    """
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return *value* as a UTC-aware datetime.

    A naive datetime is rejected rather than assumed to be UTC. Silently attaching a
    timezone to a naive value is how bitemporal correctness is lost: the two timestamps we
    care most about (`observed_at`, `ingested_at`) would drift by the local offset without
    anything failing.
    """
    if value.tzinfo is None:
        raise ValueError(
            "naive datetime rejected; construct with core.clock.utc() or attach tzinfo"
        )
    return value.astimezone(UTC)


@runtime_checkable
class Clock(Protocol):
    """Source of the current instant.

    Injected everywhere. Implementations must return timezone-aware UTC datetimes.
    """

    def now(self) -> datetime:  # pragma: no cover - protocol declaration
        ...


class SystemClock:
    """Wall-clock time. The only implementation that reads the host clock."""

    __slots__ = ()

    def now(self) -> datetime:
        # The single authorised wall-clock read in the codebase.
        # tools/check_clock_access.py allows this module and no other.
        return datetime.now(UTC)


class FrozenClock:
    """A clock pinned to one instant. Used by tests that must not observe elapsed time."""

    __slots__ = ("_at",)

    def __init__(self, at: datetime) -> None:
        self._at = ensure_utc(at)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = ensure_utc(at)


class ReplayClock:
    """A clock driven by the replay engine.

    Time advances only when the replay harness advances it, and never backwards —
    a backwards step would let a later decision observe an earlier state and silently
    corrupt a replay run (`10-REPLAY.md` §2).
    """

    __slots__ = ("_at",)

    def __init__(self, start: datetime) -> None:
        self._at = ensure_utc(start)

    def now(self) -> datetime:
        return self._at

    def advance_to(self, at: datetime) -> None:
        target = ensure_utc(at)
        if target < self._at:
            raise ValueError(
                f"replay clock cannot move backwards: {self._at.isoformat()} -> "
                f"{target.isoformat()}"
            )
        self._at = target

    def advance_by(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("replay clock cannot advance by a negative interval")
        self._at = self._at + delta
