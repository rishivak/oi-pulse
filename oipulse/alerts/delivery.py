"""Alert delivery — channels and retry.

Roadmap Phase 5: *SSE + one out-of-band channel*.

**Delivery never mutates signal truth.** A `Deliverer` receives an `AlertOccurrence`,
which holds a `signal_id` rather than a `Signal`, so the signal is not even reachable
from here. `tools/check_alert_purity.py` enforces that structurally.

Retry is bounded and every attempt is **recorded rather than overwritten**, so the
history answers "did we try, how often, and what failed?" A delivery that exhausts its
attempts leaves the occurrence `FAILED` — which is a fact about delivery, not about the
signal, and it is exactly why the two are separate records.

No claim of exactly-once delivery is made anywhere. `03-EVENT_MODEL.md` is explicit
that the guarantee is exactly-once *database application* per `(subscriber, event_id)`;
an external side effect cannot be made exactly-once, and pretending otherwise is how
duplicate notifications get blamed on the network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from oipulse.alerts.model import (
    AlertChannel,
    AlertOccurrence,
    DeliveryAttempt,
    DeliveryStatus,
)

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "Deliverer",
    "DeliveryOutcome",
    "DeliveryResult",
    "SseDeliverer",
    "WebhookDeliverer",
    "deliver_with_retry",
]

#: Bounded. An unbounded retry loop turns one broken destination into a stuck queue.
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What one channel attempt produced."""

    succeeded: bool
    detail: str = ""


class Deliverer(Protocol):
    """A channel. Receives an occurrence; cannot reach the signal behind it."""

    @property
    def channel(self) -> AlertChannel: ...

    def send(self, occurrence: AlertOccurrence) -> DeliveryOutcome: ...


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """The occurrence after delivery, plus what happened."""

    occurrence: AlertOccurrence
    attempts: int
    delivered: bool
    last_detail: str = ""


class SseDeliverer:
    """Server-sent events. Appends to an in-process buffer the API streams from.

    The buffer is injected rather than global: a second process must not silently
    share one, and a test must be able to inspect it.
    """

    __slots__ = ("_buffer", "_fail")

    def __init__(
        self,
        buffer: list[AlertOccurrence],
        *,
        fail: Callable[[AlertOccurrence], str | None] | None = None,
    ) -> None:
        self._buffer = buffer
        # Injected failure, so retry behaviour is testable without a real transport.
        self._fail = fail

    @property
    def channel(self) -> AlertChannel:
        return AlertChannel.SSE

    def send(self, occurrence: AlertOccurrence) -> DeliveryOutcome:
        reason = self._fail(occurrence) if self._fail is not None else None
        if reason:
            return DeliveryOutcome(False, reason)
        self._buffer.append(occurrence)
        return DeliveryOutcome(True, "queued for the SSE stream")


class WebhookDeliverer:
    """The out-of-band channel.

    The HTTP transport is **injected**. This module performs no network I/O itself,
    which keeps the retry and recording logic unit-testable with no server, and keeps
    the choice of client out of the alerting layer.
    """

    __slots__ = ("_endpoint", "_transport")

    def __init__(
        self,
        endpoint: str,
        transport: Callable[[str, AlertOccurrence], DeliveryOutcome],
    ) -> None:
        self._endpoint = endpoint
        self._transport = transport

    @property
    def channel(self) -> AlertChannel:
        return AlertChannel.WEBHOOK

    def send(self, occurrence: AlertOccurrence) -> DeliveryOutcome:
        return self._transport(self._endpoint, occurrence)


def deliver_with_retry(
    occurrence: AlertOccurrence,
    deliverer: Deliverer,
    *,
    attempted_at: datetime,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: timedelta = timedelta(seconds=5),
) -> DeliveryResult:
    """Attempt delivery up to `max_attempts`, recording every attempt.

    `attempted_at` is supplied and advanced by `backoff` per attempt rather than read
    from a clock, so the attempt history is reproducible under replay. No sleeping
    happens here: scheduling is the caller's concern, and a sleep inside a pure
    function would make the whole layer untestable at speed.
    """
    current = occurrence
    detail = ""
    for attempt in range(1, max_attempts + 1):
        outcome = deliverer.send(current)
        detail = outcome.detail
        current = current.with_attempt(
            DeliveryAttempt(
                attempted_at=attempted_at + backoff * (attempt - 1),
                status=(DeliveryStatus.SUCCEEDED if outcome.succeeded else DeliveryStatus.FAILED),
                channel=deliverer.channel,
                detail=outcome.detail,
            )
        )
        if outcome.succeeded:
            return DeliveryResult(current, attempt, True, detail)
    return DeliveryResult(current, max_attempts, False, detail)
