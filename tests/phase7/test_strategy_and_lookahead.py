"""Strategy interface, decision/execution separation and look-ahead prevention.

Brief §8, §9, §10 and §12. `10-REPLAY.md` §5:

> **A strategy cannot reach the database.** It receives a context and returns intents.
> This is what makes look-ahead impossible rather than merely discouraged — there is
> no API surface through which future data could be obtained.

The tests below check that claim structurally (what the context *has*) as well as
behaviourally (what it *refuses*), because a context that merely happens not to be
used for look-ahead today is not the same as one that cannot be.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from oipulse.analytics.values import MetricValue, Scope, ScopeRef
from oipulse.backtest.intents import OrderType, Side, TradeIntent
from oipulse.backtest.runner import BacktestRunner
from oipulse.backtest.strategy import AccountView, FeatureView, StrategyContext
from oipulse.marketstate.staleness import QualityStatus
from oipulse.research.access import FeatureAccessError, PointInTimeAccessor
from tests.phase3._fixtures import at
from tests.phase7 import _fixtures as fx


def _metric(feature_id: str, *, available_at, observed_at=None, value="1") -> MetricValue:
    return MetricValue(
        feature_id=feature_id,
        feature_version=1,
        scope=ScopeRef(Scope.UNDERLYING, "100"),
        value=Decimal(value),
        unit="ratio",
        observed_at=observed_at or available_at,
        knowledge_horizon=available_at,
        computed_at=available_at,
        available_at=available_at,
        build_context_id=fx.BUILD_CONTEXT,
        inputs_digest="d",
        quality_status=QualityStatus.OK,
    )


class TestStrategySurfaceIsClosed(unittest.TestCase):
    """What the context *has*, not only what it does."""

    def test_the_context_exposes_no_store_session_engine_or_clock(self) -> None:
        rows = fx.observations()
        stepped = fx.engine(rows).run(fx.timeline(rows))[0]
        ctx = StrategyContext.build(
            "run-1",
            stepped.state,
            PointInTimeAccessor(),
            AccountView(cash=Decimal(0)),
            decision_time=at(0),
            knowledge_horizon=at(0),
        )
        public = {name for name in dir(ctx) if not name.startswith("_")}
        for forbidden in ("store", "session", "engine", "timeline", "clock", "repository", "db"):
            with self.subTest(attribute=forbidden):
                self.assertNotIn(forbidden, public)

    def test_now_is_the_replayed_instant_not_the_wall_clock(self) -> None:
        rows = fx.observations()
        strategy = fx.RecordingStrategy()
        tl = fx.timeline(rows)
        BacktestRunner(
            fx.engine(rows), fill_model=fx.fill_model(), opening_cash=Decimal(100000)
        ).run(strategy, tl, PointInTimeAccessor())
        self.assertEqual([c.now for c in strategy.contexts], [s.market_time for s in tl.steps])


class TestFeatureAvailability(unittest.TestCase):
    """`10` §5: requesting an unavailable feature raises, it does not return None."""

    def test_a_feature_available_later_is_refused_not_returned(self) -> None:
        accessor = PointInTimeAccessor(metrics=(_metric("PCR", available_at=at(3)),))
        view = FeatureView(accessor, at(2))
        with self.assertRaises(FeatureAccessError) as caught:
            view.get("PCR", 1)
        self.assertIn("look-ahead refused", str(caught.exception))

    def test_an_available_feature_is_returned(self) -> None:
        accessor = PointInTimeAccessor(metrics=(_metric("PCR", available_at=at(1)),))
        self.assertEqual(FeatureView(accessor, at(2)).get("PCR", 1).feature_id, "PCR")

    def test_has_reports_availability_without_raising(self) -> None:
        accessor = PointInTimeAccessor(metrics=(_metric("PCR", available_at=at(3)),))
        self.assertFalse(FeatureView(accessor, at(2)).has("PCR", 1))
        self.assertTrue(FeatureView(accessor, at(4)).has("PCR", 1))

    def test_there_is_no_silent_none_variant_on_the_strategy_surface(self) -> None:
        """`try_feature` exists on the accessor but is deliberately not re-exposed.

        A strategy that silently receives `None` for a feature it believes it has
        produces a quietly wrong backtest.
        """
        self.assertFalse(hasattr(FeatureView, "try_get"))
        self.assertFalse(hasattr(FeatureView, "try_feature"))

    def test_the_context_narrows_by_knowledge_horizon_and_then_by_decision_time(self) -> None:
        """Both, not either.

        A metric whose row exists only at a later knowledge horizon must be invisible
        even if its `available_at` would otherwise permit it.
        """
        late_knowledge = _metric("LATE", available_at=at(1))
        import dataclasses

        late_knowledge = dataclasses.replace(late_knowledge, knowledge_horizon=at(5))
        rows = fx.observations()
        stepped = fx.engine(rows).run(fx.timeline(rows))[0]
        ctx = StrategyContext.build(
            "run-1",
            stepped.state,
            PointInTimeAccessor(metrics=(late_knowledge,)),
            AccountView(cash=Decimal(0)),
            decision_time=at(2),
            knowledge_horizon=at(2),
        )
        with self.assertRaises(FeatureAccessError):
            ctx.features.get("LATE", 1)


class TestDecisionExecutionSeparation(unittest.TestCase):
    """Brief §10: a strategy decision is not automatically a fill."""

    def test_a_fill_happens_after_the_decision_by_the_modelled_latency(self) -> None:
        rows = fx.observations()
        strategy = fx.BuyOnceStrategy()
        result = BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(latency=timedelta(seconds=60)),
            opening_cash=Decimal(500000),
        ).run(strategy, fx.timeline(rows), PointInTimeAccessor())

        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]
        self.assertEqual(fill.filled_at, at(1), "decided at minute 0, filled a minute later")

    def test_the_fill_price_comes_from_a_state_the_strategy_had_not_seen(self) -> None:
        """The substance of the separation, not just the timestamp.

        With prices rising 5 a minute, a fill at minute 1 must not be priced at the
        minute-0 level the strategy decided on.
        """
        rows = fx.observations(base_price=100, step_price=5)
        result = BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(latency=timedelta(seconds=60)),
            opening_cash=Decimal(500000),
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())

        price = result.fills[0].price
        self.assertGreater(
            price,
            Decimal("102"),
            "a fill priced at the decision-time level would be a free look-ahead",
        )

    def test_an_intent_decided_too_late_to_execute_expires_rather_than_filling(self) -> None:
        """Brief §10: no fill is fabricated where the period contains no step for it.

        With a latency longer than the remaining period, clamping to the final state
        would be the convenient answer and a false one.
        """
        rows = fx.observations(minutes=3)
        result = BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(latency=timedelta(hours=1)),
            opening_cash=Decimal(500000),
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows, fx.context(minutes=3)), PointInTimeAccessor())

        self.assertEqual(result.statistics.intents_generated, 1)
        self.assertEqual(len(result.fills), 0)
        self.assertTrue(
            any("expired unfilled" in w for w in result.coverage_warnings),
            f"the expiry must be reported, not silently dropped: {result.coverage_warnings}",
        )

    def test_the_strategy_sees_its_earlier_fills_before_deciding_again(self) -> None:
        """Order within a step: execute the due fills, *then* ask the strategy.

        A strategy that buys at minute 0 and is filled at minute 1 must see the
        position and the reduced cash from minute 1 onward. If the runner decided
        before executing, the position would appear a step late and any
        position-aware strategy would double up.
        """
        rows = fx.observations()

        class BuyThenWatch(fx.BuyOnceStrategy):
            def __init__(self) -> None:
                super().__init__()
                self.accounts: list[AccountView] = []

            def on_state(self, ctx: StrategyContext) -> list[TradeIntent]:
                self.accounts.append(ctx.account)
                return super().on_state(ctx)

        strategy = BuyThenWatch()
        BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(latency=timedelta(seconds=60)),
            opening_cash=Decimal(500000),
        ).run(strategy, fx.timeline(rows), PointInTimeAccessor())

        first, second = strategy.accounts[0], strategy.accounts[1]
        self.assertEqual(first.position(fx.TARGET), 0, "nothing is filled at the decision step")
        self.assertEqual(
            second.position(fx.TARGET),
            50,
            "the fill due at step 1 must be applied before the strategy is asked again",
        )
        self.assertLess(second.cash, first.cash, "cash must reflect the completed purchase")

    def test_a_strategy_cannot_mutate_the_ledger(self) -> None:
        """`AccountView` is read-only; positions arrive as a tuple, not the book."""
        view = AccountView(cash=Decimal(100), positions=((1, 5),))
        with self.assertRaises((AttributeError, TypeError)):
            view.cash = Decimal(999)  # type: ignore[misc]
        self.assertIsInstance(view.positions, tuple)


class TestIntentIdentity(unittest.TestCase):
    def test_the_intent_id_is_content_addressed_not_random(self) -> None:
        def make() -> TradeIntent:
            return TradeIntent(
                run_id="r",
                strategy_id="S",
                strategy_version=1,
                instrument_id=fx.TARGET,
                side=Side.BUY,
                quantity=50,
                order_type=OrderType.MARKET,
                decision_time=at(0),
                knowledge_horizon=at(0),
            )

        self.assertEqual(make().intent_id, make().intent_id)

    def test_a_quantity_change_changes_the_id(self) -> None:
        base = {
            "run_id": "r",
            "strategy_id": "S",
            "strategy_version": 1,
            "instrument_id": fx.TARGET,
            "side": Side.BUY,
            "order_type": OrderType.MARKET,
            "decision_time": at(0),
            "knowledge_horizon": at(0),
        }
        self.assertNotEqual(
            TradeIntent(**base, quantity=50).intent_id,  # type: ignore[arg-type]
            TradeIntent(**base, quantity=51).intent_id,  # type: ignore[arg-type]
        )

    def test_an_intent_cannot_rest_on_information_from_after_it_was_made(self) -> None:
        with self.assertRaises(ValueError):
            TradeIntent(
                run_id="r",
                strategy_id="S",
                strategy_version=1,
                instrument_id=fx.TARGET,
                side=Side.BUY,
                quantity=1,
                order_type=OrderType.MARKET,
                decision_time=at(3),
                knowledge_horizon=at(1),
            )

    def test_stop_and_bracket_orders_are_absent_rather_than_approximated(self) -> None:
        """They need intra-bar path data a state sequence does not carry."""
        self.assertEqual({t.value for t in OrderType}, {"MARKET", "LIMIT"})


if __name__ == "__main__":
    unittest.main()
