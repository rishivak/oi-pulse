"""`/market/state` — `12-API_SPEC.md` §2.

FastAPI is not installable in this environment, so the endpoint's *routing* is checked
by parsing and its *serialisation* by calling `serialise_state` directly. That split is
the limitation: these tests prove the response shape and the time semantics, not that
uvicorn serves them. The gap is stated rather than skipped, because skipping would
leave the envelope contract unchecked entirely.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketstate.state import CoherenceMode
from tests.phase3._fixtures import UNDERLYING, at, builder, populated_store

SOURCE = (REPO / "oipulse/api/market_state.py").read_text(encoding="utf-8")


def _serialise(market_time=None, knowledge=None):
    from oipulse.marketstate.serialisation import state_to_envelope as serialise_state

    state = builder(populated_store(at(0))).build(UNDERLYING, market_time or at(0), knowledge)
    return serialise_state(state)


class TestRouting(unittest.TestCase):
    """Parsed, because FastAPI cannot be imported here."""

    def test_the_state_route_is_registered_under_market(self):
        tree = ast.parse(SOURCE)
        routes = {
            d.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            for d in node.decorator_list
            if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
        }
        self.assertEqual(routes, {"/state"})
        self.assertIn('prefix="/market"', SOURCE)

    def test_the_router_is_included_in_the_application(self):
        app_source = (REPO / "oipulse/api/app.py").read_text(encoding="utf-8")
        self.assertIn("market_router", app_source)
        self.assertIn("include_router(market_router)", app_source)

    def test_an_unconfigured_process_answers_503_rather_than_inventing_a_service(self):
        self.assertIn("HTTP_503_SERVICE_UNAVAILABLE", SOURCE)

    def test_knowledge_time_before_market_time_is_rejected_at_the_endpoint(self):
        """`12-API_SPEC.md` §2 names the combination incoherent."""
        self.assertIn("knowledge_time < market_time is rejected", SOURCE)
        self.assertIn("HTTP_422_UNPROCESSABLE_ENTITY", SOURCE)

    def test_no_availability_filter_is_offered(self):
        """Raw observations have no `available_at`; a state is their reorganisation.
        Offering `decision_time` here would imply an availability that does not exist."""
        tree = ast.parse(SOURCE)
        handler = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "market_state"
        )
        params = {a.arg for a in handler.args.args + handler.args.kwonlyargs}
        self.assertNotIn("decision_time", params)
        self.assertEqual(params, {"request", "underlying_id", "market_time", "knowledge_time"})


class TestResponseEnvelope(unittest.TestCase):
    """Quality and provenance travel with every response (`12` §2)."""

    def setUp(self):
        self.body = _serialise()

    def test_the_envelope_has_data_and_meta(self):
        self.assertEqual(set(self.body), {"data", "meta"})

    def test_both_times_are_echoed_back(self):
        meta = self.body["meta"]
        self.assertEqual(meta["market_time"], at(0).isoformat())
        self.assertEqual(meta["knowledge_time"], at(0).isoformat())

    def test_semantics_states_which_mode_served_the_request(self):
        self.assertEqual(self.body["meta"]["semantics"], "knowledge_at")
        later = _serialise(market_time=at(0), knowledge=at(10))
        self.assertEqual(later["meta"]["semantics"], "market_truth_at")

    def test_quality_travels_with_the_response(self):
        quality = self.body["meta"]["quality"]
        self.assertIn("status", quality)
        self.assertIn("coverage_ratio", quality)
        self.assertIn("issues", quality)

    def test_provenance_travels_with_the_response(self):
        provenance = self.body["meta"]["provenance"]
        self.assertTrue(provenance["build_context_id"].startswith("bc_"))
        self.assertIn("observation_refs", provenance)
        self.assertIn("content_digest", provenance)

    def test_coherence_mode_is_reported(self):
        self.assertIn(self.body["meta"]["coherence_mode"], {m.value for m in CoherenceMode})
        self.assertIn("is_cross_sectional", self.body["meta"]["coherence"])

    def test_prices_are_strings_not_json_numbers(self):
        """A rupee price round-tripped through an IEEE double is no longer the price
        the venue quoted."""
        self.assertIsInstance(self.body["data"]["spot"]["ltp"], str)
        leg = self.body["data"]["expiries"][0]["legs"][0]
        self.assertIsInstance(leg["ltp"], str)
        self.assertIsInstance(leg["iv"], str)

    def test_counts_remain_integers(self):
        leg = self.body["data"]["expiries"][0]["legs"][0]
        self.assertIsInstance(leg["oi"], int)
        self.assertIsInstance(leg["volume"], int)

    def test_every_leaf_reports_its_own_freshness(self):
        leg = self.body["data"]["expiries"][0]["legs"][0]
        for key in ("quote_observed_at", "greeks_observed_at", "quote_age_seconds"):
            self.assertIn(key, leg)

    def test_absent_values_serialise_as_null_not_zero(self):
        from oipulse.marketdata.store.memory import InMemoryObservationStore
        from oipulse.marketstate.serialisation import state_to_envelope as serialise_state
        from tests.phase3._fixtures import index_obs

        store = InMemoryObservationStore()
        store.append([index_obs(at(0), at(0))])  # type: ignore[arg-type]
        body = serialise_state(builder(store).build(UNDERLYING, at(0), at(0)))
        self.assertEqual(body["data"]["expiries"][0]["legs"], [])
        self.assertIsNone(body["data"]["expiries"][0]["aggregates"]["total_call_oi"])
        self.assertIsNone(body["data"]["expiries"][0]["aggregates"]["pcr"])

    def test_the_four_persisted_time_dimensions_are_not_confused(self):
        """`decision_time` is a query concept and must not appear as state data."""
        flat = str(self.body)
        self.assertNotIn("decision_time", flat)
        self.assertIn("observed_at", flat)


class TestFastAPIEndpoint(unittest.TestCase):
    """End-to-end HTTP tests of /market/state using FastAPI TestClient."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from oipulse.api.app import create_app
        from oipulse.core.config import Settings
        from oipulse.marketstate.checkpoints import StateService

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
        self.obs_store = populated_store(at(0))
        self.b = builder(self.obs_store)
        self.service = StateService(self.b)
        self.app.state.state_service = self.service
        self.client = TestClient(self.app)

    def test_valid_request_with_default_knowledge_time(self):
        resp = self.client.get(
            "/market/state",
            params={
                "underlying_id": int(UNDERLYING),
                "market_time": at(0).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["semantics"], "knowledge_at")
        self.assertEqual(body["meta"]["market_time"], at(0).isoformat())
        self.assertEqual(body["meta"]["knowledge_time"], at(0).isoformat())
        self.assertIn("spot", body["data"])
        self.assertIn("expiries", body["data"])

    def test_valid_request_with_explicit_later_knowledge_time(self):
        resp = self.client.get(
            "/market/state",
            params={
                "underlying_id": int(UNDERLYING),
                "market_time": at(0).isoformat(),
                "knowledge_time": at(10).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["semantics"], "market_truth_at")
        self.assertEqual(body["meta"]["market_time"], at(0).isoformat())
        self.assertEqual(body["meta"]["knowledge_time"], at(10).isoformat())

    def test_knowledge_time_before_market_time_returns_422(self):
        resp = self.client.get(
            "/market/state",
            params={
                "underlying_id": int(UNDERLYING),
                "market_time": at(10).isoformat(),
                "knowledge_time": at(0).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertIn("knowledge_time < market_time is rejected", body["detail"])

    def test_missing_query_parameters_returns_422(self):
        resp = self.client.get("/market/state")
        self.assertEqual(resp.status_code, 422)

    def test_unconfigured_process_returns_503(self):
        from fastapi.testclient import TestClient

        from oipulse.api.app import create_app

        unconfigured_app = create_app(self.settings)
        client = TestClient(unconfigured_app)
        resp = client.get(
            "/market/state",
            params={
                "underlying_id": int(UNDERLYING),
                "market_time": at(0).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("state assembly is not configured", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
