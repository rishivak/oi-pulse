"""Signal identity, versioning, evidence and strength — `08-SIGNALS.md` §2.

Covers requirements 1 (definition/version identity), 4 (different BuildContext), 11-12
(evidence completeness and resolvable references) and the §7 anti-patterns.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import oipulse.signals  # noqa: F401  (populates RULES)
from oipulse.signals.model import (
    NONE_OBSERVED,
    Evidence,
    EvidenceKind,
    Signal,
    SignalStatus,
)
from oipulse.signals.rules import RULES, DuplicateRule, RuleRegistry
from oipulse.signals.strength import normalized_weighted_sum, resolve_strength_function
from tests.phase5._fixtures import at, evidence, signal


class TestSignalIdentity(unittest.TestCase):
    """Requirement 1 and 4: identity is deterministic and version/context addressed."""

    def test_the_same_evaluation_yields_the_same_id(self):
        self.assertEqual(signal().signal_id, signal().signal_id)

    def test_a_different_build_context_is_a_different_signal(self):
        a = signal(build_context_id="bc_one")
        b = signal(build_context_id="bc_two")
        self.assertNotEqual(a.signal_id, b.signal_id)

    def test_a_different_knowledge_horizon_is_a_different_signal(self):
        self.assertNotEqual(
            signal(knowledge_horizon=at(0, 5)).signal_id,
            signal(knowledge_horizon=at(10)).signal_id,
        )

    def test_a_different_config_digest_is_a_different_signal(self):
        """A threshold change must not silently rewrite historical meaning."""
        self.assertNotEqual(
            signal(config_digest="cfg_a").signal_id, signal(config_digest="cfg_b").signal_id
        )

    def test_a_different_rule_version_is_a_different_signal(self):
        from dataclasses import replace

        base = signal()
        other = replace(base, identity=replace(base.identity, rule_version=2))
        self.assertNotEqual(base.signal_id, other.signal_id)

    def test_a_recurrence_is_a_new_signal(self):
        """`08` §3: research counts two occurrences, not one long-lived entity."""
        first, second = signal(occurrence=1), signal(occurrence=2)
        self.assertNotEqual(first.signal_id, second.signal_id)
        self.assertNotEqual(first.identity.stream_key, second.identity.stream_key)

    def test_the_stream_key_excludes_market_time(self):
        """Successive evaluations of one developing signal share a stream."""
        a = signal(market_time=at(0))
        b = signal(market_time=at(5))
        self.assertEqual(a.identity.stream_key, b.identity.stream_key)


class TestEvidence(unittest.TestCase):
    """Requirements 11-12: evidence is complete and its references resolve."""

    def test_evidence_references_a_metric_row_not_a_rendered_string(self):
        item = evidence()
        ref = item.metric_value_ref
        self.assertEqual(len(ref.as_key()), 7, "the metric_values identity tuple")
        self.assertEqual(ref.feature_id, "PUT_OI_MIGRATION")
        self.assertEqual(ref.feature_version, 1)

    def test_every_reference_names_the_feature_version_that_produced_it(self):
        for item in (*signal().supporting, *signal().contradicting):
            with self.subTest(statement=item.statement):
                self.assertGreaterEqual(item.metric_value_ref.feature_version, 1)
                self.assertIn("@v", item.label)

    def test_supporting_evidence_cannot_carry_a_negative_weight(self):
        """A sign error would silently invert the contribution to strength."""
        with self.assertRaises(ValueError):
            Evidence(
                kind=EvidenceKind.SUPPORTING,
                metric_value_ref=evidence().metric_value_ref,
                statement="inverted",
                weight=Decimal("-0.3"),
                observed_at=at(0),
            )

    def test_contradicting_evidence_cannot_carry_a_positive_weight(self):
        with self.assertRaises(ValueError):
            Evidence(
                kind=EvidenceKind.CONTRADICTING,
                metric_value_ref=evidence().metric_value_ref,
                statement="inverted",
                weight=Decimal("0.3"),
                observed_at=at(0),
            )

    def test_metric_refs_are_returned_in_a_stable_order(self):
        self.assertEqual(signal().metric_refs(), signal().metric_refs())


class TestContradictionAssessment(unittest.TestCase):
    """`08` §2: always assessed; `NONE_OBSERVED` is a positive claim."""

    def test_none_observed_counts_as_assessed(self):
        from dataclasses import replace

        assessed = replace(signal(), contradiction_assessment=NONE_OBSERVED)
        self.assertTrue(assessed.contradiction_was_assessed)
        self.assertTrue(assessed.found_no_contradiction)
        self.assertEqual(assessed.contradicting, ())

    def test_evidence_based_assessment_is_also_assessed(self):
        self.assertTrue(signal().contradiction_was_assessed)
        self.assertFalse(signal().found_no_contradiction)
        self.assertEqual(len(signal().contradicting), 1)

    def test_the_two_forms_are_distinguishable(self):
        """ "assessed, found nothing" and "has contradicting evidence" are different
        claims and must never collapse into one."""
        from dataclasses import replace

        from oipulse.signals.serialisation import signal_to_dict

        none = signal_to_dict(replace(signal(), contradiction_assessment=NONE_OBSERVED))
        some = signal_to_dict(signal())
        self.assertEqual(none["contradiction_assessment"], "NONE_OBSERVED")
        self.assertIsInstance(some["contradiction_assessment"], list)


class TestStrength(unittest.TestCase):
    """`08` §2: derived by a declared, versioned function. No free-floating number."""

    def test_strength_is_a_normalised_weighted_sum(self):
        """support 0.30 + 0.20 = 0.50; against 0.10; total 0.60; (0.50-0.10)/0.60."""
        support = (evidence(weight="0.30"), evidence("OI_WALL_PUT", weight="0.20"))
        against = (evidence("OI_WALL_CALL", kind=EvidenceKind.CONTRADICTING, weight="0.10"),)
        self.assertAlmostEqual(
            float(normalized_weighted_sum(support, against)), 0.4 / 0.6, places=9
        )

    def test_no_evidence_derives_zero_not_a_midpoint(self):
        """A default midpoint would imply an inference nobody made."""
        self.assertEqual(normalized_weighted_sum((), ()), Decimal(0))

    def test_contradiction_outweighing_support_clamps_to_zero(self):
        support = (evidence(weight="0.10"),)
        against = (evidence(kind=EvidenceKind.CONTRADICTING, weight="0.50"),)
        self.assertEqual(normalized_weighted_sum(support, against), Decimal(0))

    def test_strength_is_bounded(self):
        support = (evidence(weight="0.90"),)
        self.assertLessEqual(normalized_weighted_sum(support, ()), Decimal(1))
        self.assertGreaterEqual(normalized_weighted_sum(support, ()), Decimal(0))

    def test_adding_contradiction_never_raises_strength(self):
        """The normalisation must not let an objection improve the score."""
        support = (evidence(weight="0.30"),)
        without = normalized_weighted_sum(support, ())
        with_against = normalized_weighted_sum(
            support, (evidence(kind=EvidenceKind.CONTRADICTING, weight="0.05"),)
        )
        self.assertLessEqual(with_against, without)

    def test_the_function_is_versioned_and_recorded(self):
        self.assertEqual(
            resolve_strength_function("NORMALIZED_WEIGHTED_SUM", 1), normalized_weighted_sum
        )
        self.assertEqual(signal().provenance.strength_function_version, 1)

    def test_there_is_no_field_for_an_unexplained_confidence(self):
        fields = set(Signal.__slots__)
        for banned in ("confidence", "score", "probability"):
            self.assertNotIn(banned, fields)


class TestRuleRegistry(unittest.TestCase):
    """Requirement 1 and 5: rule definitions are versioned and version-pinned."""

    def test_the_documented_catalogue_is_registered(self):
        """`08` §6 lists sixteen signal types across four groups."""
        expected = {
            "PUT_SUPPORT_MIGRATION",
            "CALL_RESISTANCE_MIGRATION",
            "OI_EXPANSION",
            "OI_UNWINDING",
            "POSITIONING_SHIFT",
            "CONCENTRATION_BUILDING",
            "VOLATILITY_EXPANSION",
            "VOLATILITY_CONTRACTION",
            "SKEW_STEEPENING",
            "TERM_STRUCTURE_INVERSION",
            "BREAKOUT_CONTEXT",
            "BREAKDOWN_CONTEXT",
            "GAMMA_CONCENTRATION_SHIFT",
            "REGIME_TRANSITION",
            "FUTURES_OPTIONS_DIVERGENCE",
            "BASIS_ANOMALY",
        }
        self.assertEqual(set(RULES.types()), expected)

    def test_every_rule_pins_exact_feature_versions(self):
        """A feature bumping to v3 must not silently change a rule's behaviour."""
        for spec in RULES.all():
            with self.subTest(rule=spec.label):
                self.assertTrue(spec.requires_features)
                for identifier, version in spec.requires_features:
                    self.assertIsInstance(version, int)
                    self.assertGreaterEqual(version, 1)
                    self.assertTrue(identifier.isupper())

    def test_every_pinned_feature_exists_in_the_analytics_registry(self):
        """A rule pinning a feature that does not exist can never fire."""
        from oipulse.analytics.registry import REGISTRY

        available = {s.key for s in REGISTRY.all()}
        for spec in RULES.all():
            for pinned in spec.requires_features:
                with self.subTest(rule=spec.label, feature=pinned):
                    self.assertIn(pinned, available)

    def test_every_rule_declares_a_quality_gate(self):
        for spec in RULES.all():
            with self.subTest(rule=spec.label):
                self.assertTrue(spec.quality_requirements)
                self.assertTrue(spec.refuses_quality(_unreliable()))

    def test_registering_a_duplicate_version_is_refused(self):
        local = RuleRegistry()
        spec = RULES.all()[0]
        local.register(spec)
        with self.assertRaises(DuplicateRule):
            local.register(spec)

    def test_no_signal_type_is_named_for_a_trade_direction(self):
        """`08` §6: the naming discipline keeps the layer honest."""
        for signal_type in RULES.types():
            with self.subTest(signal_type=signal_type):
                for banned in ("BUY", "SELL", "LONG_ENTRY", "SHORT_ENTRY", "BULLISH", "BEARISH"):
                    self.assertNotIn(banned, signal_type)

    def test_no_rule_definition_asserts_a_recommendation(self):
        negations = ("not ", "no ", "never", "rather than", "carrying no")
        for spec in RULES.all():
            text = spec.definition.lower()
            for word in ("recommend", "buy ", "sell "):
                position = text.find(word)
                if position == -1:
                    continue
                clause = text[max(text.rfind(".", 0, position), 0) : position]
                with self.subTest(rule=spec.label, word=word):
                    self.assertTrue(any(n in clause for n in negations))


def _unreliable():
    from oipulse.marketstate.staleness import QualityStatus

    return QualityStatus.UNRELIABLE


class TestSignalInvariants(unittest.TestCase):
    def test_an_invalidation_condition_is_mandatory(self):
        """`08` §7: an unfalsifiable signal cannot be evaluated against reality."""
        from dataclasses import replace

        with self.assertRaises(ValueError):
            replace(signal(), invalidation_condition="   ")

    def test_availability_can_never_precede_market_time(self):
        from dataclasses import replace

        with self.assertRaises(ValueError):
            replace(signal(), available_at=at(-5))

    def test_forming_is_not_actionable(self):
        self.assertFalse(SignalStatus.FORMING.is_actionable)
        self.assertTrue(SignalStatus.ACTIVE.is_actionable)
        self.assertTrue(SignalStatus.CONFIRMED.is_actionable)

    def test_terminal_states_are_the_documented_three(self):
        terminal = {s.value for s in SignalStatus if s.is_terminal}
        self.assertEqual(terminal, {"INVALIDATED", "EXPIRED", "FADED"})


if __name__ == "__main__":
    unittest.main()
