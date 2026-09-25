"""`PortfolioSnapshot` — the content-addressed portfolio state at one instant.

`11-TRADING.md` §8:

> Taken on fill and on a timer during market hours: positions · realized and
> unrealized P&L · gross and net exposure · margin utilization · portfolio delta,
> gamma, theta, vega · concentration · drawdown.

Every one of those is here and nothing else is. Brief §8: *"Do not introduce
unsupported portfolio metrics."* There is no Sharpe ratio, no beta, no VaR — none is
in the design, and each would be a methodology choice nobody made.

### Margin comes from Phase 9, not from a new model

Brief §22: *"Do not implement a full new risk engine. Use Phase 9 risk outputs where
appropriate."* Margin utilisation is therefore `deployed capital / the policy's
declared capital limit`, both of which Phase 9 already produces. When no policy is
supplied it is `None` — **not zero, and not a SPAN calculation invented here**. A
real margin model is exchange-specific and was never specified.

### Return methodology

Brief §11 forbids inventing time-weighted or money-weighted return where the
specification does not define one. The design defines neither, so this reports the
inputs a return is computed *from* — starting capital, ending equity, net cash flows,
realised and unrealised P&L, fees — and a **simple period return** whose formula is
stated on the field. It does not claim to be TWR or MWR, because choosing between
them in the presence of cash flows is exactly the methodology decision nobody made.

### Content identity (brief §24)

`content_digest` covers the valuation, the positions, the P&L and the configuration
needed to reproduce them. It excludes `computed_at` and `snapshot_id`: when the
arithmetic ran is not part of what it found.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.trading.portfolio.valuation import ValuationResult

__all__ = ["PortfolioGreeks", "PortfolioSnapshot", "ReturnInputs", "concentration_of"]


@dataclass(frozen=True, slots=True)
class PortfolioGreeks:
    """Aggregated position greeks. Every field independently absent.

    Supplied by the caller from the canonical Phase 4 features and the instrument
    version valid at the valuation time (`07` §4.3, `11` §8). **Not computed here** —
    brief §29 forbids the portfolio layer from recomputing analytics, and a second
    greek implementation would drift from the first.
    """

    delta: Decimal | None = None
    gamma: Decimal | None = None
    vega: Decimal | None = None
    theta: Decimal | None = None
    #: How many positions contributed. A total aggregated over three of ten
    #: positions is not a portfolio greek, and this is what says so.
    positions_included: int = 0
    positions_total: int = 0

    @property
    def is_complete(self) -> bool:
        return self.positions_total > 0 and self.positions_included == self.positions_total

    def as_dict(self) -> dict[str, Any]:
        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "delta": num(self.delta),
            "gamma": num(self.gamma),
            "vega": num(self.vega),
            "theta": num(self.theta),
            "positions_included": self.positions_included,
            "positions_total": self.positions_total,
            "is_complete": self.is_complete,
        }


@dataclass(frozen=True, slots=True)
class ReturnInputs:
    """The inputs a return is computed from (brief §11).

    Deliberately *inputs* plus one clearly-labelled simple return, rather than a
    TWR or MWR figure. The design defines no return methodology, and picking one
    silently would be a methodology choice presented as a fact.
    """

    starting_capital: Decimal
    ending_equity: Decimal
    #: Deposits less withdrawals over the period. Zero for a paper account that
    #: neither, which is stated rather than assumed.
    net_cash_flows: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)

    @property
    def gross_pnl(self) -> Decimal:
        """Before fees. Kept separate all the way out (brief §17)."""
        return self.realized_pnl + self.unrealized_pnl

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees

    @property
    def simple_period_return(self) -> Decimal | None:
        """`(ending - starting - net_cash_flows) / starting`. Not TWR, not MWR.

        Named `simple_period_return` so no caller can mistake it for a
        methodology the design never chose. `None` when starting capital is zero,
        because a return on nothing is undefined rather than infinite.
        """
        if self.starting_capital == 0:
            return None
        return (
            self.ending_equity - self.starting_capital - self.net_cash_flows
        ) / self.starting_capital

    def as_dict(self) -> dict[str, Any]:
        return {
            "starting_capital": str(self.starting_capital),
            "ending_equity": str(self.ending_equity),
            "net_cash_flows": str(self.net_cash_flows),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "gross_pnl": str(self.gross_pnl),
            "fees": str(self.fees),
            "net_pnl": str(self.net_pnl),
            "simple_period_return": (
                None if self.simple_period_return is None else str(self.simple_period_return)
            ),
            "return_methodology": "SIMPLE_PERIOD",
        }


def concentration_of(valuation: ValuationResult) -> tuple[tuple[str, Decimal], ...]:
    """Share of gross exposure per underlying. Sorted, so identity is stable.

    Computed over **valued** positions only, which is why `is_complete` travels
    with every snapshot: a concentration measured over a partially-valued book
    understates whichever position could not be priced.
    """
    gross = valuation.gross_exposure
    if gross <= 0:
        return ()
    buckets: dict[str, Decimal] = {}
    for valued in valuation.valued:
        underlying = valued.position.underlying_id
        key = "UNKNOWN" if underlying is None else str(underlying)
        buckets[key] = buckets.get(key, Decimal(0)) + abs(valued.market_value or Decimal(0))
    return tuple(sorted((key, value / gross) for key, value in buckets.items()))


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Portfolio state at one `(market_time, knowledge_time)`. Content-addressed."""

    account_id: str
    portfolio_id: str
    market_time: datetime
    knowledge_time: datetime
    build_context_id: str
    cash: Decimal
    valuation: ValuationResult
    returns: ReturnInputs
    greeks: PortfolioGreeks = field(default_factory=PortfolioGreeks)
    #: `deployed / limit`, both from Phase 9. None when no policy was supplied —
    #: never zero, and never an invented margin model (brief §22).
    margin_utilisation: Decimal | None = None
    margin_basis: str = "NOT_AVAILABLE"
    #: Positive magnitude of the drawdown from peak equity.
    drawdown: Decimal = Decimal(0)
    peak_equity: Decimal | None = None
    #: Execution metadata. Excluded from the digest by living apart from it.
    computed_at: datetime | None = None
    snapshot_id: str = ""

    @property
    def equity(self) -> Decimal:
        """Cash plus the marked value of the book.

        Partial whenever the valuation is: `is_complete` is carried on every
        response for exactly that reason.
        """
        return self.cash + self.valuation.total_market_value

    @property
    def concentration(self) -> tuple[tuple[str, Decimal], ...]:
        return concentration_of(self.valuation)

    @property
    def is_complete(self) -> bool:
        return self.valuation.is_complete

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "portfolio_id": self.portfolio_id,
            # Both times, always. A snapshot at T built on knowledge to K is not
            # the same object as one built on latest knowledge (brief §10).
            "market_time": self.market_time.isoformat(),
            "knowledge_time": self.knowledge_time.isoformat(),
            "build_context_id": self.build_context_id,
            "cash": str(self.cash),
            "equity": str(self.equity),
            "gross_exposure": str(self.valuation.gross_exposure),
            "net_exposure": str(self.valuation.net_exposure),
            "market_value": str(self.valuation.total_market_value),
            "realized_pnl": str(self.returns.realized_pnl),
            "unrealized_pnl": str(self.valuation.total_unrealized_pnl),
            "fees": str(self.returns.fees),
            "returns": self.returns.as_dict(),
            "greeks": self.greeks.as_dict(),
            "margin_utilisation": (
                None if self.margin_utilisation is None else str(self.margin_utilisation)
            ),
            "margin_basis": self.margin_basis,
            "drawdown": str(self.drawdown),
            "peak_equity": None if self.peak_equity is None else str(self.peak_equity),
            "concentration": {key: str(value) for key, value in self.concentration},
            "valuation": self.valuation.as_dict(),
            "is_complete": self.is_complete,
            "unvalued_instruments": [p.position.key.instrument_id for p in self.valuation.unvalued],
        }

    @property
    def content_digest(self) -> str:
        """Semantic identity (brief §24).

        Excludes `computed_at` and `snapshot_id`: when the arithmetic ran is not
        part of what it found. Two snapshots of the same book at the same
        `(T, K, BuildContext)` therefore share a digest, whatever process produced
        them or when.
        """
        body = self.as_dict()
        return (
            "psnap_"
            + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:32]
        )


def drawdown_from(peak_equity: Decimal | None, equity: Decimal) -> Decimal:
    """Positive magnitude of the fall from peak. Zero at or above it."""
    if peak_equity is None:
        return Decimal(0)
    return max(peak_equity - equity, Decimal(0))


def margin_utilisation_from(
    deployed_capital: Decimal | None, capital_limit: Decimal | None
) -> tuple[Decimal | None, str]:
    """`(utilisation, basis)` from Phase 9 outputs. `None` when either is absent.

    Returns the *basis* alongside the number so a reader knows what it is a
    fraction of. A utilisation with no stated denominator is not interpretable,
    and an absent one must not read as zero utilisation (brief §22).
    """
    if deployed_capital is None or capital_limit is None:
        return (None, "NOT_AVAILABLE")
    if capital_limit <= 0:
        return (None, "NOT_AVAILABLE")
    return (deployed_capital / capital_limit, "RISK_POLICY_MAX_DEPLOYED_CAPITAL")


__all__ += ["drawdown_from", "margin_utilisation_from"]
