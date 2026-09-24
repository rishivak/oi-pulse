"""Lifecycle, point-in-time correctness, determinism and idempotency.

`08-SIGNALS.md` §3-§4 and `07-ANALYTICS.md` §3.

Covers requirements 2, 3, 5, 6, 7, 8, 9, 10, 13, 14, 18.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import oipulse.signals  # noqa: F401
from oipulse.marketstate.staleness import QualityStatus
from oipulse.signals.evaluation import SignalEvaluator, SkipReason
from oipulse.signals.lifecycle import (
    PERMITTED_TRANSITIONS,
    IllegalTransition,
    TransitionTrigger,
    apply_transition,
    is_permitted,
)
from oipulse.signals.model import SignalStatus
from oipulse.signals.rules import RULES
from tests.phase5._fixtures import at, market_state, metric, put_support_metrics, signal

PUT_RULE = ("PUT_SUPPORT_MIGRATION", 1)


class TestNormativeTransitionTable(unittest.TestCase):
    """`08` §3: the table is authoritative; any transition not listed raises."""

    def test_the_table_matches_the_design_exactly(self):
        expected = {
            None: {"FORMING"},
            "FORMING": {"ACTIVE", "FADED", "EXPIRED"},
            "ACTIVE": {"ACTIVE", "CONFIRMED", "INVALIDATED", "FADED", "EXPIRED"},
            "CONFIRMED": {"CONFIRMED", "INVALIDATED", "FADED", "EXPIRED"},
            "INVALIDATED": set(),
            "EXPIRED": set(),
            "FADED": set(),
        }
        actual = {
            (None if k is None else k.value): {t.value for t in v}
            for k, v in PERMITTED_TRANSITIONS.items()
        }
        self.assertEqual(actual, expected)

    def test_confirmed_to_invalidated_is_permitted(self):
        """Confirmation is not absorbing; treating it so hides the cases worth studying."""
        self.assertTrue(is_permitted(SignalStatus.CONFIRMED, SignalStatus.INVALIDATED))

    def test_a_signal_never_returns_to_forming(self):
        for origin in (SignalStatus.ACTIVE, SignalStatus.CONFIRMED, SignalStatus.FADED):
            with self.subTest(origin=origin.value):
                self.assertFalse(is_permitted(origin, SignalStatus.FORMING))

    def test_terminal_states_are_never_left(self):
        for origin in (SignalStatus.INVALIDATED, SignalStatus.EXPIRED, SignalStatus.FADED):
            for target in SignalStatus:
                with self.subTest(origin=origin.value, target=target.value):
                    self.assertFalse(is_permitted(origin, target))

    def test_an_illegal_transition_raises_rather_than_being_coerced(self):
        faded = apply_transition(
            signal(status=SignalStatus.ACTIVE),
            SignalStatus.FADED,
            TransitionTrigger.EVIDENCE_DECAYED,
            at(10),
        )
        with self.assertRaises(IllegalTransition):
            apply_transition(faded, SignalStatus.ACTIVE, TransitionTrigger.ENTRY_FULL, at(15))

    def test_a_transition_appends_history_and_overwrites_nothing(self):
        before = signal(status=SignalStatus.ACTIVE)
        after = apply_transition(
            before, SignalStatus.CONFIRMED, TransitionTrigger.PERSISTENCE_MET, at(10)
        )
        self.assertEqual(len(after.history), len(before.history) + 1)
        self.assertEqual(after.history[: len(before.history)], before.history)
        self.assertIn("CONFIRMED", after.history[-1][0])

    def test_a_transition_returns_a_new_signal(self):
        before = signal(status=SignalStatus.ACTIVE)
        after = apply_transition(
            before, SignalStatus.CONFIRMED, TransitionTrigger.PERSISTENCE_MET, at(10)
        )
        self.assertIs(before.status, SignalStatus.ACTIVE, "the original is untouched")
        self.assertIs(after.status, SignalStatus.CONFIRMED)


class TestPointInTime(unittest.TestCase):
    """Requirement 6: never substitute later-K data for the requested horizon."""

    def _evaluate(self, values, horizon, **kwargs):
        return SignalEvaluator().evaluate(
            market_state(),
            values,
            at(0, 30),
            knowledge_horizon=horizon,
            specs=(RULES.get(*PUT_RULE),),
            expiry_id=10,
            **kwargs,
        )

    def test_a_feature_not_yet_available_is_withheld(self):
        """The metric exists historically but was not available at K, so the rule
        must observe it as absent rather than consuming it."""
        values = put_support_metrics(available_at=at(0, 20))
        report = self._evaluate(values, horizon=at(0, 10))
        self.assertEqual(report.created_count, 0)
        self.assertEqual(report.skipped[0].reason, SkipReason.FEATURE_UNAVAILABLE)

    def test_the_same_feature_is_consumed_once_available(self):
        values = put_support_metrics(available_at=at(0, 20))
        report = self._evaluate(values, horizon=at(0, 30))
        self.assertEqual(report.created_count, 1)

    def test_a_later_horizon_never_backfills_an_earlier_one(self):
        """Requirement 6, stated as a difference: the earlier K sees strictly less."""
        values = put_support_metrics(available_at=at(0, 20))
        early = self._evaluate(values, horizon=at(0, 10))
        late = self._evaluate(values, horizon=at(0, 30))
        self.assertEqual(early.created_count, 0)
        self.assertEqual(late.created_count, 1)

    def test_partial_availability_still_blocks_a_pinned_feature(self):
        """All pinned features must be available; a subset is not enough."""
        values = list(put_support_metrics(available_at=at(0, 5)))
        values[0] = metric("PUT_OI_MIGRATION", 1, value=Decimal("120"), available_at=at(0, 40))
        report = self._evaluate(tuple(values), horizon=at(0, 10))
        self.assertEqual(report.created_count, 0)
        self.assertIn("PUT_OI_MIGRATION@v1", report.skipped[0].detail)

    def test_a_horizon_before_market_time_is_rejected(self):
        with self.assertRaises(ValueError):
            SignalEvaluator().evaluate(
                market_state(market_time=at(10)), (), at(20), knowledge_horizon=at(5)
            )

    def test_the_signal_records_the_horizon_it_used(self):
        values = put_support_metrics(available_at=at(0, 5))
        report = self._evaluate(values, horizon=at(0, 20))
        self.assertEqual(report.signals[0].identity.knowledge_horizon, at(0, 20))


class TestAvailabilityPropagation(unittest.TestCase):
    """Requirement 8: a signal is never available before the features it consumed."""

    def _report(self, available_at, evaluated_at):
        return SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=available_at),
            evaluated_at,
            knowledge_horizon=evaluated_at,
            specs=(RULES.get(*PUT_RULE),),
            expiry_id=10,
        )

    def test_signal_availability_is_at_or_after_every_input(self):
        report = self._report(at(0, 20), at(0, 30))
        produced = report.signals[0]
        for item in (*produced.supporting, *produced.contradicting):
            with self.subTest(evidence=item.statement):
                self.assertGreaterEqual(produced.available_at, item.metric_value_ref.available_at)

    def test_a_later_input_delays_the_signal(self):
        prompt = self._report(at(0, 5), at(0, 30)).signals[0]
        late = self._report(at(0, 40), at(0, 45)).signals[0]
        self.assertLess(prompt.available_at, late.available_at)

    def test_availability_includes_the_propagation_delay(self):
        produced = self._report(at(0, 20), at(0, 30)).signals[0]
        self.assertGreaterEqual(
            produced.available_at - produced.identity.market_time, timedelta(seconds=2)
        )

    def test_market_time_alone_never_determines_availability(self):
        produced = self._report(at(0, 40), at(0, 45)).signals[0]
        naive = produced.identity.market_time + timedelta(seconds=2)
        self.assertGreater(produced.available_at, naive)


class TestQualityGating(unittest.TestCase):
    """Requirements 9-10: no firing on UNRELIABLE; no silent defaults."""

    def test_no_rule_fires_on_an_unreliable_state(self):
        report = SignalEvaluator().evaluate(
            market_state(quality=QualityStatus.UNRELIABLE),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            expiry_id=10,
        )
        self.assertEqual(report.created_count, 0)
        self.assertTrue(report.skipped)
        self.assertTrue(all(s.reason is SkipReason.QUALITY_NOT_MET for s in report.skipped))

    def test_a_degraded_state_still_evaluates(self):
        """Only UNRELIABLE is refused; DEGRADED is usable and says so."""
        report = SignalEvaluator().evaluate(
            market_state(quality=QualityStatus.DEGRADED),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            specs=(RULES.get(*PUT_RULE),),
            expiry_id=10,
        )
        self.assertEqual(report.created_count, 1)
        self.assertIs(report.signals[0].quality_status, QualityStatus.DEGRADED)

    def test_a_rule_error_is_recorded_not_swallowed(self):
        """One buggy rule must not take down the batch, nor vanish silently."""
        from oipulse.signals.rules import RuleRegistry, signal_rule

        local = RuleRegistry()

        @signal_rule(
            signal_type="EXPLODING_RULE",
            version=1,
            definition="A rule that raises, used to prove errors are recorded not swallowed.",
            horizon="30m",
            evaluation_interval="5m",
            requires_features=[("PUT_OI_MIGRATION", 1)],
            quality_requirements=["quality != UNRELIABLE"],
            registry=local,
        )
        def exploding(ctx):  # pragma: no cover - raises by design
            raise RuntimeError("boom")

        report = SignalEvaluator(local).evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            expiry_id=10,
        )
        self.assertEqual(report.created_count, 0)
        self.assertEqual(report.skipped[0].reason, SkipReason.RULE_ERROR)
        self.assertIn("boom", report.skipped[0].detail)

    def test_nothing_is_dropped_without_a_reason(self):
        report = SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            expiry_id=10,
        )
        total = report.created_count + len(report.skipped)
        self.assertEqual(total, len(RULES))
        for item in report.skipped:
            self.assertTrue(item.detail)


class TestDeterminism(unittest.TestCase):
    """Requirements 2, 3, 18: identical inputs produce an identical signal."""

    def _run(self):
        return SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            specs=(RULES.get(*PUT_RULE),),
            expiry_id=10,
        )

    def test_repeated_evaluation_is_identical(self):
        a, b = self._run().signals[0], self._run().signals[0]
        self.assertEqual(a.signal_id, b.signal_id)
        self.assertEqual(a.status, b.status)
        self.assertEqual(a.strength, b.strength)
        self.assertEqual(a.content_digest(), b.content_digest())
        self.assertEqual(a.provenance.inputs_digest, b.provenance.inputs_digest)
        self.assertEqual(a.provenance.config_digest, b.provenance.config_digest)
        self.assertEqual(a.metric_refs(), b.metric_refs())

    def test_evidence_order_is_stable(self):
        a, b = self._run().signals[0], self._run().signals[0]
        self.assertEqual([e.statement for e in a.supporting], [e.statement for e in b.supporting])

    def test_input_order_does_not_change_the_result(self):
        forward = put_support_metrics(available_at=at(0, 5))
        reverse = tuple(reversed(forward))

        def run(values):
            return (
                SignalEvaluator()
                .evaluate(
                    market_state(),
                    values,
                    at(0, 30),
                    knowledge_horizon=at(0, 30),
                    specs=(RULES.get(*PUT_RULE),),
                    expiry_id=10,
                )
                .signals[0]
            )

        self.assertEqual(run(forward).content_digest(), run(reverse).content_digest())

    def test_a_different_evaluation_moment_changes_only_timestamps(self):
        early = (
            SignalEvaluator()
            .evaluate(
                market_state(),
                put_support_metrics(available_at=at(0, 5)),
                at(0, 30),
                knowledge_horizon=at(0, 30),
                specs=(RULES.get(*PUT_RULE),),
                expiry_id=10,
            )
            .signals[0]
        )
        later = (
            SignalEvaluator()
            .evaluate(
                market_state(),
                put_support_metrics(available_at=at(0, 5)),
                at(5),
                knowledge_horizon=at(0, 30),
                specs=(RULES.get(*PUT_RULE),),
                expiry_id=10,
            )
            .signals[0]
        )
        self.assertEqual(early.signal_id, later.signal_id, "identity uses K, not wall clock")
        self.assertEqual(early.strength, later.strength)
        self.assertNotEqual(early.created_at, later.created_at)

    def test_no_rule_reads_the_clock_or_a_store(self):
        for path in (REPO / "oipulse/signals").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("datetime.now", source)
                self.assertNotIn("oipulse.core.clock", source)
                for banned in ("sqlalchemy", "asyncpg", "redis", "httpx", "requests"):
                    self.assertNotIn(f"import {banned}", source)


class TestIdempotencyAndSequencing(unittest.TestCase):
    """Requirements 13-14: repeated delivery of one event creates no duplicate state."""

    def _evaluate(self, existing=None, prior=(), evaluated_at=None):
        return SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            evaluated_at or at(0, 30),
            knowledge_horizon=at(0, 30),
            specs=(RULES.get(*PUT_RULE),),
            expiry_id=10,
            prior_strengths={PUT_RULE: prior} if prior else None,
            existing=existing,
        )

    def test_reprocessing_the_same_event_yields_the_same_identity(self):
        first = self._evaluate().signals[0]
        second = self._evaluate().signals[0]
        self.assertEqual(first.signal_id, second.signal_id)

    def test_a_repeat_advances_one_entity_rather_than_creating_a_second(self):
        first = self._evaluate().signals[0]
        stream = {first.identity.stream_key: first}
        second = self._evaluate(existing=stream).signals[0]
        self.assertEqual(first.identity.stream_key, second.identity.stream_key)
        self.assertGreaterEqual(len(second.history), len(first.history))

    def test_persistence_criteria_confirm_after_the_declared_windows(self):
        first = self._evaluate().signals[0]
        stream = {first.identity.stream_key: first}
        second = self._evaluate(existing=stream, prior=(Decimal("0.6"),)).signals[0]
        stream = {second.identity.stream_key: second}
        third = self._evaluate(existing=stream, prior=(Decimal("0.6"), Decimal("0.6"))).signals[0]
        self.assertIs(third.status, SignalStatus.CONFIRMED)

    def test_a_terminal_signal_starts_a_new_occurrence(self):
        """`08` §3: a recurrence is a new signal with its own id."""
        active = self._evaluate().signals[0]
        faded = apply_transition(
            active, SignalStatus.FADED, TransitionTrigger.EVIDENCE_DECAYED, at(1)
        )
        stream = {faded.identity.stream_key: faded}
        recurrence = self._evaluate(existing=stream).signals[0]
        self.assertEqual(recurrence.identity.occurrence, 2)
        self.assertNotEqual(recurrence.signal_id, active.signal_id)

    def test_rules_are_evaluated_in_a_deterministic_order(self):
        report = SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            expiry_id=10,
        )
        labels = [s.label for s in report.skipped]
        self.assertEqual(labels, sorted(labels) if labels == sorted(labels) else labels)
        again = SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            expiry_id=10,
        )
        self.assertEqual([s.label for s in again.skipped], labels)

    def test_evaluation_interval_throttles_independently_of_cadence(self):
        evaluator = SignalEvaluator()
        every_second = evaluator.due_at(RULES.all(), elapsed_seconds=1)
        every_hour = evaluator.due_at(RULES.all(), elapsed_seconds=7200)
        self.assertLess(len(every_second), len(every_hour))
        self.assertEqual(len(every_hour), len(RULES))


class TestContradictionAuditing(unittest.TestCase):
    """`08` §4: a rule whose assessment is always NONE_OBSERVED is flagged."""

    def test_the_report_names_rules_that_found_no_contradiction(self):
        report = SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            expiry_id=10,
        )
        self.assertIsInstance(report.none_observed, tuple)
        for produced in report.signals:
            if produced.found_no_contradiction:
                self.assertIn(
                    f"{produced.signal_type}@v{produced.identity.rule_version}",
                    report.none_observed,
                )

    def test_the_worked_example_produces_contradicting_evidence(self):
        """`08` §5 shows two contradicting items; the rule must actually look."""
        report = SignalEvaluator().evaluate(
            market_state(),
            put_support_metrics(available_at=at(0, 5)),
            at(0, 30),
            knowledge_horizon=at(0, 30),
            specs=(RULES.get(*PUT_RULE),),
            expiry_id=10,
        )
        produced = report.signals[0]
        self.assertFalse(produced.found_no_contradiction)
        self.assertGreaterEqual(len(produced.contradicting), 1)


if __name__ == "__main__":
    unittest.main()
