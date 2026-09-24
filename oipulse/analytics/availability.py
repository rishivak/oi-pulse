"""Availability computation — `07-ANALYTICS.md` §3.

```
available_at = max(
    lookback_end,                 # the window is complete
    latest_input_available_at,    # every required input is actually available
    computed_at,                  # computation has finished
) + availability_delay
```

**Input readiness, never market time.** For a raw observation the availability is
`ingested_at`; for a derived dependency it is that dependency's own `available_at`,
recursively. Using `observed_at` would reintroduce look-ahead inside the very mechanism
built to prevent it, and would do so silently — the value would look plausible:

```
observation:  observed_at = 11:40:00   ingested_at = 11:44:00

observed_at-based:  available_at = 11:40:02   <- look-ahead. We did not hold
                                                the input until 11:44.
readiness-based:    available_at = 11:44:02   <- correct.
```

`availability_delay` is applied **after** all three conditions are met. It models
propagation, not computation — computation time is already captured by `computed_at`.
Adding it before the max would let a long delay be absorbed by a late input instead of
extending past it.

This module has no clock. Every timestamp is an argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from oipulse.analytics.values import MetricValue

__all__ = [
    "AvailabilityInputs",
    "available_at",
    "check_invariants",
    "dependency_availability",
]


@dataclass(frozen=True, slots=True)
class AvailabilityInputs:
    """Everything the formula needs, supplied explicitly.

    `raw_input_ingested_at` is `None` when a feature consumed no raw observation at
    all; that is different from "ready at the epoch" and must not be defaulted to one.
    """

    lookback_end: datetime
    computed_at: datetime
    availability_delay: timedelta = timedelta(0)
    #: Max `ingested_at` over contributing raw observations (`07` §3, raw row).
    raw_input_ingested_at: datetime | None = None
    #: `available_at` of every derived dependency (`07` §3, derived row).
    dependency_available_at: tuple[datetime, ...] = ()

    @property
    def latest_input_available_at(self) -> datetime | None:
        """The later of raw readiness and every dependency's own availability.

        A dependency chain therefore propagates: feature B cannot become available
        before feature A, because A's `available_at` is one of the values maxed here.
        """
        candidates = [
            t for t in (self.raw_input_ingested_at, *self.dependency_available_at) if t is not None
        ]
        return max(candidates) if candidates else None


def available_at(inputs: AvailabilityInputs) -> datetime:
    """Apply the formula. Pure; the result depends only on the arguments."""
    latest = inputs.latest_input_available_at
    base = max([inputs.lookback_end, inputs.computed_at, *([latest] if latest is not None else [])])
    return base + inputs.availability_delay


def check_invariants(value: datetime, inputs: AvailabilityInputs) -> list[str]:
    """The three invariants from `07` §3, returned as violations rather than raised.

    Returned so a conformance test can report every breach for every feature in one
    run instead of stopping at the first.
    """
    problems: list[str] = []
    if value < inputs.lookback_end:
        problems.append(
            f"available_at {value.isoformat()} precedes lookback_end "
            f"{inputs.lookback_end.isoformat()}"
        )
    if value < inputs.computed_at:
        problems.append(
            f"available_at {value.isoformat()} precedes computed_at "
            f"{inputs.computed_at.isoformat()}"
        )
    latest = inputs.latest_input_available_at
    if latest is not None and value < latest:
        problems.append(
            f"available_at {value.isoformat()} precedes latest input availability "
            f"{latest.isoformat()}"
        )
    return problems


def dependency_availability(dependencies: dict[str, MetricValue]) -> tuple[datetime, ...]:
    """Pull `available_at` out of each resolved dependency, in a stable order."""
    return tuple(dependencies[key].available_at for key in sorted(dependencies))
