"""Checkpoint persistence, selection and materialization.

`docs/design/04-MARKETSTATE.md` §5 and `02-DATA_MODEL.md` §5.

A checkpoint is a **materialization artifact, not truth**. Observations are the source
of historical truth; deleting every checkpoint loses no history, only recomputation
time. That is why reuse can be strict without costing anything: on a miss we rebuild.

**The selection rule is exact match on the full identity tuple.**

> A checkpoint whose `knowledge_horizon` is later than the requested `K` must never be
> used. It may incorporate observations that had not yet arrived at `K`.

An *earlier*-K checkpoint is equally unusable — it is a different state, missing data
that had arrived by `K`. So selection is exact-match, never nearest-match, and a
mismatch means reconstruct. Nearest-match is the subtle version of look-ahead: it
returns something plausible, and the error only shows up as a backtest that cannot be
reproduced live.

The same `StateBuilder.build` serves live assembly, reconstruction, checkpoint
materialization and replay. There is no second code path here that could drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from oipulse.core.ids import InstrumentId
from oipulse.marketstate.builder import StateBuilder
from oipulse.marketstate.state import MarketState, StateIdentity
from oipulse.observability.logging import get_logger

log = get_logger(__name__)

__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "CheckpointTrigger",
    "InMemoryCheckpointStore",
    "StateService",
]


class CheckpointTrigger(StrEnum):
    """Why a checkpoint was written (`04` §5).

    `QUALITY_TRANSITION` matters disproportionately: it guarantees the record shows
    exactly when the data went bad, even if that fell between cadence ticks.
    """

    CADENCE = "cadence"
    CHAIN_SNAPSHOT = "chain_snapshot"
    SESSION_BOUNDARY = "session_boundary"
    QUALITY_TRANSITION = "quality_transition"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A materialized state plus why and when it was written."""

    state: MarketState
    trigger: CheckpointTrigger
    built_at: datetime

    @property
    def identity(self) -> StateIdentity:
        return self.state.identity

    @property
    def content_digest(self) -> str:
        return self.state.content_digest()


class CheckpointStore(Protocol):
    """Storage for checkpoints. Lookup is exact-match on the identity tuple."""

    def get(self, identity: StateIdentity) -> Checkpoint | None: ...

    def put(self, checkpoint: Checkpoint) -> bool: ...


class InMemoryCheckpointStore:
    """Offline twin of `state_checkpoints`.

    Mirrors the database's uniqueness exactly: the dict is keyed by the full identity
    tuple, so the same collision the `UNIQUE (underlying_id, observed_at,
    knowledge_horizon, build_context_id)` constraint prevents is prevented here too,
    and a test that passes against this twin is testing the real rule.
    """

    __slots__ = ("_by_identity", "_skipped")

    def __init__(self) -> None:
        self._by_identity: dict[tuple[int, str, str, str], Checkpoint] = {}
        self._skipped = 0

    def get(self, identity: StateIdentity) -> Checkpoint | None:
        """Exact match only. No nearest-K fallback exists, by design."""
        return self._by_identity.get(identity.as_key())

    def put(self, checkpoint: Checkpoint) -> bool:
        """Store unless byte-identical to what is already there.

        Deduplication per `04` §5: a checkpoint whose content matches its predecessor
        adds nothing, because reconstruction would produce the same state anyway.
        Returns whether a row was written, so the caller can report the skip rather
        than silently appearing to have persisted.
        """
        key = checkpoint.identity.as_key()
        existing = self._by_identity.get(key)
        if existing is not None and existing.content_digest == checkpoint.content_digest:
            self._skipped += 1
            return False
        self._by_identity[key] = checkpoint
        return True

    @property
    def count(self) -> int:
        return len(self._by_identity)

    @property
    def deduplicated(self) -> int:
        return self._skipped

    def all_checkpoints(self) -> tuple[Checkpoint, ...]:
        return tuple(self._by_identity[key] for key in sorted(self._by_identity))


class StateService:
    """Resolve a state request: exact checkpoint, else reconstruct.

    `04` §5:

    ```
    1. look for a checkpoint matching the FULL identity tuple
    2. if absent -> build_state(underlying, T, K, B) from observations
    3. optionally persist the result as a MANUAL checkpoint (cache)
    ```

    Step 3 is off by default. Caching a reconstruction is a legitimate optimisation,
    but doing it implicitly would grow `state_checkpoints` from read traffic, which is
    a surprising cost for a GET.
    """

    def __init__(
        self,
        builder: StateBuilder,
        store: CheckpointStore | None = None,
        *,
        cache_reconstructions: bool = False,
    ) -> None:
        self._builder = builder
        self._store = store
        self._cache = cache_reconstructions
        self._reused = 0
        self._reconstructed = 0

    @property
    def reuse_count(self) -> int:
        return self._reused

    @property
    def reconstruction_count(self) -> int:
        return self._reconstructed

    def get_state(
        self,
        underlying_id: InstrumentId,
        market_time: datetime,
        knowledge_horizon: datetime | None = None,
    ) -> MarketState:
        k = knowledge_horizon if knowledge_horizon is not None else market_time
        identity = StateIdentity(
            underlying_id=underlying_id,
            market_time=market_time,
            knowledge_horizon=k,
            build_context_id=self._builder.build_context.id,
        )

        if self._store is not None:
            hit = self._store.get(identity)
            if hit is not None:
                self._reused += 1
                log.debug("checkpoint_reused", extra={"identity": identity.describe()})
                return hit.state

        state = self._builder.build(underlying_id, market_time, k)
        self._reconstructed += 1
        if self._cache and self._store is not None:
            self._store.put(
                Checkpoint(
                    state=state,
                    trigger=CheckpointTrigger.MANUAL,
                    built_at=state.provenance.assembled_at,
                )
            )
        return state

    def checkpoint(
        self,
        underlying_id: InstrumentId,
        market_time: datetime,
        knowledge_horizon: datetime | None = None,
        trigger: CheckpointTrigger = CheckpointTrigger.CADENCE,
    ) -> Checkpoint:
        """Build and persist. Uses the same builder as every other path."""
        if self._store is None:
            raise RuntimeError("no checkpoint store configured")
        state = self._builder.build(underlying_id, market_time, knowledge_horizon)
        checkpoint = Checkpoint(
            state=state, trigger=trigger, built_at=state.provenance.assembled_at
        )
        self._store.put(checkpoint)
        return checkpoint
