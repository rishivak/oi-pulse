"""The replay timeline — `10-REPLAY.md` §4 and §6 of the Phase 7 brief.

A timeline is the ordered sequence of `(market_time, knowledge_horizon)` steps a run
walks. The three stepping modes come from `10` §4:

| Mode | Advances to |
|---|---|
| `CHECKPOINT` | each existing `state_checkpoint` in the period — fastest, the default |
| `FIXED_INTERVAL` | every N seconds, reconstructing where no checkpoint exists |
| `EVENT` | every observation — highest fidelity, slowest |

**Replay is not "sort by `observed_at` then execute".** A historical observation becomes
usable only when the replay knowledge horizon reaches its *availability*, which for a
raw row is `ingested_at` and for a derived value is its own `available_at`. The timeline
therefore carries both axes at every step, and `visible_observations` filters on both.

The timeline is derived from data, never from a wall clock. Two runs over the same
fixture produce the same steps in the same order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from oipulse.marketdata.observations import MarketObservation
from oipulse.replay.context import KnowledgeMode, ReplayContext, StepMode
from oipulse.replay.ordering import SessionOrdinals, replay_order

__all__ = ["ReplayStep", "ReplayTimeline", "build_timeline", "lockstep_horizons"]


@dataclass(frozen=True, slots=True)
class ReplayStep:
    """One position on the timeline.

    `market_time` and `knowledge_horizon` are both explicit, never one derived from
    the other at use time: under market-truth mode they differ, and a step that
    carried only `T` would force every consumer to re-derive `K` and risk disagreeing.
    """

    index: int
    market_time: datetime
    knowledge_horizon: datetime

    @property
    def is_lockstep(self) -> bool:
        return self.market_time == self.knowledge_horizon


@dataclass(frozen=True, slots=True)
class ReplayTimeline:
    """The ordered steps of one run, plus the observations behind them."""

    context: ReplayContext
    steps: tuple[ReplayStep, ...]
    observations: tuple[MarketObservation, ...]

    def __len__(self) -> int:
        return len(self.steps)

    def visible_observations(self, step: ReplayStep) -> tuple[MarketObservation, ...]:
        """Observations usable at this step.

        **Both axes, always.** `observed_at <= T` alone is the market-truth reading and
        would leak a late-arriving row into a lockstep replay: an observation observed
        at 11:40 but ingested at 11:44 is not usable at a knowledge horizon of 11:42,
        even though its market timestamp is in the past.
        """
        return tuple(
            obs
            for obs in self.observations
            if obs.observed_at <= step.market_time and obs.ingested_at <= step.knowledge_horizon
        )

    def step_at(self, market_time: datetime) -> ReplayStep | None:
        for step in self.steps:
            if step.market_time == market_time:
                return step
        return None

    def steps_from(self, market_time: datetime) -> tuple[ReplayStep, ...]:
        """Steps at or after `market_time` — the resume path.

        Returns the same `ReplayStep` objects the full timeline holds, so a resumed
        run walks an identical sequence rather than a recomputed approximation of one.
        """
        return tuple(s for s in self.steps if s.market_time >= market_time)


def build_timeline(
    context: ReplayContext,
    observations: Sequence[MarketObservation],
    *,
    checkpoint_times: Sequence[datetime] = (),
    ordinals: SessionOrdinals | None = None,
) -> ReplayTimeline:
    """Derive the timeline for a run. Deterministic and independent of input order.

    Observations are ordered by the cross-session key before anything else, so a
    database returning rows in a different order cannot change a replay.
    """
    ordered = replay_order(observations, ordinals)
    in_period = tuple(obs for obs in ordered if context.period.contains(obs.observed_at))

    if context.step_mode is StepMode.CHECKPOINT:
        instants = sorted({t for t in checkpoint_times if context.period.contains(t)})
    elif context.step_mode is StepMode.FIXED_INTERVAL:
        instants = []
        cursor = context.period.start
        while cursor <= context.period.end:
            instants.append(cursor)
            cursor += context.interval
    else:
        # EVENT: every distinct observation market time, highest fidelity.
        instants = sorted({obs.observed_at for obs in in_period})

    steps = tuple(
        ReplayStep(
            index=index,
            market_time=instant,
            knowledge_horizon=context.knowledge_horizon_at(instant),
        )
        for index, instant in enumerate(instants)
    )
    return ReplayTimeline(context=context, steps=steps, observations=in_period)


def lockstep_horizons(timeline: ReplayTimeline) -> bool:
    """True when every step has `K = T`. Used to assert the honest default holds."""
    return timeline.context.knowledge_mode is KnowledgeMode.LOCKSTEP and all(
        step.is_lockstep for step in timeline.steps
    )
