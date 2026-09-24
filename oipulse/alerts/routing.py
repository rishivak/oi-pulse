"""Alert routing — dedup, cooldown, and deterministic idempotency keys.

Roadmap Phase 5 names alert fatigue as the risk and dedup plus cooldown as the
mitigation, from the start rather than retrofitted.

**Dedup and cooldown answer different questions**, and collapsing them would lose one:

* *Dedup*: "is this the same logical alert we already raised?" Keyed on rule, signal
  stream and status, bucketed by the dedup window. Two evaluations of one developing
  signal inside the window are one alert.
* *Cooldown*: "have we alerted about this stream too recently?" Even a genuinely
  different occurrence is suppressed if the last one was within the cooldown, which is
  what stops a flapping signal producing a notification every evaluation.

Suppression is **recorded**, not silent: a suppressed occurrence is returned with
`AlertStatus.SUPPRESSED` so an operator asking "why did I not get an alert?" has an
answer.

Pure: no clock, no network, no store. `now` is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from oipulse.alerts.model import (
    AlertOccurrence,
    AlertRule,
    AlertStatus,
    alert_digest,
)
from oipulse.signals.model import Signal

__all__ = ["AlertRouter", "RoutingDecision", "dedup_key_for"]


def dedup_key_for(rule: AlertRule, signal: Signal, window_start: datetime) -> str:
    """Deterministic idempotency key for one logical alert.

    Built from the rule, the signal's **lifecycle stream** and its status, bucketed by
    the dedup window. Market time is excluded deliberately: successive evaluations of
    one developing signal must map to one key, or every re-evaluation would notify.

    Status is included because a signal moving ACTIVE -> CONFIRMED is a genuinely new
    thing to say, even about the same stream.
    """
    stream = signal.identity.stream_key
    return (
        "dk_"
        + alert_digest(
            {
                "rule_id": rule.id,
                "rule_config_digest": rule.config_digest,
                "stream": [str(part) for part in stream],
                "status": signal.status.value,
                "window_start": window_start.isoformat(),
            }
        )[:32]
    )


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """What routing decided, and why. A refusal always carries its reason."""

    occurrence: AlertOccurrence | None
    suppressed: bool = False
    reason: str = ""


def _window_start(at: datetime, window: timedelta, epoch: datetime) -> datetime:
    """Floor `at` into a fixed bucket, so the key is stable across processes.

    Bucketing against a fixed epoch rather than "the last alert" means two processes
    evaluating the same signal independently compute the same key without
    coordination -- which is what makes dedup work under replay and under restart.
    """
    if window.total_seconds() <= 0:
        return at
    elapsed = (at - epoch).total_seconds()
    buckets = int(elapsed // window.total_seconds())
    return epoch + timedelta(seconds=buckets * window.total_seconds())


class AlertRouter:
    """Decides whether a signal produces an alert occurrence. Pure and deterministic.

    Holds the occurrences it has already emitted so dedup and cooldown can be applied;
    that state is an explicit in-memory index the caller owns and can rebuild from the
    store, not hidden global state.
    """

    __slots__ = ("_by_key", "_epoch", "_last_by_stream")

    def __init__(self, *, epoch: datetime | None = None) -> None:
        self._by_key: dict[str, AlertOccurrence] = {}
        self._last_by_stream: dict[tuple[str, str], datetime] = {}
        # A fixed reference point for window bucketing. Supplied so replay can pin it.
        self._epoch = epoch or datetime(2026, 1, 1, tzinfo=None).replace(tzinfo=None)

    @property
    def emitted(self) -> tuple[AlertOccurrence, ...]:
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    def restore(self, occurrences: tuple[AlertOccurrence, ...]) -> None:
        """Rebuild the index after a process restart.

        Without this, a restart would re-alert everything inside the current window.
        The index is derived state; the store is the truth.
        """
        for occurrence in occurrences:
            self._by_key[occurrence.dedup_key] = occurrence
            stream = (occurrence.rule_id, occurrence.signal_id)
            previous = self._last_by_stream.get(stream)
            if previous is None or occurrence.triggered_at > previous:
                self._last_by_stream[stream] = occurrence.triggered_at

    def route(self, rule: AlertRule, signal: Signal, now: datetime) -> RoutingDecision:
        """Evaluate one signal against one rule.

        Never touches the signal. The signal is read for its status, strength and
        availability, and nothing is written back.
        """
        if not rule.matches(signal):
            return RoutingDecision(None, suppressed=False, reason="rule does not match signal")

        epoch = self._epoch.replace(tzinfo=signal.available_at.tzinfo)
        window_start = _window_start(now, rule.dedup_window, epoch)
        key = dedup_key_for(rule, signal, window_start)

        existing = self._by_key.get(key)
        if existing is not None:
            return RoutingDecision(
                existing,
                suppressed=True,
                reason=f"duplicate of {existing.occurrence_id} within the dedup window",
            )

        stream = (rule.id, signal.signal_id)
        last = self._last_by_stream.get(stream)
        if last is not None and now - last < rule.cooldown:
            suppressed = AlertOccurrence(
                rule_id=rule.id,
                rule_config_digest=rule.config_digest,
                signal_id=signal.signal_id,
                signal_type=signal.signal_type,
                underlying_id=signal.identity.underlying_id,
                status=AlertStatus.SUPPRESSED,
                severity=rule.severity,
                channel=rule.channel,
                observed_at=signal.identity.market_time,
                available_at=signal.available_at,
                triggered_at=now,
                dedup_key=key,
            )
            return RoutingDecision(
                suppressed,
                suppressed=True,
                reason=f"cooldown: last alert {now - last} ago, cooldown {rule.cooldown}",
            )

        occurrence = AlertOccurrence(
            rule_id=rule.id,
            rule_config_digest=rule.config_digest,
            signal_id=signal.signal_id,
            signal_type=signal.signal_type,
            underlying_id=signal.identity.underlying_id,
            status=AlertStatus.PENDING,
            severity=rule.severity,
            channel=rule.channel,
            observed_at=signal.identity.market_time,
            # An alert can never precede the information it reports.
            available_at=signal.available_at,
            triggered_at=now,
            dedup_key=key,
        )
        self._by_key[key] = occurrence
        self._last_by_stream[stream] = now
        return RoutingDecision(occurrence)
