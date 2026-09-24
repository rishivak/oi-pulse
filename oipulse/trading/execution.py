"""The paper execution model — `11-TRADING.md` §7.

> Fills are produced by the same `FillModel` the backtester uses (`10-REPLAY.md` §6),
> driven by live market data: spread from observed bid/ask, configurable latency and
> slippage, size-aware partial fills, real fee schedule.

**The same model, not a similar one.** This module contains no pricing arithmetic: it
delegates to `oipulse.backtest.fills.simulate_fill`. That is the whole point of §7 —
a paper fill and a backtest fill must be produced by one implementation, or the
rehearsal stops predicting the performance.

What this module adds is the part that is specific to a *live-driven* paper venue
rather than a replayed one:

* **Execution timing.** A decision at `T` executes against the state at
  `T + latency`. The caller supplies that state; this module never fetches one, which
  is what makes it structurally incapable of look-ahead — it can only price against
  what it was handed.
* **Order-level rules** the backtest fill model does not carry: expiry against
  `valid_until`, the `max_slippage` constraint, `all_or_none`, and the account's
  declared order caps.
* **Explicit refusal.** Where a requirement is unavailable, the result is a rejection
  with a reason. Phase 8 brief §17: *do not silently create a fill when the execution
  requirements are unavailable.*

Every assumption is identified by `FillModel.content_digest()` and the account's
`config_digest`, both of which travel onto the order and the journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.backtest.fills import (
    Fill,
    FillModel,
    RejectionReason,
    simulate_fill,
)
from oipulse.backtest.intents import OrderType, Side
from oipulse.backtest.intents import TradeIntent as StrategyIntent
from oipulse.marketstate.staleness import QualityStatus
from oipulse.marketstate.state import MarketState
from oipulse.trading.intents import IntentConstraints
from oipulse.trading.orders import OrderState, PaperOrder, RejectReason

__all__ = [
    "ExecutionOutcome",
    "PaperExecutionModel",
]

#: How a Phase 7 fill rejection maps onto a Phase 8 order reject reason. A declared
#: table rather than a chain of `if`s, so an unmapped reason is a visible gap.
_REJECTION_MAP: dict[RejectionReason, RejectReason] = {
    RejectionReason.NO_PRICE_AVAILABLE: RejectReason.NO_PRICE_AVAILABLE,
    RejectionReason.LIMIT_NOT_MARKETABLE: RejectReason.EXECUTION_REJECTED,
    RejectionReason.NO_LIQUIDITY: RejectReason.EXECUTION_REJECTED,
    RejectionReason.STALE_QUOTE: RejectReason.STALE_MARKET_STATE,
    RejectionReason.MODELLED_REJECTION: RejectReason.EXECUTION_REJECTED,
}


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What the paper venue did. A rejection is a result, never an exception.

    Raising would hide unfillable orders from the statistics, and that is precisely
    where a strategy's problems show up.
    """

    order: PaperOrder
    fill: Fill | None
    reject_reason: RejectReason | None = None
    detail: str = ""

    @property
    def filled(self) -> bool:
        return self.fill is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "order": self.order.as_dict(),
            "fill": None if self.fill is None else self.fill.as_dict(),
            "reject_reason": None if self.reject_reason is None else self.reject_reason.value,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PaperExecutionModel:
    """Order-level rules wrapped around the shared `FillModel`.

    Holds no state and reaches nothing. Given an order, a state and the available
    cash, it returns an outcome; the caller applies it to the ledger. That split is
    what keeps the arithmetic testable in isolation.
    """

    fill_model: FillModel
    #: Refuse to fill against a state the quality gate marked unreliable. `11` §3
    #: lists the stale-data check as a *risk* limit; until Phase 9 exists, the
    #: execution model applies the narrow version -- refuse rather than trade on a
    #: state the system itself says it does not trust.
    reject_on_unreliable_state: bool = True

    @property
    def assumptions(self) -> dict[str, Any]:
        """The printable assumption set. Travels onto the order and the journal."""
        return {
            **self.fill_model.assumptions(),
            "reject_on_unreliable_state": self.reject_on_unreliable_state,
            "execution_venue": "PAPER",
        }

    @property
    def content_digest(self) -> str:
        return self.fill_model.content_digest()

    # ------------------------------------------------------------------ execution

    def execute(
        self,
        order: PaperOrder,
        state: MarketState,
        *,
        at: datetime,
        available_cash: Decimal,
        constraints: IntentConstraints | None = None,
        reference_price: Decimal | None = None,
    ) -> ExecutionOutcome:
        """Attempt one execution of *order* against *state*.

        `state` must be the state at or after `decision_time + latency`; selecting it
        is the runtime's job, because only the runtime knows the market clock. This
        method never fetches a state, so it cannot see the future.
        """
        constraints = constraints or IntentConstraints()

        if order.is_terminal:
            return ExecutionOutcome(
                order=order,
                fill=None,
                reject_reason=RejectReason.EXECUTION_REJECTED,
                detail=f"order is already {order.state.value}",
            )

        # 1. Expiry, against market time. Checked before pricing: an expired order
        #    must not be filled even if a perfectly good quote exists.
        if constraints.valid_until is not None and at > constraints.valid_until:
            return self._expire(order, at, f"market time {at.isoformat()} is past valid_until")

        # 2. State quality. Trading on a state the system says is unreliable is a
        #    decision that must be explicit (`11` §3).
        if self.reject_on_unreliable_state and state.quality.status is QualityStatus.UNRELIABLE:
            return self._reject(
                order,
                at,
                RejectReason.STALE_MARKET_STATE,
                "the market state is marked UNRELIABLE; refusing to fill against it",
            )

        # 3. Price and size, through the shared Phase 7 model.
        # The Phase 7 fill model prices a *strategy* intent, so the order is
        # expressed as one. `run_id` carries the order id, which makes the resulting
        # `Fill.intent_id` deterministic and unique per order -- the runtime maps
        # fills back to orders explicitly rather than parsing this id, but a stable
        # value still matters: a random one would break fill idempotency.
        probe = StrategyIntent(
            run_id=order.order_id,
            strategy_id=order.strategy_id or "paper",
            strategy_version=max(order.strategy_version, 1),
            instrument_id=order.instrument_id,
            side=order.side,
            quantity=order.remaining_quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
            decision_time=order.decision_time or order.created_at,
            knowledge_horizon=order.knowledge_time or order.created_at,
        )
        outcome = simulate_fill(probe, state, self.fill_model)

        if outcome.fill is None:
            reason = outcome.rejection
            mapped = (
                _REJECTION_MAP.get(reason, RejectReason.EXECUTION_REJECTED)
                if reason is not None
                else RejectReason.EXECUTION_REJECTED
            )
            return self._reject(order, at, mapped, outcome.detail)

        fill = outcome.fill

        # 4. all_or_none. A partially filled spread is a different position from the
        #    one that was intended, so it is refused rather than half-applied.
        if constraints.all_or_none and fill.quantity < order.remaining_quantity:
            return self._reject(
                order,
                at,
                RejectReason.ALL_OR_NONE_UNFILLABLE,
                f"only {fill.quantity} of {order.remaining_quantity} fillable and the "
                f"intent is all-or-none",
            )

        # 5. Slippage constraint, measured against the declared reference.
        if constraints.max_slippage is not None and reference_price is not None:
            slipped = (fill.price - reference_price) * fill.side.sign
            if slipped > constraints.max_slippage:
                return self._reject(
                    order,
                    at,
                    RejectReason.SLIPPAGE_EXCEEDED,
                    f"slippage {slipped} exceeds the declared maximum {constraints.max_slippage}",
                )

        # 6. Cash. A buy must be affordable including costs; a sell releases cash.
        if fill.side is Side.BUY:
            required = fill.price * Decimal(fill.quantity) + fill.costs.total
            if required > available_cash:
                return self._reject(
                    order,
                    at,
                    RejectReason.INSUFFICIENT_CASH,
                    f"fill requires {required} including costs but only "
                    f"{available_cash} is available",
                )

        filled = order.apply_fill(quantity=fill.quantity, price=fill.price, at=at)

        # An IOC-style remainder is not left resting: the caller decides, but a
        # partially filled order that can never fill further should not look open.
        return ExecutionOutcome(order=filled, fill=fill)

    # -------------------------------------------------------------------- helpers

    def _reject(
        self, order: PaperOrder, at: datetime, reason: RejectReason, detail: str
    ) -> ExecutionOutcome:
        """Reject, or cancel a partially filled order that cannot continue.

        A `PARTIALLY_FILLED` order cannot transition to `REJECTED` -- the table
        forbids it, correctly, because some of it genuinely happened. The honest
        terminal state for the remainder is `CANCELLED`, and the reason is recorded
        either way.
        """
        if order.state is OrderState.PARTIALLY_FILLED:
            cancelled = order.cancel(at=at, detail=f"{reason.value}: {detail}")
            return ExecutionOutcome(order=cancelled, fill=None, reject_reason=reason, detail=detail)
        return ExecutionOutcome(
            order=order.reject(reason, at=at, detail=detail),
            fill=None,
            reject_reason=reason,
            detail=detail,
        )

    def _expire(self, order: PaperOrder, at: datetime, detail: str) -> ExecutionOutcome:
        return ExecutionOutcome(
            order=order.expire(at=at, detail=detail),
            fill=None,
            reject_reason=RejectReason.INTENT_EXPIRED,
            detail=detail,
        )

    # ------------------------------------------------------------------ validation

    @staticmethod
    def validate_shape(
        order: PaperOrder,
        *,
        max_order_quantity: int | None,
        max_order_notional: Decimal | None,
        at: datetime,
    ) -> ExecutionOutcome | None:
        """Pre-acceptance checks. Returns a rejection outcome, or None if the shape
        is acceptable.

        Separate from `execute` because these are answerable without a quote, and an
        order that is malformed should be refused before anyone looks for a price.
        """
        if max_order_quantity is not None and order.quantity > max_order_quantity:
            return ExecutionOutcome(
                order=order.reject(
                    RejectReason.EXCEEDS_ORDER_LIMIT,
                    at=at,
                    detail=f"quantity {order.quantity} exceeds the account's declared "
                    f"maximum {max_order_quantity}",
                ),
                fill=None,
                reject_reason=RejectReason.EXCEEDS_ORDER_LIMIT,
                detail="quantity above the account maximum",
            )
        if (
            max_order_notional is not None
            and order.order_type is OrderType.LIMIT
            and order.limit_price is not None
            and order.limit_price * Decimal(order.quantity) > max_order_notional
        ):
            return ExecutionOutcome(
                order=order.reject(
                    RejectReason.EXCEEDS_ORDER_LIMIT,
                    at=at,
                    detail="notional above the account maximum",
                ),
                fill=None,
                reject_reason=RejectReason.EXCEEDS_ORDER_LIMIT,
                detail="notional above the account maximum",
            )
        return None
