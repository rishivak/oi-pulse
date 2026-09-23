"""Temporal query bounds — the type-level expression of the bitemporal model.

`docs/design/05-DATA_LIFECYCLE_PIT.md` §3 requires three distinct query modes and states:
*"There is no unbounded query for callers to reach for."* This module makes that a type
constraint rather than a convention — a repository method takes a `TemporalBound`, so
there is no signature through which an unbounded read can be requested.

The four stored time dimensions are `observed_at`, `ingested_at`, `computed_at` and
`available_at`. `decision_time` is an action/query concept supplied by a caller, never a
stored field (`05` §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from oipulse.core.clock import ensure_utc

__all__ = [
    "DecisionTime",
    "KnowledgeAt",
    "KnowledgeTime",
    "MarketTime",
    "MarketTruthAt",
    "TemporalBound",
    "TradableInformationAt",
]

# Distinct aliases so a market time is not silently passed where a knowledge time is meant.
# These are documentation-grade rather than enforced at runtime; the enforced part is the
# TemporalBound hierarchy below, which cannot be bypassed.
MarketTime = NewType("MarketTime", datetime)
KnowledgeTime = NewType("KnowledgeTime", datetime)
DecisionTime = NewType("DecisionTime", datetime)


@dataclass(frozen=True, slots=True)
class TemporalBound:
    """Base for the three query modes. Never instantiated directly."""

    def describe(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class MarketTruthAt(TemporalBound):
    """What was true in the market at `valid_time`, per everything known by `knowledge_as_of`.

    Both arguments are mandatory. `05` §3 removed the single-argument form deliberately:
    once corrections exist, "market truth at 11:40" has two defensible readings — the value
    believed then, or the corrected value — and defaulting the knowledge horizon to "now"
    is exactly the silent look-ahead the model exists to prevent.

    Market-truth mode is an explicit opt-in and is recorded on any study or run that uses it.
    """

    valid_time: datetime
    knowledge_as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_time", ensure_utc(self.valid_time))
        object.__setattr__(self, "knowledge_as_of", ensure_utc(self.knowledge_as_of))

    def describe(self) -> str:
        return (
            f"market_truth_at(valid_time={self.valid_time.isoformat()}, "
            f"knowledge_as_of={self.knowledge_as_of.isoformat()})"
        )


@dataclass(frozen=True, slots=True)
class KnowledgeAt(TemporalBound):
    """What OI Pulse knew at `t`: `observed_at <= t AND ingested_at <= t`.

    The default for research, replay and state reconstruction.
    """

    t: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", ensure_utc(self.t))

    def describe(self) -> str:
        return f"knowledge_at({self.t.isoformat()})"


@dataclass(frozen=True, slots=True)
class TradableInformationAt(TemporalBound):
    """What a strategy could legitimately consume at `t`.

    `KnowledgeAt` plus `available_at <= t`. The default for strategy execution and
    backtesting. Availability derives from input readiness, never from market time
    (`07-ANALYTICS.md` §3).
    """

    t: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", ensure_utc(self.t))

    def describe(self) -> str:
        return f"tradable_information_at({self.t.isoformat()})"
