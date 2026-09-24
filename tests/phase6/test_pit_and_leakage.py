"""Point-in-time correctness and leakage — `09-RESEARCH.md` §2 and §6.

Covers brief requirements 11 (PIT), 12 (corrections/bitemporal), 13 (evidence),
14 (quality), and the deliberate-violation tests `09` §6 demands:

> **Look-ahead** | `knowledge_at` default; `available_at` enforced in the engine;
> deliberate-violation tests must fail

Every test here is a test that something is **refused**. That is the shape of the
whole section: a research layer proves itself by what it will not do.

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

from oipulse.marketstate.staleness import QualityStatus
from oipulse.research.access import FeatureAccessError, PointInTimeAccessor
from oipulse.research.dataset import build_dataset
from oipulse.research.study import QueryMode
from oipulse.research.windows import (
    DecisionWindow,
    ForwardWindow,
    IncompleteWindow,
    WindowCompleteness,
    availability_time,
)
from tests.phase4._fixtures import CONTEXT
from tests.phase6._fixtures import at, dataset, metric, study


class TestWindowCompletionRule(unittest.TestCase):
    """`09` §2: availability is input readiness, never market time."""

    def test_the_worked_example_reproduces(self):
        """Observed 11:40, ingested 11:44, 2s delay -> available 11:44:02."""
        value = availability_time(
            lookback_end=at(10),
            latest_input_available_at=at(14),
            computed_at=at(10, 1),
            availability_delay=timedelta(seconds=2),
        )
        self.assertEqual(value, at(14, 2))

    def test_a_late_input_pushes_availability(self):
        prompt = availability_time(at(10), at(10), at(10), timedelta(seconds=2))
        late = availability_time(at(10), at(14), at(10), timedelta(seconds=2))
        self.assertLess(prompt, late)

    def test_availability_is_never_before_the_window_closes(self):
        value = availability_time(at(45), at(30), at(30), timedelta(seconds=2))
        self.assertGreaterEqual(value, at(45))

    def test_a_decision_before_availability_is_refused(self):
        """A feature cannot be consumed at 11:42 because data from 11:30 exists."""
        with self.assertRaises(ValueError):
            DecisionWindow(
                lookback_start=at(0),
                lookback_end=at(15),
                availability_time=at(15, 2),
                decision_time=at(12),
            )

    def test_a_valid_decision_window_is_accepted(self):
        window = DecisionWindow(at(0), at(15), at(15, 2), at(16))
        self.assertTrue(window.admits(at(7)))
        self.assertFalse(window.admits(at(20)))


class TestForwardWindowSeparation(unittest.TestCase):
    """`09` §2: outcome data is inaccessible from any decision context."""

    def test_a_decision_window_has_no_forward_field(self):
        """Different types, so a decision context cannot be handed a forward window."""
        self.assertNotIn("forward_start", set(DecisionWindow.__slots__))
        self.assertNotIn("forward_end", set(DecisionWindow.__slots__))

    def test_a_forward_window_cannot_start_before_its_event(self):
        with self.assertRaises(ValueError):
            ForwardWindow(event_time=at(10), forward_start=at(5), forward_end=at(20))

    def test_an_incomplete_window_is_reported_not_extrapolated(self):
        window = ForwardWindow.of(at(100), timedelta(minutes=30))
        self.assertEqual(window.completeness(at(110)), WindowCompleteness.TRUNCATED)
        self.assertEqual(window.completeness(at(50)), WindowCompleteness.EMPTY)
        self.assertEqual(window.completeness(at(200)), WindowCompleteness.COMPLETE)

    def test_requiring_a_complete_window_raises_at_the_dataset_edge(self):
        window = ForwardWindow.of(at(100), timedelta(minutes=30))
        with self.assertRaises(IncompleteWindow) as ctx:
            window.require_complete(at(110))
        self.assertIn("never fabricated", str(ctx.exception))


class TestLookAheadRefused(unittest.TestCase):
    """`09` §2: the engine rejects, it does not warn."""

    def setUp(self):
        self.accessor = PointInTimeAccessor(
            metrics=(metric(available_at=at(20), knowledge_horizon=at(18)),)
        )

    def test_requesting_before_availability_raises(self):
        with self.assertRaises(FeatureAccessError) as ctx:
            self.accessor.get_feature("PUT_OI_MIGRATION", 1, at(10))
        self.assertIn("look-ahead refused", str(ctx.exception))

    def test_the_same_request_succeeds_once_available(self):
        value = self.accessor.get_feature("PUT_OI_MIGRATION", 1, at(25))
        self.assertEqual(value.value, Decimal("120"))

    def test_a_different_feature_version_is_not_substituted(self):
        """Silent formula drift: asking for v2 must not return v1."""
        with self.assertRaises(FeatureAccessError):
            self.accessor.get_feature("PUT_OI_MIGRATION", 2, at(25))

    def test_try_feature_is_a_deliberate_opt_out(self):
        """The refusing path stays the default; absence requires an explicit call."""
        self.assertIsNone(self.accessor.try_feature("PUT_OI_MIGRATION", 1, at(10)))
        self.assertIsNotNone(self.accessor.try_feature("PUT_OI_MIGRATION", 1, at(25)))

    def test_available_features_excludes_the_unavailable(self):
        self.assertEqual(self.accessor.available_features(at(10)), ())
        self.assertEqual(len(self.accessor.available_features(at(25))), 1)


class TestCorrectionsAreKnowledgeHorizonSpecific(unittest.TestCase):
    """Brief requirement 12 and `09` §6: corrections leakage.

        observed_at = T, ingested_at = K1, correction arrives at K2

    A study at K1 must see the K1-known truth; a study at K2 may see the correction.
    A "latest value" shortcut would erase the distinction and silently improve every
    historical result.
    """

    def setUp(self):
        self.original = metric(
            value=Decimal("120"),
            observed_at=at(0),
            knowledge_horizon=at(1),
            available_at=at(1, 2),
            digest="original",
        )
        self.correction = metric(
            value=Decimal("180"),
            observed_at=at(0),
            knowledge_horizon=at(60),
            available_at=at(60, 2),
            digest="corrected",
        )
        self.accessor = PointInTimeAccessor(metrics=(self.original, self.correction))

    def test_a_study_at_k1_sees_the_original(self):
        value = self.accessor.get_feature("PUT_OI_MIGRATION", 1, at(30))
        self.assertEqual(value.value, Decimal("120"))

    def test_a_study_at_k2_sees_the_correction(self):
        value = self.accessor.get_feature("PUT_OI_MIGRATION", 1, at(120))
        self.assertEqual(value.value, Decimal("180"))

    def test_the_two_horizons_give_different_answers(self):
        early = self.accessor.get_feature("PUT_OI_MIGRATION", 1, at(30))
        late = self.accessor.get_feature("PUT_OI_MIGRATION", 1, at(120))
        self.assertNotEqual(early.value, late.value)
        self.assertNotEqual(early.inputs_digest, late.inputs_digest)

    def test_narrowing_the_accessor_hides_the_correction_entirely(self):
        narrowed = self.accessor.knowledge_at(at(30))
        self.assertEqual(len(narrowed.metrics), 1)
        self.assertEqual(narrowed.get_feature("PUT_OI_MIGRATION", 1, at(120)).value, Decimal("120"))

    def test_a_dataset_built_at_k1_cannot_contain_the_k2_correction(self):
        """The extract is filtered at build time, so there is no path to the later row."""
        built = build_dataset(
            "k1",
            query_mode=QueryMode.KNOWLEDGE_AT,
            knowledge_horizon=at(30),
            build_context_id=CONTEXT.id,
            period=study().period,
            universe=study().universe,
            feature_versions=(("PUT_OI_MIGRATION", 1),),
            builder_version="1.0.0",
            metrics=(self.original, self.correction),
        )
        self.assertEqual(built.row_count, 1)
        self.assertEqual(built.metrics[0].value, Decimal("120"))

    def test_two_horizons_produce_different_dataset_hashes(self):
        """Requirement 15: a different knowledge horizon is a different artifact."""
        common = {
            "query_mode": QueryMode.KNOWLEDGE_AT,
            "build_context_id": CONTEXT.id,
            "period": study().period,
            "universe": study().universe,
            "feature_versions": (("PUT_OI_MIGRATION", 1),),
            "builder_version": "1.0.0",
            "metrics": (self.original, self.correction),
        }
        early = build_dataset("d", knowledge_horizon=at(30), **common)  # type: ignore[arg-type]
        late = build_dataset("d", knowledge_horizon=at(120), **common)  # type: ignore[arg-type]
        self.assertNotEqual(early.content_hash, late.content_hash)


class TestBackfillLeakage(unittest.TestCase):
    """`09` §6: backfilled rows carry `ingested_at` at load time and are invisible
    to earlier knowledge queries."""

    def test_a_backfilled_row_is_invisible_to_an_earlier_study(self):
        backfilled = metric(observed_at=at(0), knowledge_horizon=at(500), available_at=at(500, 2))
        built = dataset([backfilled], knowledge_horizon=at(100))
        self.assertEqual(built.row_count, 0, "a row loaded later did not exist then")

    def test_the_same_row_appears_once_knowledge_catches_up(self):
        backfilled = metric(observed_at=at(0), knowledge_horizon=at(500), available_at=at(500, 2))
        self.assertEqual(dataset([backfilled], knowledge_horizon=at(600)).row_count, 1)


class TestQualityHandling(unittest.TestCase):
    """Brief requirement 14 and `09` §3: quality-filtered by default, count reported."""

    def test_a_dataset_counts_unreliable_and_degraded_rows(self):
        built = dataset(
            [
                metric(digest="a"),
                metric(digest="b", quality=QualityStatus.UNRELIABLE),
                metric(digest="c", quality=QualityStatus.DEGRADED),
            ]
        )
        self.assertEqual(built.quality.total_rows, 3)
        self.assertEqual(built.quality.unreliable_rows, 1)
        self.assertEqual(built.quality.degraded_rows, 1)
        self.assertEqual(built.quality.usable_rows, 2)

    def test_a_missing_value_is_counted_not_imputed(self):
        built = dataset([metric(value=None, digest="missing")])
        self.assertEqual(built.quality.missing_values, 1)
        self.assertIsNone(built.metrics[0].value)


class TestRetentionLocks(unittest.TestCase):
    """`02` §8 and `09` §4: a dataset pins what it references."""

    def test_every_referenced_row_is_locked(self):
        built = dataset([metric(digest="a"), metric(digest="b", observed_at=at(5))])
        locks = built.retention_locks("res_abc")
        self.assertEqual(len(locks), 2)
        for lock in locks:
            self.assertEqual(lock.locked_by_kind, "research_result")
            self.assertEqual(lock.locked_by_id, "res_abc")

    def test_locks_are_deterministic(self):
        built = dataset([metric(digest="a"), metric(digest="b", observed_at=at(5))])
        self.assertEqual(built.retention_locks("r"), built.retention_locks("r"))


if __name__ == "__main__":
    unittest.main()
