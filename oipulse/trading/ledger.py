"""The paper ledger — positions, cash and P&L for one account.

`11-TRADING.md` §8:

> Positions are derived from **fill events**, not maintained as a running total that
> can drift. A position is a fold over its fills, so it is always reconstructable and
> always reconcilable.

This module is a thin, account-scoped layer over the **verified Phase 7 `Ledger`**
(`oipulse.backtest.ledger`), which already implements the deterministic fold, the
content-addressed idempotency key, the reversal-through-flat arithmetic and the
refusal to mark an unmarked position at cost. Phase 8 brief §11 asks for the paper
ledger to be built on those principles; reusing the implementation is the strongest
available form of that, and it means a paper position and a backtest position cannot
disagree.

What Phase 8 adds:

* **Reserved cash.** A resting buy order has committed cash that is not yet spent.
  Without reservation two orders can each pass an affordability check against the
  same rupee and both fill. `11` does not mandate reservation, so it is a declared
  account configuration rather than an assumption — but when enabled it is enforced
  here, in the one place that knows both cash and open orders.
* **Reconstruction from events.** §12 of the brief: a paper account must be
  rebuildable from its authoritative fills, and a replay of the same stream must
  produce an identical state. `replay_fills` is that function, and it is the same
  code path the live fold uses, so the two cannot diverge.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.backtest.fills import Fill
from oipulse.backtest.intents import Side
from oipulse.backtest.ledger import Ledger, LedgerSnapshot, Position

__all__ = [
    "InsufficientCash",
    "PaperLedger",
    "PaperLedgerSnapshot",
    "replay_fills",
]


class InsufficientCash(Exception):
    """A reservation or a fill that the account cannot fund.

    Raised by `reserve`, which is called before an order is accepted. `execute`
    returns a rejection instead of raising, because by then the refusal is a
    business outcome that belongs in the order's audit trail.
    """


@dataclass(frozen=True, slots=True)
class PaperLedgerSnapshot:
    """An account-scoped reading. Wraps the Phase 7 snapshot, adds reservation."""

    account_id: str
    inner: LedgerSnapshot
    reserved_cash: Decimal

    @property
    def as_of(self) -> datetime:
        return self.inner.as_of

    @property
    def cash(self) -> Decimal:
        """Total cash, including what is reserved against resting orders."""
        return self.inner.cash

    @property
    def available_cash(self) -> Decimal:
        """What a new order may spend. Total less reservations."""
        return self.inner.cash - self.reserved_cash

    @property
    def positions(self) -> tuple[Position, ...]:
        return self.inner.positions

    @property
    def realized_pnl(self) -> Decimal:
        return self.inner.realized_pnl

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.inner.unrealized_pnl

    @property
    def fees(self) -> Decimal:
        return self.inner.fees

    @property
    def gross_pnl(self) -> Decimal:
        """Before fees. Kept separate from `net_pnl` all the way out (brief §11)."""
        return self.inner.gross_pnl

    @property
    def net_pnl(self) -> Decimal:
        return self.inner.net_pnl

    @property
    def unmarked_instruments(self) -> tuple[int, ...]:
        """Positions excluded from unrealized P&L for want of a mark."""
        return self.inner.unmarked_instruments

    @property
    def equity(self) -> Decimal:
        return self.inner.equity

    def as_dict(self) -> dict[str, Any]:
        body = dict(self.inner.as_dict())
        body.update(
            {
                "account_id": self.account_id,
                "mode": "PAPER",
                "reserved_cash": str(self.reserved_cash),
                "available_cash": str(self.available_cash),
                "equity": str(self.equity),
            }
        )
        return body


class PaperLedger:
    """One account's book. Deterministic and idempotent by delegation.

    Not a pure data structure, for the same reason the Phase 7 ledger is not:
    rebuilding the whole position book per fill would make the cost of correctness
    quadratic. Determinism is preserved because application order is fill order and
    every fill carries a content-addressed key.
    """

    def __init__(self, account_id: str, *, opening_cash: Decimal) -> None:
        self._account_id = account_id
        self._inner = Ledger(opening_cash=opening_cash)
        #: order_id -> reserved amount. Keyed by order so a cancel releases exactly
        #: what that order committed, rather than a recomputed estimate.
        self._reservations: dict[str, Decimal] = {}

    # ------------------------------------------------------------------ reading

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def cash(self) -> Decimal:
        return self._inner.cash

    @property
    def reserved_cash(self) -> Decimal:
        return sum(self._reservations.values(), Decimal(0))

    @property
    def available_cash(self) -> Decimal:
        return self._inner.cash - self.reserved_cash

    @property
    def opening_cash(self) -> Decimal:
        return self._inner.opening_cash

    @property
    def realized_pnl(self) -> Decimal:
        return self._inner.realized_pnl

    @property
    def fees(self) -> Decimal:
        return self._inner.fees

    @property
    def fills_applied(self) -> int:
        return self._inner.fills_applied

    @property
    def duplicate_fills_ignored(self) -> int:
        """Exposed so a run can *report* idempotency rather than merely rely on it."""
        return self._inner.duplicate_fills_ignored

    def position(self, instrument_id: int) -> Position | None:
        return self._inner.position(instrument_id)

    def positions(self) -> tuple[Position, ...]:
        return self._inner.positions()

    def has_applied(self, fill: Fill) -> bool:
        """Whether this exact fill is already in the book. Delegated, never mirrored."""
        return self._inner.has_applied(fill)

    # --------------------------------------------------------------- reservation

    def reserve(self, order_id: str, amount: Decimal) -> None:
        """Commit cash against a resting order.

        Idempotent per order: re-reserving the same order replaces its reservation
        rather than adding a second, so a retried acceptance cannot double-commit.
        """
        if amount < 0:
            raise ValueError("cannot reserve a negative amount")
        prospective = self.reserved_cash - self._reservations.get(order_id, Decimal(0)) + amount
        if prospective > self._inner.cash:
            raise InsufficientCash(
                f"reserving {amount} for order {order_id} would commit {prospective} "
                f"against {self._inner.cash} cash"
            )
        self._reservations[order_id] = amount

    def release(self, order_id: str) -> Decimal:
        """Release an order's reservation. Returns what was released, 0 if none.

        Safe to call twice — a cancel that is retried after a restart must not
        release cash the account never committed.
        """
        return self._reservations.pop(order_id, Decimal(0))

    def reservation_for(self, order_id: str) -> Decimal:
        return self._reservations.get(order_id, Decimal(0))

    # ------------------------------------------------------------------ writing

    def apply(self, fill: Fill, *, release_order_id: str | None = None) -> bool:
        """Apply a fill. Returns False if it was a duplicate and was ignored.

        The reservation is released **only when the fill is genuinely new**. A
        redelivered fill must not release cash a second time, which is the subtle
        way an idempotent-looking ledger still drifts.
        """
        applied = self._inner.apply(fill)
        if applied and release_order_id is not None:
            self.release(release_order_id)
        return applied

    def apply_all(self, fills: Iterable[Fill]) -> int:
        return sum(1 for fill in fills if self.apply(fill))

    # --------------------------------------------------------------- snapshotting

    def snapshot(self, *, as_of: datetime, marks: Mapping[int, Decimal]) -> PaperLedgerSnapshot:
        return PaperLedgerSnapshot(
            account_id=self._account_id,
            inner=self._inner.snapshot(as_of=as_of, marks=marks),
            reserved_cash=self.reserved_cash,
        )

    def can_afford(self, *, side: Side, price: Decimal, quantity: int, costs: Decimal) -> bool:
        """Affordability against **available** cash, not total.

        A sell is always affordable in a paper account: it releases cash rather than
        consuming it. Short selling is therefore permitted and the resulting negative
        position is tracked honestly; margin is a Phase 9 risk concern, and pretending
        to enforce it here would be a limit nobody declared.
        """
        if side is Side.SELL:
            return True
        return price * Decimal(quantity) + costs <= self.available_cash


def replay_fills(account_id: str, *, opening_cash: Decimal, fills: Sequence[Fill]) -> PaperLedger:
    """Rebuild an account's book from its authoritative fills.

    §12 of the Phase 8 brief: a paper account must be reconstructable from its
    events, and replaying the same stream must produce an identical state. This is
    the *only* reconstruction path — there is no separate "recovery" implementation
    that could disagree with the live fold, because it is the same `apply` loop.

    Reservations are deliberately **not** reconstructed: they are a property of
    currently-open orders, not of history, and an order that was resting when the
    process died is either still open (and re-reserved when it is reloaded) or
    terminal (and owed nothing).
    """
    ledger = PaperLedger(account_id, opening_cash=opening_cash)
    for fill in fills:
        ledger.apply(fill)
    return ledger
