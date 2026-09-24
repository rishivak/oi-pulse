"""The replay engine — `10-REPLAY.md` §3 and §4.

Walks a timeline, producing one `MarketState` per step through the **verified Phase 3
builder**. There is no second reconstruction path: `StateService` is the same object
the live API uses, so a replayed state and a live state cannot diverge.

### Knowledge-aware checkpoint selection

> A checkpoint may be reused **only** when its `market_time`, `knowledge_horizon` and
> `build_context_id` all match the replay context. Anything else is reconstructed.
>
> This is not an optimization detail — it is the invariant. A checkpoint built live at
> `K = 11:50` must never satisfy a replay step at `K = 11:43`: it may incorporate
> observations that had not arrived by 11:43, which is exactly the look-ahead the
> whole architecture exists to prevent, arriving through a cache.

Phase 3's `StateService` already implements exact-match selection, so this engine does
not re-implement it — it counts hits and misses so a run can *report* how much of it
was reused, which is the practical consequence `10` §4 describes: a lockstep replay
reuses the live checkpoints, a market-truth replay reuses none and reconstructs
throughout.

The clock is a `ReplayClock` advanced to each step's market time. Nothing here reads
the wall clock, and `tools/check_clock_access.py` enforces that no module can.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from oipulse.core.clock import ReplayClock
from oipulse.core.ids import InstrumentId
from oipulse.marketstate.checkpoints import CheckpointStore, StateService
from oipulse.marketstate.state import MarketState, StateIdentity
from oipulse.replay.context import ReplayContext
from oipulse.replay.timeline import ReplayStep, ReplayTimeline

__all__ = ["ReplayEngine", "ReplayProgress", "ResumePoint", "SteppedState"]


@dataclass(frozen=True, slots=True)
class SteppedState:
    """One step's reconstructed state, and how it was obtained."""

    step: ReplayStep
    underlying_id: int
    state: MarketState
    #: True when an exact-identity checkpoint served the step.
    from_checkpoint: bool

    @property
    def identity(self) -> StateIdentity:
        return self.state.identity


@dataclass(slots=True)
class ReplayProgress:
    """Counters for one run. Reported, not inferred.

    `checkpoint_misses` is the number the operator actually wants: under market-truth
    it should equal the step count, and a surprising number of hits would mean the
    exact-match rule had been weakened somewhere.
    """

    steps_completed: int = 0
    states_built: int = 0
    checkpoint_hits: int = 0
    checkpoint_misses: int = 0
    observations_visible: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "steps_completed": self.steps_completed,
            "states_built": self.states_built,
            "checkpoint_hits": self.checkpoint_hits,
            "checkpoint_misses": self.checkpoint_misses,
            "observations_visible": self.observations_visible,
        }


class ReplayEngine:
    """Drives a timeline through the verified state builder.

    Holds a `ReplayClock` so any collaborator that legitimately needs "now" during a
    replay receives the replayed instant. The clock only ever moves forward; a
    backwards step raises, because it would let a later decision observe an earlier
    state and silently corrupt the run.
    """

    def __init__(
        self,
        service: StateService,
        *,
        checkpoints: CheckpointStore | None = None,
    ) -> None:
        self._service = service
        self._checkpoints = checkpoints
        self._progress = ReplayProgress()

    @property
    def progress(self) -> ReplayProgress:
        return self._progress

    def reset(self) -> None:
        """Clear counters. Not state: the engine holds none between runs."""
        self._progress = ReplayProgress()

    # ------------------------------------------------------------------ stepping

    def state_at(
        self, context: ReplayContext, step: ReplayStep, underlying_id: int
    ) -> SteppedState:
        """Resolve one underlying's state at one step.

        Checkpoint reuse is delegated to `StateService`, which matches on the full
        identity tuple. This method's own contribution is to *ask the same question*
        first, so the run can report hits and misses without duplicating the rule.
        """
        identity = StateIdentity(
            underlying_id=InstrumentId(underlying_id),
            market_time=step.market_time,
            knowledge_horizon=step.knowledge_horizon,
            build_context_id=context.build_context_id,
        )
        hit = self._checkpoints.get(identity) if self._checkpoints is not None else None
        if hit is not None:
            self._progress.checkpoint_hits += 1
        else:
            self._progress.checkpoint_misses += 1

        state = self._service.get_state(
            InstrumentId(underlying_id), step.market_time, step.knowledge_horizon
        )
        self._progress.states_built += 1
        return SteppedState(
            step=step,
            underlying_id=underlying_id,
            state=state,
            from_checkpoint=hit is not None,
        )

    def run(
        self,
        timeline: ReplayTimeline,
        *,
        from_market_time: datetime | None = None,
        clock: ReplayClock | None = None,
    ) -> tuple[SteppedState, ...]:
        """Walk the timeline, yielding one state per (step, underlying).

        `from_market_time` is the resume entry point. A resumed run walks the **same
        `ReplayStep` objects** the full timeline holds, so resume cannot drift from an
        uninterrupted run by recomputing a slightly different sequence.
        """
        context = timeline.context
        steps = (
            timeline.steps if from_market_time is None else timeline.steps_from(from_market_time)
        )
        if not steps:
            return ()

        replay_clock = clock or ReplayClock(steps[0].market_time)
        out: list[SteppedState] = []
        for step in steps:
            # Forward-only. A backwards move raises rather than being tolerated.
            replay_clock.advance_to(step.market_time)
            self._progress.observations_visible += len(timeline.visible_observations(step))
            # Sorted, so iteration over instruments never depends on set ordering.
            for underlying_id in sorted(context.underlying_ids):
                out.append(self.state_at(context, step, underlying_id))
            self._progress.steps_completed += 1
        return tuple(out)


@dataclass(frozen=True, slots=True)
class ResumePoint:
    """Where a run stopped, and everything needed to continue identically.

    Identity includes `market_time`, `knowledge_horizon` and `build_context_id` per
    the brief's §17. Resuming from a point built under a different build context, or
    at a later knowledge horizon, is refused rather than adjusted.
    """

    run_id: str
    step_index: int
    market_time: datetime
    knowledge_horizon: datetime
    build_context_id: str
    #: Opaque digest of the semantic state the caller carries across the boundary
    #: (ledger, positions, cash). Compared on resume so a mismatch is caught.
    carried_state_digest: str = ""
    extras: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def matches(self, context: ReplayContext) -> bool:
        return self.build_context_id == context.build_context_id

    def validate_for(self, context: ReplayContext) -> None:
        if not self.matches(context):
            raise ValueError(
                f"resume point was built under build context "
                f"{self.build_context_id}, but this run uses "
                f"{context.build_context_id}; a state assembled under different "
                f"configuration is a different state"
            )
        expected = context.knowledge_horizon_at(self.market_time)
        if self.knowledge_horizon > expected:
            raise ValueError(
                f"resume point carries knowledge horizon "
                f"{self.knowledge_horizon.isoformat()}, later than the "
                f"{expected.isoformat()} this run allows at that market time; "
                f"resuming would import look-ahead through the checkpoint"
            )
