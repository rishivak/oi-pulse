"""The paper order and its state machine — `11-TRADING.md` §4.

> **Invalid transitions raise.** The machine is a declared table of permitted
> transitions, not scattered `if` statements.

That table is `_PERMITTED` below, and it is the only place a transition is authorised.
Every transition appends an `OrderEvent`: the order aggregate is event-sourced because
its history is genuinely required for audit and the volume is tiny (`11` §4).

### Which states Phase 8 implements, and which it does not

`11` §4 defines the full machine including `SUBMITTED`, `UNKNOWN` and
`PENDING_RECONCILIATION`. Those three exist because a *network* sits between us and a
broker: an acknowledgement can be lost and we cannot tell accepted from rejected.

A paper order crosses no network. There is no ambiguity to represent, and inventing
one would be simulating a failure mode that cannot occur rather than modelling a real
one. Phase 8 therefore implements the subset the Phase 8 brief §7 enumerates:

```
CREATED → ACCEPTED → OPEN → PARTIALLY_FILLED → FILLED
                  ↘ CANCELLED | REJECTED | EXPIRED
```

`UNKNOWN` and `PENDING_RECONCILIATION` are Phase 10, where a broker and a network
exist. `11` §7 notes the paper adapter can be *instructed* to simulate `UNKNOWN` so
the reconciliation path is exercised; that instruction needs a reconciliation path to
exercise, and Phase 10 owns it. Recorded as a known limitation rather than half-built.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.backtest.intents import OrderType, Side

__all__ = [
    "ExecutionVenue",
    "InvalidTransition",
    "OrderEvent",
    "OrderState",
    "PaperOrder",
    "RejectReason",
    "is_terminal",
    "permitted_transitions",
]


class ExecutionVenue(StrEnum):
    """Which venue owns an order's outcome.

    Brief §18 requires paper and broker execution states to stay distinguishable.
    Carrying the venue on the order rather than inferring it from the adapter in
    use means a stored order still says which it was, long after the process that
    created it is gone.
    """

    PAPER = "PAPER"
    BROKER = "BROKER"


class OrderState(StrEnum):
    """`11-TRADING.md` §4 and §5.

    Phase 8 implemented the subset a *paper* order can reach and deliberately
    excluded the three states that exist only because a network sits between us
    and a broker. Phase 10 adds them, because that network now exists in the
    model even though live submission remains impossible.
    """

    CREATED = "CREATED"
    #: Handed to an adapter; the outcome is not yet known. Between `SUBMITTING`
    #: and an answer, an order is genuinely in flight.
    SUBMITTING = "SUBMITTING"
    #: The request reached the venue and we hold an acknowledgement.
    SUBMITTED = "SUBMITTED"
    #: The paper venue has taken the order. Shape, session and funds checked.
    ACCEPTED = "ACCEPTED"
    #: Resting, awaiting a fillable quote.
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    #: A cancel has been requested but the venue has not confirmed it. `11` §4:
    #: "A cancel request must not automatically mean the order is cancelled."
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    #: `11` §5, "the state most systems omit". The venue **may** have accepted the
    #: order and the answer was lost. Never assume rejected, never assume accepted,
    #: never resubmit from here.
    UNKNOWN = "UNKNOWN"
    #: `UNKNOWN` has been escalated; the reconciler owns resolving it.
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"


class RejectReason(StrEnum):
    """Why an order was refused. Always recorded -- never a bare REJECTED."""

    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    NO_PRICE_AVAILABLE = "NO_PRICE_AVAILABLE"
    STALE_MARKET_STATE = "STALE_MARKET_STATE"
    ACCOUNT_NOT_ACTIVE = "ACCOUNT_NOT_ACTIVE"
    EXCEEDS_ORDER_LIMIT = "EXCEEDS_ORDER_LIMIT"
    SLIPPAGE_EXCEEDED = "SLIPPAGE_EXCEEDED"
    INTENT_EXPIRED = "INTENT_EXPIRED"
    ALL_OR_NONE_UNFILLABLE = "ALL_OR_NONE_UNFILLABLE"
    RISK_REJECTED = "RISK_REJECTED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"


#: The declared transition table. Nothing else may move an order.
#:
#: `PARTIALLY_FILLED -> PARTIALLY_FILLED` is present and deliberate: a second partial
#: fill is a real transition that must append an event, and omitting it would force
#: callers to mutate quantities without recording why they changed.
#: The declared transition table (`11` §4). Nothing else may move an order.
#:
#: Three Phase 10 properties are encoded here and are worth reading directly:
#:
#: * `UNKNOWN` leads only to `PENDING_RECONCILIATION`. There is **no** edge from
#:   `UNKNOWN` to `CREATED`, `SUBMITTING` or any terminal state, so a resubmission
#:   or an assumed outcome is not merely discouraged — the machine has no path for
#:   it (`11` §5: "Never resubmit from UNKNOWN").
#: * `PENDING_RECONCILIATION` leads to every state the venue might turn out to be
#:   in, because reconciliation discovers the truth rather than choosing it.
#: * `CANCEL_PENDING` can still fill. A cancel races the market, and an order that
#:   filled before the venue processed the cancel is filled.
_PERMITTED: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset(
        {OrderState.SUBMITTING, OrderState.ACCEPTED, OrderState.REJECTED}
    ),
    OrderState.SUBMITTING: frozenset(
        {
            OrderState.SUBMITTED,
            OrderState.ACCEPTED,
            OrderState.REJECTED,
            # The ambiguous outcome: request sent, answer lost.
            OrderState.UNKNOWN,
        }
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.OPEN,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.ACCEPTED: frozenset(
        {
            OrderState.OPEN,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.OPEN: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.CANCEL_PENDING: frozenset(
        {
            # A cancel races the market; the order may fill before it lands.
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    # `11` §5: the only way out of UNKNOWN is to go and find out.
    OrderState.UNKNOWN: frozenset({OrderState.PENDING_RECONCILIATION}),
    OrderState.PENDING_RECONCILIATION: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.OPEN,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            # Reconciliation can legitimately fail to resolve; the order stays
            # unresolved rather than being guessed into a terminal state.
            OrderState.UNKNOWN,
        }
    ),
    # Terminal. `11` §4: FILLED, REJECTED, CANCELLED, EXPIRED.
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}

_TERMINAL = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}
)


def permitted_transitions(state: OrderState) -> frozenset[OrderState]:
    return _PERMITTED[state]


def is_terminal(state: OrderState) -> bool:
    return state in _TERMINAL


class InvalidTransition(Exception):
    """An attempt to move an order somewhere the machine does not allow.

    Raised rather than corrected. A state machine that quietly repairs an illegal
    move is not a state machine; it is a suggestion, and the audit trail it produces
    describes something that never happened.
    """

    def __init__(self, order_id: str, source: OrderState, target: OrderState) -> None:
        allowed = sorted(s.value for s in permitted_transitions(source))
        super().__init__(
            f"order {order_id}: {source.value} -> {target.value} is not permitted"
            + (f"; permitted: {allowed}" if allowed else f"; {source.value} is terminal")
        )
        self.order_id = order_id
        self.source = source
        self.target = target


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """One appended transition. The order's history *is* this sequence.

    `sequence` is monotonic per order and is the aggregate sequence the Phase 3 event
    model expects (`03` §4), so an out-of-order delivery is detectable rather than
    absorbed.
    """

    order_id: str
    sequence: int
    from_state: OrderState | None
    to_state: OrderState
    #: Market time the transition is attributed to.
    occurred_at: datetime
    trigger: str
    payload: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("order event sequence must be >= 1")

    @property
    def event_key(self) -> str:
        """Idempotency key. A redelivered transition resolves to the same key."""
        return f"{self.order_id}:{self.sequence}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "sequence": self.sequence,
            "from_state": None if self.from_state is None else self.from_state.value,
            "to_state": self.to_state.value,
            "occurred_at": self.occurred_at.isoformat(),
            "trigger": self.trigger,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class PaperOrder:
    """One leg of an intent, as a paper order. Immutable; transitions return a copy.

    An order is always tied to the intent that authorised it, and to that intent's
    signal, strategy, checkpoint and knowledge horizon. `11` §10 requires the whole
    chain to be answerable by traversal, and that only works if each link stores the
    reference rather than expecting a reader to reconstruct it.
    """

    order_id: str
    account_id: str
    intent_id: str
    instrument_id: int
    side: Side
    quantity: int
    order_type: OrderType
    #: Market time the order was created at.
    created_at: datetime
    limit_price: Decimal | None = None
    state: OrderState = OrderState.CREATED
    filled_quantity: int = 0
    #: Quantity-weighted average of the fills applied so far.
    average_fill_price: Decimal | None = None
    reject_reason: RejectReason | None = None
    reject_detail: str = ""
    events: tuple[OrderEvent, ...] = ()
    #: Audit references carried from the intent (`11` §10).
    signal_id: str = ""
    signal_version: int = 0
    strategy_id: str = ""
    strategy_version: int = 0
    state_checkpoint_ref: str = ""
    build_context_id: str = ""
    knowledge_time: datetime | None = None
    decision_time: datetime | None = None
    config_digest: str = ""
    #: The **exact** risk decision that authorized this order (`11-TRADING.md` §3,
    #: `02-DATA_MODEL.md` §11). Not "a decision for this intent" -- the specific one,
    #: identified by its sequence, because risk is re-evaluated and an order must
    #: record which evaluation let it through rather than the latest one.
    authorizing_risk_decision_id: str = ""
    authorizing_decision_sequence: int | None = None
    #: Which venue owns the outcome (brief §18). Paper by default: an order only
    #: becomes a broker order by being routed to a broker adapter, and no such
    #: adapter can submit in this deployment.
    venue: ExecutionVenue = ExecutionVenue.PAPER
    #: **Our** identity for one submission attempt. Deterministic, so a retry
    #: after a restart recomputes the same value. `06` §10 and `11` §5 are explicit
    #: that this gives us local dedup and audit -- it does **not** oblige the
    #: provider to reject a duplicate.
    client_order_attempt_id: str = ""
    #: The venue's own identity, when it has told us one. `None` is a real and
    #: common state: after a lost acknowledgement we may have an order at the
    #: broker whose id we do not know, which is exactly why UNKNOWN exists.
    provider_order_id: str | None = None
    #: The venue's own status string, unmapped. Kept beside the canonical state so
    #: a mapping disagreement is inspectable rather than lost in translation.
    provider_status: str | None = None
    #: When the provider says the event happened, and when we received it. `16` of
    #: the brief: provider receipt time is not market time and the two are not
    #: collapsed.
    provider_event_time: datetime | None = None
    received_at: datetime | None = None
    extras: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.filled_quantity < 0 or self.filled_quantity > self.quantity:
            raise ValueError(f"filled_quantity {self.filled_quantity} outside [0, {self.quantity}]")
        # An order naming an authorizing decision must name both halves of its
        # composite key. `02` §11 keys the authorization on (intent_id, sequence_no);
        # half of that key identifies no decision.
        if bool(self.authorizing_risk_decision_id) != (
            self.authorizing_decision_sequence is not None
        ):
            raise ValueError(
                "authorizing_risk_decision_id and authorizing_decision_sequence must "
                "be set together: the authorization is keyed on (intent_id, "
                "sequence_no) and half of that key identifies no decision"
            )

    # ------------------------------------------------------------------ identity

    @staticmethod
    def derive_id(intent_id: str, leg_index: int, account_id: str) -> str:
        """Deterministic order identity.

        Derived from the intent, the leg and the account -- never a UUID or a
        counter. Reprocessing the same intent after a restart produces the same
        order id, so the database's uniqueness constraint turns a retry into a no-op
        instead of a second order.
        """
        digest = hashlib.sha256(
            json.dumps(
                {"intent_id": intent_id, "leg": leg_index, "account_id": account_id},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return "ord_" + digest[:32]

    # ---------------------------------------------------------------- properties

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity

    @property
    def is_authorized(self) -> bool:
        """Whether this order carries the risk authorization it needs.

        A `REJECTED` order is exempt: it never reached execution, and requiring
        an approval for an order that was refused would be backwards. Everything
        else must name the exact decision that let it through.
        """
        if self.state is OrderState.REJECTED:
            return True
        return bool(self.authorizing_risk_decision_id) and (
            self.authorizing_decision_sequence is not None
        )

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.state)

    @property
    def is_open(self) -> bool:
        """Working at a venue: not terminal, and not in an unresolved limbo.

        `UNKNOWN` and `PENDING_RECONCILIATION` are deliberately excluded. An order
        we cannot describe is not an open order, and treating it as one would let
        a caller act on a position that may not exist.
        """
        return self.state in (
            OrderState.SUBMITTING,
            OrderState.SUBMITTED,
            OrderState.ACCEPTED,
            OrderState.OPEN,
            OrderState.PARTIALLY_FILLED,
            OrderState.CANCEL_PENDING,
        )

    @property
    def is_unresolved(self) -> bool:
        """`11` §5: the venue may or may not hold this order.

        An unresolved order **blocks further intents for its instrument from the
        same strategy** until reconciliation settles it, so ambiguity cannot
        compound.
        """
        return self.state in (OrderState.UNKNOWN, OrderState.PENDING_RECONCILIATION)

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1

    # --------------------------------------------------------------- transitions

    def transition(
        self,
        target: OrderState,
        *,
        at: datetime,
        trigger: str,
        payload: tuple[tuple[str, str], ...] = (),
    ) -> PaperOrder:
        """Move to *target*, appending the event. Raises on an illegal move."""
        if target not in permitted_transitions(self.state):
            raise InvalidTransition(self.order_id, self.state, target)
        event = OrderEvent(
            order_id=self.order_id,
            sequence=self.next_sequence,
            from_state=self.state,
            to_state=target,
            occurred_at=at,
            trigger=trigger,
            payload=payload,
        )
        return replace(self, state=target, events=(*self.events, event))

    def apply_fill(
        self, *, quantity: int, price: Decimal, at: datetime, trigger: str = "paper_fill"
    ) -> PaperOrder:
        """Fold a fill in, moving to PARTIALLY_FILLED or FILLED as the maths dictates.

        The destination is computed from the quantities rather than passed in, so a
        caller cannot mark an order FILLED while leaving quantity outstanding. The
        transition itself still goes through the table, so an attempt to fill a
        cancelled order raises exactly as it should.
        """
        if quantity <= 0:
            raise ValueError("fill quantity must be positive")
        if quantity > self.remaining_quantity:
            raise ValueError(
                f"fill of {quantity} exceeds the {self.remaining_quantity} remaining on "
                f"order {self.order_id}; a paper venue must not overfill"
            )

        filled = self.filled_quantity + quantity
        prior_value = (self.average_fill_price or Decimal(0)) * Decimal(self.filled_quantity)
        average = (prior_value + price * Decimal(quantity)) / Decimal(filled)
        target = OrderState.FILLED if filled == self.quantity else OrderState.PARTIALLY_FILLED

        moved = self.transition(
            target,
            at=at,
            trigger=trigger,
            payload=(("quantity", str(quantity)), ("price", str(price))),
        )
        return replace(moved, filled_quantity=filled, average_fill_price=average)

    def reject(self, reason: RejectReason, *, at: datetime, detail: str = "") -> PaperOrder:
        moved = self.transition(
            OrderState.REJECTED,
            at=at,
            trigger="reject",
            payload=(("reason", reason.value), ("detail", detail[:200])),
        )
        return replace(moved, reject_reason=reason, reject_detail=detail)

    def cancel(self, *, at: datetime, detail: str = "") -> PaperOrder:
        return self.transition(
            OrderState.CANCELLED,
            at=at,
            trigger="cancel",
            payload=(("detail", detail[:200]),),
        )

    def expire(self, *, at: datetime, detail: str = "") -> PaperOrder:
        return self.transition(
            OrderState.EXPIRED,
            at=at,
            trigger="expire",
            payload=(("detail", detail[:200]),),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account_id": self.account_id,
            "intent_id": self.intent_id,
            # Always present: a consumer must never have to infer the venue. Phase 8
            # hard-coded "PAPER" because there was only one; Phase 10 reports the
            # order's actual venue, which is still PAPER for everything this
            # deployment can execute.
            "mode": self.venue.value,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "order_type": self.order_type.value,
            "limit_price": None if self.limit_price is None else str(self.limit_price),
            "average_fill_price": (
                None if self.average_fill_price is None else str(self.average_fill_price)
            ),
            "state": self.state.value,
            "is_terminal": self.is_terminal,
            "reject_reason": None if self.reject_reason is None else self.reject_reason.value,
            "reject_detail": self.reject_detail,
            "created_at": self.created_at.isoformat(),
            "knowledge_time": (
                None if self.knowledge_time is None else self.knowledge_time.isoformat()
            ),
            "decision_time": (
                None if self.decision_time is None else self.decision_time.isoformat()
            ),
            "signal_id": self.signal_id,
            "signal_version": self.signal_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "state_checkpoint_ref": self.state_checkpoint_ref,
            "build_context_id": self.build_context_id,
            "config_digest": self.config_digest,
            "authorizing_risk_decision_id": self.authorizing_risk_decision_id,
            "authorizing_decision_sequence": self.authorizing_decision_sequence,
            "venue": self.venue.value,
            "client_order_attempt_id": self.client_order_attempt_id,
            "provider_order_id": self.provider_order_id,
            "provider_status": self.provider_status,
            "provider_event_time": (
                None if self.provider_event_time is None else self.provider_event_time.isoformat()
            ),
            "received_at": None if self.received_at is None else self.received_at.isoformat(),
            "is_unresolved": self.is_unresolved,
            "events": [e.as_dict() for e in self.events],
        }
