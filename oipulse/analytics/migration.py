"""OI migration as a tracked entity — `07-ANALYTICS.md` §5.

Migration is **not a per-tick number**. It is a lifecycle-tracked analytical
observation, because the brief's §13 requires it to be researchable:

```
detect      candidate displacement exceeds threshold over the window
            -> OIMigration(status=FORMING, first_observed_at)
confirm     persists across N consecutive windows, magnitude sustained
            -> status=CONFIRMED
track       destination strike updated as it continues; last_observed_at advances
fade        displacement reverses or decays below threshold
            -> status=FADED, duration finalized
```

The worked case from the brief — `25,000 PE -> 25,200 PE -> 25,300 PE` — is **one**
migration entity with an advancing destination and a growing duration, not three
unrelated observations. That is precisely what makes "how do migrations of this shape
resolve?" answerable, and it is why this is a tracker rather than a feature.

Purity is preserved: `advance` is a pure function from `(state of tracker, observation)`
to a **new** tracker state. Nothing here reads a clock or a store; every timestamp is an
argument. The tracker is therefore replayable — feeding the same observation sequence
always yields the same entities.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "MigrationDirection",
    "MigrationStatus",
    "MigrationTracker",
    "OIMigration",
    "OIMigrationObservation",
]


class MigrationStatus(StrEnum):
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    FADED = "FADED"


class MigrationDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True, slots=True)
class OIMigrationObservation:
    """One window's measurement, as supplied by the caller.

    `magnitude` is the OI-weighted displacement in strike points produced by
    `PUT_OI_MIGRATION` / `CALL_OI_MIGRATION`; the tracker does not recompute it, so
    there is exactly one definition of the quantity.
    """

    observed_at: datetime
    expiry_id: int
    option_type: str
    origin_strike: Decimal
    destination_strike: Decimal
    magnitude: Decimal

    @property
    def direction(self) -> MigrationDirection:
        return (
            MigrationDirection.UP
            if self.destination_strike >= self.origin_strike
            else MigrationDirection.DOWN
        )


@dataclass(frozen=True, slots=True)
class OIMigration:
    """A tracked migration entity (`02-DATA_MODEL.md` §6, `metric_oi_migrations`).

    Immutable: `advance` returns a new instance. A mutated entity would make two
    holders of "the same" migration disagree, and the history a researcher reads would
    depend on when they read it.
    """

    expiry_id: int
    option_type: str
    origin_strike: Decimal
    destination_strike: Decimal
    direction: MigrationDirection
    magnitude: Decimal
    status: MigrationStatus
    first_observed_at: datetime
    last_observed_at: datetime
    windows: int = 1
    evidence: tuple[str, ...] = ()

    @property
    def duration(self) -> timedelta:
        return self.last_observed_at - self.first_observed_at

    @property
    def key(self) -> tuple[int, str, str]:
        """Identity while tracking: one live migration per expiry, side and direction.

        The destination is deliberately NOT part of the key -- an advancing
        destination is the same migration continuing, which is the whole point of
        §5's worked case.
        """
        return (self.expiry_id, self.option_type, self.direction.value)

    @property
    def is_live(self) -> bool:
        return self.status is not MigrationStatus.FADED

    def spans(self, strike_low: Decimal, strike_high: Decimal) -> bool:
        """Is the magnitude within the chain's strike range?

        A property test asserts this: a displacement larger than the chain itself
        would mean the formula, not the market, produced the number.
        """
        return abs(self.magnitude) <= abs(strike_high - strike_low)


class MigrationTracker:
    """Advances a set of migrations across windows. Pure and deterministic.

    `confirm_after` is the N of "persists across N consecutive windows". It is a
    constructor argument rather than a constant so a research run can state the value
    it used; the default of 3 is the tracker's declared convention.
    """

    __slots__ = ("_confirm_after", "_faded", "_live", "_threshold")

    def __init__(self, *, threshold: Decimal = Decimal("50"), confirm_after: int = 3) -> None:
        self._threshold = threshold
        self._confirm_after = confirm_after
        self._live: dict[tuple[int, str, str], OIMigration] = {}
        self._faded: list[OIMigration] = []

    @property
    def threshold(self) -> Decimal:
        return self._threshold

    @property
    def confirm_after(self) -> int:
        return self._confirm_after

    def live(self) -> tuple[OIMigration, ...]:
        return tuple(self._live[key] for key in sorted(self._live))

    def faded(self) -> tuple[OIMigration, ...]:
        return tuple(self._faded)

    def all(self) -> tuple[OIMigration, ...]:
        return (*self.live(), *self.faded())

    def observe(self, observation: OIMigrationObservation) -> OIMigration | None:
        """Apply one window's measurement.

        Below threshold, an existing migration **fades** rather than being deleted: a
        migration that stopped is a fact research needs, and its duration is what makes
        "how do migrations of this shape resolve?" answerable.
        """
        key = (observation.expiry_id, observation.option_type, observation.direction.value)
        existing = self._live.get(key)

        if abs(observation.magnitude) < self._threshold:
            if existing is not None:
                faded = replace(
                    existing,
                    status=MigrationStatus.FADED,
                    last_observed_at=observation.observed_at,
                    evidence=(
                        *existing.evidence,
                        f"faded at {observation.observed_at.isoformat()}",
                    ),
                )
                self._faded.append(faded)
                del self._live[key]
                return faded
            return None

        if existing is None:
            started = OIMigration(
                expiry_id=observation.expiry_id,
                option_type=observation.option_type,
                origin_strike=observation.origin_strike,
                destination_strike=observation.destination_strike,
                direction=observation.direction,
                magnitude=observation.magnitude,
                status=MigrationStatus.FORMING,
                first_observed_at=observation.observed_at,
                last_observed_at=observation.observed_at,
                windows=1,
                evidence=(
                    f"detected {observation.origin_strike} -> "
                    f"{observation.destination_strike} magnitude {observation.magnitude}",
                ),
            )
            self._live[key] = started
            return started

        # Continuing: the destination advances and the duration grows. The origin is
        # NOT updated -- it is where this migration began, and rewriting it would
        # erase the displacement the entity exists to record.
        windows = existing.windows + 1
        status = MigrationStatus.CONFIRMED if windows >= self._confirm_after else existing.status
        advanced = replace(
            existing,
            destination_strike=observation.destination_strike,
            magnitude=observation.magnitude,
            status=status,
            last_observed_at=observation.observed_at,
            windows=windows,
            evidence=(
                *existing.evidence,
                f"advanced to {observation.destination_strike} (window {windows})",
            ),
        )
        self._live[key] = advanced
        return advanced
