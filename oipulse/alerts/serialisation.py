"""Rendering alert rules and occurrences for the API. Pure, web-stack-free."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.alerts.model import AlertOccurrence, AlertRule

__all__ = ["occurrence_to_dict", "rule_to_dict"]


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def rule_to_dict(rule: AlertRule) -> dict[str, Any]:
    """`destination` is echoed as configured and is never a credential -- it names a
    destination that deployment configuration resolves, so no secret can leak here."""
    return {
        "id": rule.id,
        "signal_type": rule.signal_type,
        "channel": rule.channel.value,
        "severity": rule.severity.value,
        "on_statuses": [s.value for s in rule.on_statuses],
        "min_strength": str(Decimal(rule.min_strength)),
        "cooldown_seconds": rule.cooldown.total_seconds(),
        "dedup_window_seconds": rule.dedup_window.total_seconds(),
        "enabled": rule.enabled,
        "underlying_id": rule.underlying_id,
        "destination": rule.destination,
        "config_digest": rule.config_digest,
    }


def occurrence_to_dict(occurrence: AlertOccurrence) -> dict[str, Any]:
    return {
        "occurrence_id": occurrence.occurrence_id,
        "rule_id": occurrence.rule_id,
        "rule_config_digest": occurrence.rule_config_digest,
        # A reference, never the signal itself: alerts do not carry signal truth.
        "signal_id": occurrence.signal_id,
        "signal_type": occurrence.signal_type,
        "underlying_id": occurrence.underlying_id,
        "status": occurrence.status.value,
        "severity": occurrence.severity.value,
        "channel": occurrence.channel.value,
        "observed_at": _ts(occurrence.observed_at),
        "available_at": _ts(occurrence.available_at),
        "triggered_at": _ts(occurrence.triggered_at),
        "dedup_key": occurrence.dedup_key,
        "delivered": occurrence.delivered,
        "attempts": [
            {
                "attempt": a.attempt,
                "attempted_at": _ts(a.attempted_at),
                "status": a.status.value,
                "channel": a.channel.value,
                "detail": a.detail,
            }
            for a in occurrence.attempts
        ],
        "acknowledged_at": _ts(occurrence.acknowledged_at),
        "acknowledged_by": occurrence.acknowledged_by,
    }
