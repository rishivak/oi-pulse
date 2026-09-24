"""Rendering replay artifacts for the API. Pure, web-stack-free.

Kept out of `api/` for the same reason the MarketState, signal and research envelopes
are: the shape is a pure function of the artifact, so the contract stays testable on
an interpreter with no web stack installed.

Two things every replay response must carry, per `10-REPLAY.md` §8 and `12-API_SPEC.md`:

* **Both market time and knowledge time**, always, on every step and every state. A
  replay step under market-truth has `K != T`, and a client rendering only `T` would
  present a hindsight state as though it were live-reproducible.
* **The run id**, so a replay-derived payload is never mistakable for a live one. §8
  requires replay events to be namespaced by run; the same discipline applies to what
  leaves over HTTP.
"""

from __future__ import annotations

from typing import Any

from oipulse.marketstate.serialisation import state_to_envelope
from oipulse.replay.context import ReplayContext
from oipulse.replay.engine import ReplayProgress, SteppedState
from oipulse.replay.timeline import ReplayStep, ReplayTimeline

__all__ = [
    "context_to_dict",
    "progress_to_dict",
    "step_to_dict",
    "stepped_state_to_dict",
    "timeline_to_dict",
]


def context_to_dict(context: ReplayContext) -> dict[str, Any]:
    return {
        "run_id": context.run_id,
        "content_digest": context.content_digest,
        "underlying_ids": list(context.underlying_ids),
        "expiry_ids": list(context.expiry_ids),
        "period": {
            "start": context.period.start.isoformat(),
            "end": context.period.end.isoformat(),
        },
        "build_context_id": context.build_context_id,
        "step_mode": context.step_mode.value,
        "knowledge_mode": context.knowledge_mode.value,
        "pinned_knowledge_horizon": (
            None
            if context.pinned_knowledge_horizon is None
            else context.pinned_knowledge_horizon.isoformat()
        ),
        # Surfaced rather than derivable: a client must be able to badge a hindsight
        # run without knowing how the modes combine.
        "is_hindsight": context.is_hindsight,
        "interval_seconds": context.interval.total_seconds(),
        "speed": context.speed.value,
        "feature_versions": dict(context.feature_versions),
        "rule_versions": dict(context.rule_versions),
    }


def step_to_dict(step: ReplayStep) -> dict[str, Any]:
    """Both times, always. Never `T` alone."""
    return {
        "index": step.index,
        "market_time": step.market_time.isoformat(),
        "knowledge_time": step.knowledge_horizon.isoformat(),
        "is_lockstep": step.is_lockstep,
    }


def timeline_to_dict(timeline: ReplayTimeline, *, include_steps: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": context_to_dict(timeline.context),
        "step_count": len(timeline.steps),
        "observation_count": len(timeline.observations),
    }
    if timeline.steps:
        body["first_step"] = step_to_dict(timeline.steps[0])
        body["last_step"] = step_to_dict(timeline.steps[-1])
    if include_steps:
        body["steps"] = [step_to_dict(s) for s in timeline.steps]
    return body


def stepped_state_to_dict(stepped: SteppedState) -> dict[str, Any]:
    """A state produced by a replay step, tagged so it cannot pass for live.

    The full state envelope is the Phase 3 one -- there is one state shape, and a
    replay must not invent a second. This adds only the replay framing around it.
    """
    return {
        "step": step_to_dict(stepped.step),
        "underlying_id": stepped.underlying_id,
        "from_checkpoint": stepped.from_checkpoint,
        "state": state_to_envelope(stepped.state),
    }


def progress_to_dict(progress: ReplayProgress) -> dict[str, Any]:
    body: dict[str, Any] = dict(progress.as_dict())
    total = progress.checkpoint_hits + progress.checkpoint_misses
    # Reported rather than left to the client to divide: a zero-step run must show
    # 0.0 reuse, not a division error or a silently absent field.
    body["checkpoint_reuse_ratio"] = 0.0 if total == 0 else progress.checkpoint_hits / total
    return body
