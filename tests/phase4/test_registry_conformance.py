"""Registry conformance — the mandatory set, enforced over EVERY feature.

`07-ANALYTICS.md` §7 and `15-TESTING.md` §3 require six things of every registered
feature before registration is permitted, and say the registry is the enforcement
point: *"a decorator without tests is a build error, not a review comment."*

Four of the six are properties of the declaration and the machinery, identical in form
for every feature, so they are asserted here by iterating the registry rather than
copied fifty-four times. Writing them out per feature would guarantee that the
fifty-fifth feature is the one somebody forgets.

The remaining two — hand-computed expected values and domain property tests — are
inherently per-feature and live in `test_domains.py` and `test_properties.py`.

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

import oipulse.analytics  # noqa: F401  (populates REGISTRY)
from oipulse.analytics.availability import AvailabilityInputs, available_at, check_invariants
from oipulse.analytics.registry import REGISTRY, Normalization, parse_duration
from oipulse.analytics.values import MetricValue, Scope, Unavailable
from oipulse.marketstate.staleness import QualityStatus
from tests.phase4._fixtures import at, ctx, state

#: Every feature is exercised with these. A feature needing more declares it and is
#: expected to report `INVALID_PARAMS` or `MISSING_INPUT` rather than raising.
UNIVERSAL_PARAMS = {
    "expiry_id": 10,
    "lot_size": 75,
    "days_to_expiry": 2,
    "contract_multiplier": 1,
}

_ALLOWED_UNITS = {
    "contracts",
    "ratio",
    "index",
    "strike",
    "strike_points",
    "category",
    "correlation",
    "iv_decimal",
    "vol_annualised",
    "delta_shares",
    "gamma_shares_per_point",
    "currency_per_day",
    "currency_per_vol_point",
    "price",
    "price_per_step",
    "zscore",
    "percentile",
    "rate_annualised",
}
_ALLOWED_NORMALIZATION = {
    Normalization.NONE,
    Normalization.ZSCORE,
    Normalization.PERCENTILE,
    Normalization.PERCENT,
    Normalization.RATIO,
}


class TestRegistryIsComplete(unittest.TestCase):
    """Nothing computes off-registry, and nothing is registered half-declared."""

    def test_the_seven_domains_are_all_represented(self):
        modules = {s.implementation_ref for s in REGISTRY.all()}
        for domain in (
            "positioning",
            "volatility",
            "greeks",
            "gamma",
            "price",
            "futures",
            "structure",
        ):
            with self.subTest(domain=domain):
                self.assertTrue(
                    any(f".{domain}." in m for m in modules),
                    f"no feature from the {domain} domain",
                )

    def test_every_feature_declares_every_registry_field(self):
        """`07` §2's table is mandatory in full. A feature that cannot state its units
        or its lookback is one whose output nobody can safely interpret."""
        for spec in REGISTRY.all():
            with self.subTest(feature=spec.label):
                self.assertTrue(spec.identifier.isupper(), "identifiers are SCREAMING_CASE")
                self.assertGreaterEqual(spec.version, 1)
                self.assertGreater(len(spec.definition), 40, "definition is prose, not a stub")
                self.assertTrue(spec.inputs, "inputs drive dependency ordering")
                self.assertTrue(spec.formula, "the mathematical statement is mandatory")
                self.assertIn(spec.units, _ALLOWED_UNITS)
                self.assertIn(spec.normalization, _ALLOWED_NORMALIZATION)
                self.assertTrue(spec.quality_requirements, "a feature must state its gate")
                self.assertIsInstance(spec.scope, Scope)
                self.assertTrue(spec.implementation_ref.startswith("oipulse.analytics"))

    def test_every_declared_duration_parses(self):
        for spec in REGISTRY.all():
            with self.subTest(feature=spec.label):
                for text in (spec.lookback, spec.availability_delay, spec.sampling_frequency):
                    self.assertIsInstance(parse_duration(text), timedelta)

    def test_every_feature_excludes_unreliable_quality(self):
        """No feature may compute from an UNRELIABLE state."""
        for spec in REGISTRY.all():
            with self.subTest(feature=spec.label):
                self.assertIn(
                    "quality!=UNRELIABLE",
                    [r.raw for r in spec.quality_requirements],
                )

    def test_every_dependency_is_itself_registered(self):
        """A dependency on an unregistered feature can never resolve."""
        keys = {s.key for s in REGISTRY.all()}
        for spec in REGISTRY.all():
            for dependency in spec.depends_on:
                with self.subTest(feature=spec.label, dependency=dependency):
                    self.assertIn(dependency, keys)

    def test_registering_a_duplicate_version_is_refused(self):
        """Overwriting would silently change the meaning of values already stored."""
        from oipulse.analytics.registry import DuplicateFeature, Registry

        local = Registry()
        spec = REGISTRY.all()[0]
        local.register(spec)
        with self.assertRaises(DuplicateFeature):
            local.register(spec)

    def test_gex_features_carry_no_directional_label(self):
        """`07` §4.4: GEX is market-structure information, never a trading conclusion."""
        for spec in REGISTRY.all():
            if not spec.identifier.startswith("GEX") and spec.identifier != "GAMMA_FLIP_LEVEL":
                continue
            with self.subTest(feature=spec.label):
                text = (spec.definition + spec.formula).lower()
                self.assertNotIn("bullish", text)
                self.assertNotIn("bearish", text)

    def test_no_feature_asserts_a_directional_conclusion(self):
        """Phase 4 produces measurements. Signals are Phase 5 and out of scope.

        A directional word is permitted only inside an explicit *disclaimer* -- several
        definitions say "no bullish or bearish label is attached", which is the
        architecture speaking, not the feature. The check therefore looks at the
        clause the word sits in: a negated clause is fine, a bare assertion is not.
        This distinction matters, because banning the word outright would push authors
        to drop the disclaimers that make the position explicit.
        """
        negations = ("no ", "not ", "never", "without", "rather than", "carrying no")
        for spec in REGISTRY.all():
            text = spec.definition.lower()
            for word in ("bullish", "bearish", "recommend", "buy signal", "sell signal"):
                position = text.find(word)
                if position == -1:
                    continue
                clause_start = max(text.rfind(".", 0, position), text.rfind(";", 0, position)) + 1
                clause = text[clause_start:position]
                with self.subTest(feature=spec.label, word=word):
                    self.assertTrue(
                        any(n in clause for n in negations),
                        f"{spec.label} asserts {word!r} outside a disclaimer: ...{clause}{word}",
                    )


class TestUnitsMatchOutput(unittest.TestCase):
    """Mandatory test 2: the declared unit matches the produced output."""

    _NUMERIC_UNITS = _ALLOWED_UNITS - {"category"}

    def test_every_produced_value_matches_its_declared_unit(self):
        produced = 0
        for spec in REGISTRY.all():
            outcome = spec.compute(ctx(**UNIVERSAL_PARAMS))
            if isinstance(outcome, Unavailable):
                continue
            produced += 1
            with self.subTest(feature=spec.label):
                self.assertEqual(outcome.unit, spec.units)
                if spec.units == "category":
                    self.assertIsInstance(outcome.value, str)
                elif isinstance(outcome.value, tuple):
                    # Profile-shaped values (GEX_BY_STRIKE, GEX_PROFILE) are pairs.
                    for key, amount in outcome.value:
                        self.assertIsInstance(key, str)
                        self.assertIsInstance(amount, Decimal)
                else:
                    self.assertIsInstance(outcome.value, (Decimal, int))
        self.assertGreater(produced, 10, "the fixture must exercise a real spread of features")


class TestQualityGating(unittest.TestCase):
    """Mandatory test 3: refuses to compute when `quality_requirements` are unmet."""

    def test_no_feature_computes_against_an_unreliable_state(self):
        bad = state(quality=QualityStatus.UNRELIABLE, coverage=0.4)
        for spec in REGISTRY.all():
            with self.subTest(feature=spec.label):
                unmet = spec.unmet_requirements(
                    status=QualityStatus.UNRELIABLE,
                    measures=ctx(bad, **UNIVERSAL_PARAMS).quality_measures(),
                )
                self.assertTrue(unmet, "an UNRELIABLE state must fail every feature's gate")

    def test_the_engine_skips_rather_than_computing(self):
        from oipulse.analytics.engine import FeatureEngine

        bad = state(quality=QualityStatus.UNRELIABLE, coverage=0.4)
        report = FeatureEngine().run(ctx(bad, **UNIVERSAL_PARAMS))
        self.assertEqual(report.computed_count, 0, "nothing may compute from UNRELIABLE")
        self.assertTrue(report.skipped)
        self.assertTrue(all(s.reason.value == "quality_not_met" for s in report.skipped))

    def test_a_greeks_feature_refuses_a_backfill_window_without_greeks(self):
        """`07` §2's worked case: historical-OI-only backfill has no greeks."""
        from tests.phase4._fixtures import expiry_slice, leg

        no_greeks = expiry_slice(
            tuple(
                leg(s, t, iv=None, gamma=None)
                for s in ("24900", "25000", "25100")
                for t in ("call", "put")
            )
        )
        context = ctx(state(expiries=(no_greeks,)), **UNIVERSAL_PARAMS)
        measures = context.quality_measures()
        self.assertEqual(measures["greeks_coverage"], 0.0)
        for spec in REGISTRY.all():
            if "greeks_coverage>=0.90" not in [r.raw for r in spec.quality_requirements]:
                continue
            with self.subTest(feature=spec.label):
                self.assertTrue(spec.unmet_requirements(status=QualityStatus.OK, measures=measures))


class TestAvailability(unittest.TestCase):
    """Mandatory test 4: `available_at` respects the window-completion rule."""

    def test_every_produced_value_satisfies_the_three_invariants(self):
        for spec in REGISTRY.all():
            outcome = spec.compute(ctx(**UNIVERSAL_PARAMS))
            if isinstance(outcome, Unavailable):
                continue
            with self.subTest(feature=spec.label):
                self.assertGreaterEqual(outcome.available_at, outcome.computed_at)
                self.assertGreaterEqual(outcome.available_at, outcome.observed_at)

    def test_availability_includes_the_declared_delay(self):
        for spec in REGISTRY.all():
            outcome = spec.compute(ctx(**UNIVERSAL_PARAMS))
            if isinstance(outcome, Unavailable):
                continue
            with self.subTest(feature=spec.label):
                self.assertGreaterEqual(
                    outcome.available_at - outcome.observed_at, spec.availability_delay_delta
                )

    def test_a_late_ingested_input_delays_availability(self):
        """The 11:40/11:44 case, at feature level: availability tracks INPUT READINESS.

        Two states identical except for when the input was ingested must produce
        different `available_at`. If they do not, availability is being derived from
        market time and the look-ahead the whole mechanism prevents is back.
        """
        prompt = ctx(state(ingested_at=at(0)), **UNIVERSAL_PARAMS)
        late = ctx(state(ingested_at=at(4)), **UNIVERSAL_PARAMS)
        compared = 0
        for spec in REGISTRY.all():
            a, b = spec.compute(prompt), spec.compute(late)
            if isinstance(a, Unavailable) or isinstance(b, Unavailable):
                continue
            compared += 1
            with self.subTest(feature=spec.label):
                self.assertLess(a.available_at, b.available_at)
        self.assertGreater(compared, 5)

    def test_observed_at_alone_never_determines_availability(self):
        """Explicit negative: `available_at` must not equal `observed_at + delay` when
        the input was ingested later than the market time."""
        late = ctx(state(ingested_at=at(4)), **UNIVERSAL_PARAMS)
        for spec in REGISTRY.all():
            outcome = spec.compute(late)
            if isinstance(outcome, Unavailable):
                continue
            with self.subTest(feature=spec.label):
                naive = outcome.observed_at + spec.availability_delay_delta
                self.assertGreater(outcome.available_at, naive)

    def test_a_dependency_propagates_its_availability(self):
        """Feature B cannot become available before feature A (`07` §12)."""
        inputs = AvailabilityInputs(
            lookback_end=at(0),
            computed_at=at(0),
            availability_delay=timedelta(seconds=2),
            raw_input_ingested_at=at(0),
            dependency_available_at=(at(10),),
        )
        value = available_at(inputs)
        self.assertEqual(value, at(10) + timedelta(seconds=2))
        self.assertEqual(check_invariants(value, inputs), [])


class TestDeterminism(unittest.TestCase):
    """Mandatory test 5: same inputs -> same `inputs_digest` -> same value."""

    def test_repeated_computation_is_identical(self):
        first = {}
        for spec in REGISTRY.all():
            outcome = spec.compute(ctx(**UNIVERSAL_PARAMS))
            if isinstance(outcome, MetricValue):
                first[spec.label] = outcome
        self.assertTrue(first)
        for spec in REGISTRY.all():
            if spec.label not in first:
                continue
            again = spec.compute(ctx(**UNIVERSAL_PARAMS))
            with self.subTest(feature=spec.label):
                self.assertIsInstance(again, MetricValue)
                assert isinstance(again, MetricValue)
                self.assertEqual(again.value, first[spec.label].value)
                self.assertEqual(again.inputs_digest, first[spec.label].inputs_digest)

    def test_computation_does_not_depend_on_the_wall_clock(self):
        """`computed_at` shifts the availability timestamp, never the value."""
        early = ctx(computed_at=at(0.1), **UNIVERSAL_PARAMS)
        later = ctx(computed_at=at(600), **UNIVERSAL_PARAMS)
        for spec in REGISTRY.all():
            a, b = spec.compute(early), spec.compute(later)
            if isinstance(a, Unavailable) or isinstance(b, Unavailable):
                continue
            with self.subTest(feature=spec.label):
                self.assertEqual(a.value, b.value)
                self.assertEqual(a.inputs_digest, b.inputs_digest)

    def test_a_different_input_changes_the_digest(self):
        base = ctx(**UNIVERSAL_PARAMS)
        moved = ctx(state(spot="25500"), **UNIVERSAL_PARAMS)
        changed = 0
        for spec in REGISTRY.all():
            a, b = spec.compute(base), spec.compute(moved)
            if isinstance(a, Unavailable) or isinstance(b, Unavailable):
                continue
            if a.inputs_digest != b.inputs_digest:
                changed += 1
        self.assertGreater(changed, 5, "a changed state must change the digest")


class TestPurity(unittest.TestCase):
    """`07` §1 and the brief §15: analytics must not mutate MarketState."""

    def test_no_feature_mutates_the_state(self):
        before = state()
        digest_before = before.content_digest()
        comparable_before = before.as_comparable()
        for spec in REGISTRY.all():
            spec.compute(ctx(before, **UNIVERSAL_PARAMS))
        self.assertEqual(before.content_digest(), digest_before)
        self.assertEqual(before.as_comparable(), comparable_before)

    def test_the_engine_does_not_mutate_the_state(self):
        from oipulse.analytics.engine import FeatureEngine

        before = state()
        digest = before.content_digest()
        FeatureEngine().run(ctx(before, **UNIVERSAL_PARAMS))
        self.assertEqual(before.content_digest(), digest)

    def test_the_analytics_package_imports_no_database_or_clock(self):
        """Asserted on the source, because an accidental import is easy and quiet."""
        for path in (REPO / "oipulse/analytics").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for banned in ("sqlalchemy", "asyncpg", "redis", "httpx", "requests"):
                    self.assertNotIn(f"import {banned}", source)
                self.assertNotIn("oipulse.core.clock", source)
                self.assertNotIn("datetime.now", source)


if __name__ == "__main__":
    unittest.main()
