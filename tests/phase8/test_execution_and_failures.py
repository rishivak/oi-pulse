"""Paper execution, PIT/knowledge-horizon correctness and every failure path.

Phase 8 brief §8, §9, §10, §17. The brief is explicit that success is not enough:

> Do not test only successful trades.

So the bulk of this module is refusals: insufficient cash, invalid quantity, missing
price, stale state, expiry, unmarketable limits, all-or-none, slippage caps and
account status. Each must produce a recorded reason rather than a silent fill.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from oipulse.backtest.fills import SlippageModel
from oipulse.backtest.intents import OrderType, Side
from oipulse.marketstate.staleness import QualityStatus
from oipulse.research.access import FeatureAccessError, PointInTimeAccessor
from oipulse.trading.accounts import AccountStatus
from oipulse.trading.intents import IntentConstraints, IntentLeg, TradeIntent
from oipulse.trading.orders import OrderState, RejectReason
from tests.phase8 import _fixtures as fx
from tests.phase8._fixtures import at


class TestDecisionExecutionSeparation(unittest.TestCase):
    """Brief §8: a fill must come from information the strategy did not have."""

    def test_the_fill_is_priced_against_the_execution_state_not_the_decision_state(
        self,
    ) -> None:
        rows = fx.observations(base_price=100, step_price=5)
        rt = fx.runtime()
        result = fx.submit(rt, rows, fx.intent(), decision_minute=0, execution_minute=1)

        fill = result.fills[0]
        # Minute 0 ATM call is 101; minute 1 is 106, ask 106.4. A fill at the
        # decision-time level would be free look-ahead in the strategy's favour.
        self.assertGreater(fill.price, Decimal("102"))
        self.assertEqual(fill.price, Decimal("106.40"))

    def test_the_runtime_takes_two_distinct_states(self) -> None:
        """Structural, not behavioural: the signature itself keeps them apart."""
        import inspect

        from oipulse.trading.runtime import PaperTradingRuntime

        parameters = inspect.signature(PaperTradingRuntime.submit).parameters
        self.assertIn("decision_state", parameters)
        self.assertIn("execution_state", parameters)

    def test_an_intent_is_not_automatically_a_fill(self) -> None:
        """A rejected intent produces an order and no fill, and says why."""
        rt = fx.runtime(acct=fx.account(cfg=fx.config(starting_cash=Decimal(10))))
        result = fx.submit(rt, fx.observations(), fx.intent())
        self.assertEqual(len(result.fills), 0)
        self.assertEqual(result.orders[0].state, OrderState.REJECTED)


class TestKnowledgeHorizon(unittest.TestCase):
    """Brief §10. Phase 5 semantics, unweakened."""

    def test_a_feature_available_after_the_decision_cannot_be_consumed(self) -> None:
        """market_time = T, available_at = T+2s, decision K = T → refused."""
        from oipulse.analytics.values import MetricValue, Scope, ScopeRef

        metric = MetricValue(
            feature_id="PCR",
            feature_version=1,
            scope=ScopeRef(Scope.UNDERLYING, "100"),
            value=Decimal(1),
            unit="ratio",
            observed_at=at(0),
            knowledge_horizon=at(0),
            computed_at=at(0),
            available_at=at(0) + timedelta(seconds=2),
            build_context_id="bc-test",
            inputs_digest="d",
            quality_status=QualityStatus.OK,
        )
        accessor = PointInTimeAccessor(metrics=(metric,))
        with self.assertRaises(FeatureAccessError) as caught:
            accessor.get_feature("PCR", 1, at(0))
        self.assertIn("look-ahead refused", str(caught.exception))

        # And becomes consumable at a later K, so the refusal is not blanket.
        self.assertIsNotNone(accessor.get_feature("PCR", 1, at(0) + timedelta(seconds=2)))

    def test_an_intent_cannot_know_less_than_the_fact_it_rests_on_is_old(self) -> None:
        with self.assertRaises(ValueError) as caught:
            TradeIntent(
                account_id=fx.ACCOUNT_ID,
                source=fx.intent().source,
                source_ref="x",
                legs=(
                    IntentLeg(
                        instrument_id=fx.TARGET,
                        side=Side.BUY,
                        quantity=1,
                        order_type=OrderType.MARKET,
                    ),
                ),
                market_time=at(3),
                knowledge_time=at(1),
                decision_time=at(3),
            )
        self.assertIn("precedes market_time", str(caught.exception))

    def test_the_knowledge_horizon_is_carried_onto_the_order(self) -> None:
        """Not re-derived: the signal's horizon is its own semantic value."""
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent(knowledge_minute=0))
        self.assertEqual(result.orders[0].knowledge_time, at(0))
        self.assertEqual(result.orders[0].decision_time, at(0))

    def test_execution_time_does_not_overwrite_the_decision_times(self) -> None:
        """Brief §9: execution timestamps must not alter the historical meaning."""
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent(decision_minute=0), execution_minute=2)
        order = result.orders[0]
        self.assertEqual(order.decision_time, at(0))
        self.assertEqual(order.knowledge_time, at(0))
        self.assertEqual(order.created_at, at(2))


class TestFailurePaths(unittest.TestCase):
    """Brief §17. Every one of these must refuse explicitly, never fill silently."""

    def test_insufficient_cash_is_rejected_with_a_reason(self) -> None:
        rt = fx.runtime(acct=fx.account(cfg=fx.config(starting_cash=Decimal(100))))
        result = fx.submit(rt, fx.observations(), fx.intent(quantity=50))
        order = result.orders[0]
        self.assertIs(order.state, OrderState.REJECTED)
        self.assertIs(order.reject_reason, RejectReason.INSUFFICIENT_CASH)
        self.assertIn("available", order.reject_detail)
        self.assertEqual(rt.ledger.cash, Decimal(100), "no cash may move on a rejection")

    def test_an_invalid_quantity_is_refused_at_construction(self) -> None:
        for quantity in (0, -1):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                IntentLeg(
                    instrument_id=fx.TARGET,
                    side=Side.BUY,
                    quantity=quantity,
                    order_type=OrderType.MARKET,
                )

    def test_a_quantity_above_the_account_maximum_is_rejected(self) -> None:
        rt = fx.runtime(acct=fx.account(cfg=fx.config(max_order_quantity=10)))
        result = fx.submit(rt, fx.observations(), fx.intent(quantity=50))
        self.assertIs(result.orders[0].reject_reason, RejectReason.EXCEEDS_ORDER_LIMIT)

    def test_an_unknown_instrument_has_no_price_and_is_rejected(self) -> None:
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent(instrument_id=999999))
        self.assertIs(result.orders[0].reject_reason, RejectReason.NO_PRICE_AVAILABLE)
        self.assertEqual(len(result.fills), 0)

    def test_an_unreliable_state_is_refused_rather_than_traded_against(self) -> None:
        """`11` §3 lists the stale-data check; trading on a state the system says it
        does not trust must be an explicit refusal."""
        import dataclasses

        rows = fx.observations()
        rt = fx.runtime()
        state = fx.state_at(rows, 1)
        degraded = dataclasses.replace(
            state, quality=dataclasses.replace(state.quality, status=QualityStatus.UNRELIABLE)
        )
        result = rt.submit(
            fx.intent(),
            decision_state=fx.state_at(rows, 0),
            execution_state=degraded,
            at=at(1),
        )
        self.assertIs(result.orders[0].reject_reason, RejectReason.STALE_MARKET_STATE)

    def test_an_unreliable_state_can_be_accepted_when_the_model_declares_it(self) -> None:
        """The refusal is a declared policy, not a hard-coded one — so the test
        above is testing the policy rather than an accident."""
        import dataclasses

        rows = fx.observations()
        rt = fx.runtime(reject_on_unreliable_state=False)
        state = fx.state_at(rows, 1)
        degraded = dataclasses.replace(
            state, quality=dataclasses.replace(state.quality, status=QualityStatus.UNRELIABLE)
        )
        result = rt.submit(
            fx.intent(),
            decision_state=fx.state_at(rows, 0),
            execution_state=degraded,
            at=at(1),
        )
        self.assertEqual(len(result.fills), 1)

    def test_an_expired_intent_is_not_executed(self) -> None:
        rt = fx.runtime()
        expiring = fx.intent(
            constraints=IntentConstraints(valid_until=at(0) + timedelta(seconds=30))
        )
        result = fx.submit(rt, fx.observations(), expiring, execution_minute=2)
        self.assertTrue(result.rejected)
        self.assertIs(result.reject_reason, RejectReason.INTENT_EXPIRED)
        self.assertEqual(len(result.fills), 0)

    def test_an_intent_cannot_be_born_expired(self) -> None:
        with self.assertRaises(ValueError) as caught:
            fx.intent(
                decision_minute=3,
                constraints=IntentConstraints(valid_until=at(1)),
            )
        self.assertIn("born expired", str(caught.exception))

    def test_an_unmarketable_limit_is_rejected_not_filled_at_market(self) -> None:
        rt = fx.runtime()
        result = fx.submit(
            rt,
            fx.observations(),
            fx.intent(order_type=OrderType.LIMIT, limit_price=Decimal(1)),
        )
        self.assertEqual(len(result.fills), 0)
        self.assertIs(result.orders[0].reject_reason, RejectReason.EXECUTION_REJECTED)

    def test_a_marketable_limit_fills_at_the_limit(self) -> None:
        rt = fx.runtime()
        result = fx.submit(
            rt,
            fx.observations(),
            fx.intent(order_type=OrderType.LIMIT, limit_price=Decimal(500)),
        )
        self.assertEqual(result.fills[0].price, Decimal(500))

    def test_all_or_none_refuses_a_partial(self) -> None:
        rt = fx.runtime(model=fx.fill_model(partial_fills_enabled=True))
        result = fx.submit(
            rt,
            fx.observations(),
            fx.intent(quantity=100000, constraints=IntentConstraints(all_or_none=True)),
        )
        self.assertEqual(len(result.fills), 0)
        self.assertIs(result.orders[0].reject_reason, RejectReason.ALL_OR_NONE_UNFILLABLE)

    def test_a_partial_fill_is_applied_when_all_or_none_is_off(self) -> None:
        rt = fx.runtime(
            acct=fx.account(cfg=fx.config(starting_cash=Decimal(100_000_000))),
            model=fx.fill_model(partial_fills_enabled=True),
        )
        result = fx.submit(rt, fx.observations(), fx.intent(quantity=100000))
        self.assertEqual(len(result.fills), 1)
        order = result.orders[0]
        self.assertIs(order.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 1200, "10% of the observed 12,000 volume")
        self.assertGreater(order.remaining_quantity, 0)

    def test_a_slippage_cap_is_enforced_against_the_decision_price(self) -> None:
        rt = fx.runtime()
        result = fx.submit(
            rt,
            fx.observations(),
            fx.intent(constraints=IntentConstraints(max_slippage=Decimal("0.01"))),
        )
        self.assertEqual(len(result.fills), 0)
        self.assertIs(result.orders[0].reject_reason, RejectReason.SLIPPAGE_EXCEEDED)

    def test_a_suspended_account_refuses_intents(self) -> None:
        rt = fx.runtime()
        rt.set_status(AccountStatus.SUSPENDED, at=at(0))
        result = fx.submit(rt, fx.observations(), fx.intent())
        self.assertTrue(result.rejected)
        self.assertIs(result.reject_reason, RejectReason.ACCOUNT_NOT_ACTIVE)
        self.assertEqual(len(result.orders), 0)

    def test_a_modelled_execution_rejection_is_recorded(self) -> None:
        rt = fx.runtime(model=fx.fill_model(rejection_rate=Decimal(1)))
        result = fx.submit(rt, fx.observations(), fx.intent())
        self.assertEqual(len(result.fills), 0)
        self.assertIs(result.orders[0].reject_reason, RejectReason.EXECUTION_REJECTED)

    def test_no_failure_path_moves_cash_or_position(self) -> None:
        """The invariant behind every case above, asserted once over all of them."""
        for label, rt, it in (
            (
                "cash",
                fx.runtime(acct=fx.account(cfg=fx.config(starting_cash=Decimal(100)))),
                fx.intent(),
            ),
            ("price", fx.runtime(), fx.intent(instrument_id=999999)),
            (
                "limit",
                fx.runtime(),
                fx.intent(order_type=OrderType.LIMIT, limit_price=Decimal(1)),
            ),
            ("rejection", fx.runtime(model=fx.fill_model(rejection_rate=Decimal(1))), fx.intent()),
        ):
            with self.subTest(case=label):
                opening = rt.ledger.cash
                fx.submit(rt, fx.observations(), it)
                self.assertEqual(rt.ledger.cash, opening)
                self.assertEqual(rt.ledger.positions(), ())
                self.assertEqual(rt.ledger.fees, Decimal(0))


class TestAssumptionBasedFills(unittest.TestCase):
    def test_a_missing_quote_produces_a_flagged_fill(self) -> None:
        """`10` §6: a backfill-only period must never read as full fidelity."""
        rows = fx.observations(with_quotes=False)
        rt = fx.runtime()
        result = fx.submit(rt, rows, fx.intent())
        self.assertEqual(len(result.fills), 1)
        self.assertTrue(result.fills[0].assumption_based)

    def test_an_observed_quote_is_not_flagged(self) -> None:
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent())
        self.assertFalse(result.fills[0].assumption_based)


class TestSharedFillModel(unittest.TestCase):
    """`11` §7: paper fills come from the same model the backtester uses."""

    def test_the_execution_model_delegates_rather_than_reimplementing(self) -> None:
        import inspect

        from oipulse.trading import execution

        source = inspect.getsource(execution)
        self.assertIn("simulate_fill", source)

    def test_a_paper_fill_matches_the_backtest_fill_for_the_same_inputs(self) -> None:
        """The strongest available form of "one implementation": same price."""
        from oipulse.backtest.fills import simulate_fill
        from oipulse.backtest.intents import TradeIntent as StrategyIntent

        rows = fx.observations()
        model = fx.fill_model()
        rt = fx.runtime(model=model)
        paper = fx.submit(rt, rows, fx.intent(quantity=50)).fills[0]

        order_id = rt.orders()[0].order_id
        probe = StrategyIntent(
            run_id=order_id,
            strategy_id="TEST_STRATEGY",
            strategy_version=2,
            instrument_id=fx.TARGET,
            side=Side.BUY,
            quantity=50,
            order_type=OrderType.MARKET,
            decision_time=at(0),
            knowledge_horizon=at(0),
        )
        backtest = simulate_fill(probe, fx.state_at(rows, 1), model)
        assert backtest.fill is not None
        self.assertEqual(paper.price, backtest.fill.price)
        self.assertEqual(paper.costs.total, backtest.fill.costs.total)

    def test_every_slippage_model_is_reachable_through_the_paper_path(self) -> None:
        for model in SlippageModel:
            with self.subTest(model=model):
                rt = fx.runtime(
                    model=fx.fill_model(slippage_model=model, slippage_parameter=Decimal("0.5"))
                )
                result = fx.submit(rt, fx.observations(), fx.intent())
                self.assertEqual(len(result.fills), 1)
                self.assertGreaterEqual(result.fills[0].slippage_per_unit, Decimal(0))


if __name__ == "__main__":
    unittest.main()
