"""Positions, cash and P&L — the backtest ledger (Phase 7 brief §15).

Two requirements shape this module.

**Idempotency.** Replay can re-present the same input: a resumed run re-enters the step
it stopped on, and a corrected observation can cause a step to be re-executed. §15
requires ledger application to be idempotent where that can happen. Every fill
therefore carries a stable, content-addressed `fill_key`, and a fill whose key has
already been applied is ignored rather than added again. Without this, resume would
quietly double a position, and the error would surface as a P&L discrepancy far from
its cause.

**Separation of P&L components.** §14 requires gross P&L, fees and slippage to stay
distinct all the way to the result. They are accumulated separately here and never
netted in passing; `net_pnl` is derived on demand from parts that remain individually
readable. The first question asked of any strategy is whether it is profitable before
costs, and a ledger that sums them at source cannot answer it.

Unrealized P&L is computed **against a supplied mark**, never against a price the
ledger fetches. A ledger that reached for a price could reach for the wrong one; this
one can only mark against what the caller -- who knows the replay step and its
knowledge horizon -- hands it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.backtest.costs import CostBreakdown
from oipulse.backtest.fills import Fill

__all__ = ["Ledger", "LedgerSnapshot", "Position", "fill_key"]


def fill_key(fill: Fill) -> str:
    """The idempotency key for a fill.

    Derived from the intent id plus the filled quantity and price, so that a genuine
    re-fill of a partially filled intent at a different price is a distinct event,
    while a replayed identical fill is recognised as the same one.
    """
    return f"{fill.intent_id}:{fill.quantity}:{fill.price}"


@dataclass(frozen=True, slots=True)
class Position:
    """A net position with an average cost basis.

    `quantity` is signed: positive long, negative short. `average_price` is the cost
    basis of the *open* quantity only -- it is not touched when a position is reduced,
    because reducing realises P&L against the existing basis rather than changing it.
    """

    instrument_id: int
    quantity: int
    average_price: Decimal

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    def market_value(self, mark: Decimal) -> Decimal:
        return mark * Decimal(self.quantity)

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        """Signed correctly for both directions: a short gains as the mark falls."""
        return (mark - self.average_price) * Decimal(self.quantity)

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "quantity": self.quantity,
            "average_price": str(self.average_price),
        }


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """An immutable reading of the ledger, safe to store on a result or a step."""

    as_of: datetime
    cash: Decimal
    positions: tuple[Position, ...]
    realized_pnl: Decimal
    fees: Decimal
    slippage_cost: Decimal
    unrealized_pnl: Decimal
    #: Positions whose mark was unavailable, so `unrealized_pnl` excludes them.
    unmarked_instruments: tuple[int, ...]
    fills_applied: int

    @property
    def gross_pnl(self) -> Decimal:
        """Before fees. Slippage is already embedded in the fill prices, so it is
        reported separately as a measurement rather than subtracted twice."""
        return self.realized_pnl + self.unrealized_pnl

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees

    @property
    def equity(self) -> Decimal:
        """Cash plus the marked value of the book.

        Written as cost basis plus unrealized rather than `mark x quantity` because
        the snapshot keeps no marks -- the two are identically equal, and this form
        needs nothing the snapshot does not already hold. Equals
        `opening_cash + net_pnl`, since cash has already absorbed the fees.
        """
        basis = sum((p.average_price * Decimal(p.quantity) for p in self.positions), Decimal(0))
        return self.cash + basis + self.unrealized_pnl

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "cash": str(self.cash),
            "positions": [p.as_dict() for p in self.positions],
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "gross_pnl": str(self.gross_pnl),
            "fees": str(self.fees),
            "slippage_cost": str(self.slippage_cost),
            "net_pnl": str(self.net_pnl),
            "unmarked_instruments": list(self.unmarked_instruments),
            "fills_applied": self.fills_applied,
        }


class Ledger:
    """Mutable during a run, snapshotted immutably.

    Deliberately not a pure data structure: a backtest applies thousands of fills in
    sequence, and rebuilding the whole position book per fill would make the cost of
    honesty quadratic. Determinism is preserved because application order is the
    fill order, which the runner derives from the replay timeline.
    """

    def __init__(self, *, opening_cash: Decimal) -> None:
        if opening_cash < 0:
            raise ValueError("opening cash cannot be negative")
        self._opening_cash = opening_cash
        self._cash = opening_cash
        self._positions: dict[int, Position] = {}
        self._realized = Decimal(0)
        self._fees = Decimal(0)
        self._slippage = Decimal(0)
        self._costs = CostBreakdown()
        self._applied: set[str] = set()
        self._duplicates = 0

    # ------------------------------------------------------------------ reading

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def opening_cash(self) -> Decimal:
        return self._opening_cash

    @property
    def realized_pnl(self) -> Decimal:
        return self._realized

    @property
    def fees(self) -> Decimal:
        return self._fees

    @property
    def cost_breakdown(self) -> CostBreakdown:
        return self._costs

    @property
    def duplicate_fills_ignored(self) -> int:
        """Exposed so a run can report idempotency rather than merely rely on it."""
        return self._duplicates

    @property
    def fills_applied(self) -> int:
        return len(self._applied)

    def position(self, instrument_id: int) -> Position | None:
        return self._positions.get(instrument_id)

    def has_applied(self, fill: Fill) -> bool:
        """Whether this exact fill is already in the book.

        Additive accessor (Phase 8): a caller sometimes needs to know whether a
        redelivered fill would be a no-op *without* applying it -- deciding whether
        to release a cash reservation, for instance. Exposing the question here keeps
        the applied-set in one place; a caller tracking its own copy would be a
        second bookkeeping system for the two to disagree about.
        """
        return fill_key(fill) in self._applied

    def positions(self) -> tuple[Position, ...]:
        """Sorted by instrument id: iteration order must not depend on insertion order,
        or two runs that traded the same instruments in a different sequence would
        produce different snapshots of an identical book."""
        return tuple(
            self._positions[key] for key in sorted(self._positions) if self._positions[key].quantity
        )

    # ------------------------------------------------------------------ writing

    def apply(self, fill: Fill) -> bool:
        """Apply one fill. Returns False if it was a duplicate and was ignored."""
        key = fill_key(fill)
        if key in self._applied:
            self._duplicates += 1
            return False
        self._applied.add(key)

        signed = fill.quantity * fill.side.sign
        existing = self._positions.get(fill.instrument_id)

        # Cash moves by the full consideration plus costs, in both directions: a sale
        # brings in premium, a purchase pays it, and fees are always an outflow.
        self._cash -= Decimal(signed) * fill.price
        total_costs = fill.costs.total
        self._cash -= total_costs
        self._fees += total_costs
        self._costs = self._costs + fill.costs
        self._slippage += fill.slippage_per_unit * Decimal(fill.quantity)

        if existing is None or existing.quantity == 0:
            self._positions[fill.instrument_id] = Position(
                instrument_id=fill.instrument_id,
                quantity=signed,
                average_price=fill.price,
            )
            return True

        self._positions[fill.instrument_id] = self._merge(existing, signed, fill.price)
        return True

    def apply_all(self, fills: Iterable[Fill]) -> int:
        return sum(1 for fill in fills if self.apply(fill))

    def _merge(self, existing: Position, signed: int, price: Decimal) -> Position:
        """Fold a signed quantity into a position, realising P&L on the closed part.

        Three cases, kept explicit rather than folded into one expression, because
        the sign handling in a reversal is where this kind of code usually goes wrong.
        """
        same_direction = (existing.quantity > 0) == (signed > 0)

        if same_direction:
            # Adding: the basis becomes the weighted average. Nothing is realised.
            total = existing.quantity + signed
            cost = existing.average_price * Decimal(existing.quantity) + price * Decimal(signed)
            return Position(
                instrument_id=existing.instrument_id,
                quantity=total,
                average_price=cost / Decimal(total),
            )

        closing = min(abs(signed), abs(existing.quantity))
        # Realised against the existing basis. The sign of the existing position
        # decides the direction: closing a long realises (exit - entry).
        direction = 1 if existing.quantity > 0 else -1
        self._realized += (price - existing.average_price) * Decimal(closing * direction)

        remaining = existing.quantity + signed
        if remaining == 0:
            return Position(
                instrument_id=existing.instrument_id, quantity=0, average_price=Decimal(0)
            )
        if (remaining > 0) == (existing.quantity > 0):
            # Partially closed: the basis of what is left is unchanged.
            return Position(
                instrument_id=existing.instrument_id,
                quantity=remaining,
                average_price=existing.average_price,
            )
        # Reversed through flat: the new position's basis is this fill's price.
        return Position(
            instrument_id=existing.instrument_id, quantity=remaining, average_price=price
        )

    # --------------------------------------------------------------- snapshots

    def snapshot(self, *, as_of: datetime, marks: Mapping[int, Decimal]) -> LedgerSnapshot:
        """Mark the book against supplied prices.

        An instrument with no mark is **excluded from unrealized P&L and named** in
        `unmarked_instruments`, rather than marked at its cost basis. Marking at cost
        would report a position as exactly break-even, which is a number, and a
        plausible one, and wrong.
        """
        unrealized = Decimal(0)
        unmarked: list[int] = []
        for position in self.positions():
            mark = marks.get(position.instrument_id)
            if mark is None:
                unmarked.append(position.instrument_id)
                continue
            unrealized += position.unrealized_pnl(mark)

        return LedgerSnapshot(
            as_of=as_of,
            cash=self._cash,
            positions=self.positions(),
            realized_pnl=self._realized,
            fees=self._fees,
            slippage_cost=self._slippage,
            unrealized_pnl=unrealized,
            unmarked_instruments=tuple(unmarked),
            fills_applied=len(self._applied),
        )
