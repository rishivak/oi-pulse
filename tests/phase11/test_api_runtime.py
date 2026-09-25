"""Real HTTP tests for `/portfolio`. Skipped where fastapi is absent.

Not interchangeable with the AST checks in
`test_attribution_and_reconciliation.py`. Those assert the router's *shape*; these
assert its *behaviour* — status codes, the knowledge-time requirement, the residual
in every attribution response, and the refusal paths.

They `skipUnless` rather than erroring on a missing import, so a sandbox without
fastapi reports them as the environment-blocked checks they are instead of inflating
an error count that hides real failures.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.trading.portfolio import (
    AttributionBucket,
    AttributionSlice,
    PositionReconciler,
    attribute_position,
)
from oipulse.trading.portfolio.valuation import ValuationRefused
from tests.phase11 import _fixtures as fx
from tests.phase11._fixtures import at

try:  # pragma: no cover - availability differs per environment
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

SKIP_REASON = "needs fastapi, which is not installed in the development sandbox"


class _Service:
    """The seam `/portfolio` reads through. Real objects, not canned dicts.

    A stub returning fixed payloads would make these tests assert the stub's shape
    rather than the system's.
    """

    def __init__(self) -> None:
        self.rows = fx.observations()
        self.refuse = False
        self._reconciler = PositionReconciler()
        self._runs: dict[str, Any] = {}

    def _snapshot(self, market_time: datetime | None) -> Any:
        if self.refuse:
            raise ValuationRefused("the market state is marked UNRELIABLE")
        minute = 3 if market_time is None else 1
        return fx.snapshot(valuation_minute=minute)

    async def snapshot(self, **kwargs: Any) -> Any:
        return self._snapshot(kwargs.get("market_time"))

    async def positions(self, **kwargs: Any) -> Any:
        snapshot = self._snapshot(kwargs.get("market_time"))
        return (
            [v.position for v in snapshot.valuation.positions],
            (snapshot.market_time, snapshot.knowledge_time),
        )

    async def valuation(self, **kwargs: Any) -> Any:
        return self._snapshot(kwargs.get("market_time")).valuation

    async def attribution(self, **kwargs: Any) -> Any:
        bucket_name = str(kwargs.get("bucket", "PORTFOLIO"))
        try:
            bucket = AttributionBucket(bucket_name)
        except ValueError as exc:
            raise ValueError(f"unknown attribution bucket {bucket_name!r}") from exc
        snapshot = self._snapshot(kwargs.get("market_time"))
        result = attribute_position(
            total_pnl=Decimal(1000),
            greeks=fx.greek_inputs(),
            bucket=bucket,
            bucket_id="S1",
        )
        return (
            [AttributionSlice(bucket=bucket, bucket_id="S1", result=result)],
            (snapshot.market_time, snapshot.knowledge_time),
        )

    async def snapshots(self, **kwargs: Any) -> Any:
        return [fx.snapshot()]

    async def snapshot_by_digest(self, digest: str) -> Any:
        snapshot = fx.snapshot()
        return snapshot if snapshot.content_digest == digest else None

    async def reconcile_positions(self, body: dict[str, Any]) -> Any:
        local = fx.book().positions(economics={fx.TARGET: fx.economics()})
        run, _ = self._reconciler.reconcile(
            local,
            [fx.broker_position(quantity=40)],
            account_id=fx.ACCOUNT_ID,
            portfolio_id=fx.PORTFOLIO_ID,
            at=at(3),
            run_id="api-rec-1",
        )
        self._runs[run.run_id] = run
        return run

    async def position_reconciliation(self, run_id: str) -> Any:
        return self._runs.get(run_id)


@unittest.skipUnless(HAVE_FASTAPI, SKIP_REASON)
class TestPortfolioApi(unittest.TestCase):
    def setUp(self) -> None:
        from oipulse.api.portfolio import router

        self.service = _Service()
        app = FastAPI()
        app.include_router(router)
        app.state.portfolio = self.service
        self.client = TestClient(app)
        self.params = {"account_id": fx.ACCOUNT_ID, "portfolio_id": fx.PORTFOLIO_ID}

    def test_the_portfolio_carries_both_times(self) -> None:
        body = self.client.get("/portfolio", params=self.params).json()
        self.assertIn("market_time", body["meta"])
        self.assertIn("knowledge_time", body["meta"])

    def test_a_historical_request_without_knowledge_time_is_422(self) -> None:
        """Brief §26, over HTTP: no latest-knowledge shortcut."""
        for path in ("/portfolio", "/portfolio/pnl", "/portfolio/exposure", "/portfolio/greeks"):
            with self.subTest(path=path):
                response = self.client.get(
                    path, params={**self.params, "market_time": "2026-03-03T06:01:00Z"}
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn("knowledge_time is required", response.json()["detail"])

    def test_a_historical_request_with_both_times_succeeds(self) -> None:
        response = self.client.get(
            "/portfolio",
            params={
                **self.params,
                "market_time": "2026-03-03T06:01:00Z",
                "knowledge_time": "2026-03-03T06:01:00Z",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_an_unconfigured_service_returns_503(self) -> None:
        from oipulse.api.portfolio import router

        bare = FastAPI()
        bare.include_router(router)
        response = TestClient(bare).get("/portfolio", params=self.params)
        self.assertEqual(response.status_code, 503)
        self.assertIn("indistinguishable", response.json()["detail"])

    def test_an_unreliable_state_is_409_not_an_empty_portfolio(self) -> None:
        self.service.refuse = True
        response = self.client.get("/portfolio", params=self.params)
        self.assertEqual(response.status_code, 409)

    def test_every_attribution_response_carries_its_residual(self) -> None:
        body = self.client.get(
            "/portfolio/attribution", params={**self.params, "bucket": "STRATEGY"}
        ).json()
        self.assertEqual(body["meta"]["count"], 1)
        self.assertIn("total_residual", body["meta"])
        self.assertIn("residual", body["data"][0]["attribution"])

    def test_an_unknown_bucket_is_422_not_a_silent_fallback(self) -> None:
        response = self.client.get(
            "/portfolio/attribution", params={**self.params, "bucket": "NONSENSE"}
        )
        self.assertEqual(response.status_code, 422)

    def test_pnl_keeps_gross_fees_and_net_separate(self) -> None:
        body = self.client.get("/portfolio/pnl", params=self.params).json()
        for field in ("gross_pnl", "fees", "net_pnl"):
            self.assertIn(field, body["data"])
        self.assertEqual(body["meta"]["return_methodology"], "SIMPLE_PERIOD")

    def test_exposure_reports_completeness(self) -> None:
        body = self.client.get("/portfolio/exposure", params=self.params).json()
        self.assertIn("is_complete", body["meta"])
        self.assertIn("unvalued", body["meta"])

    def test_a_snapshot_is_retrievable_by_content_address(self) -> None:
        digest = fx.snapshot().content_digest
        self.assertEqual(self.client.get(f"/portfolio/snapshots/{digest}").status_code, 200)
        self.assertEqual(self.client.get("/portfolio/snapshots/psnap_nope").status_code, 404)

    def test_position_reconciliation_runs_and_is_retrievable(self) -> None:
        created = self.client.post("/portfolio/position-reconciliation", json={})
        self.assertEqual(created.status_code, 200)
        run_id = created.json()["meta"]["run_id"]
        self.assertEqual(
            self.client.get(f"/portfolio/position-reconciliation/{run_id}").status_code,
            200,
        )

    def test_reconciliation_refuses_supplied_provider_positions(self) -> None:
        for field in ("provider_positions", "positions", "provider_snapshot"):
            with self.subTest(field=field):
                response = self.client.post(
                    "/portfolio/position-reconciliation", json={field: [{"forged": 1}]}
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn("may not be supplied", response.json()["detail"])

    def test_no_route_allows_writing_a_position(self) -> None:
        for path in ("/portfolio/positions", "/portfolio/pnl"):
            with self.subTest(path=path):
                self.assertIn(self.client.post(path, json={}).status_code, (404, 405))


if __name__ == "__main__":
    unittest.main()
