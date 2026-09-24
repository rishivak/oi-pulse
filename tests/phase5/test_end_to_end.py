"""The full Phase 5 chain, and its regression protection over Phases 1-4.

    MarketState -> Analytics Features -> Signal Evaluation
        -> Signal Evidence / Provenance -> Alert Generation / Delivery

Covers requirement 3's separation of layers, 13 (research/replay reconstruction),
18 (architecture guards) and the Phase 1-4 regression checks the brief asks for.

SYNTHETIC fixtures throughout. No live provider, database or clock is used.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import oipulse.analytics
import oipulse.signals  # noqa: F401
from oipulse.alerts.delivery import SseDeliverer, deliver_with_retry
from oipulse.alerts.model import AlertChannel, AlertOccurrence, AlertRule
from oipulse.alerts.routing import AlertRouter
from oipulse.analytics.engine import FeatureEngine
from oipulse.marketstate.staleness import QualityStatus
from oipulse.signals.evaluation import SignalEvaluator
from tests.phase4._fixtures import at as feature_at
from tests.phase4._fixtures import ctx as feature_ctx
from tests.phase4._fixtures import state as market_state


def _history(points: int = 9):
    return tuple(
        market_state(market_time=feature_at(-40 + i * 5), spot=str(25000 + i * 30))
        for i in range(points)
    )


def _pipeline(*, quality: QualityStatus = QualityStatus.OK):
    """MarketState -> features -> signals, exactly as the processor would run it."""
    state = market_state(market_time=feature_at(0), spot="25300", quality=quality)
    features = FeatureEngine().run(
        feature_ctx(
            state,
            history=_history(),
            expiry_id=10,
            lot_size=75,
            days_to_expiry=2,
        )
    )
    evaluated_at = feature_at(0) + timedelta(seconds=5)
    signals = SignalEvaluator().evaluate(
        state,
        features.values,
        evaluated_at,
        knowledge_horizon=evaluated_at,
        expiry_id=10,
    )
    return state, features, signals


class TestFullChain(unittest.TestCase):
    def setUp(self):
        self.state, self.features, self.signals = _pipeline()

    def test_features_are_produced_from_the_state(self):
        self.assertGreater(self.features.computed_count, 10)

    def test_signals_are_produced_from_the_features(self):
        self.assertGreater(self.signals.created_count, 0)

    def test_every_signal_references_only_features_that_were_computed(self):
        """Requirement 12: evidence references resolve."""
        produced = {
            (v.feature_id, v.feature_version, v.inputs_digest) for v in self.features.values
        }
        for produced_signal in self.signals.signals:
            for ref in produced_signal.metric_refs():
                with self.subTest(signal=produced_signal.signal_type, ref=ref.label):
                    self.assertIn(
                        (ref.feature_id, ref.feature_version, ref.inputs_digest), produced
                    )

    def test_every_signal_is_available_after_every_feature_it_used(self):
        for produced_signal in self.signals.signals:
            for ref in produced_signal.metric_refs():
                with self.subTest(signal=produced_signal.signal_type):
                    self.assertGreaterEqual(produced_signal.available_at, ref.available_at)

    def test_signals_do_not_duplicate_analytics_calculations(self):
        """Requirement 3: signals build on the feature layer, never recompute it."""
        for path in (REPO / "oipulse/signals").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("oipulse.analytics.domains", source)
                self.assertNotIn("oipulse.analytics.engine", source)

    def test_the_chain_is_deterministic_end_to_end(self):
        """Requirement 18: replay reconstructs the same decisions."""
        _, _, again = _pipeline()
        self.assertEqual(
            [s.content_digest() for s in self.signals.signals],
            [s.content_digest() for s in again.signals],
        )

    def test_an_unreliable_state_produces_no_signal_even_with_features(self):
        _, features, signals = _pipeline(quality=QualityStatus.UNRELIABLE)
        self.assertEqual(signals.created_count, 0)
        self.assertEqual(features.computed_count, 0, "Phase 4 already gates on UNRELIABLE")


class TestAlertStage(unittest.TestCase):
    """The last two links: signal -> alert -> delivery, with truth preserved."""

    def setUp(self):
        _, _, self.signals = _pipeline()
        self.assertTrue(self.signals.signals, "the fixture must produce at least one signal")
        self.signal = self.signals.signals[0]

    def _rule(self) -> AlertRule:
        return AlertRule(
            id="e2e",
            signal_type=self.signal.signal_type,
            channel=AlertChannel.SSE,
            min_strength=Decimal(0),
        )

    def test_a_signal_produces_an_alert_that_references_it(self):
        decision = AlertRouter().route(self._rule(), self.signal, self.signal.available_at)
        self.assertIsNotNone(decision.occurrence)
        self.assertEqual(decision.occurrence.signal_id, self.signal.signal_id)

    def test_the_alert_can_never_precede_the_signal(self):
        decision = AlertRouter().route(self._rule(), self.signal, self.signal.available_at)
        self.assertGreaterEqual(decision.occurrence.available_at, self.signal.available_at)

    def test_delivery_leaves_the_signal_untouched(self):
        before = self.signal.content_digest()
        decision = AlertRouter().route(self._rule(), self.signal, self.signal.available_at)
        buffer: list[AlertOccurrence] = []
        deliver_with_retry(
            decision.occurrence, SseDeliverer(buffer), attempted_at=self.signal.available_at
        )
        self.assertEqual(self.signal.content_digest(), before)
        self.assertEqual(len(buffer), 1)


class TestResearchReconstruction(unittest.TestCase):
    """Requirement 13: enough is stored to reproduce the decision later."""

    def test_provenance_carries_everything_a_reconstruction_needs(self):
        _, _, signals = _pipeline()
        for produced in signals.signals:
            provenance = produced.provenance.as_dict()
            with self.subTest(signal=produced.signal_type):
                for field in (
                    "rule_type",
                    "rule_version",
                    "config_digest",
                    "feature_versions",
                    "inputs_digest",
                    "strength_function",
                    "strength_function_version",
                    "build_context_id",
                    "state_checkpoint_ref",
                ):
                    self.assertIn(field, provenance)
                self.assertTrue(provenance["feature_versions"])

    def test_market_time_and_knowledge_time_are_both_recorded(self):
        _, _, signals = _pipeline()
        produced = signals.signals[0]
        self.assertIsNotNone(produced.identity.market_time)
        self.assertIsNotNone(produced.identity.knowledge_horizon)
        self.assertNotEqual(produced.identity.market_time, produced.identity.knowledge_horizon)

    def test_decision_time_is_not_a_persisted_signal_field(self):
        """`05` §2: it is a query/action parameter, never a fifth timestamp."""
        from oipulse.signals.model import Signal, SignalIdentity

        self.assertNotIn("decision_time", set(Signal.__slots__))
        self.assertNotIn("decision_time", set(SignalIdentity.__slots__))
        tables = (REPO / "oipulse/persistence/signal_tables.py").read_text(encoding="utf-8")
        self.assertNotIn('"decision_time"', tables)

    def test_the_four_temporal_dimensions_survive(self):
        tables = (REPO / "oipulse/persistence/signal_tables.py").read_text(encoding="utf-8")
        for column in ("observed_at", "knowledge_horizon", "created_at", "available_at"):
            self.assertIn(f'"{column}"', tables)


class TestArchitectureGuards(unittest.TestCase):
    """Requirement 18: the guards are armed and actually run."""

    def _run(self, tool: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, f"tools/{tool}", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_alert_purity_guard_passes(self):
        result = self._run("check_alert_purity.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_boundary_guard_arms_the_phase_5_contracts(self):
        result = self._run("check_import_boundaries.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("signals-are-pure", result.stdout)
        self.assertIn("alerts-never-import-signal-internals", result.stdout)

    def test_the_clock_guard_still_passes(self):
        result = self._run("check_clock_access.py", "oipulse")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_phase_6_or_trading_import_exists_in_phase_5(self):
        """Requirement 20: research, replay, risk, OMS and brokers are later phases."""
        banned = (
            "oipulse.research",
            "oipulse.replay",
            "oipulse.backtest",
            "oipulse.trading",
            "oipulse.strategies",
            "oipulse.portfolio",
        )
        for package in ("oipulse/signals", "oipulse/alerts"):
            for path in (REPO / package).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                for module in banned:
                    with self.subTest(module=path.name, banned=module):
                        self.assertNotIn(f"import {module}", source)

    def test_analytics_purity_is_unchanged_by_phase_5(self):
        """Regression: Phase 4's contract must still hold."""
        for path in (REPO / "oipulse/analytics").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("oipulse.signals", source)
                self.assertNotIn("oipulse.alerts", source)


class TestPhase1To4Regression(unittest.TestCase):
    """The brief asks for regression tests over anything Phase 5 touched."""

    def test_the_analytics_registry_is_unchanged_in_size(self):
        from oipulse.analytics.registry import REGISTRY

        self.assertEqual(len(REGISTRY), 54, "Phase 4 registered 54 features")

    def test_marketstate_identity_is_unchanged(self):
        from oipulse.marketstate.state import StateIdentity

        self.assertEqual(
            set(StateIdentity.__slots__),
            {"underlying_id", "market_time", "knowledge_horizon", "build_context_id"},
        )

    def test_feature_availability_semantics_are_unchanged(self):
        from oipulse.analytics.availability import AvailabilityInputs, available_at

        inputs = AvailabilityInputs(
            lookback_end=feature_at(0),
            computed_at=feature_at(0),
            availability_delay=timedelta(seconds=2),
            raw_input_ingested_at=feature_at(4),
        )
        self.assertEqual(available_at(inputs), feature_at(4) + timedelta(seconds=2))

    def test_the_observability_registry_still_holds_phase_4_metrics(self):
        from oipulse.observability import metrics

        self.assertEqual(metrics.ANALYTICS_SKIPPED, "analytics_skipped_total")
        self.assertEqual(metrics.SIGNALS_CREATED, "signals_created_total")


if __name__ == "__main__":
    unittest.main()
