"""In-memory observation store — the reference implementation of the semantics.

This exists so the **bitemporal and idempotency behaviour is genuinely verified**, not
merely asserted. It implements exactly the contract `postgres.py` implements, so the
tests written against it are tests of the semantics rather than of one backend.

It is a test double for the *storage medium*, not for the *rules*: identity resolution,
duplicate suppression, temporal bounds and correction chains are the real logic and are
exercised here.

What it deliberately does not model: partitioning, concurrent writers, and the
`FOR UPDATE SKIP LOCKED` behaviour of the real dispatcher. Those require PostgreSQL and
are listed in the external verification checklist.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from oipulse.core.timemode import TemporalBound
from oipulse.marketdata.observations import MarketObservation, ObservationKind
from oipulse.persistence.repository import ResolvedBound, TemporalRepository, resolve_bound

__all__ = ["AsyncSinkAdapter", "InMemoryObservationStore", "WriteResult"]


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Outcome of a write batch. `duplicates` is the idempotency evidence."""

    inserted: int
    duplicates: int

    @property
    def total(self) -> int:
        return self.inserted + self.duplicates


class InMemoryObservationStore(TemporalRepository[MarketObservation]):
    """Append-only, identity-keyed, bitemporal.

    Raw observations carry no `available_at`, so `supports_availability` stays False and
    `tradable_information_at` is refused — that mode applies to derived values only
    (`12-API_SPEC.md` §2).
    """

    supports_availability = False

    def __init__(self) -> None:
        self._rows: list[MarketObservation] = []
        self._seen: set[tuple[str, ...]] = set()
        self._next_id = 1
        self._ids: dict[int, MarketObservation] = {}

    # ------------------------------------------------------------------- writing

    def append(self, observations: Iterable[MarketObservation]) -> WriteResult:
        """Insert, suppressing anything already present by resolved identity.

        Idempotent by construction: replaying a batch, a day, or a whole session
        produces no new rows. This is what makes restart and reconnect safe — the
        collector never has to know whether it already wrote something.
        """
        inserted = duplicates = 0
        for obs in observations:
            # Scoped by kind, mirroring the real schema: obs_quotes and obs_greeks are
            # separate tables with separate unique indexes. One provider event carrying
            # both a quote and a greeks payload yields two rows, one per table — a flat
            # key across kinds would wrongly collapse them into one.
            key = (obs.kind.value, *obs.identity.dedup_key)
            if key in self._seen:
                duplicates += 1
                continue
            self._seen.add(key)
            self._rows.append(obs)
            self._ids[self._next_id] = obs
            self._next_id += 1
            inserted += 1
        return WriteResult(inserted=inserted, duplicates=duplicates)

    # ------------------------------------------------------------------- reading

    def _fetch(self, bound: ResolvedBound, **criteria: object) -> Sequence[MarketObservation]:
        """Apply the resolved temporal predicates, then any field criteria.

        Both axes are always applied. `observed_at <= T` alone would be the
        market-truth reading and would leak late-arriving data into knowledge queries —
        the precise failure the bitemporal model exists to prevent.
        """
        instrument_id = criteria.get("instrument_id")
        kind = criteria.get("kind")

        out = []
        for row in self._rows:
            if row.observed_at > bound.observed_at_max:
                continue
            if row.ingested_at > bound.ingested_at_max:
                continue
            if instrument_id is not None and row.instrument_id != instrument_id:
                continue
            if kind is not None and row.kind is not kind:
                continue
            out.append(row)
        return sorted(out, key=lambda r: (r.observed_at, r.identity.dedup_key))

    def latest(
        self, bound: TemporalBound, instrument_id: int, kind: ObservationKind
    ) -> MarketObservation | None:
        """Most recent observation for an instrument under *bound*.

        Correction-aware: a superseded row is skipped when its replacement is also
        visible under the bound, so a query after a correction sees the corrected value
        while a query before it still sees the original (`05` §6).
        """
        rows = self._fetch(resolve_bound(bound), instrument_id=instrument_id, kind=kind)
        superseded = {r.supersedes_observation_id for r in rows if r.supersedes_observation_id}
        live = [r for r in rows if self._id_of(r) not in superseded]
        return live[-1] if live else None

    def _id_of(self, obs: MarketObservation) -> int | None:
        for rid, row in self._ids.items():
            if row is obs:
                return rid
        return None

    # ---------------------------------------------------------------- inspection

    def count(self) -> int:
        return len(self._rows)

    def all_rows(self) -> tuple[MarketObservation, ...]:
        return tuple(self._rows)


class AsyncSinkAdapter:
    """Presents a synchronous store through the async `ObservationSink` contract.

    The PostgreSQL store writes over an async driver, so the collector's write path is
    async. The in-memory twin stays synchronous because the offline tests read it
    directly and gain nothing from an event loop. This adapter is the seam between
    them, and it exists so the collector has exactly one write contract to satisfy
    rather than branching on whether the result happens to be awaitable.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: InMemoryObservationStore) -> None:
        self._inner = inner

    async def append(self, observations: Iterable[MarketObservation]) -> WriteResult:
        return self._inner.append(observations)

    @property
    def inner(self) -> InMemoryObservationStore:
        return self._inner
