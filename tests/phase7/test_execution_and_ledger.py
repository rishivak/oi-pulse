"""Fill simulation, order/fill determinism, the cost model and the ledger.

Brief §11, §13, §14 and §15. `10-REPLAY.md` §6:

> Where real bid/ask exists in the observation store, it is used. Where it does not
> ... the model must use a declared assumption, and **the run is flagged as
> assumption-based** so its results are never read as equivalent to a run over
> full-fidelity data.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from oipulse.backtest.costs import INDIAN_OPTIONS_COSTS, CostBreakdown
from oipulse.backtest.fills import (
    Fill,
    PriceSource,
    RejectionReason,
    SlippageModel,
    simulate_fill,
)
from oipulse.backtest.intents import OrderType, Side, TradeIntent
from oipulse.backtest.ledger import Ledger, fill_key
from oipulse.backtest.runner import BacktestRunner
from oipulse.research.access import PointInTimeAccessor
from tests.phase3._fixtures import at
from tests.phase7 import _fixtures as fx


def _intent(
    *,
    side: Side = Side.BUY,
    quantity: int = 50,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
    decision_time=None,
) -> TradeIntent:
    return TradeIntent(
        run_id="run-test",
        strategy_id="S",
        strategy_version=1,
        instrument_id=fx.TARGET,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        decision_time=decision_time or at(0),
        knowledge_horizon=decision_time or at(0),
    )


def _state(rows: list[object], minute: int = 1):
    tl = fx.timeline(rows)
    return fx.engine(rows).state_at(tl.context, tl.steps[minute], 100).state


def _fill(*, side: Side = Side.BUY, quantity: int = 10, price: str = "100", tag: str = "") -> Fill:
    return Fill(
        intent_id=f"ti_{side}{quantity}{price}{tag}",
        instrument_id=fx.TARGET,
        side=side,
        quantity=quantity,
        requested_quantity=quantity,
        price=Decimal(price),
        reference_price=Decimal(price),
        filled_at=at(1),
        price_source=PriceSource.OBSERVED_QUOTE,
        slippage_model=SlippageModel.MID,
        costs=CostBreakdown(),
        assumption_based=False,
    )


class TestPriceSourceAndAssumptions(unittest.TestCase):
    def test_an_observed_quote_is_used_and_not_flagged(self) -> None:
        rows = fx.observations()
        outcome = simulate_fill(_intent(), _state(rows), fx.fill_model())
        self.assertIsNotNone(outcome.fill)
        assert outcome.fill is not None
        self.assertIs(outcome.fill.price_source, PriceSource.OBSERVED_QUOTE)
        self.assertFalse(outcome.fill.assumption_based)

    def test_a_missing_quote_falls_back_to_a_declared_assumption_and_is_flagged(self) -> None:
        """`10` §6: a backfill-only period must never be read as full fidelity."""
        rows = fx.observations(with_quotes=False)
        outcome = simulate_fill(_intent(), _state(rows), fx.fill_model())
        assert outcome.fill is not None
        self.assertIs(outcome.fill.price_source, PriceSource.ASSUMED_SPREAD_AROUND_LTP)
        self.assertTrue(outcome.fill.assumption_based)

    def test_the_assumption_flag_propagates_to_the_run_and_its_caveats(self) -> None:
        rows = fx.observations(with_quotes=False)
        result = BacktestRunner(
            fx.engine(rows), fill_model=fx.fill_model(), opening_cash=Decimal(500000)
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())
        self.assertTrue(result.assumption_based)
        self.assertEqual(result.statistics.assumption_based_fills, 1)
        self.assertTrue(any("assumed spread" in c for c in result.caveats()))

    def test_no_price_at_all_is_a_rejection_not_an_invented_fill(self) -> None:
        rows = fx.observations()
        outcome = simulate_fill(
            TradeIntent(
                run_id="r",
                strategy_id="S",
                strategy_version=1,
                instrument_id=999999,
                side=Side.BUY,
                quantity=1,
                order_type=OrderType.MARKET,
                decision_time=at(0),
                knowledge_horizon=at(0),
            ),
            _state(rows),
            fx.fill_model(),
        )
        self.assertIsNone(outcome.fill)
        self.assertIs(outcome.rejection, RejectionReason.NO_PRICE_AVAILABLE)


class TestSlippageModels(unittest.TestCase):
    """Four declared models, each doing something distinguishable."""

    def _price(self, model: SlippageModel, side: Side, parameter: str = "0.5") -> Decimal:
        rows = fx.observations()
        outcome = simulate_fill(
            _intent(side=side),
            _state(rows),
            fx.fill_model(slippage_model=model, slippage_parameter=Decimal(parameter)),
        )
        assert outcome.fill is not None
        return outcome.fill.price

    def test_mid_pays_no_spread(self) -> None:
        buy = self._price(SlippageModel.MID, Side.BUY)
        sell = self._price(SlippageModel.MID, Side.SELL)
        self.assertEqual(buy, sell, "a mid fill is side-independent by definition")

    def test_touch_crosses_the_spread_in_the_costly_direction(self) -> None:
        self.assertGreater(
            self._price(SlippageModel.TOUCH, Side.BUY),
            self._price(SlippageModel.TOUCH, Side.SELL),
            "a buy must pay the ask and a sell receive the bid, never the reverse",
        )

    def test_slippage_is_always_against_the_trader(self) -> None:
        """A model that could improve a price would be a model of luck."""
        rows = fx.observations()
        for model in SlippageModel:
            for side in Side:
                with self.subTest(model=model, side=side):
                    outcome = simulate_fill(
                        _intent(side=side),
                        _state(rows),
                        fx.fill_model(slippage_model=model, slippage_parameter=Decimal("0.5")),
                    )
                    assert outcome.fill is not None
                    self.assertGreaterEqual(outcome.fill.slippage_per_unit, Decimal(0))

    def test_size_impact_grows_with_size(self) -> None:
        rows = fx.observations()
        model = fx.fill_model(
            slippage_model=SlippageModel.SIZE_IMPACT, slippage_parameter=Decimal("10")
        )
        small = simulate_fill(_intent(quantity=10), _state(rows), model)
        large = simulate_fill(_intent(quantity=6000), _state(rows), model)
        assert small.fill is not None and large.fill is not None
        self.assertGreater(large.fill.price, small.fill.price)


class TestRejectionsAndPartialFills(unittest.TestCase):
    def test_a_limit_beyond_the_market_is_refused_not_filled_at_market(self) -> None:
        rows = fx.observations()
        outcome = simulate_fill(
            _intent(order_type=OrderType.LIMIT, limit_price=Decimal("1")),
            _state(rows),
            fx.fill_model(),
        )
        self.assertIsNone(outcome.fill)
        self.assertIs(outcome.rejection, RejectionReason.LIMIT_NOT_MARKETABLE)

    def test_a_marketable_limit_fills_at_the_limit_price(self) -> None:
        rows = fx.observations()
        outcome = simulate_fill(
            _intent(order_type=OrderType.LIMIT, limit_price=Decimal("1000")),
            _state(rows),
            fx.fill_model(),
        )
        assert outcome.fill is not None
        self.assertEqual(outcome.fill.price, Decimal("1000"))

    def test_partial_fills_are_capped_by_the_declared_participation_rate(self) -> None:
        rows = fx.observations()
        outcome = simulate_fill(
            _intent(quantity=100000),
            _state(rows),
            fx.fill_model(partial_fills_enabled=True, max_participation_rate=Decimal("0.1")),
        )
        assert outcome.fill is not None
        self.assertTrue(outcome.fill.is_partial)
        self.assertEqual(outcome.fill.quantity, 1200, "10% of the observed 12,000 volume")

    def test_with_partial_fills_disabled_an_order_is_all_or_nothing(self) -> None:
        rows = fx.observations()
        outcome = simulate_fill(
            _intent(quantity=100000), _state(rows), fx.fill_model(partial_fills_enabled=False)
        )
        assert outcome.fill is not None
        self.assertFalse(outcome.fill.is_partial)

    def test_modelled_rejection_is_deterministic_not_random(self) -> None:
        """Brief §13: the same intent must always meet the same fate.

        A rerun must not be able to shop for a better outcome.
        """
        rows = fx.observations()
        model = fx.fill_model(rejection_rate=Decimal("1"))
        outcomes = [simulate_fill(_intent(), _state(rows), model) for _ in range(5)]
        self.assertTrue(all(o.rejection is RejectionReason.MODELLED_REJECTION for o in outcomes))

        partial = fx.fill_model(rejection_rate=Decimal("0.5"))
        verdicts = {simulate_fill(_intent(), _state(rows), partial).rejection for _ in range(10)}
        self.assertEqual(len(verdicts), 1, "the same intent drew different outcomes")


class TestCostModel(unittest.TestCase):
    def test_stt_falls_on_the_sell_and_stamp_duty_on_the_buy(self) -> None:
        """Side matters and is not averaged away."""
        buy = INDIAN_OPTIONS_COSTS.charge(turnover=Decimal(100000), is_buy=True)
        sell = INDIAN_OPTIONS_COSTS.charge(turnover=Decimal(100000), is_buy=False)
        self.assertEqual(buy.securities_transaction_tax, Decimal("0.00"))
        self.assertGreater(sell.securities_transaction_tax, Decimal(0))
        self.assertGreater(buy.stamp_duty, Decimal(0))
        self.assertEqual(sell.stamp_duty, Decimal("0.00"))

    def test_gst_applies_to_services_not_to_statutory_taxes(self) -> None:
        charge = INDIAN_OPTIONS_COSTS.charge(turnover=Decimal(100000), is_buy=False)
        expected = (
            charge.brokerage + charge.exchange_charges + charge.sebi_charges
        ) * INDIAN_OPTIONS_COSTS.gst_rate
        self.assertAlmostEqual(float(charge.gst), float(expected), places=2)

    def test_the_components_are_separable_and_sum_to_the_total(self) -> None:
        """Brief §14: gross, fees and slippage must stay distinct."""
        charge = INDIAN_OPTIONS_COSTS.charge(turnover=Decimal(50000), is_buy=True)
        parts = (
            charge.brokerage
            + charge.securities_transaction_tax
            + charge.exchange_charges
            + charge.gst
            + charge.stamp_duty
            + charge.sebi_charges
        )
        self.assertEqual(charge.total, parts)

    def test_the_schedule_is_versioned_and_states_its_basis(self) -> None:
        """A rate with no provenance is an assumption nobody can audit."""
        self.assertEqual(INDIAN_OPTIONS_COSTS.label, "INDIAN_OPTIONS_DISCOUNT@v1")
        self.assertIn("assumption", INDIAN_OPTIONS_COSTS.basis.lower())

    def test_there_is_no_zero_cost_default(self) -> None:
        """Brief §11 forbids a hidden "zero fees" assumption."""
        import inspect

        from oipulse.backtest.costs import CostModel

        signature = inspect.signature(CostModel)
        required = [
            name
            for name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        for rate in ("brokerage_flat", "stt_rate_sell", "gst_rate", "exchange_rate"):
            self.assertIn(rate, required, f"{rate} must be declared, never defaulted")


class TestLedger(unittest.TestCase):
    def test_applying_the_same_fill_twice_changes_nothing(self) -> None:
        """Brief §15: idempotent where event replay can repeat input."""
        book = Ledger(opening_cash=Decimal(100000))
        fill = _fill(quantity=10, price="100")
        self.assertTrue(book.apply(fill))
        cash_after_first = book.cash
        position_after_first = book.position(fx.TARGET)

        self.assertFalse(book.apply(fill))
        self.assertEqual(book.cash, cash_after_first)
        self.assertEqual(book.position(fx.TARGET), position_after_first)
        self.assertEqual(book.duplicate_fills_ignored, 1)

    def test_a_genuine_second_fill_at_a_different_price_is_not_a_duplicate(self) -> None:
        book = Ledger(opening_cash=Decimal(100000))
        book.apply(_fill(quantity=10, price="100"))
        book.apply(_fill(quantity=10, price="101"))
        self.assertEqual(book.duplicate_fills_ignored, 0)
        position = book.position(fx.TARGET)
        assert position is not None
        self.assertEqual(position.quantity, 20)

    def test_the_idempotency_key_distinguishes_price_and_quantity(self) -> None:
        self.assertNotEqual(
            fill_key(_fill(quantity=10, price="100")),
            fill_key(_fill(quantity=10, price="101")),
        )

    def test_adding_averages_the_basis_and_realises_nothing(self) -> None:
        book = Ledger(opening_cash=Decimal(1000000))
        book.apply(_fill(quantity=50, price="100"))
        book.apply(_fill(quantity=50, price="120"))
        position = book.position(fx.TARGET)
        assert position is not None
        self.assertEqual(position.average_price, Decimal("110"))
        self.assertEqual(book.realized_pnl, Decimal(0))

    def test_reducing_realises_against_the_basis_and_leaves_it_unchanged(self) -> None:
        book = Ledger(opening_cash=Decimal(1000000))
        book.apply(_fill(quantity=100, price="110"))
        book.apply(_fill(side=Side.SELL, quantity=40, price="130"))
        self.assertEqual(book.realized_pnl, Decimal(800))
        position = book.position(fx.TARGET)
        assert position is not None
        self.assertEqual(position.quantity, 60)
        self.assertEqual(position.average_price, Decimal("110"))

    def test_reversing_through_flat_rebases_on_the_new_fill(self) -> None:
        """The case sign handling usually gets wrong."""
        book = Ledger(opening_cash=Decimal(1000000))
        book.apply(_fill(quantity=60, price="110"))
        book.apply(_fill(side=Side.SELL, quantity=100, price="90"))
        position = book.position(fx.TARGET)
        assert position is not None
        self.assertEqual(position.quantity, -40)
        self.assertEqual(position.average_price, Decimal("90"))
        self.assertEqual(book.realized_pnl, Decimal(-1200))

    def test_a_short_gains_as_the_mark_falls(self) -> None:
        book = Ledger(opening_cash=Decimal(1000000))
        book.apply(_fill(side=Side.SELL, quantity=40, price="90"))
        snapshot = book.snapshot(as_of=at(2), marks={fx.TARGET: Decimal(80)})
        self.assertEqual(snapshot.unrealized_pnl, Decimal(400))

    def test_an_unmarked_position_is_named_not_marked_at_cost(self) -> None:
        """Marking at cost reports break-even: a number, plausible, and wrong."""
        book = Ledger(opening_cash=Decimal(1000000))
        book.apply(_fill(quantity=10, price="100"))
        snapshot = book.snapshot(as_of=at(2), marks={})
        self.assertEqual(snapshot.unrealized_pnl, Decimal(0))
        self.assertEqual(snapshot.unmarked_instruments, (fx.TARGET,))

    def test_positions_iterate_in_a_deterministic_order(self) -> None:
        book = Ledger(opening_cash=Decimal(1000000))
        for instrument in (300, 100, 200):
            book.apply(
                Fill(
                    intent_id=f"ti_{instrument}",
                    instrument_id=instrument,
                    side=Side.BUY,
                    quantity=1,
                    requested_quantity=1,
                    price=Decimal(10),
                    reference_price=Decimal(10),
                    filled_at=at(1),
                    price_source=PriceSource.OBSERVED_QUOTE,
                    slippage_model=SlippageModel.MID,
                    costs=CostBreakdown(),
                    assumption_based=False,
                )
            )
        self.assertEqual([p.instrument_id for p in book.positions()], [100, 200, 300])

    def test_net_pnl_is_gross_less_fees_and_equity_reconciles(self) -> None:
        rows = fx.observations()
        result = BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(latency=timedelta(seconds=60)),
            opening_cash=Decimal(500000),
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())

        ledger = result.final_ledger
        self.assertEqual(ledger.net_pnl, ledger.gross_pnl - ledger.fees)
        self.assertEqual(ledger.equity, Decimal(500000) + ledger.net_pnl)
        self.assertGreater(ledger.fees, Decimal(0), "a run with a fill must have paid fees")


class TestFillDeterminism(unittest.TestCase):
    def test_two_identical_runs_produce_an_identical_fill_sequence(self) -> None:
        """Brief §13."""
        rows = fx.observations()

        def run() -> list[dict[str, object]]:
            result = BacktestRunner(
                fx.engine(rows), fill_model=fx.fill_model(), opening_cash=Decimal(500000)
            ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())
            return [f.as_dict() for f in result.fills]

        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
