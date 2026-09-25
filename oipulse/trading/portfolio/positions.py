"""Position identity and the deterministic position book.

`11-TRADING.md` §8:

> Positions are derived from **fill events**, not maintained as a running total that
> can drift. A position is a fold over its fills, so it is always reconstructable and
> always reconcilable.

### Identity (brief §5)

`PositionKey` is `(account_id, portfolio_id, instrument_id)`. Four things are
deliberately **not** in it:

* **Side.** A position is signed; long and short in the same instrument are one
  position that can cross zero, not two that can both exist. Keying on side would let
  an account hold +10 and -10 simultaneously and call itself flat in neither.
* **Strategy.** Ownership is *attribution*, not identity. Two strategies trading the
  same instrument in one account hold one position between them; splitting the key by
  strategy would produce two positions the broker cannot distinguish and
  reconciliation could never match. Strategy contribution is tracked separately, in
  `attribution.py`.
* **Lot.** The cost-basis method is weighted average (see below), which has no lots.
* **Underlying.** Derived from the instrument, not part of its identity.

### Cost basis: weighted average, and why that is not a silent choice

Brief §7 forbids silently picking FIFO, LIFO, average cost or specific
identification. The methodology is **not** chosen here — it is inherited:
`11` §8 gives `Position` an `average_price` field, and the verified Phase 7 `Ledger`
already implements weighted-average cost with realised P&L struck against the existing
basis on reduction. This module **reuses that implementation** rather than writing a
second one, so a portfolio position and a backtest position cannot disagree about what
a trade cost.

The consequence is stated rather than hidden: with average cost there are no tax lots,
so specific-identification questions are unanswerable and `POSITION_LOTS` does not
exist. That is a limitation of the inherited method, recorded in the report.

### Determinism (brief §6, §23)

Fills are folded in a declared order — by the content-addressed `fill_key`, not by
arrival or by database row order — and the same fill applied twice is ignored, because
the underlying `Ledger` is keyed on that content address. Reordering the input cannot
change the resulting book.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.backtest.fills import Fill
from oipulse.backtest.ledger import Ledger, Position, fill_key
from oipulse.trading.portfolio.economics import ContractEconomics

__all__ = [
    "CostBasisMethod",
    "PortfolioPosition",
    "PositionBook",
    "PositionKey",
    "PositionStatus",
]


class CostBasisMethod(StrEnum):
    """Declared so the method is on the record, not inferred from behaviour."""

    #: Inherited from the verified Phase 7 ledger. No tax lots exist.
    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    #: Quantity is zero. Retained rather than deleted: its realised P&L is real and
    #: a closed position is part of the day's attribution.
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class PositionKey:
    """`(account, portfolio, instrument)`. Signed quantity, so no side in the key."""

    account_id: str
    portfolio_id: str
    instrument_id: int

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("a position must name its account")
        if not self.portfolio_id:
            raise ValueError("a position must name its portfolio")

    @property
    def sort_key(self) -> tuple[str, str, int]:
        return (self.account_id, self.portfolio_id, self.instrument_id)

    @property
    def position_id(self) -> str:
        """Deterministic identity. Same key, same id, in any process."""
        digest = hashlib.sha256(
            f"{self.account_id}|{self.portfolio_id}|{self.instrument_id}".encode()
        ).hexdigest()[:24]
        return f"pos_{digest}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "account_id": self.account_id,
            "portfolio_id": self.portfolio_id,
            "instrument_id": self.instrument_id,
        }


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    """One position, with its economics and its provenance.

    Wraps the verified Phase 7 `Position` rather than restating its arithmetic. The
    additions are the portfolio-layer concerns: which account and portfolio, which
    contract economics were in force, and how many fills produced it.
    """

    key: PositionKey
    inner: Position
    #: None when the instrument version could not be resolved at the valuation time.
    #: A position with no economics is reported unvalued, never valued on a guess.
    economics: ContractEconomics | None = None
    realized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    opened_at: datetime | None = None
    last_fill_at: datetime | None = None
    #: Provenance of the fold (`11` §8: never a running total that can drift).
    fills_applied: int = 0
    last_fill_key: str = ""
    underlying_id: int | None = None
    expiry_id: int | None = None

    @property
    def quantity(self) -> int:
        return self.inner.quantity

    @property
    def average_price(self) -> Decimal:
        return self.inner.average_price

    @property
    def status(self) -> PositionStatus:
        return PositionStatus.CLOSED if self.quantity == 0 else PositionStatus.OPEN

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def cost_basis(self) -> Decimal | None:
        """Total cost of the open quantity. None without economics."""
        if self.economics is None:
            return None
        return self.economics.notional(self.average_price, self.quantity)

    def market_value(self, mark: Decimal | None) -> Decimal | None:
        """Economic value at `mark`. None when the mark or the economics is absent.

        Two separate reasons to be unvaluable, and both produce `None` rather than a
        number: a missing price and missing contract economics are equally fatal to a
        valuation, and substituting either would be inventing one.
        """
        if mark is None or self.economics is None:
            return None
        return self.economics.notional(mark, self.quantity)

    def unrealized_pnl(self, mark: Decimal | None) -> Decimal | None:
        if mark is None or self.economics is None:
            return None
        return self.economics.notional(mark - self.average_price, self.quantity)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.key.as_dict(),
            "quantity": self.quantity,
            "average_price": str(self.average_price),
            "status": self.status.value,
            "realized_pnl": str(self.realized_pnl),
            "fees": str(self.fees),
            "cost_basis": None if self.cost_basis is None else str(self.cost_basis),
            "economics": None if self.economics is None else self.economics.as_dict(),
            "opened_at": None if self.opened_at is None else self.opened_at.isoformat(),
            "last_fill_at": (None if self.last_fill_at is None else self.last_fill_at.isoformat()),
            "fills_applied": self.fills_applied,
            "last_fill_key": self.last_fill_key,
            "underlying_id": self.underlying_id,
            "expiry_id": self.expiry_id,
            "cost_basis_method": CostBasisMethod.WEIGHTED_AVERAGE.value,
        }


@dataclass
class PositionBook:
    """A fold over fills, per `(account, portfolio, instrument)`. Deterministic.

    Holds one verified `Ledger` per `(account, portfolio)` so the average-cost
    arithmetic, the realised-P&L-on-reduction rule and the content-addressed
    idempotency all come from code that Phases 7 to 10 already exercise.
    """

    cost_basis_method: CostBasisMethod = CostBasisMethod.WEIGHTED_AVERAGE
    _ledgers: dict[tuple[str, str], Ledger] = field(default_factory=dict, repr=False)
    _meta: dict[PositionKey, dict[str, Any]] = field(default_factory=dict, repr=False)
    _applied: set[str] = field(default_factory=set, repr=False)
    _duplicates: int = field(default=0, repr=False)

    @property
    def duplicate_fills_ignored(self) -> int:
        """Reported, so idempotency is demonstrable rather than merely relied on."""
        return self._duplicates

    @property
    def fills_applied(self) -> int:
        return len(self._applied)

    def _ledger(self, account_id: str, portfolio_id: str) -> Ledger:
        scope = (account_id, portfolio_id)
        if scope not in self._ledgers:
            # Opening cash is a portfolio-state concern, not a position one; the
            # book only folds quantities and bases.
            self._ledgers[scope] = Ledger(opening_cash=Decimal(0))
        return self._ledgers[scope]

    def apply(
        self,
        fill: Fill,
        *,
        account_id: str,
        portfolio_id: str,
        at: datetime,
        underlying_id: int | None = None,
        expiry_id: int | None = None,
    ) -> bool:
        """Fold one fill in. Returns False if it was a duplicate and was ignored.

        Idempotency is keyed on the Phase 7 content-addressed `fill_key`, so the
        same execution reported twice — by a reconnect, a reconciliation re-run or a
        restart — moves nothing.
        """
        key = fill_key(fill)
        if key in self._applied:
            self._duplicates += 1
            return False

        ledger = self._ledger(account_id, portfolio_id)
        if not ledger.apply(fill):  # pragma: no cover - the set above already caught it
            self._duplicates += 1
            return False
        self._applied.add(key)

        position_key = PositionKey(
            account_id=account_id,
            portfolio_id=portfolio_id,
            instrument_id=fill.instrument_id,
        )
        meta = self._meta.setdefault(
            position_key,
            {
                "opened_at": at,
                "last_fill_at": at,
                "fills_applied": 0,
                "last_fill_key": "",
                "fees": Decimal(0),
                "underlying_id": underlying_id,
                "expiry_id": expiry_id,
            },
        )
        meta["last_fill_at"] = at
        meta["fills_applied"] += 1
        meta["last_fill_key"] = key
        meta["fees"] += fill.costs.total
        if underlying_id is not None:
            meta["underlying_id"] = underlying_id
        if expiry_id is not None:
            meta["expiry_id"] = expiry_id
        return True

    def apply_all(
        self, fills: Iterable[Fill], *, account_id: str, portfolio_id: str, at: datetime
    ) -> int:
        """Fold a batch in a declared order.

        Sorted by the content-addressed fill key, **not** by arrival or row order.
        Brief §6 and §23 require the book to be independent of input order, and
        average-cost arithmetic is order-sensitive, so the order has to be imposed
        rather than assumed.
        """
        return sum(
            1
            for fill in sorted(fills, key=fill_key)
            if self.apply(fill, account_id=account_id, portfolio_id=portfolio_id, at=at)
        )

    def realized_pnl(self, account_id: str, portfolio_id: str) -> Decimal:
        return self._ledger(account_id, portfolio_id).realized_pnl

    def fees(self, account_id: str, portfolio_id: str) -> Decimal:
        return self._ledger(account_id, portfolio_id).fees

    def positions(
        self,
        *,
        economics: Mapping[int, ContractEconomics] | None = None,
        include_closed: bool = False,
    ) -> Sequence[PortfolioPosition]:
        """Every position, sorted by key. Never dependent on insertion order."""
        economics = economics or {}
        out: list[PortfolioPosition] = []
        for (account_id, portfolio_id), ledger in sorted(self._ledgers.items()):
            # `all_positions()` when closed ones are wanted: the verified ledger
            # filters flat positions out of `positions()`, and a portfolio needs
            # them for realised P&L and attribution.
            source = ledger.all_positions() if include_closed else ledger.positions()
            for inner in source:
                key = PositionKey(
                    account_id=account_id,
                    portfolio_id=portfolio_id,
                    instrument_id=inner.instrument_id,
                )
                meta = self._meta.get(key, {})
                out.append(
                    PortfolioPosition(
                        key=key,
                        inner=inner,
                        economics=economics.get(inner.instrument_id),
                        realized_pnl=ledger.realized_pnl,
                        fees=meta.get("fees", Decimal(0)),
                        opened_at=meta.get("opened_at"),
                        last_fill_at=meta.get("last_fill_at"),
                        fills_applied=meta.get("fills_applied", 0),
                        last_fill_key=meta.get("last_fill_key", ""),
                        underlying_id=meta.get("underlying_id"),
                        expiry_id=meta.get("expiry_id"),
                    )
                )
        return tuple(sorted(out, key=lambda p: p.key.sort_key))

    def position(
        self, key: PositionKey, *, economics: ContractEconomics | None = None
    ) -> PortfolioPosition | None:
        for candidate in self.positions(
            economics={key.instrument_id: economics} if economics else None,
            include_closed=True,
        ):
            if candidate.key == key:
                return candidate
        return None
