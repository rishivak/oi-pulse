"""Shared construction of a `MetricValue` from a `ComputeContext`.

Factored so every feature computes `available_at` the same way. A feature that built
its own timestamp could quietly use `observed_at`, which is the one thing `07` §3
forbids, and the resulting value would look perfectly plausible.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.analytics.availability import AvailabilityInputs, available_at, check_invariants
from oipulse.analytics.context import ComputeContext
from oipulse.analytics.registry import FeatureSpec
from oipulse.analytics.values import MetricValue, ScopeRef, inputs_digest

__all__ = ["emit"]


def emit(
    spec: FeatureSpec,
    ctx: ComputeContext,
    scope: ScopeRef,
    value: Decimal | int | str | tuple[tuple[str, Decimal | int | None], ...] | None,
    *,
    extra_inputs: dict[str, Any] | None = None,
    evidence: tuple[str, ...] = (),
) -> MetricValue:
    """Build the value with its four timestamps and its input digest.

    `available_at` follows `07` §3 exactly, and the invariants are re-checked here
    rather than trusted: this is the single place every feature passes through, so one
    assertion covers all of them.
    """
    lookback = spec.lookback_delta
    inputs = AvailabilityInputs(
        lookback_end=ctx.lookback_end(lookback),
        computed_at=ctx.computed_at,
        availability_delay=spec.availability_delay_delta,
        raw_input_ingested_at=ctx.raw_input_ingested_at,
        dependency_available_at=ctx.dependency_available_at(),
    )
    when: datetime = available_at(inputs)
    violations = check_invariants(when, inputs)
    if violations:  # pragma: no cover - a bug in the formula above, not in a feature
        raise AssertionError(f"{spec.label}: availability invariant broken: {violations}")

    return MetricValue(
        feature_id=spec.identifier,
        feature_version=spec.version,
        scope=scope,
        value=value,
        unit=spec.units,
        observed_at=ctx.market_time,
        knowledge_horizon=ctx.knowledge_horizon,
        computed_at=ctx.computed_at,
        available_at=when,
        build_context_id=ctx.build_context_id,
        inputs_digest=inputs_digest(ctx.digest_payload(extra_inputs)),
        quality_status=ctx.quality_status,
        evidence=evidence,
    )
