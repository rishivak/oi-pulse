"""Rendering signals and evidence for the API.

Kept out of `api/` for the same reason the MarketState envelope is: the shape is a
pure function of the signal, so the contract stays testable on an interpreter with no
web stack installed. `api/` parses, delegates and transports.

Evidence renders as **references plus a statement**, never as a rendered string with
numbers baked in: a consumer must be able to follow `metric_ref` back to the exact
`metric_values` row.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.signals.model import Evidence, MetricRef, Signal
from oipulse.signals.rules import SignalRuleSpec

__all__ = ["evidence_to_dict", "signal_to_dict", "signal_type_to_dict"]


def _num(value: Decimal | None) -> str | None:
    """Decimals as strings: a strength round-tripped through a double is not the
    strength that was derived."""
    return None if value is None else str(value)


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _ref_to_dict(ref: MetricRef) -> dict[str, Any]:
    return {
        "feature_id": ref.feature_id,
        "feature_version": ref.feature_version,
        "label": ref.label,
        "scope_kind": ref.scope_kind,
        "scope_ref": ref.scope_ref,
        "observed_at": _ts(ref.observed_at),
        "knowledge_horizon": _ts(ref.knowledge_horizon),
        "build_context_id": ref.build_context_id,
        "inputs_digest": ref.inputs_digest,
        "available_at": _ts(ref.available_at),
    }


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "kind": evidence.kind.value,
        "statement": evidence.statement,
        "weight": _num(evidence.weight),
        "observed_at": _ts(evidence.observed_at),
        "metric_ref": _ref_to_dict(evidence.metric_value_ref),
        "observation_refs": list(evidence.observation_refs),
    }


def signal_to_dict(signal: Signal, *, include_evidence: bool = True) -> dict[str, Any]:
    """Full signal representation.

    `contradiction_assessment` renders as the string `NONE_OBSERVED` or as a list --
    the distinction is preserved on the wire, because "assessed, found nothing" and
    "has contradicting evidence" are different claims and a client must be able to
    tell them apart.
    """
    body: dict[str, Any] = {
        "signal_id": signal.signal_id,
        "type": signal.signal_type,
        "rule_version": signal.identity.rule_version,
        "underlying_id": signal.identity.underlying_id,
        "expiry_id": signal.identity.expiry_id,
        "occurrence": signal.identity.occurrence,
        "status": signal.status.value,
        "strength": _num(signal.strength),
        "horizon_seconds": signal.horizon.total_seconds(),
        "market_time": _ts(signal.identity.market_time),
        "knowledge_time": _ts(signal.identity.knowledge_horizon),
        "created_at": _ts(signal.created_at),
        "updated_at": _ts(signal.updated_at),
        "available_at": _ts(signal.available_at),
        "expires_at": _ts(signal.expires_at),
        "invalidation_condition": signal.invalidation_condition,
        "quality_status": signal.quality_status.value,
        "contradiction_assessment": (
            "NONE_OBSERVED"
            if signal.found_no_contradiction
            else [evidence_to_dict(e) for e in signal.contradicting]
        ),
        "provenance": signal.provenance.as_dict(),
        "content_digest": signal.content_digest(),
    }
    if include_evidence:
        body["supporting_evidence"] = [evidence_to_dict(e) for e in signal.supporting]
    return body


def signal_type_to_dict(spec: SignalRuleSpec) -> dict[str, Any]:
    """The rule definition, for `/signals/types`."""
    return spec.as_dict()
