"""`ReplayContext` — `10-REPLAY.md` §2.

```
ReplayContext
├── run_id
├── universe                underlyings, expiries
├── period                  start_time → end_time
├── market_clock    T       advances through the period
├── knowledge_horizon K     advances in lockstep by default
├── step_mode               CHECKPOINT | FIXED_INTERVAL | EVENT
├── speed                   1x | 5x | 10x | MAX
└── build_context_id, feature_versions{}, rule_versions{}
```

### Knowledge horizon modes

| Mode | Meaning |
|---|---|
| `K = T` (default) | reproduces what the system could actually have known |
| `K` pinned later | market-truth analysis — **explicitly flagged** |
| `K < T` | incoherent — disallowed |

The default is the honest one, and market-truth mode is recorded on the run so no
reader can mistake one for the other. `K < T` raises at construction rather than being
clamped: a request for a state the system could not have held is a mistake, and
silently adjusting it would produce a plausible answer to a question nobody asked.

`speed` is **presentation only**. It governs how fast a UI-driven replay walks the
timeline and is deliberately excluded from the run's semantic identity — a replay at
1x and the same replay at MAX must produce byte-identical results.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from oipulse.core.errors import OIPulseError

__all__ = [
    "IncoherentReplayHorizon",
    "KnowledgeMode",
    "ReplayContext",
    "ReplayPeriod",
    "ReplaySpeed",
    "StepMode",
    "replay_digest",
]


def replay_digest(payload: dict[str, Any]) -> str:
    """Stable digest. Sorted keys, compact separators, `Decimal` via `str`."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class IncoherentReplayHorizon(OIPulseError):
    """`K < T` — disallowed (`10` §2). Raised, never clamped."""


class StepMode(StrEnum):
    """`10` §4. CHECKPOINT is the default because checkpoints already exist at the
    cadence the live system used, so it reproduces the live decision points exactly."""

    CHECKPOINT = "CHECKPOINT"
    FIXED_INTERVAL = "FIXED_INTERVAL"
    EVENT = "EVENT"


class KnowledgeMode(StrEnum):
    """How `K` relates to `T` through the run."""

    #: `K = T`. Reproduces what the system could actually have known.
    LOCKSTEP = "LOCKSTEP"
    #: `K` pinned at a later instant. Hindsight, and flagged everywhere it appears.
    MARKET_TRUTH = "MARKET_TRUTH"

    @property
    def is_hindsight(self) -> bool:
        return self is KnowledgeMode.MARKET_TRUTH


class ReplaySpeed(StrEnum):
    """Presentation only. Excluded from semantic identity."""

    X1 = "1x"
    X5 = "5x"
    X10 = "10x"
    MAX = "MAX"


@dataclass(frozen=True, slots=True)
class ReplayPeriod:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"replay period ends {self.end.isoformat()} before it starts "
                f"{self.start.isoformat()}"
            )

    def contains(self, at: datetime) -> bool:
        return self.start <= at <= self.end


@dataclass(frozen=True, slots=True)
class ReplayContext:
    """One replay run's complete, reproducible configuration."""

    run_id: str
    underlying_ids: tuple[int, ...]
    period: ReplayPeriod
    build_context_id: str
    feature_versions: tuple[tuple[str, int], ...] = ()
    rule_versions: tuple[tuple[str, int], ...] = ()
    step_mode: StepMode = StepMode.CHECKPOINT
    knowledge_mode: KnowledgeMode = KnowledgeMode.LOCKSTEP
    #: Only meaningful under MARKET_TRUTH; ignored in lockstep.
    pinned_knowledge_horizon: datetime | None = None
    #: Only meaningful under FIXED_INTERVAL.
    interval: timedelta = timedelta(seconds=5)
    speed: ReplaySpeed = ReplaySpeed.MAX
    expiry_ids: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.knowledge_mode is KnowledgeMode.MARKET_TRUTH:
            if self.pinned_knowledge_horizon is None:
                raise ValueError(
                    "MARKET_TRUTH mode requires an explicit pinned_knowledge_horizon; "
                    "hindsight is never entered by default"
                )
            if self.pinned_knowledge_horizon < self.period.end:
                raise IncoherentReplayHorizon(
                    f"pinned knowledge horizon "
                    f"{self.pinned_knowledge_horizon.isoformat()} precedes the end of "
                    f"the replay period {self.period.end.isoformat()}; a horizon "
                    f"inside the period would be K < T for part of the run"
                )
        if self.interval <= timedelta(0):
            raise ValueError("FIXED_INTERVAL step requires a positive interval")

    def knowledge_horizon_at(self, market_time: datetime) -> datetime:
        """`K` for a given `T`. Lockstep by default; pinned under market-truth."""
        if self.knowledge_mode is KnowledgeMode.MARKET_TRUTH:
            assert self.pinned_knowledge_horizon is not None
            return self.pinned_knowledge_horizon
        return market_time

    @property
    def is_hindsight(self) -> bool:
        return self.knowledge_mode.is_hindsight

    @property
    def content_digest(self) -> str:
        """Semantic identity of the replay configuration.

        `run_id` and `speed` are excluded: the first is an execution label and the
        second is presentation. Two runs differing only in those must produce the same
        results, and including either would make that untestable by comparison.
        """
        return (
            "rpc_"
            + replay_digest(
                {
                    "underlying_ids": sorted(self.underlying_ids),
                    "expiry_ids": sorted(self.expiry_ids),
                    "period": [self.period.start.isoformat(), self.period.end.isoformat()],
                    "build_context_id": self.build_context_id,
                    "feature_versions": [f"{i}@v{v}" for i, v in sorted(self.feature_versions)],
                    "rule_versions": [f"{i}@v{v}" for i, v in sorted(self.rule_versions)],
                    "step_mode": self.step_mode.value,
                    "knowledge_mode": self.knowledge_mode.value,
                    "pinned_knowledge_horizon": (
                        None
                        if self.pinned_knowledge_horizon is None
                        else self.pinned_knowledge_horizon.isoformat()
                    ),
                    "interval_seconds": self.interval.total_seconds(),
                }
            )[:32]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "underlying_ids": list(self.underlying_ids),
            "expiry_ids": list(self.expiry_ids),
            "period": {
                "start": self.period.start.isoformat(),
                "end": self.period.end.isoformat(),
            },
            "build_context_id": self.build_context_id,
            "feature_versions": [f"{i}@v{v}" for i, v in self.feature_versions],
            "rule_versions": [f"{i}@v{v}" for i, v in self.rule_versions],
            "step_mode": self.step_mode.value,
            "knowledge_mode": self.knowledge_mode.value,
            "is_hindsight": self.is_hindsight,
            "pinned_knowledge_horizon": (
                None
                if self.pinned_knowledge_horizon is None
                else self.pinned_knowledge_horizon.isoformat()
            ),
            "interval_seconds": self.interval.total_seconds(),
            "speed": self.speed.value,
            "content_digest": self.content_digest,
        }
