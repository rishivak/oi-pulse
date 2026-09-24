"""Alert rules and occurrences.

Schema per `18-ROADMAP.md` Phase 5: `alert_rules`, `alert_occurrences`.

An **occurrence** is one notification-worthy event derived from a signal. Its identity
is deterministic — derived from the rule, the signal and the dedup window — so
reprocessing the same source event cannot create a second occurrence. That is the
idempotency guarantee the transactional-inbox model gives us
(`03-EVENT_MODEL.md`): exactly-once *database application* per `(subscriber, event_id)`,
never exactly-once delivery, and nothing here claims otherwise.

Delivery attempts are recorded **against** the occurrence rather than mutating it, so a
retry history is queryable and a failed delivery never rewrites what was alerted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.signals.model import Signal, SignalStatus

__all__ = [
    "AlertChannel",
    "AlertOccurrence",
    "AlertRule",
    "AlertSeverity",
    "AlertStatus",
    "DeliveryAttempt",
    "DeliveryStatus",
    "alert_digest",
]


class AlertChannel(StrEnum):
    """Phase 5 ships SSE plus one out-of-band channel (roadmap).

    `WEBHOOK` is the out-of-band one. No third-party integration is implemented: the
    specification does not require one, and adding it would be scope creep into
    notification vendors.
    """

    SSE = "sse"
    WEBHOOK = "webhook"


class AlertSeverity(StrEnum):
    INFO = "info"
    NOTICE = "notice"
    IMPORTANT = "important"


class AlertStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED = "suppressed"


class DeliveryStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def alert_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class AlertRule:
    """When a signal should produce an alert, and how often it may.

    `cooldown` and `dedup_window` exist from the start rather than being retrofitted:
    the roadmap names alert fatigue as the risk, and a notification system without
    them trains its users to ignore it within a week.
    """

    id: str
    signal_type: str
    channel: AlertChannel
    severity: AlertSeverity = AlertSeverity.NOTICE
    #: Only these statuses alert. FORMING is excluded by default: partially met
    #: entry conditions are not worth waking anyone for.
    on_statuses: tuple[SignalStatus, ...] = (SignalStatus.ACTIVE, SignalStatus.CONFIRMED)
    min_strength: Decimal = Decimal("0.5")
    #: No second alert for the same logical event inside this window.
    cooldown: timedelta = timedelta(minutes=15)
    #: Occurrences sharing a dedup key inside this window are the same alert.
    dedup_window: timedelta = timedelta(minutes=15)
    enabled: bool = True
    underlying_id: int | None = None
    destination: str = ""

    @property
    def config_digest(self) -> str:
        """Content address of the rule's behaviour-affecting settings.

        A threshold edit produces a new digest, so occurrences from before and after
        the change are distinguishable rather than silently pooled.
        """
        return (
            "alr_"
            + alert_digest(
                {
                    "signal_type": self.signal_type,
                    "channel": self.channel.value,
                    "severity": self.severity.value,
                    "on_statuses": sorted(s.value for s in self.on_statuses),
                    "min_strength": str(self.min_strength),
                    "cooldown_seconds": self.cooldown.total_seconds(),
                    "dedup_window_seconds": self.dedup_window.total_seconds(),
                    "underlying_id": self.underlying_id,
                }
            )[:24]
        )

    def matches(self, signal: Signal) -> bool:
        """Does this signal qualify? Read-only: never touches the signal."""
        if not self.enabled:
            return False
        if signal.signal_type != self.signal_type:
            return False
        if self.underlying_id is not None and signal.identity.underlying_id != self.underlying_id:
            return False
        if signal.status not in self.on_statuses:
            return False
        return signal.strength >= self.min_strength


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One attempt. Recorded, never overwritten -- retry history is queryable."""

    attempted_at: datetime
    status: DeliveryStatus
    channel: AlertChannel
    detail: str = ""
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class AlertOccurrence:
    """One notification-worthy event. Immutable; recording an attempt returns a copy."""

    rule_id: str
    rule_config_digest: str
    signal_id: str
    signal_type: str
    underlying_id: int
    status: AlertStatus
    severity: AlertSeverity
    channel: AlertChannel
    #: The signal's market time, so an occurrence is placed in market time, not
    #: wall-clock time -- a replayed alert lands where it belongs.
    observed_at: datetime
    #: When the alert became eligible: the signal's own availability. An alert can
    #: never precede the information it reports.
    available_at: datetime
    triggered_at: datetime
    dedup_key: str
    attempts: tuple[DeliveryAttempt, ...] = field(default_factory=tuple)
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None

    @property
    def occurrence_id(self) -> str:
        """Deterministic. The same logical alert always has the same id."""
        return (
            "alo_"
            + alert_digest(
                {
                    "rule_id": self.rule_id,
                    "rule_config_digest": self.rule_config_digest,
                    "dedup_key": self.dedup_key,
                }
            )[:32]
        )

    @property
    def delivered(self) -> bool:
        return any(a.status is DeliveryStatus.SUCCEEDED for a in self.attempts)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def with_attempt(self, attempt: DeliveryAttempt) -> AlertOccurrence:
        """Record a delivery attempt. Returns a new occurrence; mutates nothing.

        The occurrence's own `status` follows delivery, but the **signal** it refers
        to is untouched: this layer holds a `signal_id`, not a `Signal`.
        """
        attempts = (*self.attempts, replace(attempt, attempt=len(self.attempts) + 1))
        status = (
            AlertStatus.DELIVERED
            if attempt.status is DeliveryStatus.SUCCEEDED
            else AlertStatus.FAILED
        )
        if self.status is AlertStatus.ACKNOWLEDGED:
            status = AlertStatus.ACKNOWLEDGED
        return replace(self, attempts=attempts, status=status)

    def acknowledge(self, at: datetime, by: str) -> AlertOccurrence:
        return replace(
            self, status=AlertStatus.ACKNOWLEDGED, acknowledged_at=at, acknowledged_by=by
        )
