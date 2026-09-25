"""Real HTTP tests for `/reconciliation`. Skipped where fastapi is absent.

Phase 10 brief §29 is explicit that these are **not** interchangeable with the AST
checks in `test_reconciliation_and_guards.py`:

> Do not call these equivalent: AST route verification vs real HTTP ...

So both exist. The AST checks assert the router's *shape* — that no route submits,
that no parameter accepts provider truth — and run everywhere. These assert its
*behaviour*: status codes, refusals, envelopes and the service seam.

They `skipUnless` rather than erroring on a missing import, so a sandbox without
fastapi reports them as the environment-blocked checks they are instead of
contributing to an error count that hides real failures.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from oipulse.trading.oms import OrderManager
from oipulse.trading.reconciliation import Reconciler, ReconciliationTrigger, startup_gate
from tests.phase10 import _fixtures as fx
from tests.phase10._fixtures import at

try:  # pragma: no cover - availability differs per environment
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

SKIP_REASON = "needs fastapi, which is not installed in the development sandbox"


class _Service:
    """The seam `/reconciliation` reads through.

    A real `OrderManager` and `Reconciler` behind it — not a mock returning
    canned dicts. A stub would make these tests assert the stub's shape rather
    than the system's, which is the failure mode §29 is warning about.
    """

    def __init__(self) -> None:
        self.venue = fx.venue()
        self.manager = OrderManager(adapter=self.venue)
        self.reconciler = Reconciler(manager=self.manager, adapter=self.venue)
        self._runs: dict[str, Any] = {}
        self._counter = 0

    def orders(self) -> Any:
        return self.manager.orders()

    def unresolved_orders(self) -> Any:
        return self.manager.unresolved()

    def order(self, order_id: str) -> Any:
        return self.manager.order(order_id)

    def runs(self) -> Any:
        return self.reconciler.runs()

    def run(self, run_id: str) -> Any:
        return self._runs.get(run_id)

    def readiness(self) -> tuple[bool, str]:
        runs = self.reconciler.runs()
        return startup_gate(runs[-1] if runs else None)

    async def reconcile(self, body: dict[str, Any]) -> Any:
        self._counter += 1
        run_id = f"api-{self._counter}"
        run = await self.reconciler.reconcile(
            trigger=ReconciliationTrigger.MANUAL,
            since=at(0),
            at=at(5),
            run_id=run_id,
            scope=str(body.get("scope", "orders")),
        )
        self._runs[run_id] = run
        return run

    async def provider_state(self, order_id: str) -> Any:
        order = self.manager.order(order_id)
        assert order is not None and order.provider_order_id is not None
        return await self.venue.get_order(order.provider_order_id)

    async def cancel(self, order_id: str, body: dict[str, Any]) -> Any:
        return await self.manager.request_cancel(order_id, at=at(6))


@unittest.skipUnless(HAVE_FASTAPI, SKIP_REASON)
class TestReconciliationApi(unittest.TestCase):
    """Exercised over HTTP, not parsed."""

    def setUp(self) -> None:
        from oipulse.api.reconciliation import router

        self.service = _Service()
        app = FastAPI()
        app.include_router(router)
        app.state.oms = self.service
        self.client = TestClient(app)

        # One acknowledged order to look at.
        self.outcome = fx.run(fx.submit_one(self.service.manager))

    def test_status_reports_not_ready_before_any_reconciliation(self) -> None:
        response = self.client.get("/reconciliation/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["data"]["trader_ready"])
        self.assertIn("no reconciliation has run", body["data"]["reason"])

    def test_every_response_states_the_execution_posture(self) -> None:
        for path in ("/reconciliation/status", "/reconciliation/orders", "/reconciliation/runs"):
            with self.subTest(path=path):
                meta = self.client.get(path).json()["meta"]
                self.assertFalse(meta["live_execution_enabled"])
                self.assertEqual(meta["execution_mode"], "PAPER")

    def test_an_unconfigured_service_returns_503(self) -> None:
        from oipulse.api.reconciliation import router

        bare = FastAPI()
        bare.include_router(router)
        response = TestClient(bare).get("/reconciliation/orders")
        self.assertEqual(response.status_code, 503)
        self.assertIn("absence of outstanding orders", response.json()["detail"])

    def test_an_unknown_order_is_404(self) -> None:
        self.assertEqual(self.client.get("/reconciliation/orders/nope").status_code, 404)

    def test_the_order_lifecycle_is_readable(self) -> None:
        order_id = self.outcome.order.order_id
        body = self.client.get(f"/reconciliation/orders/{order_id}/events").json()
        self.assertEqual([e["to_state"] for e in body["data"]], ["SUBMITTING", "SUBMITTED"])

    def test_trigger_runs_a_real_reconciliation(self) -> None:
        response = self.client.post("/reconciliation/trigger", json={})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("content_digest", body["meta"])
        self.assertTrue(body["meta"]["is_clean"])

    def test_trigger_refuses_supplied_provider_state(self) -> None:
        """Brief §25, over HTTP rather than by reading the source."""
        for field in ("provider_orders", "provider_fills", "provider_state", "orders"):
            with self.subTest(field=field):
                response = self.client.post(
                    "/reconciliation/trigger", json={field: [{"forged": True}]}
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn("may not be supplied", response.json()["detail"])

    def test_status_becomes_ready_after_a_clean_run(self) -> None:
        self.client.post("/reconciliation/trigger", json={})
        body = self.client.get("/reconciliation/status").json()
        self.assertTrue(body["data"]["trader_ready"])

    def test_provider_state_is_queryable_when_an_id_is_known(self) -> None:
        order_id = self.outcome.order.order_id
        response = self.client.get(f"/reconciliation/orders/{order_id}/provider-state")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["meta"]["present_at_provider"])

    def test_provider_state_is_409_when_no_provider_id_is_known(self) -> None:
        """The normal state after a lost acknowledgement, reported honestly."""
        order = fx.order(fx.intent(client_order_intent_id="lost"))
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        self.service.manager = OrderManager(adapter=venue)
        self.service.venue = venue
        fx.run(fx.submit_one(self.service.manager, ord_=order))

        response = self.client.get(f"/reconciliation/orders/{order.order_id}/provider-state")
        self.assertEqual(response.status_code, 409)
        self.assertIn("lost", response.json()["detail"].lower())

    def test_cancelling_a_filled_order_does_not_report_cancelled(self) -> None:
        order = self.outcome.order
        assert order.provider_order_id is not None
        self.service.venue.venue_fill(
            order.provider_order_id, quantity=50, price=Decimal("106.40"), at=at(4)
        )
        response = self.client.post(f"/reconciliation/orders/{order.order_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["data"]["state"], "CANCELLED")

    def test_cancelling_an_unknown_order_is_404(self) -> None:
        self.assertEqual(self.client.post("/reconciliation/orders/nope/cancel").status_code, 404)

    def test_no_submit_route_is_reachable(self) -> None:
        """Brief §24, over HTTP: the surface simply is not there."""
        for path in (
            "/reconciliation/submit",
            "/reconciliation/orders/submit",
            "/reconciliation/place",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}).status_code, 404)

    def test_unresolved_orders_are_listable(self) -> None:
        order = fx.order(fx.intent(client_order_intent_id="amb"))
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        self.service.manager = OrderManager(adapter=venue)
        fx.run(fx.submit_one(self.service.manager, ord_=order))
        body = self.client.get("/reconciliation/orders?unresolved_only=true").json()
        self.assertEqual(body["meta"]["count"], 1)
        self.assertEqual(body["meta"]["without_provider_identity"], 1)


if __name__ == "__main__":
    unittest.main()
