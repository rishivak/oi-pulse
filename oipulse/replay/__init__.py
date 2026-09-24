"""Layer 7a — Replay.

`docs/design/10-REPLAY.md`. Replay reconstructs the market-state sequence from canonical
observations, deterministically and free of look-ahead **by construction**.

> A backtest is replay with a strategy attached and a fill simulator on the end.
> Sharing the mechanism means a backtest cannot diverge from a replay, and neither can
> diverge from live processing — because all three drive the *same* pipeline.

There is no separate "historical" code path here. `build_state`, the Phase 4 feature
registry and the Phase 5 rules are reused unchanged; this package supplies a timeline, a
knowledge horizon and an injected `ReplayClock`, and nothing else.
"""

from oipulse.replay.context import (
    IncoherentReplayHorizon,
    KnowledgeMode,
    ReplayContext,
    StepMode,
)
from oipulse.replay.engine import ReplayEngine, ReplayProgress, ResumePoint, SteppedState
from oipulse.replay.ordering import ObservationOrderKey, SessionOrdinals, replay_order
from oipulse.replay.timeline import ReplayStep, ReplayTimeline, build_timeline

__all__ = [
    "IncoherentReplayHorizon",
    "KnowledgeMode",
    "ObservationOrderKey",
    "ReplayContext",
    "ReplayEngine",
    "ReplayProgress",
    "ReplayStep",
    "ReplayTimeline",
    "ResumePoint",
    "SessionOrdinals",
    "StepMode",
    "SteppedState",
    "build_timeline",
    "replay_order",
]
