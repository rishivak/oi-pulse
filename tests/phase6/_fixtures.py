"""SYNTHETIC fixtures for Phase 6. Not recorded from Upstox.

Metric values and signals are constructed directly: a research test needs to state
exactly which `available_at` and `knowledge_horizon` it is probing, and running the
real pipeline to obtain them would obscure the very thing under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from oipulse.analytics.values import MetricValue, Scope, ScopeRef
from oipulse.marketstate.staleness import QualityStatus
from oipulse.research.dataset import build_dataset
from oipulse.research.events import EventDefinition, EventKind, threshold_crossing_event
from oipulse.research.study import EventStudy, QueryMode, StudyPeriod, Universe
from tests.phase4._fixtures import CONTEXT

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

BASE = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)
UNDERLYING = 100
EXPIRY_A = 10
BUILDER_VERSION = "1.0.0"


def at(minutes: float = 0, seconds: float = 0) -> datetime:
    return BASE + timedelta(minutes=minutes, seconds=seconds)


def metric(
    feature_id: str = "PUT_OI_MIGRATION",
    version: int = 1,
    *,
    value: Decimal | int | str | None = Decimal("120"),
    observed_at: datetime | None = None,
    ingested_at: datetime | None = None,
    available_at: datetime | None = None,
    knowledge_horizon: datetime | None = None,
    quality: QualityStatus = QualityStatus.OK,
    scope: ScopeRef | None = None,
    digest: str = "d0",
) -> MetricValue:
    observed = observed_at or BASE
    known = knowledge_horizon or ingested_at or observed
    return MetricValue(
        feature_id=feature_id,
        feature_version=version,
        scope=scope or ScopeRef(Scope.UNDERLYING, str(UNDERLYING)),
        value=value,  # type: ignore[arg-type]
        unit="strike_points",
        observed_at=observed,
        knowledge_horizon=known,
        computed_at=known + timedelta(seconds=1),
        available_at=available_at or (known + timedelta(seconds=2)),
        build_context_id=CONTEXT.id,
        inputs_digest=f"{feature_id}:{digest}",
        quality_status=quality,
    )


def surge_definition(
    *, threshold: str = "100", version: int = 1, exclude_unreliable: bool = True
) -> EventDefinition:
    """`PUT_OI_MIGRATION` crossing a declared threshold."""
    return EventDefinition(
        event_id="PUT_OI_SURGE",
        version=version,
        kind=EventKind.FEATURE_THRESHOLD_CROSSING,
        definition="Put OI migration exceeds the declared threshold over the window.",
        requires_features=(("PUT_OI_MIGRATION", 1),),
        config=(("threshold", threshold), ("direction", "above")),
        detector=threshold_crossing_event,
        exclude_unreliable=exclude_unreliable,
    )


def study(
    *,
    definition: EventDefinition | None = None,
    horizons: tuple[timedelta, ...] | None = None,
    minimum_sample: int = 1,
    query_mode: QueryMode = QueryMode.KNOWLEDGE_AT,
    version: int = 1,
    period_end_minutes: int = 240,
    question: str = "When put OI migrates above the threshold, what happens next?",
    **kwargs: object,
) -> EventStudy:
    return EventStudy(
        study_id="PUT_OI_SURGE_FORWARD",
        version=version,
        question=question,
        event_definition=definition or surge_definition(),
        universe=Universe(underlying_ids=(UNDERLYING,)),
        period=StudyPeriod(BASE, at(period_end_minutes)),
        horizons=horizons or EventStudy.horizons_of(5, 15),
        feature_versions=(("PUT_OI_MIGRATION", 1),),
        query_mode=query_mode,
        minimum_sample=minimum_sample,
        **kwargs,  # type: ignore[arg-type]
    )


def dataset(metrics, *, knowledge_horizon: datetime | None = None, name: str = "ds"):
    definition = study()
    return build_dataset(
        name,
        query_mode=definition.query_mode,
        knowledge_horizon=knowledge_horizon or at(600),
        build_context_id=CONTEXT.id,
        period=definition.period,
        universe=definition.universe,
        feature_versions=definition.feature_versions,
        builder_version=BUILDER_VERSION,
        metrics=tuple(metrics),
    )


def price_series(
    *, start: int = 0, end: int = 400, step: int = 5, slope: str = "1"
) -> tuple[tuple[int, datetime, Decimal], ...]:
    """A deterministic spot path: 25000 plus `slope` per minute."""
    return tuple(
        (UNDERLYING, at(minute), Decimal(25000) + Decimal(slope) * Decimal(minute))
        for minute in range(start, end + 1, step)
    )
