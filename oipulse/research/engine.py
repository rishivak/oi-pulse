"""The event-study engine — `09-RESEARCH.md` §2 to §4.

```
Historical Market Truth -> Knowledge-Horizon / PIT Selection
  -> Feature / Signal Evidence -> Event Detection
  -> Event Qualification / Sampling -> Forward / Backward Windows
  -> Outcome Computation -> Research Result -> Content-Addressed Artifact
```

Pure and deterministic. The engine reads a `Dataset` and an outcome series supplied by
the caller; it opens no connection, reads no clock, and computes no feature — there is
**no second analytics implementation** here.

Three behaviours are the point of the whole layer, and each is enforced rather than
documented:

* **Look-ahead is refused.** Event detection runs through `PointInTimeAccessor`, so a
  condition can only be evaluated from values available at that instant.
* **Outcome data lives in its own namespace.** Forward prices reach the engine through
  `OutcomeSource`, which is never handed to a detector. A detector physically cannot
  see the future it is being measured against.
* **Insufficient sample is reported, not averaged.** Below the study's minimum the
  result carries `INSUFFICIENT_SAMPLE` and no distribution, judged on the **effective**
  post-clustering count.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from oipulse.research.access import PointInTimeAccessor
from oipulse.research.dataset import Dataset
from oipulse.research.events import Event, detect_over
from oipulse.research.result import (
    ExecutionMetadata,
    HorizonResult,
    ResultStatus,
    StudyResult,
)
from oipulse.research.sampling import SamplingResult, apply_sampling
from oipulse.research.statistics import (
    OutcomeSeries,
    SampleStats,
    describe,
    excursion,
)
from oipulse.research.study import ControlBucket, EventStudy
from oipulse.research.windows import ForwardWindow, WindowCompleteness

__all__ = ["EventStudyEngine", "OutcomeSource", "SeriesOutcomeSource"]


class OutcomeSource(Protocol):
    """Supplies the forward path used to measure an outcome.

    Deliberately a separate collaborator from the accessor. Outcome data is
    future-looking relative to the decision point, so it is legitimate for measurement
    and must be unreachable from any detector or rule context (`09` §2). Keeping it in
    a different object is what makes that structural rather than a convention.
    """

    @property
    def dataset_end(self) -> datetime: ...

    def value_at(self, underlying_id: int, at: datetime) -> Decimal | None: ...


@dataclass(frozen=True, slots=True)
class SeriesOutcomeSource:
    """An outcome source backed by an explicit `(underlying, time) -> value` series.

    A missing instant returns `None` and is counted as missing. It is never filled
    with zero, the previous value or a mean: `09` §3 excludes missing observations and
    reports the exclusion, and imputing would manufacture the very data the study is
    trying to measure.
    """

    points: tuple[tuple[int, datetime, Decimal], ...]

    @property
    def dataset_end(self) -> datetime:
        return max(point[1] for point in self.points) if self.points else _EPOCH

    def value_at(self, underlying_id: int, at: datetime) -> Decimal | None:
        for candidate_id, when, value in self.points:
            if candidate_id == underlying_id and when == at:
                return value
        return None


_EPOCH = datetime.min.replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class _Qualified:
    """An event that passed qualification, with its measured outcomes."""

    event: Event
    baseline: Decimal
    per_horizon: tuple[tuple[timedelta, Decimal | None, str, OutcomeSeries], ...]


class EventStudyEngine:
    """Runs an `EventStudy` over a `Dataset`. Pure; every input is supplied."""

    def __init__(self, *, sample_step: timedelta = timedelta(minutes=5)) -> None:
        # How finely a forward window is sampled for excursion measurement. Declared
        # rather than inferred, because it changes MFE/MAE and therefore belongs to
        # the engine's stated behaviour.
        self._step = sample_step

    # ------------------------------------------------------------------- run

    def run(
        self,
        study: EventStudy,
        dataset: Dataset,
        outcomes: OutcomeSource,
        *,
        scan_instants: Sequence[datetime] | None = None,
        execution: ExecutionMetadata | None = None,
        control_of: Callable[[Event, str], str] | None = None,
    ) -> StudyResult:
        """Detect, qualify, sample, measure, and produce the artifact."""
        accessor = PointInTimeAccessor(metrics=dataset.metrics, signals=dataset.signals)
        instants = tuple(scan_instants) if scan_instants else self._default_instants(dataset)

        detected = detect_over(accessor, study.event_definition, instants)
        in_period = tuple(e for e in detected if study.period.contains(e.market_time))

        excluded_quality = sum(1 for e in in_period if e.quality_status == "unreliable")
        qualifying = tuple(
            e
            for e in in_period
            if not (study.event_definition.exclude_unreliable and e.quality_status == "unreliable")
        )

        sampling = apply_sampling(qualifying, study.sampling, excluded_quality=excluded_quality)

        if not sampling.selected:
            return self._empty(
                study,
                dataset,
                sampling,
                ResultStatus.NO_EVENTS if not in_period else ResultStatus.INSUFFICIENT_SAMPLE,
                "no qualifying event in the study period"
                if not in_period
                else "every detected event was excluded",
                execution,
            )

        qualified, incomplete_by_horizon, missing_total = self._measure(
            study, sampling.selected, outcomes
        )

        # Minimum sample is enforced on the EFFECTIVE count (`09` §3): a mean of nine
        # clusters reported as n=412 is exactly what this prevents.
        if sampling.effective_sample < study.minimum_sample:
            return self._empty(
                study,
                dataset,
                sampling,
                ResultStatus.INSUFFICIENT_SAMPLE,
                f"effective sample {sampling.effective_sample} is below the declared "
                f"minimum {study.minimum_sample}; no statistics are reported",
                execution,
                incomplete=sum(incomplete_by_horizon.values()),
                missing=missing_total,
            )

        horizons = tuple(
            self._horizon_result(study, horizon, qualified, incomplete_by_horizon, control_of)
            for horizon in sorted(study.horizons)
        )

        return StudyResult(
            study_id=study.study_id,
            study_version=study.version,
            study_digest=study.content_digest,
            event_definition_digest=study.event_definition.content_digest,
            dataset_content_hash=dataset.content_hash,
            query_mode=study.query_mode.value,
            knowledge_horizon=dataset.knowledge_horizon,
            build_context_id=dataset.build_context_id,
            feature_versions=tuple(sorted(study.feature_versions)),
            signal_versions=tuple(sorted(study.signal_versions)),
            builder_version=dataset.builder_version,
            sample=SampleStats(
                raw_events=sampling.raw_count,
                effective_sample=sampling.effective_sample,
                clusters=sampling.cluster_count,
                excluded_quality=sampling.excluded_quality,
                excluded_incomplete_window=sum(incomplete_by_horizon.values()),
                missing_observations=missing_total,
                comparisons=study.comparison_count,
            ),
            horizons=horizons,
            status=ResultStatus.OK,
            execution=execution or ExecutionMetadata(),
        )

    # -------------------------------------------------------------- internals

    def _default_instants(self, dataset: Dataset) -> tuple[datetime, ...]:
        """Scan at every instant a value became available.

        Derived from the data rather than a fixed grid: a condition can only become
        true when something new arrives, and scanning on a grid would either miss
        events between ticks or invent evaluation points at which nothing changed.
        """
        instants = {m.available_at for m in dataset.metrics}
        instants |= {s.available_at for s in dataset.signals}
        return tuple(sorted(instants))

    def _measure(
        self,
        study: EventStudy,
        events: Sequence[Event],
        outcomes: OutcomeSource,
    ) -> tuple[tuple[_Qualified, ...], dict[timedelta, int], int]:
        qualified: list[_Qualified] = []
        incomplete: dict[timedelta, int] = dict.fromkeys(study.horizons, 0)
        missing_total = 0

        for event in sorted(events, key=lambda e: (e.market_time, e.occurrence_id)):
            baseline = outcomes.value_at(event.underlying_id, event.market_time)
            if baseline is None or baseline == 0:
                # No anchor, so no return can be computed. Counted as missing rather
                # than assumed -- a zero baseline would make every return infinite.
                missing_total += 1
                continue

            per_horizon: list[tuple[timedelta, Decimal | None, str, OutcomeSeries]] = []
            for horizon in sorted(study.horizons):
                window = ForwardWindow.of(event.market_time, horizon)
                completeness = window.completeness(outcomes.dataset_end)
                if completeness != WindowCompleteness.COMPLETE:
                    # Never fabricated, never truncated silently: excluded and counted.
                    incomplete[horizon] += 1
                    per_horizon.append((horizon, None, completeness, OutcomeSeries((), ())))
                    continue
                series = self._sample_window(outcomes, event.underlying_id, window)
                missing_total += series.missing_count
                terminal = outcomes.value_at(event.underlying_id, window.forward_end)
                forward_return = None if terminal is None else (terminal - baseline) / baseline
                per_horizon.append((horizon, forward_return, completeness, series))

            qualified.append(_Qualified(event, baseline, tuple(per_horizon)))

        return tuple(qualified), incomplete, missing_total

    def _sample_window(
        self, outcomes: OutcomeSource, underlying_id: int, window: ForwardWindow
    ) -> OutcomeSeries:
        offsets: list[timedelta] = []
        values: list[Decimal | None] = []
        cursor = window.forward_start
        while cursor <= window.forward_end:
            offsets.append(cursor - window.event_time)
            values.append(outcomes.value_at(underlying_id, cursor))
            cursor += self._step
        return OutcomeSeries(tuple(offsets), tuple(values))

    def _horizon_result(
        self,
        study: EventStudy,
        horizon: timedelta,
        qualified: Sequence[_Qualified],
        incomplete: dict[timedelta, int],
        control_of: Callable[[Event, str], str] | None,
    ) -> HorizonResult:
        returns: list[Decimal] = []
        merged_offsets: list[timedelta] = []
        merged_values: list[Decimal | None] = []
        by_control: dict[str, dict[str, list[Decimal]]] = {c.value: {} for c in study.controls}

        for item in qualified:
            for candidate, forward_return, _completeness, series in item.per_horizon:
                if candidate != horizon:
                    continue
                if forward_return is not None:
                    returns.append(forward_return)
                    for control in study.controls:
                        bucket = (
                            control_of(item.event, control.value)
                            if control_of is not None
                            else _default_bucket(item.event, control)
                        )
                        by_control[control.value].setdefault(bucket, []).append(forward_return)
                for offset, value in zip(series.offsets, series.values, strict=True):
                    merged_offsets.append(offset)
                    merged_values.append(
                        None if value is None else (value - item.baseline) / item.baseline
                    )

        breakdowns = tuple(
            (
                control,
                tuple(
                    (bucket, describe(values, bucket=Decimal(study.histogram_bucket)))
                    for bucket, values in sorted(buckets.items())
                ),
            )
            for control, buckets in sorted(by_control.items())
            if buckets
        )

        return HorizonResult(
            horizon=horizon,
            distribution=describe(returns, bucket=Decimal(study.histogram_bucket)),
            excursion=excursion(
                OutcomeSeries(tuple(merged_offsets), tuple(merged_values)), Decimal(0)
            ),
            incomplete_windows=incomplete.get(horizon, 0),
            breakdowns=breakdowns,
        )

    def _empty(
        self,
        study: EventStudy,
        dataset: Dataset,
        sampling: SamplingResult,
        status: ResultStatus,
        detail: str,
        execution: ExecutionMetadata | None,
        *,
        incomplete: int = 0,
        missing: int = 0,
    ) -> StudyResult:
        """A result with no statistics but full identity.

        Still content-addressed and still stored: "we looked and the sample was too
        small" is a finding, and a study that silently produced nothing would be
        indistinguishable from one that was never run.
        """
        return StudyResult(
            study_id=study.study_id,
            study_version=study.version,
            study_digest=study.content_digest,
            event_definition_digest=study.event_definition.content_digest,
            dataset_content_hash=dataset.content_hash,
            query_mode=study.query_mode.value,
            knowledge_horizon=dataset.knowledge_horizon,
            build_context_id=dataset.build_context_id,
            feature_versions=tuple(sorted(study.feature_versions)),
            signal_versions=tuple(sorted(study.signal_versions)),
            builder_version=dataset.builder_version,
            sample=SampleStats(
                raw_events=sampling.raw_count,
                effective_sample=sampling.effective_sample,
                clusters=sampling.cluster_count,
                excluded_quality=sampling.excluded_quality,
                excluded_incomplete_window=incomplete,
                missing_observations=missing,
                comparisons=study.comparison_count,
            ),
            horizons=(),
            status=status,
            status_detail=detail,
            execution=execution or ExecutionMetadata(),
        )


def _default_bucket(event: Event, control: ControlBucket) -> str:
    """Bucket an event for a breakdown when the caller supplies no classifier.

    Only dimensions derivable from the event itself are bucketed; `REGIME` needs the
    Phase 4 classifier's output and is reported as `unknown` rather than guessed.
    """
    if control is ControlBucket.TIME_OF_DAY:
        return f"{event.market_time.hour:02d}h"
    if control is ControlBucket.QUALITY_STATUS:
        return event.quality_status
    if control is ControlBucket.EXPIRY_PROXIMITY:
        return "unknown" if event.expiry_id is None else f"expiry:{event.expiry_id}"
    return "unknown"
