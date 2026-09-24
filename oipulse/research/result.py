"""`StudyResult` — the content-addressed research artifact (`09-RESEARCH.md` §7).

> A `StudyResult` records everything needed to reproduce it: dataset content hash,
> feature versions, builder version, rule versions, query mode, knowledge horizon,
> code version, random seed, parameters, and the resulting statistics.
>
> Re-running with identical inputs must produce identical output. This is a CI test,
> not an aspiration.

`content_hash` therefore covers the study digest, the dataset hash, the sample counts
and every computed statistic — and **excludes** `executed_at`, `duration` and any other
execution metadata. Two runs of the same study over the same immutable dataset produce
the same hash; a run over changed source content produces a different one. Both
directions are tested.

**Insufficient sample is a result, not an absence.** `09` §3: below the minimum the
study reports insufficiency rather than a mean of nine clusters. The artifact still
exists, still hashes, and still records why — so "we looked and the sample was too
small" is a finding a reader can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from oipulse.research.events import research_digest
from oipulse.research.statistics import DistributionStats, ExcursionStats, SampleStats

__all__ = [
    "ExecutionMetadata",
    "HorizonResult",
    "ResultStatus",
    "StudyResult",
]


class ResultStatus(StrEnum):
    OK = "ok"
    #: Effective sample below the study's minimum. A finding, not a failure.
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    #: No qualifying event in the period. Also a finding.
    NO_EVENTS = "no_events"
    #: The dataset did not cover the study period.
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class HorizonResult:
    """Outcome statistics at one forward horizon."""

    horizon: timedelta
    distribution: DistributionStats
    excursion: ExcursionStats
    #: Events whose forward window ran past the end of the dataset. Excluded from the
    #: distribution and counted here -- never truncated silently and never fabricated.
    incomplete_windows: int = 0
    #: Optional per-control breakdowns, e.g. {"regime": {"TRENDING_UP": stats}}.
    breakdowns: tuple[tuple[str, tuple[tuple[str, DistributionStats], ...]], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon_seconds": self.horizon.total_seconds(),
            "distribution": self.distribution.as_dict(),
            "excursion": self.excursion.as_dict(),
            "incomplete_windows": self.incomplete_windows,
            "breakdowns": {
                control: {bucket: stats.as_dict() for bucket, stats in buckets}
                for control, buckets in self.breakdowns
            },
        }

    def digest_payload(self) -> dict[str, Any]:
        """The part of a horizon result that belongs to semantic identity."""
        return {
            "horizon_s": self.horizon.total_seconds(),
            "distribution": self.distribution.as_dict(),
            "excursion": self.excursion.as_dict(),
            "incomplete_windows": self.incomplete_windows,
            "breakdowns": {
                control: {bucket: stats.as_dict() for bucket, stats in buckets}
                for control, buckets in self.breakdowns
            },
        }


@dataclass(frozen=True, slots=True)
class ExecutionMetadata:
    """When and how the run happened. **Excluded from the content hash**, by design.

    Kept as a separate object rather than loose fields so the exclusion is structural:
    the hash function never receives this type, so no future field added here can leak
    into semantic identity by accident.
    """

    executed_at: datetime | None = None
    duration: timedelta | None = None
    engine_version: str = "1.0.0"
    host: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "executed_at": None if self.executed_at is None else self.executed_at.isoformat(),
            "duration_seconds": (None if self.duration is None else self.duration.total_seconds()),
            "engine_version": self.engine_version,
            "host": self.host,
        }


@dataclass(frozen=True, slots=True)
class StudyResult:
    """An immutable, content-addressed research artifact."""

    study_id: str
    study_version: int
    study_digest: str
    event_definition_digest: str
    dataset_content_hash: str
    query_mode: str
    knowledge_horizon: datetime
    build_context_id: str
    feature_versions: tuple[tuple[str, int], ...]
    signal_versions: tuple[tuple[str, int], ...]
    builder_version: str
    sample: SampleStats
    horizons: tuple[HorizonResult, ...]
    status: ResultStatus
    #: Present when status is not OK, explaining what was found instead of a number.
    status_detail: str = ""
    #: Recorded per `09` §7. No randomness is used today; the field exists so a future
    #: sampling method cannot introduce one without it being reproducible.
    random_seed: int = 0
    execution: ExecutionMetadata = field(default_factory=ExecutionMetadata)

    @property
    def is_hindsight(self) -> bool:
        """Carried onto the result so a hindsight number is never mistaken for a
        live-reproducible one."""
        return self.query_mode == "market_truth_at"

    @property
    def content_hash(self) -> str:
        """Semantic identity. Execution metadata is structurally excluded."""
        return "res_" + research_digest(
            {
                "study_id": self.study_id,
                "study_version": self.study_version,
                "study_digest": self.study_digest,
                "event_definition_digest": self.event_definition_digest,
                "dataset_content_hash": self.dataset_content_hash,
                "query_mode": self.query_mode,
                "knowledge_horizon": self.knowledge_horizon.isoformat(),
                "build_context_id": self.build_context_id,
                "feature_versions": [f"{i}@v{v}" for i, v in sorted(self.feature_versions)],
                "signal_versions": [f"{i}@v{v}" for i, v in sorted(self.signal_versions)],
                "builder_version": self.builder_version,
                "sample": self.sample.as_dict(),
                "horizons": [
                    h.digest_payload() for h in sorted(self.horizons, key=lambda h: h.horizon)
                ],
                "status": self.status.value,
                "status_detail": self.status_detail,
                "random_seed": self.random_seed,
            }
        )

    def horizon(self, horizon: timedelta) -> HorizonResult | None:
        for entry in self.horizons:
            if entry.horizon == horizon:
                return entry
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "study_version": self.study_version,
            "study_digest": self.study_digest,
            "event_definition_digest": self.event_definition_digest,
            "dataset_content_hash": self.dataset_content_hash,
            "query_mode": self.query_mode,
            "is_hindsight": self.is_hindsight,
            "knowledge_horizon": self.knowledge_horizon.isoformat(),
            "build_context_id": self.build_context_id,
            "feature_versions": [f"{i}@v{v}" for i, v in self.feature_versions],
            "signal_versions": [f"{i}@v{v}" for i, v in self.signal_versions],
            "builder_version": self.builder_version,
            "sample": self.sample.as_dict(),
            "horizons": [h.as_dict() for h in self.horizons],
            "status": self.status.value,
            "status_detail": self.status_detail,
            "random_seed": self.random_seed,
            "content_hash": self.content_hash,
            "execution": self.execution.as_dict(),
        }
