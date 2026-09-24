"""SYNTHETIC fixtures for Phase 5. Not recorded from Upstox.

Metric values are constructed directly rather than run through the Phase 4 engine
where a test only needs a specific input value; the end-to-end path is exercised
separately in `test_evaluation.py`. Constructing them keeps each rule test readable
and lets a test state exactly which availability it is probing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from oipulse.analytics.values import MetricValue, Scope, ScopeRef
from oipulse.marketstate.staleness import QualityStatus
from oipulse.signals.context import RuleConfig, RuleContext
from oipulse.signals.model import (
    Evidence,
    EvidenceKind,
    MetricRef,
    Signal,
    SignalIdentity,
    SignalProvenance,
    SignalStatus,
)
from tests.phase4._fixtures import CONTEXT, UNDERLYING, state

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

BASE = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)
EXPIRY_A = 10


def at(minutes: float = 0, seconds: float = 0) -> datetime:
    return BASE + timedelta(minutes=minutes, seconds=seconds)


def metric(
    feature_id: str,
    version: int = 1,
    *,
    value: object = Decimal("120"),
    observed_at: datetime | None = None,
    available_at: datetime | None = None,
    knowledge_horizon: datetime | None = None,
    unit: str = "strike_points",
    scope: ScopeRef | None = None,
    quality: QualityStatus = QualityStatus.OK,
    digest: str = "d0",
) -> MetricValue:
    observed = observed_at or BASE
    return MetricValue(
        feature_id=feature_id,
        feature_version=version,
        scope=scope or ScopeRef(Scope.EXPIRY, str(EXPIRY_A)),
        value=value,  # type: ignore[arg-type]
        unit=unit,
        observed_at=observed,
        knowledge_horizon=knowledge_horizon or observed,
        computed_at=observed + timedelta(seconds=1),
        available_at=available_at or (observed + timedelta(seconds=2)),
        build_context_id=CONTEXT.id,
        inputs_digest=f"{feature_id}:{digest}",
        quality_status=quality,
    )


def put_support_metrics(
    *,
    migration: str = "120",
    available_at: datetime | None = None,
    observed_at: datetime | None = None,
) -> tuple[MetricValue, ...]:
    """The metric set `PUT_SUPPORT_MIGRATION` pins (the design's worked example)."""
    common = {"observed_at": observed_at or BASE, "available_at": available_at}
    return (
        metric("PUT_OI_MIGRATION", 1, value=Decimal(migration), **common),  # type: ignore[arg-type]
        metric("OI_WALL_PUT", 1, value=Decimal("25000"), unit="strike", **common),  # type: ignore[arg-type]
        metric("FUTURES_OI_CHANGE", 1, value=2100, unit="contracts", **common),  # type: ignore[arg-type]
        metric("ATM_IV", 1, value=Decimal("0.1345"), unit="iv_decimal", **common),  # type: ignore[arg-type]
        metric("OI_WALL_CALL", 1, value=Decimal("25200"), unit="strike", **common),  # type: ignore[arg-type]
        metric("IV_CHANGE", 1, value=Decimal("0.012"), unit="iv_decimal", **common),  # type: ignore[arg-type]
    )


def market_state(
    *,
    market_time: datetime | None = None,
    quality: QualityStatus = QualityStatus.OK,
    spot: str = "25000",
):
    return state(market_time=market_time or BASE, quality=quality, spot=spot)


def rule_context(
    values: tuple[MetricValue, ...],
    *,
    knowledge_horizon: datetime | None = None,
    config: RuleConfig | None = None,
    quality: QualityStatus = QualityStatus.OK,
    prior_strengths: tuple[Decimal, ...] = (),
) -> RuleContext:
    horizon = knowledge_horizon or at(0, 5)
    return RuleContext(
        state=market_state(quality=quality),
        knowledge_horizon_override=horizon,
        metrics=RuleContext.available_only(values, horizon),
        config=config or RuleConfig(),
        prior_strengths=prior_strengths,
    )


def evidence(
    feature_id: str = "PUT_OI_MIGRATION",
    *,
    kind: EvidenceKind = EvidenceKind.SUPPORTING,
    weight: str = "0.30",
    statement: str = "synthetic evidence",
) -> Evidence:
    return Evidence(
        kind=kind,
        metric_value_ref=MetricRef(
            feature_id=feature_id,
            feature_version=1,
            scope_kind="expiry",
            scope_ref=str(EXPIRY_A),
            observed_at=BASE,
            knowledge_horizon=BASE,
            build_context_id=CONTEXT.id,
            inputs_digest="d0",
            available_at=BASE + timedelta(seconds=2),
        ),
        statement=statement,
        weight=Decimal(weight) if kind is EvidenceKind.SUPPORTING else -abs(Decimal(weight)),
        observed_at=BASE,
    )


def signal(
    *,
    signal_type: str = "PUT_SUPPORT_MIGRATION",
    status: SignalStatus = SignalStatus.ACTIVE,
    strength: str = "0.68",
    market_time: datetime | None = None,
    knowledge_horizon: datetime | None = None,
    occurrence: int = 1,
    config_digest: str = "cfg_synthetic",
    build_context_id: str | None = None,
) -> Signal:
    t = market_time or BASE
    # The signal's knowledge horizon is never before its market time: features derived
    # from a state at T only become available after T.
    k = knowledge_horizon or (t + timedelta(seconds=5))
    identity = SignalIdentity(
        signal_type=signal_type,
        rule_version=1,
        underlying_id=int(UNDERLYING),
        expiry_id=EXPIRY_A,
        market_time=t,
        knowledge_horizon=k,
        build_context_id=build_context_id or CONTEXT.id,
        config_digest=config_digest,
        occurrence=occurrence,
    )
    return Signal(
        identity=identity,
        horizon=timedelta(minutes=30),
        status=status,
        strength=Decimal(strength),
        evidence=(evidence(),),
        contradiction_assessment=(evidence(kind=EvidenceKind.CONTRADICTING, weight="0.12"),),
        invalidation_condition="put wall returns below 24,950",
        provenance=SignalProvenance(
            rule_type=signal_type,
            rule_version=1,
            config_digest=config_digest,
            feature_versions=(("PUT_OI_MIGRATION", 1),),
            inputs_digest="inputs0",
            strength_function="NORMALIZED_WEIGHTED_SUM",
            strength_function_version=1,
            build_context_id=build_context_id or CONTEXT.id,
        ),
        created_at=k,
        updated_at=k,
        available_at=k,
        expires_at=t + timedelta(minutes=30),
        quality_status=QualityStatus.OK,
        history=((f"{status.value}:seed", k),),
    )
