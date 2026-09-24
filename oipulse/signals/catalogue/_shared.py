"""Helpers shared by the rule catalogue. Pure; no I/O, no clock.

`evidence_from` is the only way a rule builds evidence, so every item references a real
`metric_values` row rather than a rendered string with numbers baked in. That is what
makes `Signal -> Evidence -> MetricValue -> MarketState -> Observation` resolve by join.
"""

from __future__ import annotations

from decimal import Decimal

from oipulse.analytics.values import MetricValue
from oipulse.signals.context import RuleContext, metric_ref_of
from oipulse.signals.model import Evidence, EvidenceKind

__all__ = ["contradicting", "evidence_from", "numeric", "supporting"]


def evidence_from(value: MetricValue, statement: str, weight: str, kind: EvidenceKind) -> Evidence:
    """Build one referenced evidence item.

    `weight` is a string so a rule's declared weight is exact -- 0.30 written as a
    float is not 0.30, and strength would drift by the last decimal place between
    otherwise identical evaluations.
    """
    magnitude = Decimal(weight)
    return Evidence(
        kind=kind,
        metric_value_ref=metric_ref_of(value),
        statement=statement,
        weight=magnitude if kind is EvidenceKind.SUPPORTING else -abs(magnitude),
        observed_at=value.observed_at,
        observation_refs=(),
    )


def supporting(value: MetricValue, statement: str, weight: str) -> Evidence:
    return evidence_from(value, statement, weight, EvidenceKind.SUPPORTING)


def contradicting(value: MetricValue, statement: str, weight: str) -> Evidence:
    return evidence_from(value, statement, weight, EvidenceKind.CONTRADICTING)


def numeric(ctx: RuleContext, identifier: str, version: int) -> Decimal | None:
    """Pinned numeric metric, or None. Never coerced from a categorical value."""
    return ctx.numeric(identifier, version)
