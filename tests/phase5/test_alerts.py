"""Alerts — dedup, cooldown, retry, and separation from signal truth.

Covers requirements 9, 10, 15, 16, 17 and 19 (alert API behaviour).

The through-line: **a signal exists whether or not anyone is listening.** Every test
here that touches delivery also asserts the signal came out the other side unchanged.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import ast
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.alerts.delivery import (
    DeliveryOutcome,
    SseDeliverer,
    WebhookDeliverer,
    deliver_with_retry,
)
from oipulse.alerts.model import (
    AlertChannel,
    AlertOccurrence,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    DeliveryStatus,
)
from oipulse.alerts.routing import AlertRouter, dedup_key_for
from oipulse.signals.model import SignalStatus
from tests.phase5._fixtures import at, signal

ALERTS_SOURCE = (REPO / "oipulse/api/alerts.py").read_text(encoding="utf-8")


def rule(**kwargs) -> AlertRule:
    defaults = {
        "id": "r1",
        "signal_type": "PUT_SUPPORT_MIGRATION",
        "channel": AlertChannel.SSE,
        "severity": AlertSeverity.NOTICE,
        "min_strength": Decimal("0.5"),
        "cooldown": timedelta(minutes=15),
        "dedup_window": timedelta(minutes=15),
    }
    defaults.update(kwargs)
    return AlertRule(**defaults)  # type: ignore[arg-type]


class TestRuleMatching(unittest.TestCase):
    def test_a_disabled_rule_matches_nothing(self):
        self.assertFalse(rule(enabled=False).matches(signal()))

    def test_a_weak_signal_does_not_alert(self):
        self.assertFalse(rule(min_strength=Decimal("0.9")).matches(signal(strength="0.68")))

    def test_forming_is_excluded_by_default(self):
        """Partially met entry conditions are not worth waking anyone for."""
        self.assertFalse(rule().matches(signal(status=SignalStatus.FORMING)))
        self.assertTrue(rule().matches(signal(status=SignalStatus.ACTIVE)))

    def test_matching_never_touches_the_signal(self):
        target = signal()
        before = target.content_digest()
        rule().matches(target)
        self.assertEqual(target.content_digest(), before)

    def test_a_threshold_change_produces_a_new_config_digest(self):
        self.assertNotEqual(
            rule(min_strength=Decimal("0.5")).config_digest,
            rule(min_strength=Decimal("0.7")).config_digest,
        )


class TestDedupAndCooldown(unittest.TestCase):
    """Requirement 15: the same logical event cannot alert twice."""

    def test_a_duplicate_within_the_window_is_suppressed(self):
        router, target = AlertRouter(), signal()
        first = router.route(rule(), target, at(0, 10))
        second = router.route(rule(), target, at(0, 20))
        self.assertFalse(first.suppressed)
        self.assertTrue(second.suppressed)
        self.assertEqual(first.occurrence.occurrence_id, second.occurrence.occurrence_id)

    def test_the_dedup_key_is_deterministic(self):
        a = dedup_key_for(rule(), signal(), at(0))
        b = dedup_key_for(rule(), signal(), at(0))
        self.assertEqual(a, b)

    def test_the_dedup_key_excludes_market_time(self):
        """Successive evaluations of one developing signal are one alert."""
        self.assertEqual(
            dedup_key_for(rule(), signal(market_time=at(0)), at(0)),
            dedup_key_for(rule(), signal(market_time=at(5)), at(0)),
        )

    def test_a_status_change_is_a_new_thing_to_say(self):
        self.assertNotEqual(
            dedup_key_for(rule(), signal(status=SignalStatus.ACTIVE), at(0)),
            dedup_key_for(rule(), signal(status=SignalStatus.CONFIRMED), at(0)),
        )

    def test_cooldown_suppresses_even_a_distinct_occurrence(self):
        router = AlertRouter()
        router.route(rule(), signal(status=SignalStatus.ACTIVE), at(0, 10))
        decision = router.route(rule(), signal(status=SignalStatus.CONFIRMED), at(0, 20))
        self.assertTrue(decision.suppressed)
        self.assertIn("cooldown", decision.reason)
        self.assertEqual(decision.occurrence.status, AlertStatus.SUPPRESSED)

    def test_suppression_is_recorded_not_silent(self):
        """ "Why did I not get an alert?" must have an answer."""
        router = AlertRouter()
        router.route(rule(), signal(), at(0, 10))
        decision = router.route(rule(), signal(), at(0, 20))
        self.assertTrue(decision.reason)

    def test_alerting_resumes_after_the_cooldown(self):
        router = AlertRouter(epoch=at(0))
        router.route(
            rule(cooldown=timedelta(minutes=5), dedup_window=timedelta(minutes=5)),
            signal(),
            at(0, 10),
        )
        decision = router.route(
            rule(cooldown=timedelta(minutes=5), dedup_window=timedelta(minutes=5)),
            signal(),
            at(20),
        )
        self.assertFalse(decision.suppressed)

    def test_a_restart_does_not_re_alert(self):
        """Requirement 10: process restart must not duplicate."""
        router = AlertRouter()
        first = router.route(rule(), signal(), at(0, 10))
        restarted = AlertRouter()
        restarted.restore((first.occurrence,))
        decision = restarted.route(rule(), signal(), at(0, 20))
        self.assertTrue(decision.suppressed)

    def test_routing_never_mutates_the_signal(self):
        target = signal()
        before = target.content_digest()
        AlertRouter().route(rule(), target, at(0, 10))
        self.assertEqual(target.content_digest(), before)


class TestDelivery(unittest.TestCase):
    """Requirement 16: retry is bounded and every attempt is recorded."""

    def _occurrence(self) -> AlertOccurrence:
        decision = AlertRouter().route(rule(), signal(), at(0, 10))
        assert decision.occurrence is not None
        return decision.occurrence

    def test_a_successful_delivery_records_one_attempt(self):
        buffer: list[AlertOccurrence] = []
        result = deliver_with_retry(
            self._occurrence(), SseDeliverer(buffer), attempted_at=at(0, 15)
        )
        self.assertTrue(result.delivered)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(buffer), 1)
        self.assertIs(result.occurrence.status, AlertStatus.DELIVERED)

    def test_retry_is_bounded_and_every_attempt_is_recorded(self):
        buffer: list[AlertOccurrence] = []
        deliverer = SseDeliverer(buffer, fail=lambda _o: "transport down")
        result = deliver_with_retry(
            self._occurrence(), deliverer, attempted_at=at(0, 15), max_attempts=3
        )
        self.assertFalse(result.delivered)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.occurrence.attempt_count, 3)
        self.assertIs(result.occurrence.status, AlertStatus.FAILED)
        self.assertEqual([a.attempt for a in result.occurrence.attempts], [1, 2, 3])

    def test_a_later_attempt_succeeds_and_the_history_survives(self):
        buffer: list[AlertOccurrence] = []
        calls = {"n": 0}

        def flaky(_o: AlertOccurrence) -> str | None:
            calls["n"] += 1
            return "not yet" if calls["n"] < 3 else None

        result = deliver_with_retry(
            self._occurrence(), SseDeliverer(buffer, fail=flaky), attempted_at=at(0, 15)
        )
        self.assertTrue(result.delivered)
        self.assertEqual(result.occurrence.attempt_count, 3)
        statuses = [a.status for a in result.occurrence.attempts]
        self.assertEqual(
            statuses,
            [DeliveryStatus.FAILED, DeliveryStatus.FAILED, DeliveryStatus.SUCCEEDED],
        )

    def test_attempt_timestamps_are_supplied_not_read_from_a_clock(self):
        buffer: list[AlertOccurrence] = []
        result = deliver_with_retry(
            self._occurrence(),
            SseDeliverer(buffer, fail=lambda _o: "down"),
            attempted_at=at(0, 15),
            max_attempts=2,
            backoff=timedelta(seconds=30),
        )
        times = [a.attempted_at for a in result.occurrence.attempts]
        self.assertEqual(times, [at(0, 15), at(0, 45)])

    def test_the_webhook_channel_performs_no_network_io_itself(self):
        sent: list[str] = []

        def transport(endpoint: str, _o: AlertOccurrence) -> DeliveryOutcome:
            sent.append(endpoint)
            return DeliveryOutcome(True, "202 accepted")

        result = deliver_with_retry(
            self._occurrence(),
            WebhookDeliverer("https://example.invalid/hook", transport),
            attempted_at=at(0, 15),
        )
        self.assertTrue(result.delivered)
        self.assertEqual(sent, ["https://example.invalid/hook"])

    def test_delivery_never_mutates_the_signal(self):
        """Requirement 17, the central separation."""
        target = signal()
        before = target.content_digest()
        decision = AlertRouter().route(rule(), target, at(0, 10))
        deliver_with_retry(
            decision.occurrence,
            SseDeliverer([], fail=lambda _o: "down"),
            attempted_at=at(0, 15),
        )
        self.assertEqual(target.content_digest(), before)
        self.assertIs(target.status, SignalStatus.ACTIVE)

    def test_an_occurrence_holds_a_signal_id_not_a_signal(self):
        """You cannot mutate what you do not hold."""
        occurrence = self._occurrence()
        self.assertIsInstance(occurrence.signal_id, str)
        self.assertFalse(hasattr(occurrence, "signal"))


class TestSignalSurvivesAlertFailure(unittest.TestCase):
    """A signal exists with no destination, with delay, with failure, with retry."""

    def test_a_signal_exists_with_no_rule_configured(self):
        target = signal()
        decision = AlertRouter().route(rule(signal_type="SOMETHING_ELSE"), target, at(0, 10))
        self.assertIsNone(decision.occurrence)
        self.assertIs(target.status, SignalStatus.ACTIVE)

    def test_a_signal_is_unchanged_after_total_delivery_failure(self):
        target = signal()
        before = target.content_digest()
        decision = AlertRouter().route(rule(), target, at(0, 10))
        result = deliver_with_retry(
            decision.occurrence,
            SseDeliverer([], fail=lambda _o: "down"),
            attempted_at=at(0, 15),
        )
        self.assertFalse(result.delivered)
        self.assertEqual(target.content_digest(), before)

    def test_acknowledgement_marks_the_occurrence_not_the_signal(self):
        target = signal()
        before = target.content_digest()
        decision = AlertRouter().route(rule(), target, at(0, 10))
        acknowledged = decision.occurrence.acknowledge(at(1), "operator")
        self.assertIs(acknowledged.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(target.content_digest(), before)


class TestAlertApiSurface(unittest.TestCase):
    """Requirement 19. Parsed, because FastAPI is not installable here."""

    def test_the_documented_routes_exist(self):
        tree = ast.parse(ALERTS_SOURCE)
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
                "/rules",
                "/rules/{rule_id}",
                "/rules/{rule_id}/test",
                "/occurrences",
                "/occurrences/{occurrence_id}/acknowledge",
            },
        )

    def test_an_unconfigured_process_answers_503_not_an_empty_list(self):
        self.assertIn("HTTP_503_SERVICE_UNAVAILABLE", ALERTS_SOURCE)
        self.assertIn("indistinguishable from no alerts having fired", ALERTS_SOURCE)

    def test_the_dry_run_neither_delivers_nor_persists(self):
        self.assertIn('"dry_run": True', ALERTS_SOURCE)
        self.assertIn('"delivered": False', ALERTS_SOURCE)
        self.assertIn('"persisted": False', ALERTS_SOURCE)

    def test_the_router_is_included_in_the_application(self):
        app_source = (REPO / "oipulse/api/app.py").read_text(encoding="utf-8")
        self.assertIn("include_router(alerts_router)", app_source)
        self.assertIn("include_router(signals_router)", app_source)

    def test_no_alert_endpoint_writes_to_a_signal(self):
        for banned in ("apply_transition", "SignalEvaluator", ".evaluate("):
            self.assertNotIn(banned, ALERTS_SOURCE)

    def test_a_rule_destination_is_never_a_credential(self):
        tables = (REPO / "oipulse/persistence/signal_tables.py").read_text(encoding="utf-8")
        for banned in ('"secret"', '"token"', '"password"', '"api_key"'):
            self.assertNotIn(banned, tables)


class MockSignalReader:
    def __init__(self, signals: list[Any] | None = None):
        self._signals = signals or []

    def list_signals(
        self,
        underlying_id: int | None = None,
        signal_type: str | None = None,
        signal_status: str | None = None,
        market_time: Any = None,
        knowledge_horizon: Any = None,
        decision_time: Any = None,
    ) -> list[Any]:
        res = self._signals
        if signal_type:
            res = [s for s in res if s.signal_type == signal_type]
        if signal_status:
            res = [s for s in res if s.status.value == signal_status]
        return res

    def get_signal(self, signal_id: str) -> Any | None:
        for s in self._signals:
            if s.signal_id == signal_id:
                return s
        return None


class MockAlertStore:
    def __init__(self):
        self.rules: dict[str, AlertRule] = {}
        self.occurrences: dict[str, AlertOccurrence] = {}

    def list_rules(self) -> list[AlertRule]:
        return list(self.rules.values())

    def get_rule(self, rule_id: str) -> AlertRule | None:
        return self.rules.get(rule_id)

    def create_rule(self, body: dict[str, Any]) -> AlertRule:
        if "id" not in body or "signal_type" not in body or "channel" not in body:
            raise KeyError("missing required field")
        r = AlertRule(
            id=body["id"],
            signal_type=body["signal_type"],
            channel=AlertChannel(body["channel"]),
            severity=AlertSeverity(body.get("severity", "notice")),
            min_strength=Decimal(str(body.get("min_strength", "0.5"))),
        )
        self.rules[r.id] = r
        return r

    def delete_rule(self, rule_id: str) -> bool:
        if rule_id in self.rules:
            del self.rules[rule_id]
            return True
        return False

    def list_occurrences(
        self,
        rule_id: str | None = None,
        signal_id: str | None = None,
        since: Any = None,
    ) -> list[AlertOccurrence]:
        res = list(self.occurrences.values())
        if rule_id:
            res = [o for o in res if o.rule_id == rule_id]
        if signal_id:
            res = [o for o in res if o.signal_id == signal_id]
        return res

    def acknowledge(self, occurrence_id: str, by: str) -> AlertOccurrence | None:
        occ = self.occurrences.get(occurrence_id)
        if occ is None:
            return None
        updated = occ.acknowledge(at(0), by)
        self.occurrences[occurrence_id] = updated
        return updated


class TestSignalsAndAlertsFastAPIEndpoints(unittest.TestCase):
    """End-to-end HTTP tests of /signals and /alerts using FastAPI TestClient."""

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

    def test_signals_types_returns_catalogue(self):
        resp = self.client.get("/signals/types")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("data", body)
        self.assertGreater(body["meta"]["count"], 0)

    def test_signals_type_version_definition(self):
        resp = self.client.get("/signals/types/PUT_SUPPORT_MIGRATION/versions/1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["signal_type"], "PUT_SUPPORT_MIGRATION")
        self.assertEqual(body["data"]["version"], 1)

    def test_unknown_signal_type_returns_404(self):
        resp = self.client.get("/signals/types/NONEXISTENT_SIGNAL/versions/1")
        self.assertEqual(resp.status_code, 404)

    def test_signals_unconfigured_reader_returns_503(self):
        resp = self.client.get("/signals", params={"market_time": at(0).isoformat()})
        self.assertEqual(resp.status_code, 503)

    def test_signals_knowledge_time_before_market_time_returns_422(self):
        resp = self.client.get(
            "/signals",
            params={
                "market_time": at(10).isoformat(),
                "knowledge_time": at(5).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_signals_with_configured_reader_returns_200(self):
        test_sig = signal()
        self.app.state.signal_reader = MockSignalReader([test_sig])
        resp = self.client.get("/signals", params={"market_time": at(0).isoformat()})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["meta"]["count"], 1)
        self.assertEqual(body["data"][0]["signal_id"], test_sig.signal_id)

    def test_signals_get_by_id_and_history(self):
        test_sig = signal()
        self.app.state.signal_reader = MockSignalReader([test_sig])
        resp = self.client.get(f"/signals/{test_sig.signal_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["signal_id"], test_sig.signal_id)

        resp_hist = self.client.get(f"/signals/{test_sig.signal_id}/history")
        self.assertEqual(resp_hist.status_code, 200)
        self.assertIn("data", resp_hist.json())

    def test_alerts_unconfigured_store_returns_503(self):
        self.app.state.alert_store = None
        self.assertEqual(self.client.get("/alerts/rules").status_code, 503)
        self.assertEqual(self.client.get("/alerts/occurrences").status_code, 503)

    def test_alerts_full_crud_and_test(self):
        store = MockAlertStore()
        self.app.state.alert_store = store
        test_sig = signal()
        self.app.state.signal_reader = MockSignalReader([test_sig])

        # Create
        create_resp = self.client.post(
            "/alerts/rules",
            json={
                "id": "rule-fastapi-test",
                "signal_type": "PUT_SUPPORT_MIGRATION",
                "channel": "sse",
                "severity": "important",
                "min_strength": "0.6",
            },
        )
        self.assertEqual(create_resp.status_code, 201)
        rule_data = create_resp.json()["data"]
        self.assertEqual(rule_data["id"], "rule-fastapi-test")

        # List
        list_resp = self.client.get("/alerts/rules")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()["data"]), 1)

        # Get
        get_resp = self.client.get("/alerts/rules/rule-fastapi-test")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["data"]["id"], "rule-fastapi-test")

        # Test / dry-run
        test_resp = self.client.post(
            "/alerts/rules/rule-fastapi-test/test",
            params={"signal_id": test_sig.signal_id},
        )
        self.assertEqual(test_resp.status_code, 200)
        self.assertTrue(test_resp.json()["meta"]["dry_run"])

        # Delete
        del_resp = self.client.delete("/alerts/rules/rule-fastapi-test")
        self.assertEqual(del_resp.status_code, 204)

        # Delete again -> 404
        self.assertEqual(self.client.delete("/alerts/rules/rule-fastapi-test").status_code, 404)


if __name__ == "__main__":
    unittest.main()
