"""The `EventStudy` definition — `09-RESEARCH.md` §3.

```
EventStudy
├── question               prose statement of the hypothesis
├── condition              an evaluable predicate over MarketState + features
├── universe               underlyings, expiries, strike selection
├── period                 study start and end
├── query_mode             knowledge_at (default) | market_truth_at (flagged)
├── horizons[]             5m, 15m, 30m, 60m, EOD
├── controls               regime, time-of-day, expiry-proximity buckets
├── sampling               event/overlap policy
├── feature_versions{}     exact versions pinned
└── minimum_sample         below which no result is reported
```

**The study's identity is its content, not when it ran.** `content_digest` covers the
question, the event definition and version, the pinned feature and signal versions, the
universe, the period, the query mode, the horizons, the sampling policy, the knowledge
policy, the controls and the minimum sample. It deliberately excludes `created_at` and
every other execution timestamp: re-running the same study tomorrow must produce the
same identity, or reproducibility cannot be checked by comparison.

`query_mode` defaults to `knowledge_at`. `market_truth_at` is the hindsight mode and is
**flagged on the study and carried onto every result**, so a hindsight number can never
be mistaken for a live-reproducible one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from oipulse.research.events import EventDefinition, research_digest
from oipulse.research.sampling import SamplingPolicy

__all__ = [
    "ControlBucket",
    "EventStudy",
    "QueryMode",
    "StudyPeriod",
    "Universe",
    "horizons_from",
]


class QueryMode(StrEnum):
    """`09` §3. `knowledge_at` is the default; `market_truth_at` is flagged.

    The third mode, `tradable_information_at`, is what the accessor enforces on every
    feature read regardless of study mode -- availability is never optional.
    """

    KNOWLEDGE_AT = "knowledge_at"
    MARKET_TRUTH_AT = "market_truth_at"

    @property
    def is_hindsight(self) -> bool:
        """True for the mode that may see corrections arriving after the event."""
        return self is QueryMode.MARKET_TRUTH_AT


class ControlBucket(StrEnum):
    """The breakdown dimensions `09` §3 names."""

    REGIME = "regime"
    TIME_OF_DAY = "time_of_day"
    EXPIRY_PROXIMITY = "expiry_proximity"
    QUALITY_STATUS = "quality_status"


@dataclass(frozen=True, slots=True)
class StudyPeriod:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"study period ends {self.end.isoformat()} before it starts "
                f"{self.start.isoformat()}"
            )

    def contains(self, at: datetime) -> bool:
        return self.start <= at <= self.end


@dataclass(frozen=True, slots=True)
class Universe:
    """Which instruments the study covers.

    Resolved **as-of each point in time** by the caller from instrument versions, so
    expired contracts are present as they were: the survivorship control in `09` §6.
    The study records what it asked for; the accessor supplies what existed.
    """

    underlying_ids: tuple[int, ...]
    expiry_ids: tuple[int, ...] = ()
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "underlying_ids": list(self.underlying_ids),
            "expiry_ids": list(self.expiry_ids),
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class EventStudy:
    """A versioned, content-addressed research question."""

    study_id: str
    version: int
    question: str
    event_definition: EventDefinition
    universe: Universe
    period: StudyPeriod
    horizons: tuple[timedelta, ...]
    #: Exact versions pinned, so a feature bump cannot silently change a result.
    feature_versions: tuple[tuple[str, int], ...] = ()
    signal_versions: tuple[tuple[str, int], ...] = ()
    query_mode: QueryMode = QueryMode.KNOWLEDGE_AT
    sampling: SamplingPolicy = field(default_factory=SamplingPolicy)
    controls: tuple[ControlBucket, ...] = ()
    #: Enforced on the **effective** (post-clustering) count, never the raw one.
    minimum_sample: int = 30
    #: Bucket width for the returned histogram.
    histogram_bucket: str = "0.005"

    def __post_init__(self) -> None:
        if not self.horizons:
            raise ValueError(f"{self.study_id}: a study must declare at least one horizon")
        if self.minimum_sample < 1:
            raise ValueError(f"{self.study_id}: minimum_sample must be at least 1")

    @property
    def key(self) -> tuple[str, int]:
        return (self.study_id, self.version)

    @property
    def label(self) -> str:
        return f"{self.study_id}@v{self.version}"

    @property
    def longest_horizon(self) -> timedelta:
        return max(self.horizons)

    @property
    def comparison_count(self) -> int:
        """Horizons times breakdown buckets, plus the unconditional pass.

        Recorded on every result so a reader can discount for multiple comparisons
        (`09` §3): running many horizons and breakdowns inflates the chance of a
        spurious hit, and hiding the count hides the inflation.
        """
        return len(self.horizons) * (len(self.controls) + 1)

    @property
    def content_digest(self) -> str:
        """Semantic identity. Excludes every execution timestamp, by design."""
        return (
            "std_"
            + research_digest(
                {
                    "study_id": self.study_id,
                    "version": self.version,
                    "question": self.question,
                    "event_definition": self.event_definition.content_digest,
                    "universe": self.universe.as_dict(),
                    "period": [self.period.start.isoformat(), self.period.end.isoformat()],
                    "horizons_s": sorted(h.total_seconds() for h in self.horizons),
                    "feature_versions": [f"{i}@v{v}" for i, v in sorted(self.feature_versions)],
                    "signal_versions": [f"{i}@v{v}" for i, v in sorted(self.signal_versions)],
                    "query_mode": self.query_mode.value,
                    "sampling": self.sampling.content_digest,
                    "controls": sorted(c.value for c in self.controls),
                    "minimum_sample": self.minimum_sample,
                    "histogram_bucket": self.histogram_bucket,
                }
            )[:32]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "version": self.version,
            "question": self.question,
            "event_definition": self.event_definition.as_dict(),
            "universe": self.universe.as_dict(),
            "period": {
                "start": self.period.start.isoformat(),
                "end": self.period.end.isoformat(),
            },
            "horizons_seconds": [h.total_seconds() for h in self.horizons],
            "feature_versions": [f"{i}@v{v}" for i, v in self.feature_versions],
            "signal_versions": [f"{i}@v{v}" for i, v in self.signal_versions],
            "query_mode": self.query_mode.value,
            "is_hindsight": self.query_mode.is_hindsight,
            "sampling": self.sampling.as_dict(),
            "controls": [c.value for c in self.controls],
            "minimum_sample": self.minimum_sample,
            "comparison_count": self.comparison_count,
            "content_digest": self.content_digest,
        }

    @staticmethod
    def horizons_of(*minutes: int) -> tuple[timedelta, ...]:
        return tuple(timedelta(minutes=m) for m in sorted(minutes))

    def with_sampling_defaults(self) -> EventStudy:
        """Derive `minimum_event_separation` from the longest horizon (`09` §3).

        Applied explicitly rather than in `__post_init__` so a study that states its
        own separation keeps it, and the default is visibly a default.
        """
        from dataclasses import replace

        return replace(
            self,
            sampling=SamplingPolicy.for_horizons(
                self.horizons,
                event_sampling_policy=self.sampling.event_sampling_policy,
                overlap_policy=self.sampling.overlap_policy,
                cluster_method=self.sampling.cluster_method,
            ),
        )


def horizons_from(seconds: Sequence[float]) -> tuple[timedelta, ...]:
    return tuple(timedelta(seconds=s) for s in sorted(seconds))
