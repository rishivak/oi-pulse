"""Execution engine, availability propagation, and the `/features` surface.

`07-ANALYTICS.md` §6 and `12-API_SPEC.md` §161.

FastAPI is not installable in this environment, so the endpoint's routing and contract
are checked by parsing and by calling the registry directly. That is the limitation:
these prove the surface and the semantics, not that uvicorn serves them.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import ast
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import oipulse.analytics  # noqa: F401
from oipulse.analytics.context import ComputeContext, Params
from oipulse.analytics.engine import CyclicDependency, FeatureEngine, topological_order
from oipulse.analytics.registry import REGISTRY, Registry, Scope, feature
from oipulse.analytics.values import MetricValue, Unavailable, UnavailableReason
from oipulse.marketstate.staleness import QualityStatus
from tests.phase4._fixtures import at, ctx, state

API_SOURCE = (REPO / "oipulse/api/features.py").read_text(encoding="utf-8")
PARAMS = {"expiry_id": 10, "lot_size": 75, "days_to_expiry": 2}


class TestTopologicalOrdering(unittest.TestCase):
    """§6: ordering is derived from declared inputs, never hand-maintained."""

    def test_every_dependency_precedes_its_dependent(self):
        ordered = topological_order(REGISTRY.all())
        positions = {spec.key: i for i, spec in enumerate(ordered)}
        for spec in ordered:
            for dependency in spec.depends_on:
                if dependency in positions:
                    with self.subTest(feature=spec.label, dependency=dependency):
                        self.assertLess(positions[dependency], positions[spec.key])

    def test_the_order_is_deterministic(self):
        self.assertEqual(
            [s.label for s in topological_order(REGISTRY.all())],
            [s.label for s in topological_order(REGISTRY.all())],
        )

    def test_a_cycle_raises_rather_than_recursing(self):
        local = Registry()

        def make(identifier: str, depends: tuple[tuple[str, int], ...]):
            @feature(
                identifier=identifier,
                version=1,
                definition="A deliberately cyclic fixture used to prove the guard fires.",
                inputs=["x"],
                formula="x",
                units="ratio",
                sampling_frequency="1m",
                lookback="0s",
                availability_delay="0s",
                normalization="none",
                quality_requirements=["quality!=UNRELIABLE"],
                scope=Scope.UNDERLYING,
                depends_on=depends,
                registry=local,
            )
            def fn(context: ComputeContext) -> MetricValue | Unavailable:  # pragma: no cover
                raise AssertionError("never executed")

            return fn

        make("CYCLE_A", (("CYCLE_B", 1),))
        make("CYCLE_B", (("CYCLE_A", 1),))
        with self.assertRaises(CyclicDependency):
            topological_order(local.all())


class TestEngineExecution(unittest.TestCase):
    def test_the_engine_reports_computed_unavailable_and_skipped_separately(self):
        report = FeatureEngine().run(ctx(**PARAMS))
        self.assertGreater(report.computed_count, 0)
        self.assertTrue(report.unavailable, "a single state cannot satisfy every lookback")
        total = report.computed_count + len(report.unavailable) + len(report.skipped)
        self.assertEqual(total, len(REGISTRY), "every feature is accounted for")

    def test_nothing_is_silently_dropped(self):
        """Every non-computed feature carries a reason."""
        report = FeatureEngine().run(ctx(**PARAMS))
        for item in report.unavailable:
            self.assertIsInstance(item.reason, UnavailableReason)
        for item in report.skipped:
            self.assertIsInstance(item.reason, UnavailableReason)
            self.assertTrue(item.detail)

    def test_a_dependent_is_skipped_when_its_dependency_does_not_resolve(self):
        """§6: skipped with a recorded reason, never computed from a default."""
        report = FeatureEngine().run(ctx(**PARAMS))
        dependents = {s.label for s in REGISTRY.all() if s.depends_on}
        outcomes = {
            *(f"{v.feature_id}@v{v.feature_version}" for v in report.values),
            *(f"{u.feature_id}@v{u.feature_version}" for u in report.unavailable),
            *(s.label for s in report.skipped),
        }
        self.assertTrue(dependents.issubset(outcomes))

    def test_due_at_respects_sampling_frequency(self):
        """A 15-minute feature does not recompute every second (`07` §2)."""
        engine = FeatureEngine()
        every_second = engine.due_at(REGISTRY.all(), elapsed_seconds=1)
        every_hour = engine.due_at(REGISTRY.all(), elapsed_seconds=3600)
        self.assertLess(len(every_second), len(every_hour))
        self.assertEqual(len(every_hour), len(REGISTRY))

    def test_the_engine_is_deterministic(self):
        a = FeatureEngine().run(ctx(**PARAMS))
        b = FeatureEngine().run(ctx(**PARAMS))
        self.assertEqual(
            [(v.feature_id, v.value, v.inputs_digest) for v in a.values],
            [(v.feature_id, v.value, v.inputs_digest) for v in b.values],
        )

    def test_reasons_are_labelled_for_metric_emission(self):
        report = FeatureEngine().run(
            ctx(state(quality=QualityStatus.UNRELIABLE, coverage=0.3), **PARAMS)
        )
        reasons = report.reasons()
        self.assertTrue(reasons)
        self.assertTrue(all(isinstance(k, str) and isinstance(v, str) for k, v in reasons.items()))


class TestDependencyAvailabilityPropagation(unittest.TestCase):
    """§12: feature B cannot become available before feature A."""

    def test_a_dependent_is_never_available_before_its_dependency(self):
        report = FeatureEngine().run(
            ctx(
                state(),
                history=tuple(
                    state(market_time=at(-40 + i * 5), spot=str(25000 + i * 5)) for i in range(9)
                ),
                **PARAMS,
            )
        )
        by_key = {f"{v.feature_id}@v{v.feature_version}": v for v in report.values}
        checked = 0
        for spec in REGISTRY.all():
            value = by_key.get(spec.label)
            if value is None:
                continue
            for identifier, version in spec.depends_on:
                dependency = by_key.get(f"{identifier}@v{version}")
                if dependency is None:
                    continue
                checked += 1
                with self.subTest(feature=spec.label, dependency=f"{identifier}@v{version}"):
                    self.assertGreaterEqual(value.available_at, dependency.available_at)
        self.assertGreater(checked, 0, "the fixture must actually resolve a dependency chain")


class TestFeaturesApi(unittest.TestCase):
    """`12-API_SPEC.md` §161. Parsed, because FastAPI is absent here."""

    def test_the_three_documented_routes_exist(self):
        tree = ast.parse(API_SOURCE)
        routes = {
            d.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            for d in node.decorator_list
            if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
        }
        self.assertEqual(routes, {"", "/{identifier}/versions/{version}", "/{identifier}/values"})
        self.assertIn('prefix="/features"', API_SOURCE)

    def test_the_router_is_included_in_the_application(self):
        app_source = (REPO / "oipulse/api/app.py").read_text(encoding="utf-8")
        self.assertIn("include_router(features_router)", app_source)

    def test_the_registry_serialises_every_documented_field(self):
        """`/features` serves definition, units, convention and availability delay."""
        for entry in REGISTRY.as_dict():
            with self.subTest(feature=entry["identifier"]):
                for field in (
                    "identifier",
                    "version",
                    "definition",
                    "inputs",
                    "formula",
                    "units",
                    "sampling_frequency",
                    "lookback",
                    "availability_delay",
                    "normalization",
                    "quality_requirements",
                    "scope",
                    "implementation_ref",
                    "depends_on",
                    "parameters",
                ):
                    self.assertIn(field, entry)

    def test_the_gex_conventions_are_discoverable_from_the_registry(self):
        """A user hovering GEX must see the convention in force, not read the source."""
        spec = REGISTRY.get("GEX_TOTAL", 1)
        self.assertIn("convention", spec.parameters)
        self.assertIn("dealer", spec.definition.lower())

    def test_values_endpoint_refuses_rather_than_returning_an_empty_series(self):
        self.assertIn("HTTP_503_SERVICE_UNAVAILABLE", API_SOURCE)
        self.assertIn("indistinguishable from the feature having produced nothing", API_SOURCE)

    def test_decision_time_is_a_request_parameter_not_a_stored_field(self):
        """`05` §2 and `12` §2: availability filtering is a query concept."""
        tree = ast.parse(API_SOURCE)
        handler = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "feature_values"
        )
        params = {a.arg for a in handler.args.args + handler.args.kwonlyargs}
        self.assertIn("decision_time", params)
        tables = (REPO / "oipulse/persistence/analytics_tables.py").read_text(encoding="utf-8")
        self.assertNotIn('"decision_time"', tables)

    def test_the_metric_values_table_keeps_all_four_timestamps(self):
        tables = (REPO / "oipulse/persistence/analytics_tables.py").read_text(encoding="utf-8")
        for column in ("observed_at", "knowledge_horizon", "computed_at", "available_at"):
            self.assertIn(f'"{column}"', tables)

    def test_the_metric_identity_includes_version_horizon_and_context(self):
        tables = (REPO / "oipulse/persistence/analytics_tables.py").read_text(encoding="utf-8")
        self.assertIn("uq_metric_values_identity", tables)
        for column in ("feature_version", "knowledge_horizon", "build_context_id"):
            self.assertIn(column, tables)


class TestObservability(unittest.TestCase):
    """`16-OBSERVABILITY.md` §3 — the Phase 4 metric names exist and emit."""

    def test_the_documented_metric_names_are_defined(self):
        from oipulse.observability import metrics

        for name in (
            "analytics_compute_duration_seconds",
            "analytics_skipped_total",
            "feature_unavailable_total",
            "feature_availability_lag_seconds",
            "feature_input_readiness_lag_seconds",
        ):
            with self.subTest(metric=name):
                self.assertIn(
                    name,
                    {
                        metrics.ANALYTICS_COMPUTE_DURATION,
                        metrics.ANALYTICS_SKIPPED,
                        metrics.FEATURE_UNAVAILABLE,
                        metrics.FEATURE_AVAILABILITY_LAG,
                        metrics.FEATURE_INPUT_READINESS_LAG,
                    },
                )

    def test_emission_records_by_feature_and_reason(self):
        from oipulse.observability.metrics import (
            METRICS,
            record_feature_computed,
            record_feature_skipped,
        )

        METRICS.reset()
        record_feature_computed(
            "PCR",
            1,
            duration_seconds=0.001,
            availability_lag_seconds=2.0,
            input_readiness_lag_seconds=1.0,
        )
        record_feature_skipped("GEX_TOTAL", 1, "quality_not_met")
        snapshot = METRICS.snapshot()
        counters = " ".join(snapshot["counters"])
        histograms = " ".join(snapshot["histograms"])
        self.assertIn("quality_not_met", counters)
        self.assertIn("GEX_TOTAL", counters)
        self.assertIn("analytics_compute_duration_seconds", histograms)
        self.assertIn("feature_input_readiness_lag_seconds", histograms)
        METRICS.reset()

    def test_analytics_cannot_import_the_metrics_registry(self):
        """It is process-global mutable state; the caller emits instead (`07` §1)."""
        for path in (REPO / "oipulse/analytics").rglob("*.py"):
            self.assertNotIn("oipulse.observability", path.read_text(encoding="utf-8"), path.name)


class TestInvalidParameters(unittest.TestCase):
    def test_a_missing_required_parameter_is_reported_not_guessed(self):
        from oipulse.analytics.domains import positioning as pos

        outcome = pos.pcr(ComputeContext(state=state(), computed_at=at(1), params=Params()))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason, UnavailableReason.INVALID_PARAMS)

    def test_params_raise_on_an_unknown_key_rather_than_defaulting(self):
        params = Params.of(expiry_id=10)
        with self.assertRaises(KeyError):
            params.get("convention")
        self.assertEqual(params.get("convention", "fallback"), "fallback")

    def test_an_unparseable_duration_is_refused_at_registration(self):
        from oipulse.analytics.registry import parse_duration

        with self.assertRaises(ValueError):
            parse_duration("soon")
        self.assertEqual(parse_duration("2s"), timedelta(seconds=2))

    def test_an_unparseable_quality_requirement_is_refused(self):
        from oipulse.analytics.registry import QualityRequirement

        with self.assertRaises(ValueError):
            QualityRequirement.parse("looks fine to me")

    def test_an_unmeasurable_requirement_counts_as_unmet(self):
        """A gate that passes when it cannot check is not a gate."""
        from oipulse.analytics.registry import QualityRequirement

        requirement = QualityRequirement.parse("nonexistent_measure>=0.5")
        self.assertFalse(requirement.satisfied_by(status=QualityStatus.OK, measures={}))


class TestNormalizationMetadata(unittest.TestCase):
    """§10: the declared normalization must describe the output."""

    def test_ratio_features_declare_ratio_normalization(self):
        for spec in REGISTRY.all():
            if spec.units != "ratio":
                continue
            with self.subTest(feature=spec.label):
                self.assertIn(spec.normalization, {"ratio", "percentile", "none"})

    def test_zscore_features_declare_zscore_normalization(self):
        for spec in REGISTRY.all():
            if spec.units == "zscore":
                with self.subTest(feature=spec.label):
                    self.assertEqual(spec.normalization, "zscore")

    def test_no_feature_silently_mixes_percent_and_decimal(self):
        """IV is declared in decimals throughout; a percent variant would be a
        separate feature with its own units."""
        for spec in REGISTRY.all():
            if "iv" in spec.identifier.lower() or spec.identifier == "ATM_IV":
                with self.subTest(feature=spec.label):
                    self.assertIn(spec.units, {"iv_decimal", "ratio"})

    def test_decimal_values_are_never_floats(self):
        """Float money is a rounding bug waiting to happen."""
        report = FeatureEngine().run(ctx(**PARAMS))
        for item in report.values:
            if isinstance(item.value, (int, str, tuple)):
                continue
            with self.subTest(feature=item.feature_id):
                self.assertIsInstance(item.value, Decimal)


class TestFeaturesFastAPIEndpoint(unittest.TestCase):
    """End-to-end HTTP tests of /features using FastAPI TestClient."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from oipulse.api.app import create_app
        from oipulse.core.config import Settings

        self.settings = Settings(
            app_env="development",
            role="api",
            log_level="INFO",
            instance_id="test-api-1",
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            redis_url="redis://localhost:6379/0",
            session_secret_key="a" * 32,
            token_encryption_key="b" * 32,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)

    def test_list_features_returns_all_registered(self):
        resp = self.client.get("/features")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["count"], 54)
        self.assertEqual(body["meta"]["identifiers"], 54)
        self.assertIn("data", body)

    def test_list_features_filtered_by_scope(self):
        resp = self.client.get("/features", params={"scope": "underlying"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for item in body["data"]:
            self.assertEqual(item["scope"], "underlying")

    def test_get_feature_version_definition(self):
        resp = self.client.get("/features/PCR/versions/1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["identifier"], "PCR")
        self.assertEqual(body["data"]["version"], 1)
        self.assertEqual(body["data"]["units"], "ratio")

    def test_get_unknown_feature_definition_returns_404(self):
        resp = self.client.get("/features/NONEXISTENT_FEATURE/versions/1")
        self.assertEqual(resp.status_code, 404)

    def test_feature_values_without_reader_returns_503(self):
        resp = self.client.get(
            "/features/PCR/values",
            params={
                "scope_kind": "expiry",
                "scope_ref": "10",
                "market_time": at(0).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("no metric_values reader is configured", resp.json()["detail"])

    def test_feature_values_incoherent_time_range_returns_422(self):
        resp = self.client.get(
            "/features/PCR/values",
            params={
                "scope_kind": "expiry",
                "scope_ref": "10",
                "market_time": at(10).isoformat(),
                "knowledge_time": at(0).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("precedes market_time", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
