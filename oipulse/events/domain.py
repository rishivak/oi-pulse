"""Domain events and the aggregate-ordering contract.

`docs/design/03-EVENT_MODEL.md` §4: per-aggregate ordering is **not** a property the
outbox provides for free. `FOR UPDATE SKIP LOCKED` with several dispatchers lets worker B
claim event #2 while worker A still holds #1, and deliver it first.

Ordering is therefore explicit: every event carries
`(aggregate_type, aggregate_id, aggregate_sequence)`, dispatch is serialized per
aggregate, and a consumer defers sequence *n* while *n-1* is unprocessed. **The
correctness invariant lives in the sequence check, not in a Redis lock** — the lock is an
efficiency measure, and losing it degrades throughput rather than correctness.

Phase 1 defines the envelope and the ordering rule. The dispatcher and the storage tables
arrive with the outbox in this phase's schema; concrete event types arrive with the
domains that emit them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from oipulse.core.clock import ensure_utc
from oipulse.core.ids import CausationId, CorrelationId, EventId, new_event_id

__all__ = [
    "AggregateType",
    "DomainEvent",
    "OutboxStatus",
    "SequenceGap",
    "check_sequence",
]


class AggregateType(StrEnum):
    """Aggregates whose events must be applied in order.

    Only aggregates that genuinely require ordering are listed. An event with no ordering
    requirement belongs to a `SYSTEM` aggregate, whose sequence is still allocated so the
    dispatcher has one code path.
    """

    SYSTEM = "system"
    MARKET_STATE = "market_state"
    SIGNAL = "signal"
    ALERT = "alert"
    TRADE_INTENT = "trade_intent"
    ORDER = "order"
    POSITION = "position"
    PORTFOLIO = "portfolio"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """An internal state transition we concluded — never an external fact.

    Market events (facts received from a venue) live in the observation store and are a
    different concept entirely (`03` §1). Replay re-derives domain events from market
    events; it never replays stored domain events as input.
    """

    event_type: str
    aggregate_type: AggregateType
    aggregate_id: str
    aggregate_sequence: int
    occurred_at: datetime
    payload: Mapping[str, Any]

    event_id: EventId = field(default_factory=new_event_id)
    schema_version: int = 1
    correlation_id: CorrelationId | None = None
    causation_id: CausationId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))
        if self.aggregate_sequence < 1:
            raise ValueError(f"aggregate_sequence must be >= 1, got {self.aggregate_sequence}")
        if not self.aggregate_id:
            raise ValueError("aggregate_id must be non-empty")

    @property
    def aggregate_key(self) -> tuple[str, str]:
        return (self.aggregate_type.value, self.aggregate_id)


class SequenceGap(Exception):
    """Raised when an event arrives before its predecessor has been applied.

    The event is **deferred, not dropped** (`03` §4) — it returns to pending and is
    retried once the gap closes.
    """

    def __init__(self, aggregate_key: tuple[str, str], expected: int, got: int) -> None:
        super().__init__(
            f"{aggregate_key[0]}:{aggregate_key[1]} expected sequence {expected}, "
            f"got {got}; deferring"
        )
        self.aggregate_key = aggregate_key
        self.expected = expected
        self.got = got


def check_sequence(last_applied: int | None, event: DomainEvent) -> None:
    """Assert *event* is the next in its aggregate's sequence.

    `last_applied` is the highest sequence already applied for this aggregate, or None if
    none has been. Raises `SequenceGap` when the event is ahead of its turn.

    An event *behind* the watermark is not an error — it is a redelivery, and the
    transactional inbox absorbs it idempotently (`03` §4). Treating it as a failure would
    turn ordinary at-least-once delivery into an alert.
    """
    expected = 1 if last_applied is None else last_applied + 1
    if event.aggregate_sequence > expected:
        raise SequenceGap(event.aggregate_key, expected, event.aggregate_sequence)
