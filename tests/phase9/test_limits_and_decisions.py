"""Every limit at its boundary, fail-closed behaviour, expiry and intent scoping.

Phase 9 brief §6, §9, §10, §11, §13, §21, §22; `18-ROADMAP.md` Phase 9: "Each limit
at boundary; kill switch halts immediately; decisions append rather than replace".

The boundary cases matter more than the obvious ones. A limit of 100 must accept
100 and refuse 101, and getting that backwards is the kind of error that only
surfaces when a position is one lot over.
"""

from __future__ import annotations

import unittest
from datetime import time, timedelta
from decimal import Decimal

from oipulse.backtest.intents import OrderType, Side
from oipulse.trading.intents import IntentLeg
from oipulse.trading.risk import (
    AuthorizationStatus,
    ExposureSnapshot,
    KillSwitchState,
    LimitStatus,
    RiskDecisionRecord,
    RiskLimits,
    RiskPolicy,
    RiskVerdict,
    SessionWindow,
    VenueHealth,
)
from tests.phase9 import _fixtures as fx
from tests.phase9._fixtures import at


def _limit(decision: object, limit_id: str) -> object:
    return next(
        limit
        for limit in decision.limits_evaluated  # type: ignore[attr-defined]
        if limit.limit_id == limit_id
    )


class TestEveryLimitIsEvaluated(unittest.TestCase):
    def test_no_check_short_circuits_after_a_breach(self) -> None:
        """`11` §3: the complete picture, not the first failure.

        An operator who fixes the one limit a short-circuiting report showed them
        would resubmit straight into the next breach.
        """
        decision = fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_order_quantity=1, max_position_per_instrument=1)),
            it=fx.intent(quantity=50),
        )
        self.assertGreaterEqual(len(fx.breached(decision)), 2)
        self.assertEqual(len(decision.limits_evaluated), 27)

    def test_every_category_is_represented(self) -> None:
        from oipulse.trading.risk import LimitCategory

        decision = fx.evaluate()
        covered = {limit.category for limit in decision.limits_evaluated}
        for category in LimitCategory:
            with self.subTest(category=category):
                self.assertIn(category, covered)


class TestLimitBoundaries(unittest.TestCase):
    """At the limit passes; one beyond breaches."""

    def _at_and_over(self, limit_field: str, limit_value: object, **intent_kwargs: object):
        engine = fx.engine(lim=fx.limits(**{limit_field: limit_value}))
        return engine, intent_kwargs

    def test_order_quantity_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_order_quantity=100))
        self.assertTrue(
            fx.evaluate(eng=engine, it=fx.intent(quantity=100)).is_approved,
            "exactly at the limit must pass",
        )
        over = fx.evaluate(eng=engine, it=fx.intent(quantity=101))
        self.assertIn("max_order_quantity", fx.breached(over))

    def test_position_per_instrument_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_position_per_instrument=100))
        state = fx.state(positions=(fx.position(quantity=90),))
        self.assertTrue(fx.evaluate(eng=engine, it=fx.intent(quantity=10), st=state).is_approved)
        over = fx.evaluate(eng=engine, it=fx.intent(quantity=11), st=state)
        self.assertIn("max_position_per_instrument", fx.breached(over))

    def test_the_resulting_position_is_measured_not_the_current_one(self) -> None:
        """A limit that only looked at what is held would let the total march past."""
        engine = fx.engine(lim=fx.limits(max_position_per_instrument=100))
        state = fx.state(positions=(fx.position(quantity=100),))
        decision = fx.evaluate(eng=engine, it=fx.intent(quantity=1), st=state)
        self.assertIn("max_position_per_instrument", fx.breached(decision))

    def test_a_reducing_trade_is_permitted_at_the_cap(self) -> None:
        """Selling down from the cap must not be blocked by the cap."""
        engine = fx.engine(lim=fx.limits(max_position_per_instrument=100))
        state = fx.state(positions=(fx.position(quantity=100),))
        decision = fx.evaluate(eng=engine, it=fx.intent(side=Side.SELL, quantity=10), st=state)
        self.assertTrue(decision.is_approved, decision.reason)

    def test_order_notional_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_order_notional=Decimal(1000)))
        # mark is 100, so 10 lots is exactly 1000.
        self.assertTrue(fx.evaluate(eng=engine, it=fx.intent(quantity=10)).is_approved)
        over = fx.evaluate(eng=engine, it=fx.intent(quantity=11))
        self.assertIn("max_order_notional", fx.breached(over))

    def test_cash_sufficiency_boundary(self) -> None:
        engine = fx.engine()
        exact = fx.state(cash="1000", equity="1000")
        self.assertTrue(fx.evaluate(eng=engine, it=fx.intent(quantity=10), st=exact).is_approved)
        over = fx.evaluate(eng=engine, it=fx.intent(quantity=11), st=exact)
        self.assertIn("cash_sufficiency", fx.breached(over))

    def test_cash_sufficiency_uses_available_not_total(self) -> None:
        state = fx.state(cash="1000", equity="1000", reserved_cash=Decimal(500))
        decision = fx.evaluate(it=fx.intent(quantity=10), st=state)
        self.assertIn("cash_sufficiency", fx.breached(decision))

    def test_a_sell_does_not_require_cash(self) -> None:
        """A sale releases cash rather than consuming it."""
        state = fx.state(cash="0", equity="0")
        decision = fx.evaluate(it=fx.intent(side=Side.SELL, quantity=10), st=state)
        self.assertEqual(_limit(decision, "cash_sufficiency").status, LimitStatus.PASSED)

    def test_daily_loss_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_daily_loss=Decimal(1000)))
        self.assertTrue(fx.evaluate(eng=engine, st=fx.state(daily_loss=Decimal(1000))).is_approved)
        over = fx.evaluate(eng=engine, st=fx.state(daily_loss=Decimal(1001)))
        self.assertIn("max_daily_loss", fx.breached(over))

    def test_drawdown_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_drawdown=Decimal(500)))
        over = fx.evaluate(eng=engine, st=fx.state(drawdown=Decimal(501)))
        self.assertIn("max_drawdown", fx.breached(over))

    def test_leverage_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_leverage=Decimal(2)))
        # equity 1000, existing gross 900, intent adds 1000*... keep it simple:
        state = fx.state(cash="100000", equity="1000", exposure=fx.exposure(gross="1000"))
        # (1000 + 10*100) / 1000 == 2.0 exactly.
        self.assertTrue(fx.evaluate(eng=engine, it=fx.intent(quantity=10), st=state).is_approved)
        over = fx.evaluate(eng=engine, it=fx.intent(quantity=11), st=state)
        self.assertIn("max_leverage", fx.breached(over))

    def test_exposure_limits_use_absolute_values(self) -> None:
        """A short delta of -6000 breaches a 5000 cap just as a long one does."""
        engine = fx.engine(lim=fx.limits(max_net_delta=Decimal(5000)))
        state = fx.state(exposure=fx.exposure(net_delta="-6000"))
        self.assertIn("max_net_delta", fx.breached(fx.evaluate(eng=engine, st=state)))

    def test_concentration_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_underlying_concentration=Decimal("0.5")))
        state = fx.state(
            exposure=fx.exposure(gross="1000", by_underlying=((1, Decimal(500)), (2, Decimal(500))))
        )
        self.assertTrue(fx.evaluate(eng=engine, st=state).is_approved)

        concentrated = fx.state(
            exposure=fx.exposure(gross="1000", by_underlying=((1, Decimal(501)), (2, Decimal(499))))
        )
        self.assertIn(
            "max_underlying_concentration", fx.breached(fx.evaluate(eng=engine, st=concentrated))
        )

    def test_an_empty_book_is_not_concentrated(self) -> None:
        """Checked before the bucket test: an empty book has no buckets either,
        and treating that as unevaluable would fail-closed every flat account."""
        decision = fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_underlying_concentration=Decimal("0.1")))
        )
        self.assertEqual(
            _limit(decision, "max_underlying_concentration").status, LimitStatus.PASSED
        )

    def test_order_rate_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_orders_per_interval=3))
        self.assertTrue(fx.evaluate(eng=engine, st=fx.state(orders_in_interval=2)).is_approved)
        over = fx.evaluate(eng=engine, st=fx.state(orders_in_interval=3))
        self.assertIn("max_orders_per_interval", fx.breached(over))

    def test_strategy_position_limit(self) -> None:
        engine = fx.engine(lim=fx.limits(max_position_per_strategy=20))
        state = fx.state(position_by_strategy=(("TEST_STRATEGY", 15),))
        self.assertTrue(fx.evaluate(eng=engine, it=fx.intent(quantity=5), st=state).is_approved)
        over = fx.evaluate(eng=engine, it=fx.intent(quantity=6), st=state)
        self.assertIn("max_position_per_strategy", fx.breached(over))

    def test_strategy_loss_limit(self) -> None:
        engine = fx.engine(lim=fx.limits(max_loss_per_strategy=Decimal(100)))
        state = fx.state(loss_by_strategy=(("TEST_STRATEGY", Decimal(101)),))
        self.assertIn("max_loss_per_strategy", fx.breached(fx.evaluate(eng=engine, st=state)))


class TestDataAndSessionLimits(unittest.TestCase):
    """`11` §3: the data check is "the one most systems omit"."""

    def test_an_unreliable_state_is_refused(self) -> None:
        decision = fx.evaluate(st=fx.state(quality_status="UNRELIABLE"))
        self.assertIn("reject_unreliable_state", fx.breached(decision))

    def test_an_unknown_quality_is_not_evaluable_and_fails_closed(self) -> None:
        """Quality that was never established is not quality."""
        decision = fx.evaluate(st=fx.state(quality_status="UNKNOWN"))
        self.assertIn("reject_unreliable_state", fx.unevaluable(decision))
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)

    def test_staleness_boundary(self) -> None:
        engine = fx.engine(lim=fx.limits(max_state_staleness=timedelta(seconds=30)))
        self.assertTrue(
            fx.evaluate(eng=engine, st=fx.state(staleness=timedelta(seconds=30))).is_approved
        )
        over = fx.evaluate(eng=engine, st=fx.state(staleness=timedelta(seconds=31)))
        self.assertIn("max_state_staleness", fx.breached(over))

    def test_coverage_is_a_floor_not_a_ceiling(self) -> None:
        """The inverted comparison: below the floor breaches."""
        engine = fx.engine(lim=fx.limits(min_state_coverage=Decimal("0.9")))
        self.assertTrue(fx.evaluate(eng=engine, st=fx.state(coverage="0.9")).is_approved)
        under = fx.evaluate(eng=engine, st=fx.state(coverage="0.89"))
        self.assertIn("min_state_coverage", fx.breached(under))

    def test_an_unavailable_venue_is_refused(self) -> None:
        for health in (VenueHealth.DEGRADED, VenueHealth.UNAVAILABLE, VenueHealth.UNKNOWN):
            with self.subTest(health=health):
                decision = fx.evaluate(st=fx.state(venue=health))
                self.assertIn("require_healthy_venue", fx.breached(decision))

    def test_the_paper_venue_is_usable_and_distinct_from_healthy(self) -> None:
        """`PAPER_SIMULATED` must not claim a real venue was checked."""
        self.assertTrue(VenueHealth.PAPER_SIMULATED.is_usable)
        self.assertNotEqual(VenueHealth.PAPER_SIMULATED, VenueHealth.HEALTHY)

    def test_outside_the_session_window_is_refused(self) -> None:
        engine = fx.engine(
            lim=fx.limits(session=SessionWindow(opens_at=time(9, 15), closes_at=time(15, 30)))
        )
        # The Phase 3 fixture clock is 06:00 UTC, outside a 09:15-15:30 window.
        decision = fx.evaluate(eng=engine)
        self.assertIn("session_window", fx.breached(decision))


class TestKillSwitch(unittest.TestCase):
    def test_a_global_kill_switch_halts_everything(self) -> None:
        decision = fx.evaluate(
            st=fx.state(kill_switch=KillSwitchState(engaged=True, reason="operator halt"))
        )
        self.assertIn("kill_switch", fx.breached(decision))
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)

    def test_a_per_strategy_halt_stops_only_that_strategy(self) -> None:
        switch = KillSwitchState(halted_strategies=("TEST_STRATEGY",))
        halted = fx.evaluate(st=fx.state(kill_switch=switch), it=fx.intent())
        self.assertIn("kill_switch", fx.breached(halted))

        other = fx.evaluate(st=fx.state(kill_switch=switch), it=fx.intent(strategy_id="OTHER"))
        self.assertTrue(other.is_approved, other.reason)

    def test_the_kill_switch_cannot_be_switched_off_by_policy(self) -> None:
        """A kill switch a policy could disable would not be a kill switch."""
        decision = fx.engine(pol=RiskPolicy("EMPTY", 1, RiskLimits())).evaluate(
            fx.intent(),
            fx.state(kill_switch=KillSwitchState(engaged=True)),
            sequence_no=1,
            at=at(0),
        )
        self.assertIn("kill_switch", fx.breached(decision))

    def test_a_kill_switch_breach_is_never_resized_away(self) -> None:
        """Trading less does not satisfy a halt."""
        decision = fx.evaluate(
            eng=fx.engine(allow_resizing=True),
            st=fx.state(kill_switch=KillSwitchState(engaged=True)),
        )
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)
        self.assertEqual(decision.approved_quantity, 0)


class TestFailClosed(unittest.TestCase):
    """Brief §22: never default an uncertain risk state to APPROVED."""

    def test_a_configured_but_unevaluable_limit_rejects(self) -> None:
        state = fx.state(exposure=ExposureSnapshot())  # every greek absent
        decision = fx.evaluate(st=state)
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)
        self.assertGreater(len(decision.unevaluable()), 0)
        self.assertIn("could not be evaluated", decision.reason)

    def test_an_unpriceable_intent_rejects(self) -> None:
        decision = fx.evaluate(it=fx.intent(instrument_id=999999))
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)
        self.assertIn("cash_sufficiency", fx.unevaluable(decision))

    def test_nothing_is_substituted_for_a_missing_price(self) -> None:
        """Brief §15: no stale, latest, zero, previous or estimated value."""
        decision = fx.evaluate(it=fx.intent(instrument_id=999999))
        limit = _limit(decision, "cash_sufficiency")
        self.assertIs(limit.status, LimitStatus.NOT_EVALUABLE)
        self.assertIn("no value was substituted", limit.detail)

    def test_not_configured_and_not_evaluable_are_distinct(self) -> None:
        """Collapsing them is how a report overstates what was checked."""
        unconfigured = fx.evaluate(eng=fx.engine(lim=fx.limits(max_gross_vega=None)))
        self.assertIs(_limit(unconfigured, "max_gross_vega").status, LimitStatus.NOT_CONFIGURED)
        self.assertTrue(unconfigured.is_approved)

        missing = fx.evaluate(st=fx.state(exposure=ExposureSnapshot()))
        self.assertIs(_limit(missing, "max_gross_vega").status, LimitStatus.NOT_EVALUABLE)
        self.assertFalse(missing.is_approved)

    def test_an_unconfigured_limit_does_not_block(self) -> None:
        self.assertTrue(fx.evaluate(eng=fx.engine(lim=fx.limits(max_daily_loss=None))).is_approved)


class TestKnowledgeHorizon(unittest.TestCase):
    """Brief §13: risk must not consume information from after its horizon."""

    def test_a_state_from_after_the_horizon_is_refused(self) -> None:
        """market state available at K2, risk evaluation at K1 -> refused."""
        decision = fx.evaluate(
            st=fx.state(as_of_minute=0, knowledge_minute=5),
            at_minute=0,
            knowledge_horizon=at(1),
        )
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)
        self.assertIn("knowledge_horizon", fx.breached(decision))
        self.assertIn("not entitled to", decision.reason)

    def test_the_horizon_is_checked_before_any_limit(self) -> None:
        """No amount of limit-passing makes a look-ahead decision legitimate."""
        decision = fx.evaluate(
            st=fx.state(as_of_minute=0, knowledge_minute=5),
            at_minute=0,
            knowledge_horizon=at(1),
        )
        self.assertEqual(len(decision.limits_evaluated), 1)

    def test_a_state_at_the_horizon_is_permitted(self) -> None:
        decision = fx.evaluate(
            st=fx.state(as_of_minute=0, knowledge_minute=1),
            at_minute=1,
            knowledge_horizon=at(1),
        )
        self.assertTrue(decision.is_approved, decision.reason)

    def test_a_later_evaluation_may_read_a_later_state(self) -> None:
        """`11` §3 expects exactly this: risk re-evaluates against what it sees now,
        which is not what the strategy saw when it formed the intent."""
        decision = fx.evaluate(
            it=fx.intent(decision_minute=0),
            st=fx.state(as_of_minute=2, knowledge_minute=2),
            at_minute=2,
        )
        self.assertTrue(decision.is_approved, decision.reason)
        self.assertEqual(decision.knowledge_horizon, at(2))


class TestApprovalExpiry(unittest.TestCase):
    """Brief §21: exact boundary semantics at `approved_until`."""

    def test_an_approval_carries_a_validity_horizon(self) -> None:
        decision = fx.evaluate(eng=fx.engine(validity=timedelta(seconds=30)))
        self.assertEqual(decision.approved_until, at(0) + timedelta(seconds=30))

    def test_the_boundary_is_inclusive(self) -> None:
        decision = fx.evaluate(eng=fx.engine(validity=timedelta(seconds=30)))
        horizon = decision.approved_until
        assert horizon is not None
        self.assertTrue(decision.is_actionable_at(horizon - timedelta(microseconds=1)))
        self.assertTrue(decision.is_actionable_at(horizon), "valid *until* T includes T")
        self.assertFalse(decision.is_actionable_at(horizon + timedelta(microseconds=1)))

    def test_an_expired_approval_reports_expired_not_approved(self) -> None:
        decision = fx.evaluate(eng=fx.engine(validity=timedelta(seconds=30)))
        later = at(0) + timedelta(minutes=5)
        self.assertIs(decision.authorization_status(later), AuthorizationStatus.EXPIRED)
        self.assertFalse(decision.authorizes(decision.intent_id, at=later))

    def test_an_approved_decision_must_have_a_validity_horizon(self) -> None:
        with self.assertRaises(ValueError) as caught:
            RiskDecisionRecord(
                intent_id="int_x",
                sequence_no=1,
                verdict=RiskVerdict.APPROVED,
                evaluated=True,
                requested_quantity=10,
                approved_quantity=10,
            )
        self.assertIn("approved_until", str(caught.exception))

    def test_a_rejection_has_no_validity_and_is_never_actionable(self) -> None:
        decision = fx.evaluate(st=fx.state(kill_switch=KillSwitchState(engaged=True)))
        self.assertIsNone(decision.approved_until)
        self.assertFalse(decision.is_actionable_at(at(0)))


class TestIntentScopedApproval(unittest.TestCase):
    """Brief §6: an approval belongs to its own intent."""

    def test_a_decision_does_not_authorize_a_different_intent(self) -> None:
        """Even when the two are identical in every trading respect."""
        first = fx.intent(source_ref="SIG-A")
        second = fx.intent(source_ref="SIG-B")
        self.assertNotEqual(first.intent_id, second.intent_id)

        decision = fx.engine().evaluate(first, fx.state(), sequence_no=1, at=at(0))
        self.assertTrue(decision.authorizes(first.intent_id, at=at(0)))
        self.assertFalse(decision.authorizes(second.intent_id, at=at(0)))

    def test_the_decision_records_the_intent_it_evaluated(self) -> None:
        intent = fx.intent()
        decision = fx.engine().evaluate(intent, fx.state(), sequence_no=1, at=at(0))
        self.assertEqual(decision.intent_id, intent.intent_id)

    def test_a_decision_must_name_an_intent(self) -> None:
        with self.assertRaises(ValueError):
            RiskDecisionRecord(
                intent_id="",
                sequence_no=1,
                verdict=RiskVerdict.REJECTED,
                evaluated=True,
            )

    def test_the_decision_id_is_deterministic_from_the_composite_key(self) -> None:
        a = fx.engine().evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        b = fx.engine().evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(1))
        self.assertEqual(a.risk_decision_id, b.risk_decision_id)

    def test_each_sequence_gets_a_distinct_id(self) -> None:
        a = fx.engine().evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        b = fx.engine().evaluate(fx.intent(), fx.state(), sequence_no=2, at=at(0))
        self.assertNotEqual(a.risk_decision_id, b.risk_decision_id)


class TestResizing(unittest.TestCase):
    """Brief §11: distinguish requested, approved and rejected quantity."""

    def test_resizing_is_off_by_default(self) -> None:
        """Silently trading less than a strategy asked is opt-in."""
        decision = fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_order_quantity=5)), it=fx.intent(quantity=10)
        )
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)
        self.assertEqual(decision.approved_quantity, 0)

    def test_a_size_breach_can_be_resized_when_enabled(self) -> None:
        decision = fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_order_quantity=5), allow_resizing=True),
            it=fx.intent(quantity=10),
        )
        self.assertIs(decision.verdict, RiskVerdict.MODIFIED)
        self.assertEqual(decision.requested_quantity, 10)
        self.assertEqual(decision.approved_quantity, 5)
        self.assertIsNotNone(decision.approved_until)

    def test_a_non_size_breach_is_never_resized(self) -> None:
        """Trading less does not make a stale state fresh."""
        decision = fx.evaluate(
            eng=fx.engine(allow_resizing=True), st=fx.state(quality_status="UNRELIABLE")
        )
        self.assertIs(decision.verdict, RiskVerdict.REJECTED)

    def test_the_intent_is_never_mutated(self) -> None:
        intent = fx.intent(quantity=10)
        before = intent.content_digest
        fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_order_quantity=5), allow_resizing=True), it=intent
        )
        self.assertEqual(intent.total_quantity, 10)
        self.assertEqual(intent.content_digest, before)

    def test_a_multi_leg_intent_is_reduced_pro_rata(self) -> None:
        """A spread scaled unevenly would become a different position."""
        legs = (
            IntentLeg(
                instrument_id=fx.INSTRUMENT,
                side=Side.BUY,
                quantity=10,
                order_type=OrderType.MARKET,
            ),
            IntentLeg(
                instrument_id=fx.OTHER_INSTRUMENT,
                side=Side.SELL,
                quantity=10,
                order_type=OrderType.MARKET,
            ),
        )
        decision = fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_order_quantity=6), allow_resizing=True),
            it=fx.intent(legs=legs),
        )
        self.assertIs(decision.verdict, RiskVerdict.MODIFIED)
        self.assertEqual(dict(decision.approved_legs), {0: 8, 1: 8})

    def test_approved_can_never_exceed_requested(self) -> None:
        with self.assertRaises(ValueError) as caught:
            RiskDecisionRecord(
                intent_id="int_x",
                sequence_no=1,
                verdict=RiskVerdict.APPROVED,
                evaluated=True,
                requested_quantity=5,
                approved_quantity=6,
                approved_until=at(1),
            )
        self.assertIn("never enlarge", str(caught.exception))

    def test_a_rejection_cannot_approve_a_quantity(self) -> None:
        with self.assertRaises(ValueError):
            RiskDecisionRecord(
                intent_id="int_x",
                sequence_no=1,
                verdict=RiskVerdict.REJECTED,
                evaluated=True,
                requested_quantity=5,
                approved_quantity=1,
            )


if __name__ == "__main__":
    unittest.main()
