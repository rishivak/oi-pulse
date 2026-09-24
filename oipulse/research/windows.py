"""The five window fields and the window-completion rule — `09-RESEARCH.md` §2.

> Every feature declares five window fields. This is the heart of the document.

| Field | Meaning |
|---|---|
| `lookback_start` | earliest observation the feature may consume |
| `lookback_end` | latest observation the feature may consume |
| `availability_time` | when the computed value may be consumed by a strategy |
| `forward_start` | earliest point of the outcome window |
| `forward_end` | latest point of the outcome window |

**The decision window and the outcome window are separate types, not two ranges in one
object.** `09` §2 keeps them in separate namespaces precisely so they cannot be
confused: outcome data is future-looking relative to the decision point, legitimate for
measuring what happened and never available to the decision itself. Making them
different types means a decision context physically cannot be handed a forward window.

No clock. Every timestamp is an argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from oipulse.core.errors import OIPulseError

__all__ = [
    "DecisionWindow",
    "ForwardWindow",
    "IncompleteWindow",
    "WindowCompleteness",
    "availability_time",
]


class IncompleteWindow(OIPulseError):
    """A forward window extends past the end of the available dataset."""


class WindowCompleteness(str):
    """How much of a forward window the dataset actually covers.

    A string subtype so it renders directly in a result without a mapping step.
    """

    COMPLETE = "complete"
    #: The dataset ends inside the window. Reported, never extrapolated.
    TRUNCATED = "truncated"
    #: The dataset has nothing at or after `forward_start`.
    EMPTY = "empty"


def availability_time(
    lookback_end: datetime,
    latest_input_available_at: datetime | None,
    computed_at: datetime,
    availability_delay: timedelta,
) -> datetime:
    """The window-completion rule, restated here so research applies the same formula.

        available_at = max(lookback_end, latest_input_available_at, computed_at)
                       + availability_delay

    Identical to `07-ANALYTICS.md` §3 by design: research must judge availability by
    exactly the rule that produced it, or a study would disagree with the pipeline
    about what was knowable and nobody could tell which was right.

    A late-arriving input (observed 11:40, ingested 11:44) pushes availability to
    11:44 + delay, not 11:40 + delay.
    """
    candidates = [lookback_end, computed_at]
    if latest_input_available_at is not None:
        candidates.append(latest_input_available_at)
    return max(candidates) + availability_delay


@dataclass(frozen=True, slots=True)
class DecisionWindow:
    """What a decision at `decision_time` was allowed to look at.

    Deliberately carries no forward bound. A decision context cannot reach the outcome
    window because this type has no field for it.
    """

    lookback_start: datetime
    lookback_end: datetime
    availability_time: datetime
    decision_time: datetime

    def __post_init__(self) -> None:
        if self.lookback_end < self.lookback_start:
            raise ValueError("lookback_end precedes lookback_start")
        if self.availability_time < self.lookback_end:
            raise ValueError("availability_time precedes lookback_end; the window is not complete")
        if self.decision_time < self.availability_time:
            raise ValueError(
                f"decision_time {self.decision_time.isoformat()} precedes "
                f"availability_time {self.availability_time.isoformat()}: the value "
                f"was not consumable at the decision point"
            )

    @property
    def lookback(self) -> timedelta:
        return self.lookback_end - self.lookback_start

    def admits(self, observed_at: datetime) -> bool:
        """May an observation at this market time be consumed by the decision?"""
        return self.lookback_start <= observed_at <= self.lookback_end


@dataclass(frozen=True, slots=True)
class ForwardWindow:
    """The outcome measurement window, relative to an event.

    Separate type from `DecisionWindow` by design (§2). Outcome data is inaccessible
    from any strategy or rule context.
    """

    event_time: datetime
    forward_start: datetime
    forward_end: datetime

    def __post_init__(self) -> None:
        if self.forward_end < self.forward_start:
            raise ValueError("forward_end precedes forward_start")
        if self.forward_start < self.event_time:
            raise ValueError(
                "forward_start precedes the event; an outcome window measures what "
                "happened after the event"
            )

    @staticmethod
    def of(event_time: datetime, horizon: timedelta) -> ForwardWindow:
        return ForwardWindow(event_time, event_time, event_time + horizon)

    @property
    def horizon(self) -> timedelta:
        return self.forward_end - self.event_time

    def contains(self, at: datetime) -> bool:
        return self.forward_start <= at <= self.forward_end

    def completeness(self, dataset_end: datetime) -> str:
        """How much of this window the dataset covers.

        Returning a state rather than raising lets a study *report* truncation, which
        `09` §3's honesty requirements need: a truncated window is excluded from
        outcome statistics and counted, not quietly treated as a short one.
        """
        if dataset_end < self.forward_start:
            return WindowCompleteness.EMPTY
        if dataset_end < self.forward_end:
            return WindowCompleteness.TRUNCATED
        return WindowCompleteness.COMPLETE

    def require_complete(self, dataset_end: datetime) -> None:
        state = self.completeness(dataset_end)
        if state != WindowCompleteness.COMPLETE:
            raise IncompleteWindow(
                f"forward window to {self.forward_end.isoformat()} is {state}: the "
                f"dataset ends at {dataset_end.isoformat()}. Future values are never "
                f"fabricated."
            )
