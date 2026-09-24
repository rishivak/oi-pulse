"""Event sampling and overlap — `09-RESEARCH.md` §3.

The problem, in the design's own words:

```
11:00 event ─┐
11:05 event ─┤  all four share most of a 30-minute forward window.
11:10 event ─┤  Raw n = 4.  Independent information ≈ 1.
11:15 event ─┘
```

> Treating n=4 as four independent observations inflates significance — the classic
> way a spurious result survives a sample-size check.

So the study declares its policy, and **every result reports both counts, always**:

```
raw events            412
clusters              63
effective sample      63        <- significance is judged on this
mean overlap          4.8 events/cluster
```

`minimum_event_separation` defaults to the longest forward horizon in the study, so
forward windows cannot overlap unless the researcher opts in.

Phase 1 does not require block bootstrap or Newey-West. It **does** require that the
engine know events overlap and say so; reporting `n = 412` when there are 63
independent clusters is the failure being prevented.

Pure and deterministic: the same event stream and policy always select the same events.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from oipulse.research.events import Event, research_digest

__all__ = [
    "ClusterMethod",
    "EventCluster",
    "EventSamplingPolicy",
    "OverlapPolicy",
    "SamplingPolicy",
    "SamplingResult",
    "apply_sampling",
]


class EventSamplingPolicy(StrEnum):
    ALL = "ALL"
    #: The conservative default (`09` §3).
    FIRST_PER_CLUSTER = "FIRST_PER_CLUSTER"
    DECORRELATED = "DECORRELATED"


class OverlapPolicy(StrEnum):
    ALLOW = "ALLOW"
    DROP_OVERLAPPING = "DROP_OVERLAPPING"
    CLUSTER = "CLUSTER"


class ClusterMethod(StrEnum):
    TIME_PROXIMITY = "TIME_PROXIMITY"
    SIGNAL_INSTANCE = "SIGNAL_INSTANCE"
    REGIME_BLOCK = "REGIME_BLOCK"


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    """The declared policy block from `09` §3."""

    event_sampling_policy: EventSamplingPolicy = EventSamplingPolicy.FIRST_PER_CLUSTER
    overlap_policy: OverlapPolicy = OverlapPolicy.CLUSTER
    #: Defaults to the longest forward horizon; see `for_horizons`.
    minimum_event_separation: timedelta = timedelta(minutes=30)
    cluster_method: ClusterMethod = ClusterMethod.TIME_PROXIMITY

    @staticmethod
    def for_horizons(
        horizons: Sequence[timedelta],
        *,
        event_sampling_policy: EventSamplingPolicy = EventSamplingPolicy.FIRST_PER_CLUSTER,
        overlap_policy: OverlapPolicy = OverlapPolicy.CLUSTER,
        cluster_method: ClusterMethod = ClusterMethod.TIME_PROXIMITY,
    ) -> SamplingPolicy:
        """`minimum_event_separation` defaults to the longest forward horizon.

        Derived rather than left to the researcher, so forward windows cannot overlap
        unless someone opts in explicitly (`09` §3).
        """
        longest = max(horizons) if horizons else timedelta(minutes=30)
        return SamplingPolicy(
            event_sampling_policy=event_sampling_policy,
            overlap_policy=overlap_policy,
            minimum_event_separation=longest,
            cluster_method=cluster_method,
        )

    @property
    def content_digest(self) -> str:
        return (
            "smp_"
            + research_digest(
                {
                    "event_sampling_policy": self.event_sampling_policy.value,
                    "overlap_policy": self.overlap_policy.value,
                    "minimum_event_separation_s": self.minimum_event_separation.total_seconds(),
                    "cluster_method": self.cluster_method.value,
                }
            )[:24]
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "event_sampling_policy": self.event_sampling_policy.value,
            "overlap_policy": self.overlap_policy.value,
            "minimum_event_separation_seconds": (self.minimum_event_separation.total_seconds()),
            "cluster_method": self.cluster_method.value,
        }


@dataclass(frozen=True, slots=True)
class EventCluster:
    """A group of events treated as one piece of independent information."""

    members: tuple[Event, ...]

    @property
    def representative(self) -> Event:
        """The earliest member. Deterministic, and the conservative choice: the first
        occurrence is the one a strategy could actually have acted on."""
        return self.members[0]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True, slots=True)
class SamplingResult:
    """Selected events plus **both** counts. Neither is ever omitted."""

    selected: tuple[Event, ...]
    clusters: tuple[EventCluster, ...]
    raw_count: int
    dropped_overlapping: int = 0
    dropped_separation: int = 0
    excluded_quality: int = 0
    policy: SamplingPolicy = field(default_factory=SamplingPolicy)

    @property
    def effective_sample(self) -> int:
        """The count significance is judged on (`09` §3)."""
        return len(self.selected)

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)

    @property
    def mean_overlap(self) -> Decimal:
        """Mean events per cluster. Reported so the reader can see the inflation."""
        if not self.clusters:
            return Decimal(0)
        total = sum(c.size for c in self.clusters)
        return Decimal(total) / Decimal(len(self.clusters))

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_events": self.raw_count,
            "clusters": self.cluster_count,
            "effective_sample": self.effective_sample,
            "mean_overlap": str(self.mean_overlap),
            "dropped_overlapping": self.dropped_overlapping,
            "dropped_separation": self.dropped_separation,
            "excluded_quality": self.excluded_quality,
            "policy": self.policy.as_dict(),
        }


def _cluster(events: tuple[Event, ...], policy: SamplingPolicy) -> tuple[EventCluster, ...]:
    """Group events into independent blocks under the declared method."""
    if not events:
        return ()

    if policy.cluster_method is ClusterMethod.SIGNAL_INSTANCE:
        by_instance: dict[str, list[Event]] = {}
        for event in events:
            key = event.evidence_refs[0] if event.evidence_refs else event.occurrence_id
            by_instance.setdefault(key, []).append(event)
        return tuple(
            EventCluster(tuple(sorted(group, key=lambda e: e.market_time)))
            for _, group in sorted(by_instance.items())
        )

    # TIME_PROXIMITY and REGIME_BLOCK both reduce to contiguous runs in market time
    # here; REGIME_BLOCK additionally breaks a run when the underlying changes, since
    # two underlyings are never one block of market information.
    clusters: list[list[Event]] = []
    for event in events:
        if not clusters:
            clusters.append([event])
            continue
        current = clusters[-1]
        gap = event.market_time - current[-1].market_time
        same_scope = (
            event.underlying_id == current[-1].underlying_id
            if policy.cluster_method is ClusterMethod.REGIME_BLOCK
            else True
        )
        if gap < policy.minimum_event_separation and same_scope:
            current.append(event)
        else:
            clusters.append([event])
    return tuple(EventCluster(tuple(group)) for group in clusters)


def apply_sampling(
    events: Sequence[Event],
    policy: SamplingPolicy,
    *,
    excluded_quality: int = 0,
) -> SamplingResult:
    """Select events under the declared policy. Deterministic for a given input.

    Events are sorted by `(market_time, occurrence_id)` first, so two runs over the
    same set in different orders select identically — the reordered-input determinism
    the Phase 6 gate requires.
    """
    ordered = tuple(sorted(events, key=lambda e: (e.market_time, e.occurrence_id)))
    raw_count = len(ordered)
    clusters = _cluster(ordered, policy)

    if policy.event_sampling_policy is EventSamplingPolicy.ALL:
        # Overlap permitted, but the effective sample is still reported: the caller
        # sees both numbers and can judge significance on the cluster count.
        selected = ordered
        dropped_overlapping = 0
        if policy.overlap_policy is OverlapPolicy.DROP_OVERLAPPING:
            selected, dropped_overlapping = _drop_overlapping(ordered, policy)
        return SamplingResult(
            selected=selected,
            clusters=clusters,
            raw_count=raw_count,
            dropped_overlapping=dropped_overlapping,
            excluded_quality=excluded_quality,
            policy=policy,
        )

    if policy.event_sampling_policy is EventSamplingPolicy.FIRST_PER_CLUSTER:
        selected = tuple(c.representative for c in clusters)
        return SamplingResult(
            selected=selected,
            clusters=clusters,
            raw_count=raw_count,
            dropped_overlapping=raw_count - len(selected),
            excluded_quality=excluded_quality,
            policy=policy,
        )

    # DECORRELATED: enforce the minimum separation strictly across the whole stream,
    # so no two selected events can share a forward window.
    selected_list: list[Event] = []
    dropped = 0
    for event in ordered:
        if selected_list:
            gap = event.market_time - selected_list[-1].market_time
            if gap < policy.minimum_event_separation:
                dropped += 1
                continue
        selected_list.append(event)
    return SamplingResult(
        selected=tuple(selected_list),
        clusters=clusters,
        raw_count=raw_count,
        dropped_separation=dropped,
        excluded_quality=excluded_quality,
        policy=policy,
    )


def _drop_overlapping(
    events: tuple[Event, ...], policy: SamplingPolicy
) -> tuple[tuple[Event, ...], int]:
    kept: list[Event] = []
    dropped = 0
    for event in events:
        if kept and event.market_time - kept[-1].market_time < policy.minimum_event_separation:
            dropped += 1
            continue
        kept.append(event)
    return tuple(kept), dropped
