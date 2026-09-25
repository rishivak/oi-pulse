"""`OrderManager` — the OMS. Owns the canonical order lifecycle.

`11-TRADING.md` §4 and §5, `18-ROADMAP.md` Phase 10.

```
approved RiskDecision ──► OMS order ──► adapter ──► ack | reject | SILENCE
                                                            │
                                                            ▼
                                            UNKNOWN ──► PENDING_RECONCILIATION
```

### The identities this manager keeps apart (brief §5)

| Identity | Whose | Meaning |
|---|---|---|
| `intent_id` | ours, content-addressed | what a strategy asked for |
| `risk_decision_id` | ours, `(intent, sequence)` | which approval authorized it |
| `order_id` | ours, `(intent, leg, account)` | the OMS order |
| `client_order_attempt_id` | ours, `(order, attempt)` | one submission try |
| `provider_order_id` | the venue's, or `None` | the venue's order |

Collapsing any two would lose a question somebody needs to ask. The most important
pairing is the last two: an order can exist at the venue with **no** provider id
known to us, which is precisely the `UNKNOWN` case.

### Silence is not rejection

`11` §5's hard rules, implemented literally:

* **Never assume rejected.** A timeout moves the order to `UNKNOWN`, never to a
  terminal state.
* **Never assume accepted.** `UNKNOWN` is not `SUBMITTED`; no fill may be attributed
  to it.
* **Never resubmit from `UNKNOWN`.** The state machine has no edge from `UNKNOWN`
  to any submittable state — the only exit is `PENDING_RECONCILIATION`. This is not
  a policy the manager enforces; it is a path the machine does not contain.
* **An order in `UNKNOWN` blocks** further intents for that instrument from the same
  strategy until resolved, so ambiguity cannot compound.

### Attempt identity is deterministic, and promises nothing about the venue

`client_order_attempt_id` is derived from `(order_id, attempt_number)`. A retry after
a restart recomputes the same value, which gives us local dedup and an audit trail.
`06-UPSTOX_INTEGRATION.md` §10 is explicit that it does **not** oblige a provider to
reject a duplicate, and nothing here claims it does. Safety against duplicates comes
from refusing to resubmit without reconciliation, not from the key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.trading.brokers.capability import ExecutionCapability, LiveExecutionDisabled
from oipulse.trading.brokers.protocol import (
    BrokerAck,
    BrokerAdapter,
    BrokerOrderRequest,
    BrokerSubmissionTimeout,
)
from oipulse.trading.intents import TradeIntent
from oipulse.trading.oms.authorization import (
    SubmissionAuthorization,
    authorize_submission,
)
from oipulse.trading.orders import (
    ExecutionVenue,
    OrderState,
    PaperOrder,
    RejectReason,
)
from oipulse.trading.risk import RiskDecisionRecord

__all__ = ["OrderManager", "SubmissionOutcome", "SubmissionResultKind", "attempt_id_for"]


class SubmissionResultKind(StrEnum):
    """What came back. Three outcomes, and the third is the interesting one."""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    #: No answer. The venue may or may not hold the order.
    AMBIGUOUS = "AMBIGUOUS"
    #: The gate refused before anything was sent.
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    #: The adapter is not permitted to submit at all.
    CAPABILITY_DENIED = "CAPABILITY_DENIED"


def attempt_id_for(order_id: str, attempt: int) -> str:
    """Deterministic identity for one submission attempt.

    Content-addressed over `(order_id, attempt)`, so a retry after a process
    restart recomputes the same value rather than minting a new one. That is what
    makes local dedup possible; it is **not** a promise about the provider.
    """
    digest = hashlib.sha256(f"{order_id}:{attempt}".encode()).hexdigest()[:24]
    return f"coa_{digest}"


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """The result of one submission attempt, including the refusals."""

    kind: SubmissionResultKind
    order: PaperOrder
    authorization: SubmissionAuthorization
    ack: BrokerAck | None = None
    detail: str = ""

    @property
    def submitted(self) -> bool:
        return self.kind is SubmissionResultKind.ACKNOWLEDGED

    @property
    def is_ambiguous(self) -> bool:
        return self.kind is SubmissionResultKind.AMBIGUOUS

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "order": self.order.as_dict(),
            "authorization": self.authorization.as_dict(),
            "ack": None if self.ack is None else self.ack.as_dict(),
            "detail": self.detail,
        }


@dataclass
class OrderManager:
    """The OMS. Holds orders, attempts and the unresolved set.

    Stateful by nature — an order book is — but every transition is driven by
    supplied market time and supplied provider evidence, so replaying the same
    inputs reproduces the same book. Nothing here reads a clock.
    """

    adapter: BrokerAdapter
    venue: ExecutionVenue = ExecutionVenue.PAPER
    _orders: dict[str, PaperOrder] = field(default_factory=dict, repr=False)
    #: order_id -> how many attempts have been made. Not a retry budget: the
    #: counter exists so a *reconciliation-authorised* retry gets a distinct
    #: attempt id, and so the audit shows how many times we tried.
    _attempts: dict[str, int] = field(default_factory=dict, repr=False)
    #: Attempt ids already sent. The local half of submission idempotency.
    _sent: set[str] = field(default_factory=set, repr=False)

    # ------------------------------------------------------------------ reading

    def order(self, order_id: str) -> PaperOrder | None:
        return self._orders.get(order_id)

    def orders(self) -> tuple[PaperOrder, ...]:
        """Sorted by id, so iteration never depends on insertion order."""
        return tuple(self._orders[key] for key in sorted(self._orders))

    def unresolved(self) -> tuple[PaperOrder, ...]:
        """Orders whose venue state we cannot describe. The reconciler's queue."""
        return tuple(o for o in self.orders() if o.is_unresolved)

    def attempts_for(self, order_id: str) -> int:
        return self._attempts.get(order_id, 0)

    def blocks_new_intents(self, *, instrument_id: int, strategy_id: str) -> bool:
        """`11` §5: an unresolved order blocks its instrument for its strategy.

        Scoped rather than global on purpose. Halting the whole account on one
        ambiguous order would be safer still, but it is not what the design says,
        and over-blocking has its own cost: a strategy that cannot exit a position
        because an unrelated order is unresolved is not obviously safer.
        """
        return any(
            o.instrument_id == instrument_id and o.strategy_id == strategy_id
            for o in self.unresolved()
        )

    def track(self, order: PaperOrder) -> PaperOrder:
        """Bring an existing order under OMS ownership."""
        self._orders[order.order_id] = order
        return order

    # --------------------------------------------------------------- submission

    async def submit(
        self,
        order: PaperOrder,
        *,
        intent: TradeIntent,
        decision: RiskDecisionRecord | None,
        at: datetime,
    ) -> SubmissionOutcome:
        """Authorize, then attempt one submission. Never resubmits on its own.

        The whole method is written so that the dangerous transitions are the ones
        that require the most evidence:

        * refusing needs nothing;
        * an acknowledgement needs the venue to have answered;
        * a terminal state needs the venue to have said so;
        * `UNKNOWN` is what silence produces, and silence is the default.
        """
        authorization = authorize_submission(
            intent=intent,
            decision=decision,
            order_id=order.order_id,
            order_intent_id=order.intent_id,
            order_quantity=order.quantity,
            at=at,
        )
        if not authorization.authorized:
            refused = order.reject(
                RejectReason.RISK_REJECTED,
                at=at,
                detail=f"{authorization.refusal}: {authorization.detail}"
                if authorization.refusal
                else authorization.detail,
            )
            self._orders[refused.order_id] = refused
            return SubmissionOutcome(
                kind=SubmissionResultKind.NOT_AUTHORIZED,
                order=refused,
                authorization=authorization,
                detail=authorization.detail,
            )

        # An unresolved order for this instrument and strategy blocks the next one.
        if self.blocks_new_intents(
            instrument_id=order.instrument_id, strategy_id=order.strategy_id
        ):
            refused = order.reject(
                RejectReason.RISK_REJECTED,
                at=at,
                detail=(
                    "an earlier order for this instrument and strategy is unresolved; "
                    "ambiguity must not compound (11-TRADING.md §5)"
                ),
            )
            self._orders[refused.order_id] = refused
            return SubmissionOutcome(
                kind=SubmissionResultKind.NOT_AUTHORIZED,
                order=refused,
                authorization=authorization,
                detail="blocked by an unresolved order",
            )

        attempt = self._attempts.get(order.order_id, 0) + 1
        client_attempt = attempt_id_for(order.order_id, attempt)

        # Local idempotency: the same attempt is never sent twice. This protects
        # against our own retries and restarts. It does NOT protect against the
        # venue having received an earlier attempt whose answer we lost -- that is
        # what reconciliation is for.
        if client_attempt in self._sent:
            return SubmissionOutcome(
                kind=SubmissionResultKind.AMBIGUOUS,
                order=self._orders.get(order.order_id, order),
                authorization=authorization,
                detail=f"attempt {client_attempt} has already been sent",
            )

        working = order.transition(OrderState.SUBMITTING, at=at, trigger="oms_submit")
        from dataclasses import replace

        working = replace(
            working,
            venue=self.venue,
            client_order_attempt_id=client_attempt,
            authorizing_risk_decision_id=authorization.decision_id,
            authorizing_decision_sequence=authorization.decision_sequence,
        )
        self._orders[working.order_id] = working
        self._attempts[order.order_id] = attempt
        self._sent.add(client_attempt)

        request = BrokerOrderRequest(
            client_order_attempt_id=client_attempt,
            order_id=working.order_id,
            instrument_id=working.instrument_id,
            side=working.side,
            quantity=working.quantity,
            order_type=working.order_type,
            limit_price=working.limit_price,
            requested_at=at,
        )

        try:
            ack = await self.adapter.place_order(request)
        except LiveExecutionDisabled as exc:
            # The adapter is not allowed to submit. The order never left, so this
            # is a refusal rather than an ambiguity -- nothing can be at the venue.
            refused = working.transition(OrderState.REJECTED, at=at, trigger="capability_denied")
            refused = replace(
                refused,
                reject_reason=RejectReason.EXECUTION_REJECTED,
                reject_detail=str(exc)[:500],
            )
            self._orders[refused.order_id] = refused
            return SubmissionOutcome(
                kind=SubmissionResultKind.CAPABILITY_DENIED,
                order=refused,
                authorization=authorization,
                detail=str(exc),
            )
        except BrokerSubmissionTimeout as exc:
            # `11` §5. The request went out; no answer came back. The venue MAY
            # hold this order. Not rejected, not accepted, not retried.
            unknown = working.transition(
                OrderState.UNKNOWN,
                at=at,
                trigger="submission_timeout",
                payload=(("detail", str(exc)[:200]),),
            )
            self._orders[unknown.order_id] = unknown
            return SubmissionOutcome(
                kind=SubmissionResultKind.AMBIGUOUS,
                order=unknown,
                authorization=authorization,
                detail=str(exc),
            )

        return self._apply_ack(working, ack, authorization, at=at)

    def _apply_ack(
        self,
        order: PaperOrder,
        ack: BrokerAck,
        authorization: SubmissionAuthorization,
        *,
        at: datetime,
    ) -> SubmissionOutcome:
        from dataclasses import replace

        if not ack.accepted:
            rejected = order.transition(
                OrderState.REJECTED,
                at=at,
                trigger="provider_reject",
                payload=(("reason", ack.reason[:200]),),
            )
            rejected = replace(
                rejected,
                reject_reason=RejectReason.EXECUTION_REJECTED,
                reject_detail=ack.reason,
                provider_order_id=ack.provider_order_id,
                provider_status=(
                    None if ack.provider_status is None else ack.provider_status.value
                ),
                provider_event_time=ack.provider_time,
                received_at=ack.received_at,
            )
            self._orders[rejected.order_id] = rejected
            return SubmissionOutcome(
                kind=SubmissionResultKind.REJECTED,
                order=rejected,
                authorization=authorization,
                ack=ack,
                detail=ack.reason,
            )

        submitted = order.transition(OrderState.SUBMITTED, at=at, trigger="provider_ack")
        submitted = replace(
            submitted,
            provider_order_id=ack.provider_order_id,
            provider_status=(None if ack.provider_status is None else ack.provider_status.value),
            provider_event_time=ack.provider_time,
            received_at=ack.received_at,
        )
        self._orders[submitted.order_id] = submitted
        return SubmissionOutcome(
            kind=SubmissionResultKind.ACKNOWLEDGED,
            order=submitted,
            authorization=authorization,
            ack=ack,
        )

    # ------------------------------------------------------------------ recovery

    def escalate_to_reconciliation(self, order_id: str, *, at: datetime) -> PaperOrder:
        """Move an `UNKNOWN` order into the reconciler's hands.

        The only transition out of `UNKNOWN`. Separated from `submit` so the
        escalation is an explicit act with its own event, rather than something
        that happens invisibly inside a failure path.
        """
        order = self._orders[order_id]
        escalated = order.transition(
            OrderState.PENDING_RECONCILIATION, at=at, trigger="escalate_to_reconciliation"
        )
        self._orders[order_id] = escalated
        return escalated

    # -------------------------------------------------------------------- cancel

    async def request_cancel(self, order_id: str, *, at: datetime) -> PaperOrder:
        """Ask the venue to cancel. **A request, not an outcome** (brief §15).

        The order moves to `CANCEL_PENDING` first and only reaches `CANCELLED` if
        the venue says so. An order that filled before the cancel landed is filled,
        and reporting it cancelled would tell the ledger it had no position when it
        has one.
        """
        from dataclasses import replace

        order = self._orders[order_id]
        pending = order.transition(OrderState.CANCEL_PENDING, at=at, trigger="cancel_requested")
        self._orders[order_id] = pending

        if pending.provider_order_id is None:
            # We cannot ask about an order whose venue id we never learned. It is
            # unresolved, not cancelled.
            unknown = pending.transition(
                OrderState.UNKNOWN,
                at=at,
                trigger="cancel_without_provider_id",
                payload=(("detail", "no provider order id; cannot address the cancel"),),
            )
            self._orders[order_id] = unknown
            return unknown

        try:
            ack = await self.adapter.cancel_order(pending.provider_order_id)
        except BrokerSubmissionTimeout as exc:
            unknown = pending.transition(
                OrderState.UNKNOWN,
                at=at,
                trigger="cancel_timeout",
                payload=(("detail", str(exc)[:200]),),
            )
            self._orders[order_id] = unknown
            return unknown

        if not ack.accepted:
            # The venue refused, usually because the order already filled. Leave it
            # in CANCEL_PENDING and record why -- reconciliation will settle it.
            recorded = replace(
                pending,
                provider_status=(
                    None if ack.provider_status is None else ack.provider_status.value
                ),
            )
            self._orders[order_id] = recorded
            return recorded

        cancelled = pending.transition(OrderState.CANCELLED, at=at, trigger="provider_cancel_ack")
        cancelled = replace(
            cancelled,
            provider_status=(None if ack.provider_status is None else ack.provider_status.value),
        )
        self._orders[order_id] = cancelled
        return cancelled

    # --------------------------------------------------------------- bookkeeping

    def apply_provider_state(
        self, order_id: str, *, state: OrderState, provider_status: str, at: datetime
    ) -> PaperOrder:
        """Move an order to the state provider evidence establishes.

        Used by the reconciler. Goes through the transition table like everything
        else, so provider truth cannot force an illegal move — if the venue claims
        something the machine forbids, that is a mapping bug and it raises rather
        than corrupting the order.
        """
        from dataclasses import replace

        order = self._orders[order_id]
        if order.state is state:
            return order
        moved = order.transition(
            state,
            at=at,
            trigger="reconciliation",
            payload=(("provider_status", provider_status),),
        )
        self._orders[order_id] = replace(moved, provider_status=provider_status)
        return self._orders[order_id]

    def adopt_provider_identity(
        self, order_id: str, *, provider_order_id: str, at: datetime
    ) -> PaperOrder:
        """Record a provider id that reconciliation discovered.

        After a lost acknowledgement we hold an order at the venue whose id we
        never learned. Reconciliation can find it -- by matching our attempt id --
        and without writing the id back we would rediscover it on every run and,
        worse, be unable to cancel the order, because `cancel_order` addresses it
        by provider id.

        Refuses to overwrite a different id. Two provider ids for one order means
        either a duplicate submission or a mis-match, and silently taking the
        newer one would hide whichever it is.
        """
        from dataclasses import replace

        order = self._orders[order_id]
        if order.provider_order_id not in (None, provider_order_id):
            raise ValueError(
                f"order {order_id} already carries provider id "
                f"{order.provider_order_id}; refusing to replace it with "
                f"{provider_order_id}. Two provider ids for one order means a "
                f"duplicate submission or a bad match, and both need a human"
            )
        adopted = replace(order, provider_order_id=provider_order_id)
        self._orders[order_id] = adopted
        return adopted

    def record_fill(
        self, order_id: str, *, quantity: int, price: Decimal, at: datetime
    ) -> PaperOrder:
        """Fold a provider fill into the order. Idempotency is the caller's job.

        Deliberately not deduplicated here: the fill's identity lives on the
        `BrokerFill`, and the reconciler owns the applied-set. Two dedup systems
        would eventually disagree.
        """
        order = self._orders[order_id]
        filled = order.apply_fill(quantity=quantity, price=price, at=at, trigger="provider_fill")
        self._orders[order_id] = filled
        return filled

    def snapshot(self) -> Sequence[dict[str, Any]]:
        return [order.as_dict() for order in self.orders()]

    @property
    def capabilities(self) -> frozenset[ExecutionCapability]:
        return self.adapter.capabilities
