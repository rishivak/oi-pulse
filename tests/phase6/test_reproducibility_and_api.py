"""Content hashing, reproducibility, the engine, the API and Phase 1-5 regression.

Covers brief requirements 6 (the content-hash gate), 15-19, 21, 25-28 and §22's
architecture guards.

The central gate, from `09-RESEARCH.md` §4 and §7:

> Re-running with identical inputs must produce identical output. This is a CI test,
> not an aspiration.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketstate.staleness import QualityStatus
from oipulse.research.engine import EventStudyEngine, SeriesOutcomeSource
from oipulse.research.result import ExecutionMetadata, ResultStatus
from oipulse.research.sampling import EventSamplingPolicy, SamplingPolicy
from oipulse.research.signal_evaluation import attribute_evidence, evaluate_by_transition
from oipulse.research.statistics import OutcomeSeries, describe, excursion
from oipulse.research.study import EventStudy, QueryMode
from oipulse.signals.model import SignalStatus
from tests._http_auth import authenticate
from tests.phase5._fixtures import signal as make_signal
from tests.phase6._fixtures import (
    at,
    dataset,
    metric,
    price_series,
    study,
    surge_definition,
)

RESEARCH_API = (REPO / "oipulse/api/research.py").read_text(encoding="utf-8")


def _metrics(minutes, value="120", quality=QualityStatus.OK):
    return tuple(
        metric(
            value=Decimal(value),
            observed_at=at(m),
            knowledge_horizon=at(m),
            available_at=at(m, 2),
            quality=quality,
            digest=f"d{m}",
        )
        for m in minutes
    )


def _run(
    minutes=(0, 40, 80, 120),
    *,
    the_study=None,
    outcomes=None,
    execution=None,
    value="120",
):
    definition = the_study or study(
        horizons=EventStudy.horizons_of(5, 15),
        minimum_sample=1,
        sampling=SamplingPolicy(
            event_sampling_policy=EventSamplingPolicy.DECORRELATED,
            minimum_event_separation=timedelta(minutes=30),
        ),
    )
    built = dataset(_metrics(minutes, value=value))
    source = outcomes or SeriesOutcomeSource(price_series())
    return definition, built, EventStudyEngine().run(definition, built, source, execution=execution)


class TestContentHashGate(unittest.TestCase):
    """Requirement 6: the Phase 6 reproducibility gate."""

    def test_the_same_request_twice_hashes_identically(self):
        _, _, first = _run()
        _, _, second = _run()
        self.assertEqual(first.content_hash, second.content_hash)

    def test_reordered_source_rows_hash_identically(self):
        definition = study(minimum_sample=1)
        forward = dataset(_metrics([0, 40, 80]))
        reverse = dataset(tuple(reversed(_metrics([0, 40, 80]))))
        self.assertEqual(forward.content_hash, reverse.content_hash)

        source = SeriesOutcomeSource(price_series())
        engine = EventStudyEngine()
        self.assertEqual(
            engine.run(definition, forward, source).content_hash,
            engine.run(definition, reverse, source).content_hash,
        )

    def test_execution_metadata_does_not_change_the_hash(self):
        """Different wall-clock time, duration and host must not matter."""
        _, _, early = _run(
            execution=ExecutionMetadata(executed_at=at(1), duration=timedelta(seconds=1), host="a")
        )
        _, _, late = _run(
            execution=ExecutionMetadata(
                executed_at=at(9999), duration=timedelta(minutes=5), host="b"
            )
        )
        self.assertEqual(early.content_hash, late.content_hash)
        self.assertNotEqual(early.execution.executed_at, late.execution.executed_at)

    def test_execution_metadata_is_structurally_excluded(self):
        """A future field added to ExecutionMetadata cannot leak into identity."""
        import inspect

        source = inspect.getsource(type(_run()[2]).content_hash.fget)  # type: ignore[attr-defined]
        self.assertNotIn("execution", source)

    def test_changed_source_content_changes_the_artifact(self):
        """Requirement 21: a different dataset must be distinguishable."""
        _, _, base = _run(value="120")
        _, _, changed = _run(value="500")
        self.assertNotEqual(base.dataset_content_hash, changed.dataset_content_hash)
        self.assertNotEqual(base.content_hash, changed.content_hash)

    def test_a_changed_study_definition_changes_the_artifact(self):
        _, _, base = _run()
        _, _, other = _run(
            the_study=study(
                minimum_sample=1,
                definition=surge_definition(threshold="150"),
                horizons=EventStudy.horizons_of(5, 15),
            )
        )
        self.assertNotEqual(base.study_digest, other.study_digest)
        self.assertNotEqual(base.content_hash, other.content_hash)

    def test_a_dataset_rebuild_with_the_same_parameters_is_identical(self):
        """`09` §4: a differing hash means something non-deterministic changed."""
        self.assertEqual(
            dataset(_metrics([0, 40])).content_hash, dataset(_metrics([0, 40])).content_hash
        )

    def test_the_dataset_name_and_creation_time_are_excluded(self):
        a = dataset(_metrics([0]), name="first")
        b = dataset(_metrics([0]), name="second")
        self.assertEqual(a.content_hash, b.content_hash)


class TestEngineBehaviour(unittest.TestCase):
    """Requirements 10, 15, 16, 23, 24."""

    def test_a_run_reports_both_event_counts(self):
        _, _, result = _run()
        self.assertGreater(result.sample.raw_events, 0)
        self.assertGreaterEqual(result.sample.raw_events, result.sample.effective_sample)

    def test_the_comparison_count_is_recorded(self):
        """`09` §3: running many horizons inflates the chance of a spurious hit."""
        definition = study(horizons=EventStudy.horizons_of(5, 15, 30), minimum_sample=1)
        _, _, result = _run(the_study=definition)
        self.assertEqual(result.sample.comparisons, definition.comparison_count)
        self.assertEqual(result.sample.comparisons, 3)

    def test_below_the_minimum_sample_no_statistics_are_reported(self):
        """`09` §3: insufficiency is a finding, not a mean of nine clusters."""
        definition = study(minimum_sample=50)
        _, _, result = _run(the_study=definition)
        self.assertIs(result.status, ResultStatus.INSUFFICIENT_SAMPLE)
        self.assertEqual(result.horizons, ())
        self.assertIn("below the declared minimum", result.status_detail)

    def test_an_insufficient_result_still_has_identity(self):
        """A study that silently produced nothing would look like one never run."""
        _, _, result = _run(the_study=study(minimum_sample=50))
        self.assertTrue(result.content_hash.startswith("res_"))
        self.assertGreater(result.sample.raw_events, 0)

    def test_the_minimum_is_enforced_on_the_effective_count(self):
        """Four overlapping events must not satisfy a minimum of two."""
        definition = study(
            minimum_sample=2,
            sampling=SamplingPolicy(minimum_event_separation=timedelta(minutes=30)),
        )
        _, _, result = _run(minutes=(0, 5, 10, 15), the_study=definition)
        self.assertEqual(result.sample.raw_events, 4)
        self.assertEqual(result.sample.effective_sample, 1)
        self.assertIs(result.status, ResultStatus.INSUFFICIENT_SAMPLE)

    def test_no_qualifying_event_reports_no_events(self):
        definition = study(minimum_sample=1)
        built = dataset(_metrics([0], value="10"))
        result = EventStudyEngine().run(definition, built, SeriesOutcomeSource(price_series()))
        self.assertIs(result.status, ResultStatus.NO_EVENTS)

    def test_an_incomplete_forward_window_is_excluded_and_counted(self):
        """Requirement 10: never fabricate future values at the dataset edge."""
        definition = study(
            horizons=EventStudy.horizons_of(30), minimum_sample=1, period_end_minutes=400
        )
        built = dataset(_metrics([0, 380]))
        # The series stops at minute 385, so the event at 380 has no 30m window.
        source = SeriesOutcomeSource(price_series(end=385))
        result = EventStudyEngine().run(definition, built, source)
        self.assertGreaterEqual(result.sample.excluded_incomplete_window, 1)
        horizon = result.horizon(timedelta(minutes=30))
        self.assertIsNotNone(horizon)
        assert horizon is not None
        self.assertGreaterEqual(horizon.incomplete_windows, 1)

    def test_a_missing_baseline_is_counted_not_assumed(self):
        definition = study(minimum_sample=1)
        built = dataset(_metrics([3]))  # no price point at minute 3
        result = EventStudyEngine().run(definition, built, SeriesOutcomeSource(price_series()))
        self.assertGreaterEqual(result.sample.missing_observations, 1)

    def test_the_hindsight_flag_travels_with_the_result(self):
        definition = study(minimum_sample=1, query_mode=QueryMode.MARKET_TRUTH_AT)
        _, _, result = _run(the_study=definition)
        self.assertTrue(result.is_hindsight)
        self.assertEqual(result.query_mode, "market_truth_at")

    def test_knowledge_at_is_the_default_mode(self):
        self.assertIs(study().query_mode, QueryMode.KNOWLEDGE_AT)
        self.assertFalse(study().query_mode.is_hindsight)


class TestStatistics(unittest.TestCase):
    """Requirement 23-24: no silent defaults, no undefined numbers."""

    def test_an_empty_sample_has_no_mean(self):
        self.assertIsNone(describe([]).mean)
        self.assertEqual(describe([]).count, 0)

    def test_a_single_observation_has_no_stdev(self):
        """Not zero: a one-point dispersion is not measurable."""
        self.assertIsNone(describe([Decimal("0.01")]).stdev)

    def test_profit_factor_is_none_without_losses(self):
        """An infinite profit factor is not a number."""
        self.assertIsNone(describe([Decimal("0.01"), Decimal("0.02")]).profit_factor)

    def test_hand_computed_distribution(self):
        """Values 0.01, -0.005, 0.02, 0.0: mean 0.00625, 2 of 4 positive."""
        stats = describe([Decimal("0.01"), Decimal("-0.005"), Decimal("0.02"), Decimal("0")])
        self.assertEqual(stats.mean, Decimal("0.00625"))
        self.assertEqual(stats.win_rate, Decimal("0.5"))

    def test_excursion_skips_missing_points_rather_than_carrying_forward(self):
        series = OutcomeSeries(
            (timedelta(0), timedelta(minutes=5), timedelta(minutes=10)),
            (Decimal("10"), None, Decimal("30")),
        )
        stats = excursion(series, Decimal("10"))
        self.assertEqual(stats.mfe, Decimal("20"))
        self.assertEqual(stats.time_to_mfe, timedelta(minutes=10))
        self.assertEqual(stats.missing, 1)

    def test_excursion_over_nothing_reports_none(self):
        stats = excursion(OutcomeSeries((), ()), Decimal("10"))
        self.assertIsNone(stats.mfe)
        self.assertEqual(stats.observations, 0)


class TestSignalEvaluation(unittest.TestCase):
    """`09` §5: forward behaviour by lifecycle transition, and attribution."""

    def _pairs(self):
        return [
            (make_signal(status=SignalStatus.ACTIVE), Decimal("0.01")),
            (make_signal(status=SignalStatus.ACTIVE, market_time=at(5)), Decimal("0.02")),
            (make_signal(status=SignalStatus.CONFIRMED), Decimal("0.03")),
            (make_signal(status=SignalStatus.INVALIDATED), Decimal("-0.02")),
        ]

    def test_all_four_documented_states_are_evaluated(self):
        evaluations = evaluate_by_transition(self._pairs(), minimum_sample=1)
        statuses = {e.status for e in evaluations}
        self.assertIn(SignalStatus.ACTIVE, statuses)
        self.assertIn(SignalStatus.CONFIRMED, statuses)
        self.assertIn(SignalStatus.INVALIDATED, statuses)

    def test_below_the_minimum_the_distribution_is_withheld(self):
        evaluations = evaluate_by_transition(self._pairs(), minimum_sample=30)
        for evaluation in evaluations:
            self.assertFalse(evaluation.is_reportable)
            self.assertIsNone(evaluation.as_dict()["distribution"])
            self.assertGreater(evaluation.sample, 0, "the count is still reported")

    def test_evaluation_is_deterministic(self):
        self.assertEqual(
            [e.as_dict() for e in evaluate_by_transition(self._pairs(), minimum_sample=1)],
            [e.as_dict() for e in evaluate_by_transition(self._pairs(), minimum_sample=1)],
        )

    def test_attribution_reports_both_sides(self):
        attributions = attribute_evidence(self._pairs())
        self.assertTrue(attributions)
        for attribution in attributions:
            payload = attribution.as_dict()
            self.assertIn("with_item", payload)
            self.assertIn("without_item", payload)

    def test_separation_is_none_when_one_side_is_empty(self):
        """All signals carry the same evidence, so "without" is empty."""
        attribution = attribute_evidence(self._pairs())[0]
        self.assertEqual(attribution.without_item.count, 0)
        self.assertIsNone(attribution.separation)


class TestResearchApi(unittest.TestCase):
    """Requirement 25. Parsed, because FastAPI is not installable here."""

    def test_the_documented_routes_exist(self):
        tree = ast.parse(RESEARCH_API)
        routes = {
            d.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            for d in node.decorator_list
            if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
        }
        self.assertEqual(
            routes,
            {
                "/studies",
                "/studies/{study_id}/versions/{version}",
                "/studies/{study_id}/run",
                "/results",
                "/results/{content_hash}",
                "/datasets",
                "/datasets/{content_hash}",
                "/signal-evaluations",
            },
        )

    def test_knowledge_time_is_required_on_a_run(self):
        """A historical query is never silently answered with latest knowledge."""
        tree = ast.parse(RESEARCH_API)
        handler = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_study"
        )
        params = {a.arg for a in handler.args.args + handler.args.kwonlyargs}
        self.assertIn("knowledge_time", params)
        self.assertIn("K — required", RESEARCH_API)

    def test_an_unconfigured_process_answers_503_not_an_empty_list(self):
        self.assertIn("HTTP_503_SERVICE_UNAVAILABLE", RESEARCH_API)
        self.assertIn("indistinguishable from a study having found", RESEARCH_API)

    def test_a_missing_artifact_is_a_404(self):
        self.assertIn("HTTP_404_NOT_FOUND", RESEARCH_API)

    def test_results_are_retrievable_by_content_hash(self):
        self.assertIn("/results/{content_hash}", RESEARCH_API)
        self.assertIn("/datasets/{content_hash}", RESEARCH_API)

    def test_the_router_is_included_in_the_application(self):
        app_source = (REPO / "oipulse/api/app.py").read_text(encoding="utf-8")
        self.assertIn("include_router(research_router)", app_source)

    def test_every_result_response_carries_the_honesty_numbers(self):
        from oipulse.research.serialisation import result_to_dict

        _, _, result = _run()
        meta = result_to_dict(result)["meta"]
        for field in (
            "raw_events",
            "effective_sample",
            "clusters",
            "excluded_quality",
            "excluded_incomplete_window",
            "comparisons",
            "is_hindsight",
            "content_hash",
        ):
            self.assertIn(field, meta)


class TestArchitectureGuards(unittest.TestCase):
    """Requirement 22 and §21: no second analytics implementation, no future phases."""

    def _guard(self, tool: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, f"tools/{tool}", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_boundary_guard_arms_the_phase_6_contracts(self):
        result = self._guard("check_import_boundaries.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("research-is-pure", result.stdout)
        self.assertIn("research-never-recomputes-analytics", result.stdout)

    def test_research_does_not_recompute_analytics(self):
        for path in (REPO / "oipulse/research").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("oipulse.analytics.domains", source)
                self.assertNotIn("oipulse.analytics.engine", source)
                self.assertNotIn("oipulse.signals.evaluation", source)

    def test_research_reads_no_clock_and_no_store(self):
        for path in (REPO / "oipulse/research").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("datetime.now", source)
                self.assertNotIn("oipulse.core.clock", source)
                for banned in ("sqlalchemy", "asyncpg", "redis", "httpx", "requests"):
                    self.assertNotIn(f"import {banned}", source)

    def test_no_future_phase_import_exists(self):
        banned = (
            "oipulse.replay",
            "oipulse.backtest",
            "oipulse.trading",
            "oipulse.strategies",
            "oipulse.portfolio",
            "oipulse.terminal",
        )
        for path in (REPO / "oipulse/research").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for module in banned:
                with self.subTest(module=path.name, banned=module):
                    self.assertNotIn(f"import {module}", source)

    def test_research_does_not_import_alerts(self):
        """Delivery has no bearing on what history shows."""
        for path in (REPO / "oipulse/research").rglob("*.py"):
            self.assertNotIn("oipulse.alerts", path.read_text(encoding="utf-8"))

    def test_the_alert_purity_guard_still_passes(self):
        self.assertEqual(self._guard("check_alert_purity.py").returncode, 0)

    def test_the_clock_guard_still_passes(self):
        self.assertEqual(self._guard("check_clock_access.py", "oipulse").returncode, 0)


class TestPhase1To5Regression(unittest.TestCase):
    """Requirement 28: contracts Phase 6 touched must still hold."""

    def test_the_analytics_registry_is_unchanged(self):
        from oipulse.analytics.registry import REGISTRY

        self.assertEqual(len(REGISTRY), 54)

    def test_the_signal_catalogue_is_unchanged(self):
        import oipulse.signals  # noqa: F401
        from oipulse.signals.rules import RULES

        self.assertEqual(len(RULES), 16)

    def test_marketstate_identity_is_unchanged(self):
        from oipulse.marketstate.state import StateIdentity

        self.assertEqual(
            set(StateIdentity.__slots__),
            {"underlying_id", "market_time", "knowledge_horizon", "build_context_id"},
        )

    def test_feature_availability_semantics_are_unchanged(self):
        from oipulse.analytics.availability import AvailabilityInputs
        from oipulse.analytics.availability import available_at as feature_available_at

        inputs = AvailabilityInputs(
            lookback_end=at(0),
            computed_at=at(0),
            availability_delay=timedelta(seconds=2),
            raw_input_ingested_at=at(4),
        )
        self.assertEqual(feature_available_at(inputs), at(4, 2))

    def test_research_applies_the_same_availability_formula(self):
        """A study must judge availability by exactly the rule that produced it."""
        from oipulse.analytics.availability import AvailabilityInputs
        from oipulse.analytics.availability import available_at as feature_available_at
        from oipulse.research.windows import availability_time

        inputs = AvailabilityInputs(
            lookback_end=at(0),
            computed_at=at(1),
            availability_delay=timedelta(seconds=2),
            raw_input_ingested_at=at(4),
        )
        self.assertEqual(
            feature_available_at(inputs),
            availability_time(at(0), at(4), at(1), timedelta(seconds=2)),
        )

    def test_decision_time_is_still_not_a_persisted_field(self):
        tables = (REPO / "oipulse/persistence/research_tables.py").read_text(encoding="utf-8")
        self.assertNotIn('"decision_time"', tables)


class MockResearchStore:
    def __init__(self, studies=(), results=(), datasets=(), evaluations=()):
        self._studies = {f"{s.study_id}@{s.version}": s for s in studies}
        self._results = {r.content_hash: r for r in results}
        self._datasets = {d.content_hash: d for d in datasets}
        self._evaluations = list(evaluations)

    def list_studies(self):
        return list(self._studies.values())

    def get_study(self, study_id: str, version: int):
        return self._studies.get(f"{study_id}@{version}")

    def create_study(self, body: dict):
        if "question" not in body or not body["question"]:
            raise ValueError("question is required")
        s = study(question=body["question"])
        self._studies[f"{s.study_id}@{s.version}"] = s
        return s

    def delete_study(self, study_id: str, version: int) -> bool:
        key = f"{study_id}@{version}"
        if key in self._studies:
            del self._studies[key]
            return True
        return False

    def run_study(self, the_study, knowledge_time):
        _, _, res = _run(the_study=the_study)
        self._results[res.content_hash] = res
        return res

    def get_result(self, content_hash: str):
        return self._results.get(content_hash)

    def list_results(self, study_id=None, dataset_content_hash=None):
        return list(self._results.values())

    def get_dataset(self, content_hash: str):
        return self._datasets.get(content_hash)

    def list_datasets(self):
        return list(self._datasets.values())

    def list_signal_evaluations(self, result_content_hash=None, signal_type=None):
        return list(self._evaluations)


class TestResearchFastAPIEndpoints(unittest.TestCase):
    """End-to-end HTTP tests of /research endpoints using FastAPI TestClient."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from oipulse.api.app import create_app
        from oipulse.core.config import Settings

        self.settings = Settings(
            app_env="development",
            role="api",
            log_level="INFO",
            instance_id="test-api-research",
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            redis_url="redis://localhost:6379/0",
            session_secret_key="a" * 32,
            token_encryption_key="b" * 32,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        # The Phase 12 gate protects every route; a client without a
        # session now tests the 401, which is covered in tests/phase12/.
        authenticate(self.app, self.client)

    def test_unconfigured_store_returns_503(self):
        resp = self.client.get("/research/studies")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("no research store is configured", resp.json()["detail"])

    def test_studies_list_and_get(self):
        s = study(question="Test question")
        self.app.state.research_store = MockResearchStore(studies=[s])

        resp = self.client.get("/research/studies")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["count"], 1)
        self.assertEqual(body["data"][0]["study_id"], s.study_id)

        get_resp = self.client.get(f"/research/studies/{s.study_id}/versions/{s.version}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["data"]["question"], "Test question")

    def test_study_get_unknown_returns_404(self):
        self.app.state.research_store = MockResearchStore()
        resp = self.client.get("/research/studies/unknown/versions/1")
        self.assertEqual(resp.status_code, 404)

    def test_study_create_and_delete(self):
        self.app.state.research_store = MockResearchStore()
        create_resp = self.client.post("/research/studies", json={"question": "New hypothesis"})
        self.assertEqual(create_resp.status_code, 201)
        data = create_resp.json()["data"]
        study_id = data["study_id"]
        version = data["version"]

        del_resp = self.client.delete(f"/research/studies/{study_id}/versions/{version}")
        self.assertEqual(del_resp.status_code, 204)

        del_unknown = self.client.delete(f"/research/studies/{study_id}/versions/{version}")
        self.assertEqual(del_unknown.status_code, 404)

    def test_study_create_invalid_returns_422(self):
        self.app.state.research_store = MockResearchStore()
        resp = self.client.post("/research/studies", json={})
        self.assertEqual(resp.status_code, 422)

    def test_run_study_missing_knowledge_time_returns_422(self):
        s = study()
        self.app.state.research_store = MockResearchStore(studies=[s])
        resp = self.client.post(
            f"/research/studies/{s.study_id}/run", params={"version": s.version}
        )
        self.assertEqual(resp.status_code, 422)

    def test_run_study_with_knowledge_time_returns_200(self):
        s = study(
            horizons=EventStudy.horizons_of(5, 15),
            minimum_sample=1,
            sampling=SamplingPolicy(
                event_sampling_policy=EventSamplingPolicy.DECORRELATED,
                minimum_event_separation=timedelta(minutes=30),
            ),
        )
        self.app.state.research_store = MockResearchStore(studies=[s])
        resp = self.client.post(
            f"/research/studies/{s.study_id}/run",
            params={"version": s.version, "knowledge_time": at(100).isoformat()},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("data", body)
        self.assertIn("meta", body)
        self.assertTrue(body["meta"]["content_hash"].startswith("res_"))

    def test_results_get_and_list(self):
        _, _, res = _run()
        self.app.state.research_store = MockResearchStore(results=[res])

        list_resp = self.client.get("/research/results")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.json()["meta"]["count"], 1)

        get_resp = self.client.get(f"/research/results/{res.content_hash}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["meta"]["content_hash"], res.content_hash)

        unknown_resp = self.client.get("/research/results/res_unknown")
        self.assertEqual(unknown_resp.status_code, 404)

    def test_datasets_get_and_list(self):
        d = dataset(_metrics([0, 40]))
        self.app.state.research_store = MockResearchStore(datasets=[d])

        list_resp = self.client.get("/research/datasets")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.json()["meta"]["count"], 1)

        get_resp = self.client.get(f"/research/datasets/{d.content_hash}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["data"]["content_hash"], d.content_hash)

        unknown_resp = self.client.get("/research/datasets/dset_unknown")
        self.assertEqual(unknown_resp.status_code, 404)

    def test_signal_evaluations_list(self):
        from oipulse.research.signal_evaluation import evaluate_by_transition

        pairs = [
            (make_signal(status=SignalStatus.ACTIVE), Decimal("0.01")),
            (make_signal(status=SignalStatus.CONFIRMED), Decimal("0.03")),
        ]
        evals = evaluate_by_transition(pairs, minimum_sample=1)
        self.app.state.research_store = MockResearchStore(evaluations=evals)

        resp = self.client.get("/research/signal-evaluations")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["count"], len(evals))


if __name__ == "__main__":
    unittest.main()
