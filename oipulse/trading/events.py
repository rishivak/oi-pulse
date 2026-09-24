"""Paper-trading domain events — `03-EVENT_MODEL.md` §4, Phase 8 brief §13.

Every paper-trading state transition is a `DomainEvent` carrying
`aggregate_type` · `aggregate_id` · `aggregate_sequence`, exactly as the verified
Phase 1 event model defines. Nothing here reimplements ordering or idempotency:

* `check_sequence` (Phase 1) defers an event that arrives ahead of its turn.
* `InboxGuard.claim` / `InMemoryInbox.apply_once` (Phase 1) make the marker and the
  mutation commit together.

**What is guaranteed, stated precisely** (`03` §4): *exactly-once database application
per `(subscriber, event_id)` transaction*. This is **not** global exactly-once
processing, and nothing in this module claims it is. An event redelivered after a
crash is absorbed; a paper fill applied twice does not double a position. An external
side effect — an outbound notification, say — is not covered and would need its own
idempotency at the boundary.

Reordered delivery is handled by deferral, not by tolerance: an out-of-order event
raises `SequenceGap` and returns to pending, because applying a fill before the order
that authorised it would produce a position with no traceable cause.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar, runtime_checkable

from oipulse.core.ids import EventId
from oipulse.events.domain import AggregateType, DomainEvent, check_sequence

__all__ = [
    "PAPER_TRADING_SUBSCRIBER",
    "AggregateWatermarks",
    "PaperEventType",
    "TransactionalInbox",
    "fill_applied_event",
    "intent_created_event",
    "order_event",
]

R = TypeVar("R")

#: The inbox subscriber name for the paper-trading applier. One name, so a redelivery
#: to any process resolves against the same marker.
PAPER_TRADING_SUBSCRIBER = "paper_trading"


class PaperEventType:
    """Event type names. Constants rather than an enum because `DomainEvent.event_type`
    is a free-form string in the verified Phase 1 model and narrowing it here would
    diverge from every other producer."""

    INTENT_CREATED = "paper.intent.created"
    INTENT_REJECTED = "paper.intent.rejected"
    ORDER_CREATED = "paper.order.created"
    ORDER_ACCEPTED = "paper.order.accepted"
    ORDER_STATE_CHANGED = "paper.order.state_changed"
    ORDER_CANCELLED = "paper.order.cancelled"
    ORDER_EXPIRED = "paper.order.expired"
    ORDER_REJECTED = "paper.order.rejected"
    FILL_APPLIED = "paper.fill.applied"
    ACCOUNT_STATUS_CHANGED = "paper.account.status_changed"


def intent_created_event(
    intent_id: str, *, sequence: int, occurred_at: datetime, payload: dict[str, Any]
) -> DomainEvent:
    """Aggregate: the intent. Its id is content-addressed, so a reprocessed decision
    produces the same aggregate rather than a second one."""
    return DomainEvent(
        event_type=PaperEventType.INTENT_CREATED,
        aggregate_type=AggregateType.TRADE_INTENT,
        aggregate_id=intent_id,
        aggregate_sequence=sequence,
        occurred_at=occurred_at,
        payload=payload,
    )


def order_event(
    event_type: str,
    order_id: str,
    *,
    sequence: int,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> DomainEvent:
    """Aggregate: the order. `sequence` is the order's own event sequence, so the
    domain event and the order's event log cannot drift apart."""
    return DomainEvent(
        event_type=event_type,
        aggregate_type=AggregateType.ORDER,
        aggregate_id=order_id,
        aggregate_sequence=sequence,
        occurred_at=occurred_at,
        payload=payload,
    )


def fill_applied_event(
    order_id: str, *, sequence: int, occurred_at: datetime, payload: dict[str, Any]
) -> DomainEvent:
    return order_event(
        PaperEventType.FILL_APPLIED,
        order_id,
        sequence=sequence,
        occurred_at=occurred_at,
        payload=payload,
    )


@runtime_checkable
class TransactionalInbox(Protocol):
    """An inbox that runs a mutation at most once, transactionally.

    Narrower than reaching for `InMemoryInbox` directly and stronger than the
    verified `InboxGuard`, which exposes only `claim`. The difference matters:
    `claim` alone leaves the caller responsible for releasing the marker when the
    mutation fails, and a caller that forgets turns a retryable failure into a lost
    write. `apply_once` owns both halves. `oipulse.events.inbox.InMemoryInbox`
    satisfies this protocol as written.
    """

    def apply_once(
        self, subscriber: str, event_id: EventId, mutation: Callable[[], R]
    ) -> R | None: ...


@dataclass
class AggregateWatermarks:
    """Highest applied sequence per aggregate.

    Kept explicitly rather than derived from stored events, because the sequence
    check must be answerable *before* the mutation runs — that is what lets an
    out-of-order event be deferred instead of half-applied and rolled back.
    """

    _by_aggregate: dict[tuple[str, str], int]

    def __init__(self) -> None:
        self._by_aggregate = {}

    def last_applied(self, event: DomainEvent) -> int | None:
        return self._by_aggregate.get(event.aggregate_key)

    def record(self, event: DomainEvent) -> None:
        key = event.aggregate_key
        current = self._by_aggregate.get(key)
        # max(), not assignment: a redelivered earlier event must not move the
        # watermark backwards and re-open a gap that has already closed.
        self._by_aggregate[key] = (
            event.aggregate_sequence if current is None else max(current, event.aggregate_sequence)
        )

    def apply_ordered(
        self,
        event: DomainEvent,
        inbox: TransactionalInbox,
        mutation: Callable[[], R],
        *,
        subscriber: str = PAPER_TRADING_SUBSCRIBER,
    ) -> R | None:
        """Apply *mutation* once, in order. Returns None if it was a redelivery.

        Two distinct protections, in the order they must happen:

        1. `check_sequence` raises `SequenceGap` if the event is ahead of its turn.
           The event is deferred, not dropped, and nothing is mutated.
        2. `apply_once` claims `(subscriber, event_id)` **in the same transaction as
           the mutation**. A crash between them cannot leave the mutation applied
           with the marker absent, and a mutation that raises releases the claim so
           the event can be redelivered.

        `apply_once` is used rather than a bare `claim` for that second reason. A
        claim that survived a failed mutation would suppress the retry and lose the
        write -- the precise failure the transactional inbox exists to prevent
        (`oipulse/events/inbox.py`).

        The watermark is advanced **inside** the mutation, so it moves if and only
        if the mutation committed. Advancing it outside would leave a rolled-back
        event marked as applied and permanently defer its successor.

        A sequence *behind* the watermark is not an error -- that is an ordinary
        at-least-once redelivery, and the inbox absorbs it.
        """
        check_sequence(self.last_applied(event), event)

        def apply_and_record() -> R:
            result = mutation()
            self.record(event)
            return result

        return inbox.apply_once(subscriber, event.event_id, apply_and_record)
