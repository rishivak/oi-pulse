"""RiskState identity, RiskPolicy versioning, determinism and reproducibility.

Phase 9 brief §1, §2, §7, §8, §12, §14. The claim under test:

> Identical semantic inputs must produce identical RiskState identity/content.

and its consequence for decisions: the same intent, policy and state must produce
the same verdict and the same `inputs_digest`, whatever order the inputs arrived in
or when the process ran.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from oipulse.trading.risk import (
    CONSERVATIVE_LIMITS,
    ExposureSnapshot,
    KillSwitchState,
    RiskLimits,
    RiskPolicy,
    build_exposure,
    inputs_digest_for,
)
from tests.phase9 import _fixtures as fx
from tests.phase9._fixtures import at


class TestRiskStateIdentity(unittest.TestCase):
    def test_identical_content_produces_an_identical_ref(self) -> None:
        self.assertEqual(fx.state().risk_state_ref, fx.state().risk_state_ref)

    def test_reordered_positions_produce_the_same_ref(self) -> None:
        """Brief §14: row order must not change identity."""
        a = fx.position(fx.INSTRUMENT, 10)
        b = fx.position(fx.OTHER_INSTRUMENT, -5)
        self.assertEqual(
            fx.state(positions=(a, b)).risk_state_ref,
            fx.state(positions=(b, a)).risk_state_ref,
        )

    def test_reordered_pending_intents_produce_the_same_ref(self) -> None:
        self.assertEqual(
            fx.state(pending_intent_ids=("b", "a", "c")).risk_state_ref,
            fx.state(pending_intent_ids=("c", "a", "b")).risk_state_ref,
        )

    def test_reordered_exposure_buckets_produce_the_same_ref(self) -> None:
        forward = ExposureSnapshot(
            gross_notional=Decimal(100),
            by_underlying=((1, Decimal(60)), (2, Decimal(40))),
        )
        backward = ExposureSnapshot(
            gross_notional=Decimal(100),
            by_underlying=((2, Decimal(40)), (1, Decimal(60))),
        )
        self.assertEqual(
            fx.state(exposure=forward).risk_state_ref,
            fx.state(exposure=backward).risk_state_ref,
        )

    def test_reordered_halted_strategies_produce_the_same_ref(self) -> None:
        self.assertEqual(
            fx.state(kill_switch=KillSwitchState(halted_strategies=("b", "a"))).risk_state_ref,
            fx.state(kill_switch=KillSwitchState(halted_strategies=("a", "b"))).risk_state_ref,
        )

    def test_a_different_position_produces_a_different_ref(self) -> None:
        """The other direction: identity must actually discriminate."""
        self.assertNotEqual(
            fx.state(positions=(fx.position(quantity=10),)).risk_state_ref,
            fx.state(positions=(fx.position(quantity=11),)).risk_state_ref,
        )

    def test_a_different_cash_balance_produces_a_different_ref(self) -> None:
        self.assertNotEqual(
            fx.state(cash="1000").risk_state_ref, fx.state(cash="1001").risk_state_ref
        )

    def test_the_ref_does_not_depend_on_when_it_was_computed(self) -> None:
        import time as wallclock

        first = fx.state().risk_state_ref
        wallclock.sleep(0.01)
        self.assertEqual(first, fx.state().risk_state_ref)

    def test_a_state_cannot_know_less_than_the_fact_it_describes(self) -> None:
        with self.assertRaises(ValueError) as caught:
            fx.state(as_of_minute=3, knowledge_minute=1)
        self.assertIn("precedes as_of", str(caught.exception))


class TestRiskStateQueries(unittest.TestCase):
    def test_an_unknown_grouping_yields_none_not_zero(self) -> None:
        """Zero would pass an underlying limit that was never evaluated."""
        state = fx.state(positions=(fx.position(quantity=5, underlying_id=None),))
        self.assertIsNone(state.position_in_underlying(fx.UNDERLYING))

    def test_available_cash_excludes_reservations(self) -> None:
        state = fx.state(cash="1000", reserved_cash=Decimal(300))
        self.assertEqual(state.available_cash, Decimal(700))

    def test_a_mark_is_returned_only_when_supplied(self) -> None:
        state = fx.state(marks=((fx.INSTRUMENT, Decimal(42)),))
        self.assertEqual(state.mark_for(fx.INSTRUMENT), Decimal(42))
        self.assertIsNone(state.mark_for(999999))


class TestBuildExposure(unittest.TestCase):
    def test_an_unpriced_position_makes_the_totals_absent_not_understated(self) -> None:
        """A gross that silently omitted an unpriced position would read as
        headroom that does not exist."""
        exposure = build_exposure(
            (fx.position(quantity=10), fx.position(fx.OTHER_INSTRUMENT, 5, mark=None))
        )
        self.assertIsNone(exposure.gross_notional)

    def test_a_fully_priced_book_aggregates(self) -> None:
        exposure = build_exposure(
            (
                fx.position(fx.INSTRUMENT, 10, mark="100"),
                fx.position(fx.OTHER_INSTRUMENT, -5, mark="200"),
            )
        )
        self.assertEqual(exposure.gross_notional, Decimal(2000))
        self.assertEqual(exposure.net_notional, Decimal(0))

    def test_aggregation_is_order_independent(self) -> None:
        a = fx.position(fx.INSTRUMENT, 10, mark="100")
        b = fx.position(fx.OTHER_INSTRUMENT, 5, mark="200")
        self.assertEqual(build_exposure((a, b)), build_exposure((b, a)))


class TestRiskPolicyVersioning(unittest.TestCase):
    """Brief §8: a limit change must produce a distinguishable identity."""

    def test_the_digest_is_stable_for_identical_policies(self) -> None:
        self.assertEqual(fx.policy().policy_digest, fx.policy().policy_digest)

    def test_every_limit_change_changes_the_digest(self) -> None:
        """Walks the limit fields rather than spot-checking three of them."""
        import dataclasses

        base = fx.policy()
        changes: dict[str, object] = {
            "max_position_per_instrument": 7,
            "max_order_quantity": 7,
            "max_order_notional": Decimal(7),
            "max_deployed_capital": Decimal(7),
            "max_leverage": Decimal(7),
            "max_daily_loss": Decimal(7),
            "max_drawdown": Decimal(7),
            "max_net_delta": Decimal(7),
            "max_gross_vega": Decimal(7),
            "max_underlying_concentration": Decimal("0.07"),
            "max_state_staleness": timedelta(seconds=7),
            "min_state_coverage": Decimal("0.07"),
            "reject_unreliable_state": False,
            "require_healthy_venue": False,
            "max_orders_per_interval": 7,
        }
        for field, value in changes.items():
            with self.subTest(limit=field):
                altered = dataclasses.replace(
                    base, limits=dataclasses.replace(base.limits, **{field: value})
                )
                self.assertNotEqual(base.policy_digest, altered.policy_digest)

    def test_a_version_bump_changes_the_digest(self) -> None:
        self.assertNotEqual(fx.policy(version=1).policy_digest, fx.policy(version=2).policy_digest)

    def test_a_description_change_changes_the_digest(self) -> None:
        """A policy whose stated purpose changed is a different policy to a reader."""
        self.assertNotEqual(
            fx.policy(description="a").policy_digest,
            fx.policy(description="b").policy_digest,
        )

    def test_an_unset_limit_is_distinguishable_from_a_set_one(self) -> None:
        import dataclasses

        base = fx.policy()
        unset = dataclasses.replace(
            base, limits=dataclasses.replace(base.limits, max_order_quantity=None)
        )
        self.assertNotEqual(base.policy_digest, unset.policy_digest)

    def test_configured_count_reports_relaxation(self) -> None:
        """`18` Phase 9's mitigation: relaxation must be audited, not invisible."""
        self.assertGreater(CONSERVATIVE_LIMITS.configured_count(), 20)
        self.assertLess(RiskLimits().configured_count(), 5)

    def test_a_policy_must_be_identified_and_versioned(self) -> None:
        for kwargs in ({"policy_id": ""}, {"version": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RiskPolicy(
                    policy_id=str(kwargs.get("policy_id", "P")),
                    version=int(kwargs.get("version", 1)),
                    limits=RiskLimits(),
                )

    def test_there_is_no_default_policy_object(self) -> None:
        """A policy that materialised from nothing would be limits nobody chose."""
        import inspect

        required = [
            name
            for name, parameter in inspect.signature(RiskPolicy).parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        for field in ("policy_id", "version", "limits"):
            self.assertIn(field, required)


class TestEvaluationDeterminism(unittest.TestCase):
    """Brief §5 and §38: same input, same decision."""

    def test_the_same_inputs_produce_the_same_decision_digest(self) -> None:
        engine = fx.engine()
        first = engine.evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        second = engine.evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        self.assertEqual(first.decision_digest, second.decision_digest)
        self.assertEqual(first.inputs_digest, second.inputs_digest)

    def test_the_digest_excludes_the_evaluation_time(self) -> None:
        """Two runs a minute apart on identical inputs are the same decision.

        Including the time would make the digest unable to demonstrate
        reproducibility, which is its only purpose.
        """
        engine = fx.engine()
        early = engine.evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        late = engine.evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(1))
        self.assertEqual(early.decision_digest, late.decision_digest)
        self.assertNotEqual(early.risk_evaluation_time, late.risk_evaluation_time)

    def test_a_different_intent_produces_a_different_inputs_digest(self) -> None:
        engine = fx.engine()
        a = engine.evaluate(fx.intent(quantity=10), fx.state(), sequence_no=1, at=at(0))
        b = engine.evaluate(fx.intent(quantity=11), fx.state(), sequence_no=1, at=at(0))
        self.assertNotEqual(a.inputs_digest, b.inputs_digest)

    def test_a_different_policy_produces_a_different_inputs_digest(self) -> None:
        a = fx.engine().evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        b = fx.engine(lim=fx.limits(max_order_quantity=99)).evaluate(
            fx.intent(), fx.state(), sequence_no=1, at=at(0)
        )
        self.assertNotEqual(a.inputs_digest, b.inputs_digest)

    def test_a_different_state_produces_a_different_inputs_digest(self) -> None:
        engine = fx.engine()
        a = engine.evaluate(fx.intent(), fx.state(cash="1000000"), sequence_no=1, at=at(0))
        b = engine.evaluate(fx.intent(), fx.state(cash="999999"), sequence_no=1, at=at(0))
        self.assertNotEqual(a.inputs_digest, b.inputs_digest)

    def test_the_inputs_digest_is_exactly_the_three_inputs(self) -> None:
        """Re-derivable by a verifier from what the decision records."""
        decision = fx.evaluate()
        expected = inputs_digest_for(
            intent_digest=fx.intent().content_digest,
            policy_digest=fx.policy().policy_digest,
            risk_state_ref=fx.state().risk_state_ref,
        )
        self.assertEqual(decision.inputs_digest, expected)

    def test_the_risk_state_ref_matches_the_state_evaluated(self) -> None:
        state = fx.state(cash="777777")
        decision = fx.engine().evaluate(fx.intent(), state, sequence_no=1, at=at(0))
        self.assertEqual(decision.risk_state_ref, state.risk_state_ref)

    def test_the_evidence_order_is_stable(self) -> None:
        """A fixed check order keeps the digest stable across runs."""
        a = fx.evaluate()
        b = fx.evaluate()
        self.assertEqual(
            [limit.limit_id for limit in a.limits_evaluated],
            [limit.limit_id for limit in b.limits_evaluated],
        )

    def test_a_reordered_state_produces_an_identical_decision(self) -> None:
        """Brief §14 and §39, end to end through the engine."""
        p1 = fx.position(fx.INSTRUMENT, 10)
        p2 = fx.position(fx.OTHER_INSTRUMENT, 5)
        engine = fx.engine()
        forward = engine.evaluate(
            fx.intent(), fx.state(positions=(p1, p2)), sequence_no=1, at=at(0)
        )
        backward = engine.evaluate(
            fx.intent(), fx.state(positions=(p2, p1)), sequence_no=1, at=at(0)
        )
        self.assertEqual(forward.decision_digest, backward.decision_digest)


class TestConservativeDefaults(unittest.TestCase):
    def test_the_shipped_defaults_configure_every_category(self) -> None:
        """`18` Phase 9: conservative defaults requiring explicit relaxation."""
        decision = fx.conservative_engine().evaluate(
            fx.intent(quantity=1), fx.state(), sequence_no=1, at=at(0)
        )
        statuses = {limit.status.value for limit in decision.limits_evaluated}
        self.assertNotIn(
            "NOT_CONFIGURED",
            statuses,
            "the conservative defaults must leave no limit unconfigured",
        )

    def test_an_empty_policy_reports_every_limit_as_relaxed(self) -> None:
        """The audit trail of a relaxation: recorded, never invisible."""
        decision = fx.engine(pol=RiskPolicy("EMPTY", 1, RiskLimits())).evaluate(
            fx.intent(), fx.state(), sequence_no=1, at=at(0)
        )
        relaxed = [
            limit.limit_id
            for limit in decision.limits_evaluated
            if limit.status.value == "NOT_CONFIGURED"
        ]
        self.assertGreater(len(relaxed), 15)
        # The kill switch and cash sufficiency are never optional.
        ids = {limit.limit_id for limit in decision.limits_evaluated}
        self.assertIn("kill_switch", ids)
        self.assertIn("cash_sufficiency", ids)


if __name__ == "__main__":
    unittest.main()
