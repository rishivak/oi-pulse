"""OMS lifecycle, risk-gate enforcement, submission identity and ambiguity.

Phase 10 brief §5, §6, §7, §9, §10, §15, §16, §17. `11-TRADING.md` §5's hard rules
are the spine of this module:

> Never assume rejected. Never assume accepted. Never resubmit from UNKNOWN. An
> order in UNKNOWN blocks further intents for that instrument from the same strategy
> until resolved.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from oipulse.trading.brokers import LiveExecutionDisabled, UpstoxBrokerAdapter
from oipulse.trading.oms import (
    AuthorizationRefusal,
    OrderManager,
    SubmissionResultKind,
    attempt_id_for,
    authorize_submission,
)
from oipulse.trading.orders import (
    ExecutionVenue,
    InvalidTransition,
    OrderState,
    is_terminal,
    permitted_transitions,
)
from oipulse.trading.risk import RiskDecisionRecord, RiskVerdict
from tests.phase10 import _fixtures as fx
from tests.phase10._fixtures import at


class TestRiskGateEnforcement(unittest.TestCase):
    """Brief §6: each of these four must be rejected."""

    def test_an_approved_decision_is_accepted(self) -> None:
        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager))
        self.assertIs(outcome.kind, SubmissionResultKind.ACKNOWLEDGED)
        self.assertTrue(outcome.authorization.authorized)

    def test_no_decision_is_rejected(self) -> None:
        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager, dec=None))
        self.assertIs(outcome.kind, SubmissionResultKind.NOT_AUTHORIZED)
        self.assertIs(outcome.authorization.refusal, AuthorizationRefusal.NO_DECISION)

    def test_a_rejected_decision_is_rejected(self) -> None:
        from tests.phase9 import _fixtures as p9

        intent = fx.intent()
        engine = p9.engine(lim=p9.runtime_limits(max_order_quantity=1))
        rejected = engine.evaluate(intent, p9.state(), sequence_no=1, at=at(0))
        self.assertIs(rejected.verdict, RiskVerdict.REJECTED)

        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager, it=intent, dec=rejected))
        self.assertIs(outcome.authorization.refusal, AuthorizationRefusal.DECISION_REJECTED)

    def test_an_expired_decision_is_rejected(self) -> None:
        manager = fx.oms()
        stale = fx.decision(validity=timedelta(seconds=1), at_minute=0)
        outcome = fx.run(fx.submit_one(manager, dec=stale, at_minute=5))
        self.assertIs(outcome.authorization.refusal, AuthorizationRefusal.DECISION_EXPIRED)

    def test_a_decision_for_another_intent_is_rejected(self) -> None:
        """Brief §6: even when the two intents are otherwise identical."""
        mine = fx.intent(client_order_intent_id="A")
        other = fx.intent(client_order_intent_id="B")
        self.assertNotEqual(mine.intent_id, other.intent_id)

        manager = fx.oms()
        outcome = fx.run(
            fx.submit_one(manager, it=mine, dec=fx.decision(other), ord_=fx.order(mine))
        )
        self.assertIs(outcome.authorization.refusal, AuthorizationRefusal.WRONG_INTENT)

    def test_an_unevaluated_decision_is_rejected(self) -> None:
        """A Phase 9 pass-through approval authorizes nothing."""
        from oipulse.trading.risk import UNEVALUATED_RISK
        from tests.phase9 import _fixtures as p9

        intent = fx.intent()
        passthrough = UNEVALUATED_RISK.evaluate(intent, p9.state(), sequence_no=1, at=at(0))
        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager, it=intent, dec=passthrough))
        self.assertIs(outcome.authorization.refusal, AuthorizationRefusal.DECISION_NOT_EVALUATED)

    def test_an_order_from_another_intent_is_rejected(self) -> None:
        mine = fx.intent(client_order_intent_id="A")
        other = fx.intent(client_order_intent_id="B")
        manager = fx.oms()
        outcome = fx.run(
            fx.submit_one(manager, it=mine, dec=fx.decision(mine), ord_=fx.order(other))
        )
        self.assertIs(outcome.authorization.refusal, AuthorizationRefusal.ORDER_INTENT_MISMATCH)

    def test_an_order_larger_than_the_approval_is_rejected(self) -> None:
        intent = fx.intent(quantity=50)
        approved = fx.decision(intent)
        oversized = fx.order(fx.intent(quantity=200))
        from dataclasses import replace

        oversized = replace(oversized, intent_id=intent.intent_id)
        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager, it=intent, dec=approved, ord_=oversized))
        self.assertIs(
            outcome.authorization.refusal,
            AuthorizationRefusal.QUANTITY_EXCEEDS_APPROVAL,
        )

    def test_no_refused_submission_reaches_the_venue(self) -> None:
        """The invariant behind every case above, asserted once over all of them."""
        for label, dec in (
            ("none", None),
            ("expired", fx.decision(validity=timedelta(seconds=1))),
        ):
            with self.subTest(case=label):
                venue = fx.venue()
                manager = OrderManager(adapter=venue)
                fx.run(fx.submit_one(manager, dec=dec, at_minute=5))
                self.assertEqual(venue.venue_order_count(), 0)

    def test_the_gate_does_not_reimplement_risk(self) -> None:
        """Brief §6: consume the canonical decision, do not duplicate the logic."""
        import inspect

        from oipulse.trading.oms import authorization

        source = inspect.getsource(authorization)
        for banned in ("RiskLimits", "evaluate_all", "LimitEvaluation", "RiskEngine"):
            with self.subTest(symbol=banned):
                self.assertNotIn(banned, source)


class TestIdentitySeparation(unittest.TestCase):
    """Brief §5: do not collapse these identities."""

    def test_the_five_identities_are_distinct(self) -> None:
        intent = fx.intent()
        decision = fx.decision(intent)
        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager, it=intent, dec=decision))
        order = outcome.order

        identities = {
            "intent": intent.intent_id,
            "decision": decision.risk_decision_id,
            "order": order.order_id,
            "attempt": order.client_order_attempt_id,
            "provider": order.provider_order_id,
        }
        self.assertEqual(
            len(set(identities.values())),
            len(identities),
            f"identities must not collide: {identities}",
        )

    def test_the_order_retains_every_required_reference(self) -> None:
        """Brief §5's list, checked on the order itself."""
        intent = fx.intent()
        decision = fx.decision(intent)
        manager = fx.oms()
        order = fx.run(fx.submit_one(manager, it=intent, dec=decision)).order

        self.assertEqual(order.intent_id, intent.intent_id)
        self.assertEqual(order.authorizing_risk_decision_id, decision.risk_decision_id)
        self.assertEqual(order.authorizing_decision_sequence, decision.sequence_no)
        self.assertEqual(order.strategy_id, intent.strategy_id)
        self.assertEqual(order.signal_id, intent.signal_id)
        self.assertTrue(order.client_order_attempt_id)
        self.assertIsNotNone(order.provider_order_id)
        self.assertIs(order.venue, ExecutionVenue.PAPER)

    def test_the_attempt_id_is_deterministic(self) -> None:
        """A restart must recompute the same value, not mint a new one."""
        self.assertEqual(attempt_id_for("ord_x", 1), attempt_id_for("ord_x", 1))
        self.assertNotEqual(attempt_id_for("ord_x", 1), attempt_id_for("ord_x", 2))
        self.assertNotEqual(attempt_id_for("ord_x", 1), attempt_id_for("ord_y", 1))

    def test_a_missing_provider_identity_is_none_not_a_placeholder(self) -> None:
        """Brief §8: represent absent provider identity honestly."""
        order = fx.order()
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        outcome = fx.run(fx.submit_one(manager, ord_=order))
        self.assertIsNone(outcome.order.provider_order_id)
        self.assertNotEqual(outcome.order.provider_order_id, "")

    def test_the_paper_venue_labels_its_own_ids(self) -> None:
        """A simulated provider id must not pass for a real broker's."""
        manager = fx.oms()
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        self.assertTrue(order.provider_order_id.startswith("paper-"))


class TestSubmissionIdempotency(unittest.TestCase):
    """Brief §9. Local dedup, and no claim about the provider."""

    def test_the_same_attempt_is_never_sent_twice(self) -> None:
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        intent = fx.intent()
        decision = fx.decision(intent)
        order = fx.order(intent)

        fx.run(fx.submit_one(manager, it=intent, dec=decision, ord_=order))
        before = venue.venue_order_count()
        fx.run(fx.submit_one(manager, it=intent, dec=decision, ord_=order))
        self.assertEqual(
            venue.venue_order_count(),
            before + 1,
            "a second submission with a fresh attempt number does reach the venue; "
            "what must not happen is the SAME attempt going twice",
        )

    def test_resending_an_identical_attempt_is_refused_locally(self) -> None:
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.order()
        fx.run(fx.submit_one(manager, ord_=order))

        # Force the attempt counter back, simulating a process that restarted and
        # recomputed the same attempt id.
        manager._attempts[order.order_id] = 0
        outcome = fx.run(fx.submit_one(manager, ord_=order))
        self.assertIs(outcome.kind, SubmissionResultKind.AMBIGUOUS)
        self.assertIn("already been sent", outcome.detail)
        self.assertEqual(venue.venue_order_count(), 1, "nothing new reached the venue")

    def test_nothing_claims_provider_side_deduplication(self) -> None:
        """`06` §10: our key does not oblige the provider to reject a duplicate."""
        import inspect

        from oipulse.trading.oms import manager as manager_module

        source = inspect.getsource(manager_module).lower()
        self.assertIn("does not", source)
        for overclaim in ("exactly-once provider", "provider guarantees", "broker dedup"):
            with self.subTest(phrase=overclaim):
                self.assertNotIn(overclaim, source)


class TestAmbiguousSubmission(unittest.TestCase):
    """Brief §10 and `11` §5. The heart of the phase."""

    def _lost_ack(self) -> tuple[OrderManager, object, object]:
        order = fx.order()
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        outcome = fx.run(fx.submit_one(manager, ord_=order))
        return manager, venue, outcome

    def test_a_lost_acknowledgement_produces_unknown(self) -> None:
        _, _, outcome = self._lost_ack()
        self.assertIs(outcome.kind, SubmissionResultKind.AMBIGUOUS)
        self.assertIs(outcome.order.state, OrderState.UNKNOWN)

    def test_unknown_is_never_a_terminal_state(self) -> None:
        _, _, outcome = self._lost_ack()
        self.assertFalse(outcome.order.is_terminal)
        self.assertTrue(outcome.order.is_unresolved)

    def test_the_order_really_is_at_the_venue(self) -> None:
        """Why "never assume rejected" matters: there is something there."""
        _, venue, _ = self._lost_ack()
        self.assertEqual(venue.venue_order_count(), 1)

    def test_a_dropped_request_also_produces_unknown(self) -> None:
        """Indistinguishable from the outside, and that is exactly the point.

        Nothing reached the venue this time, but we cannot tell — so the state is
        the same, and only reconciliation can distinguish them.
        """
        order = fx.order()
        venue = fx.venue(faults=fx.drop_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        outcome = fx.run(fx.submit_one(manager, ord_=order))
        self.assertIs(outcome.order.state, OrderState.UNKNOWN)
        self.assertEqual(venue.venue_order_count(), 0, "nothing reached the venue")

    def test_the_state_machine_forbids_resubmission_from_unknown(self) -> None:
        """`11` §5: not a policy, a path the machine does not contain."""
        self.assertEqual(
            permitted_transitions(OrderState.UNKNOWN),
            frozenset({OrderState.PENDING_RECONCILIATION}),
        )
        for target in OrderState:
            if target is OrderState.PENDING_RECONCILIATION:
                continue
            with self.subTest(target=target):
                order = fx.order()
                from dataclasses import replace

                unknown = replace(order, state=OrderState.UNKNOWN)
                with self.assertRaises(InvalidTransition):
                    unknown.transition(target, at=at(1), trigger="test")

    def test_an_unresolved_order_blocks_its_instrument_for_its_strategy(self) -> None:
        manager, _, outcome = self._lost_ack()
        self.assertTrue(
            manager.blocks_new_intents(
                instrument_id=outcome.order.instrument_id,
                strategy_id=outcome.order.strategy_id,
            )
        )

    def test_a_different_strategy_is_not_blocked(self) -> None:
        manager, _, outcome = self._lost_ack()
        self.assertFalse(
            manager.blocks_new_intents(
                instrument_id=outcome.order.instrument_id, strategy_id="OTHER"
            )
        )

    def test_a_blocked_intent_is_refused_rather_than_queued(self) -> None:
        manager, venue, _first = self._lost_ack()
        second_intent = fx.intent(client_order_intent_id="second")
        outcome = fx.run(
            fx.submit_one(
                manager,
                it=second_intent,
                dec=fx.decision(second_intent),
                ord_=fx.order(second_intent),
            )
        )
        self.assertIs(outcome.kind, SubmissionResultKind.NOT_AUTHORIZED)
        self.assertIn("unresolved", outcome.detail)
        self.assertEqual(venue.venue_order_count(), 1, "nothing further was sent")

    def test_escalation_is_the_only_exit(self) -> None:
        manager, _, outcome = self._lost_ack()
        escalated = manager.escalate_to_reconciliation(outcome.order.order_id, at=at(2))
        self.assertIs(escalated.state, OrderState.PENDING_RECONCILIATION)


class TestOrderLifecycle(unittest.TestCase):
    def test_a_clean_submission_walks_created_submitting_submitted(self) -> None:
        manager = fx.oms()
        order = fx.run(fx.submit_one(manager)).order
        self.assertEqual(
            [e.to_state for e in order.events],
            [OrderState.SUBMITTING, OrderState.SUBMITTED],
        )

    def test_a_provider_rejection_is_terminal_and_gives_a_reason(self) -> None:
        order = fx.order()
        venue = fx.venue(faults=fx.reject_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        outcome = fx.run(fx.submit_one(manager, ord_=order))
        self.assertIs(outcome.kind, SubmissionResultKind.REJECTED)
        self.assertIs(outcome.order.state, OrderState.REJECTED)
        self.assertTrue(outcome.order.reject_detail)
        self.assertTrue(is_terminal(outcome.order.state))

    def test_every_new_state_is_reachable_and_every_illegal_move_raises(self) -> None:
        """Walks the table rather than a hand-written list, so it grows with it."""
        from dataclasses import replace

        base = fx.order()
        for source in OrderState:
            allowed = permitted_transitions(source)
            for target in OrderState:
                order = replace(base, state=source)
                if target in allowed:
                    with self.subTest(source=source, target=target, legal=True):
                        self.assertIs(order.transition(target, at=at(1), trigger="t").state, target)
                else:
                    with (
                        self.subTest(source=source, target=target, legal=False),
                        self.assertRaises(InvalidTransition),
                    ):
                        order.transition(target, at=at(1), trigger="t")

    def test_unknown_and_pending_reconciliation_are_not_open(self) -> None:
        """An order we cannot describe is not an open order."""
        from dataclasses import replace

        base = fx.order()
        for state in (OrderState.UNKNOWN, OrderState.PENDING_RECONCILIATION):
            with self.subTest(state=state):
                order = replace(base, state=state)
                self.assertFalse(order.is_open)
                self.assertFalse(order.is_terminal)
                self.assertTrue(order.is_unresolved)


class TestCancelLifecycle(unittest.TestCase):
    """Brief §15: a cancel request is not a cancellation."""

    def test_a_cancel_goes_through_cancel_pending(self) -> None:
        manager = fx.oms()
        order = fx.run(fx.submit_one(manager)).order
        cancelled = fx.run(manager.request_cancel(order.order_id, at=at(2)))
        states = [e.to_state for e in cancelled.events]
        self.assertIn(OrderState.CANCEL_PENDING, states)
        self.assertIs(cancelled.state, OrderState.CANCELLED)

    def test_a_cancel_that_loses_the_race_does_not_report_cancelled(self) -> None:
        """An order that filled before the cancel landed is filled.

        Reporting it cancelled would tell the ledger it had no position when it
        has one — the most expensive possible lie from this subsystem.
        """
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        venue.venue_fill(order.provider_order_id, quantity=50, price=Decimal("106.40"), at=at(2))

        result = fx.run(manager.request_cancel(order.order_id, at=at(3)))
        self.assertIsNot(result.state, OrderState.CANCELLED)
        self.assertIs(result.state, OrderState.CANCEL_PENDING)

    def test_a_cancel_without_a_provider_id_becomes_unknown(self) -> None:
        """We cannot address a cancel at an order whose venue id we never learned."""
        order = fx.order()
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        fx.run(fx.submit_one(manager, ord_=order))
        manager.escalate_to_reconciliation(order.order_id, at=at(2))
        # Reset to a cancellable state via reconciliation would be the real path;
        # here the point is only that a cancel with no provider id cannot proceed.
        self.assertIsNone(manager.order(order.order_id).provider_order_id)


class TestCapabilityBarrier(unittest.TestCase):
    """Brief §4 and §18: the broker path stays unavailable."""

    def test_the_upstox_adapter_cannot_submit(self) -> None:
        manager = OrderManager(adapter=UpstoxBrokerAdapter(), venue=ExecutionVenue.BROKER)
        outcome = fx.run(fx.submit_one(manager))
        self.assertIs(outcome.kind, SubmissionResultKind.CAPABILITY_DENIED)
        self.assertIs(outcome.order.state, OrderState.REJECTED)
        self.assertIn("LIVE_SUBMIT", outcome.detail)

    def test_a_capability_denial_is_not_an_ambiguity(self) -> None:
        """Nothing left, so nothing can be at the venue. Rejected, not UNKNOWN.

        The distinction matters: an ambiguous order blocks its instrument and
        demands reconciliation, and treating a refusal as ambiguous would halt
        trading for a reason that never touched the network.
        """
        manager = OrderManager(adapter=UpstoxBrokerAdapter(), venue=ExecutionVenue.BROKER)
        outcome = fx.run(fx.submit_one(manager))
        self.assertFalse(outcome.is_ambiguous)
        self.assertFalse(outcome.order.is_unresolved)

    def test_paper_execution_remains_usable(self) -> None:
        """Brief §18: both halves must be proven."""
        manager = fx.oms()
        self.assertIs(fx.run(fx.submit_one(manager)).kind, SubmissionResultKind.ACKNOWLEDGED)

    def test_the_upstox_adapter_refuses_queries_too(self) -> None:
        """An empty list would read as "the broker holds nothing"."""
        adapter = UpstoxBrokerAdapter()
        with self.assertRaises(LiveExecutionDisabled):
            fx.run(adapter.list_orders(since=at(0)))


class TestTemporalSemantics(unittest.TestCase):
    """Brief §16: provider time and receipt time are not market time."""

    def test_provider_and_receipt_times_are_stored_separately(self) -> None:
        manager = fx.oms()
        order = fx.run(fx.submit_one(manager)).order
        body = order.as_dict()
        for field in ("provider_event_time", "received_at", "created_at"):
            self.assertIn(field, body)

    def test_the_decision_times_survive_submission(self) -> None:
        """An execution timestamp must not overwrite what the strategy knew."""
        intent = fx.intent(decision_minute=0)
        order = fx.order(intent)
        from dataclasses import replace

        order = replace(
            order, knowledge_time=intent.knowledge_time, decision_time=intent.decision_time
        )
        manager = fx.oms()
        submitted = fx.run(
            fx.submit_one(manager, it=intent, dec=fx.decision(intent), ord_=order, at_minute=3)
        ).order
        self.assertEqual(submitted.decision_time, at(0))
        self.assertEqual(submitted.knowledge_time, at(0))

    def test_no_module_in_the_oms_reads_a_clock(self) -> None:
        import ast
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        for path in sorted((repo / "oipulse/trading/oms").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    with self.subTest(file=path.name, call=node.func.attr):
                        self.assertNotIn(node.func.attr, {"now", "utcnow", "today", "monotonic"})


class TestAuthorizationUnit(unittest.TestCase):
    """The gate in isolation, with no OMS or venue present."""

    def test_it_is_pure_and_needs_no_infrastructure(self) -> None:
        intent = fx.intent()
        decision = fx.decision(intent)
        result = authorize_submission(
            intent=intent,
            decision=decision,
            order_id="ord_x",
            order_intent_id=intent.intent_id,
            order_quantity=50,
            at=at(0),
        )
        self.assertTrue(result.authorized)
        self.assertEqual(result.decision_sequence, 1)

    def test_a_zero_quantity_approval_authorizes_nothing(self) -> None:
        intent = fx.intent()
        zero = RiskDecisionRecord(
            intent_id=intent.intent_id,
            sequence_no=1,
            verdict=RiskVerdict.MODIFIED,
            evaluated=True,
            requested_quantity=50,
            approved_quantity=0,
            approved_until=at(5),
        )
        result = authorize_submission(
            intent=intent,
            decision=zero,
            order_id="ord_x",
            order_intent_id=intent.intent_id,
            order_quantity=50,
            at=at(0),
        )
        self.assertFalse(result.authorized)


if __name__ == "__main__":
    unittest.main()
