"""`Dataset` — a materialized, point-in-time-correct extract (`09-RESEARCH.md` §4).

```
Dataset
├── id, name, created_at
├── query_mode, knowledge_horizon, build_context_id
├── period, universe
├── feature_versions{}
├── builder_version          which MarketState builder
├── row_count
├── content_hash             <- reproducibility check
└── quality_summary
```

> `content_hash` lets a study be re-run later and the dataset verified identical. If a
> dataset rebuild produces a different hash with the same parameters, something
> non-deterministic has changed and the study result is suspect — CI treats this as a
> failure.

That is the Phase 6 gate, so the hash is computed over **sorted content only**.
`created_at` and the dataset's own name are execution metadata and are excluded: a
rebuild at a different moment must hash identically, or the check tells you nothing.

Datasets are **retention-locked** (`02` §8): the observations, checkpoints and metrics
they reference cannot be pruned while a study result depends on them. `retention_locks`
emits the lock rows the pruning job joins against.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from oipulse.analytics.values import MetricValue
from oipulse.research.events import research_digest
from oipulse.research.study import QueryMode, StudyPeriod, Universe
from oipulse.signals.model import Signal

__all__ = ["Dataset", "QualitySummary", "RetentionLock", "build_dataset"]


@dataclass(frozen=True, slots=True)
class QualitySummary:
    """What the extract contains and what it had to leave out."""

    total_rows: int
    unreliable_rows: int
    degraded_rows: int
    missing_values: int

    @property
    def usable_rows(self) -> int:
        return self.total_rows - self.unreliable_rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "unreliable_rows": self.unreliable_rows,
            "degraded_rows": self.degraded_rows,
            "missing_values": self.missing_values,
            "usable_rows": self.usable_rows,
        }


@dataclass(frozen=True, slots=True)
class RetentionLock:
    """One pin, per `02` §8's `sys_retention_locks`.

    Emitted rather than written here: this layer does not touch a database. The caller
    that persists the dataset writes these in the same transaction, so the audit chain
    cannot be pruned out from under a stored result.
    """

    subject_kind: str
    subject_id: str
    locked_by_kind: str
    locked_by_id: str


@dataclass(frozen=True, slots=True)
class Dataset:
    """An immutable extract with a reproducible content hash."""

    name: str
    query_mode: QueryMode
    knowledge_horizon: datetime
    build_context_id: str
    period: StudyPeriod
    universe: Universe
    feature_versions: tuple[tuple[str, int], ...]
    builder_version: str
    metrics: tuple[MetricValue, ...] = ()
    signals: tuple[Signal, ...] = ()
    quality: QualitySummary = field(default_factory=lambda: QualitySummary(0, 0, 0, 0))
    #: Execution metadata. Recorded, and deliberately NOT in the content hash.
    created_at: datetime | None = None

    @property
    def row_count(self) -> int:
        return len(self.metrics) + len(self.signals)

    @property
    def content_hash(self) -> str:
        """Hash of the extract's **content**, sorted and canonical.

        Two rebuilds with the same parameters over the same immutable source must
        agree. Row order cannot affect it, because rows are sorted by their own
        identity before hashing; nor can the moment of the rebuild, because
        `created_at` and `name` are excluded.
        """
        metric_rows = sorted(
            "|".join(str(part) for part in m.identity) + f"|{m.inputs_digest}|{m.value}"
            for m in self.metrics
        )
        signal_rows = sorted(f"{s.signal_id}|{s.content_digest()}" for s in self.signals)
        return "ds_" + research_digest(
            {
                "query_mode": self.query_mode.value,
                "knowledge_horizon": self.knowledge_horizon.isoformat(),
                "build_context_id": self.build_context_id,
                "period": [self.period.start.isoformat(), self.period.end.isoformat()],
                "universe": self.universe.as_dict(),
                "feature_versions": [f"{i}@v{v}" for i, v in sorted(self.feature_versions)],
                "builder_version": self.builder_version,
                "metrics": metric_rows,
                "signals": signal_rows,
            }
        )

    def retention_locks(self, locked_by_id: str) -> tuple[RetentionLock, ...]:
        """The rows a pruning job must join against (`02` §8)."""
        locks = [
            RetentionLock(
                "metric_value",
                "|".join(str(p) for p in m.identity),
                "research_result",
                locked_by_id,
            )
            for m in self.metrics
        ]
        locks.extend(
            RetentionLock("signal", s.signal_id, "research_result", locked_by_id)
            for s in self.signals
        )
        return tuple(sorted(locks, key=lambda lock: (lock.subject_kind, lock.subject_id)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "query_mode": self.query_mode.value,
            "knowledge_horizon": self.knowledge_horizon.isoformat(),
            "build_context_id": self.build_context_id,
            "period": {
                "start": self.period.start.isoformat(),
                "end": self.period.end.isoformat(),
            },
            "universe": self.universe.as_dict(),
            "feature_versions": [f"{i}@v{v}" for i, v in self.feature_versions],
            "builder_version": self.builder_version,
            "row_count": self.row_count,
            "content_hash": self.content_hash,
            "quality_summary": self.quality.as_dict(),
            "created_at": None if self.created_at is None else self.created_at.isoformat(),
        }


def build_dataset(
    name: str,
    *,
    query_mode: QueryMode,
    knowledge_horizon: datetime,
    build_context_id: str,
    period: StudyPeriod,
    universe: Universe,
    feature_versions: Sequence[tuple[str, int]],
    builder_version: str,
    metrics: Sequence[MetricValue] = (),
    signals: Sequence[Signal] = (),
    created_at: datetime | None = None,
) -> Dataset:
    """Materialize an extract, filtered to what was known by the knowledge horizon.

    Filtering happens here rather than at read time so the content hash covers exactly
    the rows a study will see. A row whose `knowledge_horizon` is later than the
    dataset's is not in the dataset at all -- there is no path by which a study reaches
    it and no way for a later correction to leak in through the extract.
    """
    kept_metrics = tuple(
        sorted(
            (m for m in metrics if m.knowledge_horizon <= knowledge_horizon),
            key=lambda m: m.identity,
        )
    )
    kept_signals = tuple(
        sorted(
            (s for s in signals if s.identity.knowledge_horizon <= knowledge_horizon),
            key=lambda s: s.signal_id,
        )
    )
    unreliable = sum(1 for m in kept_metrics if m.quality_status.value == "unreliable")
    degraded = sum(1 for m in kept_metrics if m.quality_status.value == "degraded")
    missing = sum(1 for m in kept_metrics if m.value is None)

    return Dataset(
        name=name,
        query_mode=query_mode,
        knowledge_horizon=knowledge_horizon,
        build_context_id=build_context_id,
        period=period,
        universe=universe,
        feature_versions=tuple(sorted(feature_versions)),
        builder_version=builder_version,
        metrics=kept_metrics,
        signals=kept_signals,
        quality=QualitySummary(
            total_rows=len(kept_metrics) + len(kept_signals),
            unreliable_rows=unreliable,
            degraded_rows=degraded,
            missing_values=missing,
        ),
        created_at=created_at,
    )
