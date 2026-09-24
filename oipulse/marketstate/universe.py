"""Instrument universe resolution as-of a market time.

`docs/design/04-MARKETSTATE.md` §4 step 1: *resolve instrument universe as-of T →
instrument versions and vendor mappings valid at T*.

Resolution is **temporal**, not current: "front expiry" means something different in
March than today, and an expired contract must not appear in a state reconstructed for
a date when it was live. Storing a `is_front` flag would be the point-in-time violation
this avoids (`instruments/models.py`).

The resolver is a Protocol so the builder does not depend on where instruments live. The
in-memory implementation here is what the offline tests use and what a replay harness
can drive; a PostgreSQL-backed resolver reading `instrument_*` is a Phase 3 supporting
concern implemented against the same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.instruments.models import Expiry, Instrument

__all__ = ["ExpiryUniverse", "ResolvedUniverse", "StaticUniverseResolver", "UniverseResolver"]


@dataclass(frozen=True, slots=True)
class ExpiryUniverse:
    """One expiry and the option contracts the universe expects for it.

    `legs` is the *expected* set. A leg with no visible observation at `(T, K)` is
    counted as missing rather than omitted silently, which is what makes coverage a
    real measurement instead of a tautology over whatever happened to be present.
    """

    expiry: Expiry
    legs: tuple[Instrument, ...]

    @property
    def expiry_id(self) -> ExpiryId:
        return self.expiry.id


@dataclass(frozen=True, slots=True)
class ResolvedUniverse:
    """What exists for one underlying at one market time."""

    underlying_id: InstrumentId
    spot_instrument_id: InstrumentId | None
    futures: tuple[Instrument, ...] = ()
    expiries: tuple[ExpiryUniverse, ...] = ()
    #: Expiries the shard subscribed to. Used by the escalation rule "no chain data for
    #: a subscribed expiry -> UNRELIABLE" (`04` §3), which cannot be evaluated from the
    #: observations alone: an expiry nobody subscribed to is not a coverage failure.
    subscribed_expiry_ids: frozenset[int] = field(default_factory=frozenset)

    def sorted_expiries(self) -> tuple[ExpiryUniverse, ...]:
        """Deterministic ordering. Iteration order must never affect the built state."""
        return tuple(sorted(self.expiries, key=lambda e: (e.expiry.expiry_date, int(e.expiry.id))))


class UniverseResolver(Protocol):
    """Resolves the instrument universe valid at a market time."""

    def resolve(self, underlying_id: InstrumentId, at: datetime) -> ResolvedUniverse: ...


class StaticUniverseResolver:
    """A fixed universe, filtered by expiry activity as-of the requested date.

    Deliberately minimal: it applies the one temporal rule that matters for correctness
    here -- an expiry that had already expired at `T` is not part of the universe at `T`
    -- without pretending to implement the full instrument-version history, which lives
    in `instruments/` and is resolved by the PostgreSQL-backed resolver.
    """

    __slots__ = ("_universes",)

    def __init__(self, universes: dict[int, ResolvedUniverse] | None = None) -> None:
        self._universes: dict[int, ResolvedUniverse] = dict(universes or {})

    def register(self, universe: ResolvedUniverse) -> None:
        self._universes[int(universe.underlying_id)] = universe

    def resolve(self, underlying_id: InstrumentId, at: datetime) -> ResolvedUniverse:
        universe = self._universes.get(int(underlying_id))
        if universe is None:
            return ResolvedUniverse(underlying_id=underlying_id, spot_instrument_id=None)
        as_of: date = at.date()
        live = tuple(
            e for e in universe.expiries if e.expiry.is_active and not e.expiry.is_expired(as_of)
        )
        return ResolvedUniverse(
            underlying_id=universe.underlying_id,
            spot_instrument_id=universe.spot_instrument_id,
            futures=universe.futures,
            expiries=live,
            subscribed_expiry_ids=universe.subscribed_expiry_ids,
        )
