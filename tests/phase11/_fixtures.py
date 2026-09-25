"""SYNTHETIC fixtures for Phase 11. Not recorded from Upstox.

Everything here is constructed, and the module name and this docstring say so.
Recorded provider captures live in `tests/fixtures/recorded/` and cover market data
only.

Built on the Phase 3 fixtures and the Phase 8 runtime deliberately: the portfolio
layer must fold the *canonical* fills the rest of the system produces, and a
test-only fill would prove nothing about that.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from oipulse.backtest.costs import CostBreakdown
from oipulse.backtest.fills import Fill, PriceSource, SlippageModel
from oipulse.backtest.intents import Side
from oipulse.core.ids import InstrumentId
from oipulse.instruments.models import Exchange, InstrumentVersion
from oipulse.marketdata.store.memory import InMemoryObservationStore
from oipulse.marketstate.checkpoints import StateService
from oipulse.marketstate.state import MarketState
from oipulse.trading.brokers.protocol import BrokerPosition
from oipulse.trading.portfolio import (
    ContractEconomics,
    GreekInputs,
    PortfolioGreeks,
    PortfolioSnapshot,
    PositionBook,
    PositionKey,
    PositionReconciler,
    ReturnInputs,
    resolve_economics,
    value_positions,
)
from tests.phase3._fixtures import (
    UNDERLYING,
    at,
    builder,
    greeks_obs,
    index_obs,
    leg_id,
    quote_obs,
)

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

ACCOUNT_ID = "acc-portfolio"
PORTFOLIO_ID = "pf-1"
TARGET = leg_id(0, 1, True)
OTHER = leg_id(0, 2, True)
UNDERLYING_ID = int(UNDERLYING)
EXPIRY_ID = 10

#: A lot-size revision partway through, so `07` §4.3's rule is exercisable rather
#: than merely asserted: a valuation before the revision must see the old size.
LOT_SIZE_BEFORE = 50
LOT_SIZE_AFTER = 75


def observations(*, minutes: int = 4, base_price: int = 100, step: int = 5) -> list[Any]:
    rows: list[Any] = []
    for minute in range(minutes):
        moment = at(minute)
        rows.append(index_obs(moment))
        for strike_index in range(3):
            for is_call in (True, False):
                instrument = leg_id(0, strike_index, is_call)
                rows.append(
                    quote_obs(
                        instrument,
                        moment,
                        ltp=str(base_price + minute * step + strike_index),
                    )
                )
                rows.append(greeks_obs(instrument, moment))
    return rows


def state_at(rows: list[Any], minute: int, *, knowledge_minute: int | None = None) -> MarketState:
    """A canonical state at `(T, K)`. The only price source valuation may use."""
    store = InMemoryObservationStore()
    store.append(rows)
    service = StateService(builder(store))
    return service.get_state(
        UNDERLYING,
        at(minute),
        at(knowledge_minute if knowledge_minute is not None else minute),
    )


def instrument_versions(
    *, instrument_id: int = TARGET, revision_minute: int = 2, multiplier: str | None = None
) -> list[InstrumentVersion]:
    """Two non-overlapping versions with different lot sizes.

    `multiplier`, when given, is placed in `contract_attributes` so the
    `INSTRUMENT_METADATA` path is exercised rather than only the declared default.
    """
    attributes = {} if multiplier is None else {"contract_multiplier": multiplier}
    return [
        InstrumentVersion(
            instrument_id=InstrumentId(instrument_id),
            valid_from=at(0),
            valid_to=at(revision_minute),
            symbol="NIFTY-C",
            exchange=Exchange.NSE,
            lot_size=LOT_SIZE_BEFORE,
            tick_size=Decimal("0.05"),
            contract_attributes=attributes,
        ),
        InstrumentVersion(
            instrument_id=InstrumentId(instrument_id),
            valid_from=at(revision_minute),
            valid_to=None,
            symbol="NIFTY-C",
            exchange=Exchange.NSE,
            lot_size=LOT_SIZE_AFTER,
            tick_size=Decimal("0.05"),
            contract_attributes=attributes,
        ),
    ]


def economics(
    *, instrument_id: int = TARGET, minute: int = 1, multiplier: str | None = None
) -> ContractEconomics:
    resolved = resolve_economics(
        instrument_id,
        instrument_versions(instrument_id=instrument_id, multiplier=multiplier),
        at=at(minute),
    )
    assert resolved is not None
    return resolved


def fill(
    *,
    instrument_id: int = TARGET,
    side: Side = Side.BUY,
    quantity: int = 50,
    price: str = "100",
    fees: str = "0",
    tag: str = "",
) -> Fill:
    """A canonical Phase 7 fill. The portfolio folds these, never a bespoke type."""
    costs = CostBreakdown(brokerage=Decimal(fees))
    return Fill(
        intent_id=f"ti_{instrument_id}{side}{quantity}{price}{tag}",
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        requested_quantity=quantity,
        price=Decimal(price),
        reference_price=Decimal(price),
        filled_at=at(1),
        price_source=PriceSource.OBSERVED_QUOTE,
        slippage_model=SlippageModel.MID,
        costs=costs,
        assumption_based=False,
    )


def book(fills: list[Fill] | None = None, *, minute: int = 1) -> PositionBook:
    resolved = PositionBook()
    for one in fills if fills is not None else [fill()]:
        resolved.apply(
            one,
            account_id=ACCOUNT_ID,
            portfolio_id=PORTFOLIO_ID,
            at=at(minute),
            underlying_id=UNDERLYING_ID,
            expiry_id=EXPIRY_ID,
        )
    return resolved


def key(instrument_id: int = TARGET) -> PositionKey:
    return PositionKey(
        account_id=ACCOUNT_ID, portfolio_id=PORTFOLIO_ID, instrument_id=instrument_id
    )


def valued(
    *,
    fills: list[Fill] | None = None,
    rows: list[Any] | None = None,
    valuation_minute: int = 3,
    econ_minute: int = 1,
    **kwargs: Any,
) -> Any:
    resolved_rows = rows if rows is not None else observations()
    positions = book(fills).positions(economics={TARGET: economics(minute=econ_minute)})
    return value_positions(positions, state_at(resolved_rows, valuation_minute), **kwargs)


def snapshot(
    *,
    cash: str = "500000",
    starting_capital: str = "500000",
    greeks: PortfolioGreeks | None = None,
    **kwargs: Any,
) -> PortfolioSnapshot:
    valuation = valued(**kwargs)
    return PortfolioSnapshot(
        account_id=ACCOUNT_ID,
        portfolio_id=PORTFOLIO_ID,
        market_time=valuation.market_time,
        knowledge_time=valuation.knowledge_time,
        build_context_id=valuation.build_context_id,
        cash=Decimal(cash),
        valuation=valuation,
        returns=ReturnInputs(
            starting_capital=Decimal(starting_capital),
            ending_equity=Decimal(cash) + valuation.total_market_value,
            unrealized_pnl=valuation.total_unrealized_pnl,
        ),
        greeks=greeks or PortfolioGreeks(),
    )


def broker_position(
    *, instrument_id: int = TARGET, quantity: int = 50, average_price: str = "100"
) -> BrokerPosition:
    return BrokerPosition(
        instrument_id=instrument_id,
        quantity=quantity,
        average_price=Decimal(average_price),
        provider_time=at(3),
    )


def reconciler() -> PositionReconciler:
    return PositionReconciler()


def greek_inputs(**overrides: Any) -> GreekInputs:
    """A fully-populated set, so a test removing one field isolates that removal."""
    base: dict[str, Any] = {
        "delta": Decimal("50"),
        "gamma": Decimal("2"),
        "vega": Decimal("100"),
        "theta": Decimal("-20"),
        "underlying_move": Decimal("10"),
        "implied_vol_move": Decimal("0.01"),
        "time_elapsed_days": Decimal("1"),
    }
    base.update(overrides)
    return GreekInputs(**base)


__all__ = [
    "ACCOUNT_ID",
    "EXPIRY_ID",
    "LOT_SIZE_AFTER",
    "LOT_SIZE_BEFORE",
    "OTHER",
    "PORTFOLIO_ID",
    "PROVENANCE",
    "TARGET",
    "UNDERLYING_ID",
    "at",
    "book",
    "broker_position",
    "economics",
    "fill",
    "greek_inputs",
    "instrument_versions",
    "key",
    "observations",
    "reconciler",
    "snapshot",
    "state_at",
    "valued",
]
