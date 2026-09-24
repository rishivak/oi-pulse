"""The paper-trading runtime — the chain from intent to position.

`11-TRADING.md` §1, restricted to what Phase 8 implements:

```
TradeIntent ──► [risk seam] ──► PaperOrder ──► PaperBrokerAdapter ──► Fill
                                                       │
                                                       ▼
                                          position · cash · P&L · journal
```

**A signal is not an order and an intent is not a fill.** Each arrow is a separate,
recorded step with its own identity, and each can fail without the next happening.
That is the whole reason the chain has four objects rather than one function.

### Two clocks, kept apart

Phase 8 brief §9: paper trading has a market clock and a system execution clock.
This runtime is driven **entirely** by market time, supplied by the caller at every
entry point. It never reads a wall clock — `tools/check_clock_access.py` enforces
that no module outside `oipulse/core/clock.py` can — so a paper run driven by live
data and the same run driven by a replay produce identical results.

Execution timestamps may be recorded alongside, but they never enter an identity or
change the meaning of a decision.

### Where look-ahead is prevented

The runtime takes the decision state and the execution state as **separate
arguments**. The strategy saw the first; the fill is priced against the second. The
runtime cannot conjure either, so it cannot price a fill against information the
decision already had, nor can it let a decision see the execution state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.backtest.fills import Fill
from oipulse.marketstate.state import MarketState
from oipulse.trading.accounts import AccountStatus, PaperAccount
from oipulse.trading.brokers import PaperBrokerAdapter, adapter_for
from oipulse.trading.events import (
    AggregateWatermarks,
    PaperEventType,
    TransactionalInbox,
    intent_created_event,
    order_event,
)
from oipulse.trading.execution import PaperExecutionModel
from oipulse.trading.intents import TradeIntent
from oipulse.trading.ledger import PaperLedger
from oipulse.trading.orders import PaperOrder, RejectReason
from oipulse.trading.risk import UNEVALUATED_RISK, RiskDecisionRecord, RiskGate

__all__ = ["PaperTradingRuntime", "SubmissionResult"]


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    """What happened to one intent. Every stage is visible, including the refusals.

    A refusal is a result, not an exception: an intent rejected for insufficient
    cash is a fact the audit trail must contain, and raising would lose it.
    """

    intent: TradeIntent
    risk_decision: RiskDecisionRecord
    orders: tuple[PaperOrder, ...] = ()
    fills: tuple[Fill, ...] = ()
    rejected: bool = False
    reject_reason: RejectReason | None = None
    detail: str = ""
    #: True when this intent had already been processed and nothing was re-applied.
    duplicate: bool = False

    @property
    def accepted(self) -> bool:
        return not self.rejected and bool(self.orders)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.as_dict(),
            "risk_decision": self.risk_decision.as_dict(),
            "orders": [o.as_dict() for o in self.orders],
            "fills": [f.as_dict() for f in self.fills],
            "rejected": self.rejected,
            "reject_reason": None if self.reject_reason is None else self.reject_reason.value,
            "detail": self.detail,
            "duplicate": self.duplicate,
            "mode": "PAPER",
        }


class PaperTradingRuntime:
    """One account's paper-trading runtime. Deterministic and idempotent.

    Holds the account, its ledger, its open orders and the applied-intent set. Not a
    pure function, because a trading account is inherently stateful — but every
    transition is driven by supplied market time and supplied state, so replaying
    the same inputs reproduces the same account exactly.
    """

    def __init__(
        self,
        account: PaperAccount,
        *,
        execution: PaperExecutionModel,
        inbox: TransactionalInbox,
        risk_gate: RiskGate | None = None,
    ) -> None:
        # Resolves the mode, and raises for anything but PAPER. Done in the
        # constructor so a non-paper account cannot even be wired up, let alone traded.
        self._adapter: PaperBrokerAdapter = adapter_for(account, execution)
        self._account = account
        self._execution = execution
        self._inbox = inbox
        self._risk = risk_gate if risk_gate is not None else UNEVALUATED_RISK
        self._ledger = PaperLedger(account.account_id, opening_cash=account.config.starting_cash)
        self._orders: dict[str, PaperOrder] = {}
        self._intents: dict[str, TradeIntent] = {}
        self._decisions: dict[str, tuple[RiskDecisionRecord, ...]] = {}
        self._fills: list[Fill] = []
        #: Explicit fill -> order linkage. A `Fill` carries the Phase 7 probe's id,
        #: not the trading intent's, so the chain is recorded here rather than
        #: reconstructed by parsing an identifier that does not mean what it looks
        #: like it means (`11` §10 requires traversal, not inference).
        self._fills_by_order: dict[str, list[Fill]] = {}
        self._watermarks = AggregateWatermarks()
        #: Which order-event sequences have already been emitted as domain events.
        #: Emitting one twice is harmless (the inbox absorbs it) but skipping one
        #: is not: it opens a sequence gap that defers every later event.
        self._emitted_order_sequences: dict[str, set[int]] = {}
        self._duplicate_intents = 0

    # ------------------------------------------------------------------ reading

    @property
    def account(self) -> PaperAccount:
        return self._account

    @property
    def ledger(self) -> PaperLedger:
        return self._ledger

    @property
    def risk_evaluated(self) -> bool:
        """False while Phase 9 is pending. Travels onto every account response."""
        return self._risk.evaluates_risk

    @property
    def duplicate_intents_ignored(self) -> int:
        return self._duplicate_intents

    def orders(self) -> tuple[PaperOrder, ...]:
        """Sorted by id, so iteration never depends on insertion order."""
        return tuple(self._orders[key] for key in sorted(self._orders))

    def order(self, order_id: str) -> PaperOrder | None:
        return self._orders.get(order_id)

    def intent(self, intent_id: str) -> TradeIntent | None:
        return self._intents.get(intent_id)

    def decisions_for(self, intent_id: str) -> tuple[RiskDecisionRecord, ...]:
        """The full appended sequence, per `11` §3 — never a single verdict."""
        return self._decisions.get(intent_id, ())

    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    def fills_for_order(self, order_id: str) -> tuple[Fill, ...]:
        """The fills that moved this order. The audit chain's fill link."""
        return tuple(self._fills_by_order.get(order_id, ()))

    def open_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(o for o in self.orders() if o.is_open)

    # ------------------------------------------------------------------ account

    def set_status(self, status: AccountStatus, *, at: datetime) -> PaperAccount:
        self._account = self._account.with_status(status, at=at)
        return self._account

    # ----------------------------------------------------------------- submission

    def submit(
        self,
        intent: TradeIntent,
        *,
        decision_state: MarketState,
        execution_state: MarketState,
        at: datetime,
    ) -> SubmissionResult:
        """Run one intent through the whole chain.

        `decision_state` is what the strategy saw; `execution_state` is what the
        paper venue prices against. They are separate parameters, and the runtime
        never substitutes one for the other -- that substitution is precisely how a
        backtest or a paper run acquires free look-ahead.
        """
        # 1. Idempotency. The intent id is content-addressed, so a reprocessed
        #    decision resolves to the same key and is recognised rather than
        #    duplicated (brief §16).
        key = intent.idempotency_key
        if key in self._intents:
            self._duplicate_intents += 1
            existing = self._intents[key]
            orders = tuple(o for o in self.orders() if o.intent_id == existing.intent_id)
            decisions = self.decisions_for(existing.intent_id)
            return SubmissionResult(
                intent=existing,
                risk_decision=decisions[-1],
                orders=orders,
                fills=tuple(f for o in orders for f in self.fills_for_order(o.order_id)),
                duplicate=True,
                detail="this intent has already been processed; nothing was re-applied",
            )

        # 2. Account must be open for business.
        if not self._account.status.accepts_intents:
            return self._refuse(
                intent,
                at,
                RejectReason.ACCOUNT_NOT_ACTIVE,
                f"account is {self._account.status.value}",
            )

        # 3. Expiry, against market time.
        if intent.has_expired(at):
            return self._refuse(
                intent, at, RejectReason.INTENT_EXPIRED, "intent expired before submission"
            )

        # 4. The risk seam. Appends a decision even when nothing evaluated it, so the
        #    sequence exists from the first intent and Phase 9 slots in.
        decision = self._risk.evaluate(intent, sequence_no=1, at=at)
        self._decisions[intent.intent_id] = (decision,)
        if not decision.is_approved:
            self._intents[key] = intent
            return SubmissionResult(
                intent=intent,
                risk_decision=decision,
                rejected=True,
                reject_reason=RejectReason.RISK_REJECTED,
                detail=decision.reason,
            )
        if not decision.is_actionable_at(at):
            self._intents[key] = intent
            return SubmissionResult(
                intent=intent,
                risk_decision=decision,
                rejected=True,
                reject_reason=RejectReason.RISK_REJECTED,
                detail="the risk approval has expired; re-evaluation is required",
            )

        # 5. One order per leg, with a deterministic id.
        self._intents[key] = intent
        self._record_intent_event(intent, at)

        orders: list[PaperOrder] = []
        fills: list[Fill] = []
        for index, leg in enumerate(intent.legs):
            order = PaperOrder(
                order_id=PaperOrder.derive_id(intent.intent_id, index, intent.account_id),
                account_id=intent.account_id,
                intent_id=intent.intent_id,
                instrument_id=leg.instrument_id,
                side=leg.side,
                quantity=leg.quantity,
                order_type=leg.order_type,
                limit_price=leg.limit_price,
                created_at=at,
                signal_id=intent.signal_id,
                signal_version=intent.signal_version,
                strategy_id=intent.strategy_id,
                strategy_version=intent.strategy_version,
                state_checkpoint_ref=intent.state_checkpoint_ref,
                build_context_id=intent.build_context_id,
                knowledge_time=intent.knowledge_time,
                decision_time=intent.decision_time,
                config_digest=self._account.config.content_digest,
            )

            shape = PaperExecutionModel.validate_shape(
                order,
                max_order_quantity=self._account.config.max_order_quantity,
                max_order_notional=self._account.config.max_order_notional,
                at=at,
            )
            if shape is not None:
                self._store(shape.order)
                orders.append(shape.order)
                continue

            outcome = self._adapter.submit(
                order,
                execution_state,
                at=at,
                available_cash=self._ledger.available_cash,
                constraints=intent.constraints,
                reference_price=self._reference_price(decision_state, leg.instrument_id),
            )
            self._store(outcome.order)
            orders.append(outcome.order)
            if outcome.fill is not None:
                if self._ledger.apply(outcome.fill, release_order_id=outcome.order.order_id):
                    self._fills.append(outcome.fill)
                    self._fills_by_order.setdefault(outcome.order.order_id, []).append(outcome.fill)
                    fills.append(outcome.fill)
                self._record_order_events(outcome.order, at)

        return SubmissionResult(
            intent=intent,
            risk_decision=decision,
            orders=tuple(orders),
            fills=tuple(fills),
        )

    # ------------------------------------------------------------------- control

    def cancel(self, order_id: str, *, at: datetime, detail: str = "") -> PaperOrder:
        """Cancel a resting order. Raises `InvalidTransition` if it is terminal.

        Cancelling an already-cancelled order is an error rather than a no-op: the
        caller believes something is outstanding that is not, and silently
        succeeding would let that belief persist into the next decision.
        """
        order = self._orders[order_id]
        cancelled = self._adapter.cancel(order, at=at, detail=detail)
        self._ledger.release(order_id)
        self._store(cancelled)
        self._record_order_events(cancelled, at)
        return cancelled

    def expire_stale_orders(self, *, at: datetime) -> tuple[PaperOrder, ...]:
        """Expire resting orders past their intent's `valid_until`.

        Driven by market time, so a paper session and its replay expire exactly the
        same orders.
        """
        expired: list[PaperOrder] = []
        for order in self.open_orders():
            intent = self._intents.get(order.intent_id)
            if intent is None:
                continue
            if intent.has_expired(at):
                moved = order.expire(at=at, detail="past the intent's valid_until")
                self._ledger.release(order.order_id)
                self._store(moved)
                self._record_order_events(moved, at)
                expired.append(moved)
        return tuple(expired)

    # -------------------------------------------------------------- snapshotting

    def snapshot(self, *, as_of: datetime, marks: Mapping[int, Decimal]) -> Any:
        return self._ledger.snapshot(as_of=as_of, marks=marks)

    def marks_from(self, state: MarketState) -> dict[int, Decimal]:
        """Closing marks from observed last traded prices only.

        An instrument with no observed price contributes no mark, so the ledger
        excludes it from unrealized P&L and names it rather than marking at cost.
        """
        marks: dict[int, Decimal] = {}
        for expiry in state.expiries:
            for leg in expiry.legs:
                if leg.ltp is not None:
                    marks[int(leg.instrument_id)] = leg.ltp
        return marks

    # -------------------------------------------------------------------- internals

    def _store(self, order: PaperOrder) -> None:
        self._orders[order.order_id] = order

    def _refuse(
        self, intent: TradeIntent, at: datetime, reason: RejectReason, detail: str
    ) -> SubmissionResult:
        decision = self._risk.evaluate(intent, sequence_no=1, at=at)
        self._intents[intent.idempotency_key] = intent
        self._decisions[intent.intent_id] = (decision,)
        return SubmissionResult(
            intent=intent,
            risk_decision=decision,
            rejected=True,
            reject_reason=reason,
            detail=detail,
        )

    @staticmethod
    def _reference_price(state: MarketState, instrument_id: int) -> Decimal | None:
        """The decision-time price a slippage constraint is measured against."""
        for expiry in state.expiries:
            for leg in expiry.legs:
                if int(leg.instrument_id) == instrument_id:
                    return leg.ltp
        return None

    def _record_intent_event(self, intent: TradeIntent, at: datetime) -> None:
        event = intent_created_event(
            intent.intent_id, sequence=1, occurred_at=at, payload=intent.as_dict()
        )
        self._watermarks.apply_ordered(event, self._inbox, lambda: None)

    def _record_order_events(self, order: PaperOrder, at: datetime) -> None:
        """Emit a domain event for **every** appended transition, in sequence.

        Not just the latest one. An order that is accepted and then filled appends
        two events, and emitting only the second leaves sequence 1 missing -- which
        the Phase 1 sequence check correctly refuses as a gap, deferring the fill
        event forever. The order's own event log is the authority for what has
        happened, so this emits everything in it that has not been emitted yet.
        """
        already = self._emitted_order_sequences.setdefault(order.order_id, set())
        for appended in order.events:
            if appended.sequence in already:
                continue
            event = order_event(
                PaperEventType.ORDER_STATE_CHANGED,
                order.order_id,
                sequence=appended.sequence,
                occurred_at=at,
                payload=appended.as_dict(),
            )
            self._watermarks.apply_ordered(event, self._inbox, lambda: None)
            already.add(appended.sequence)

    # ------------------------------------------------------------------ recovery

    def rebuild_ledger_from_fills(self, fills: Sequence[Fill]) -> PaperLedger:
        """Reconstruct this account's book from an authoritative fill stream.

        Brief §12 and §23. The same `apply` loop the live path uses, so a rebuilt
        account and a continuously-run one cannot disagree — there is no separate
        recovery implementation for them to disagree *with*.
        """
        from oipulse.trading.ledger import replay_fills

        return replay_fills(
            self._account.account_id,
            opening_cash=self._account.config.starting_cash,
            fills=fills,
        )
