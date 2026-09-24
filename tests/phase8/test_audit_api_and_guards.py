"""Audit chain, API envelopes, persistence shape, observability and the guards.

Phase 8 brief §14, §19, §20, §21, §25. `11-TRADING.md` §10: eleven questions, one
join path, no log archaeology.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

from oipulse.trading.audit import NOT_RECORDED, build_audit_chain
from oipulse.trading.execution import PaperExecutionModel
from oipulse.trading.serialisation import (
    account_to_dict,
    audit_to_dict,
    fills_to_dict,
    intent_to_dict,
    order_to_dict,
    orders_to_dict,
    positions_to_dict,
    snapshot_to_dict,
)
from tests.phase8 import _fixtures as fx
from tests.phase8._fixtures import at

REPO = Path(__file__).resolve().parents[2]


def _filled_chain() -> tuple[object, object]:
    rows = fx.observations()
    rt = fx.runtime()
    result = fx.submit(rt, rows, fx.intent())
    order = result.orders[0]
    chain = build_audit_chain(
        order,
        result.intent,
        risk_decisions=rt.decisions_for(result.intent.intent_id),
        fills=rt.fills_for_order(order.order_id),
        execution_assumptions=PaperExecutionModel(fill_model=fx.fill_model()).assumptions,
    )
    return chain, rt


class TestAuditChain(unittest.TestCase):
    """Every question the brief §14 requires an answer to."""

    def test_the_chain_answers_every_required_question(self) -> None:
        chain, _ = _filled_chain()
        answers = chain.answers()
        for question in (
            "why_was_the_trade_created",
            "which_signal_fired",
            "which_signal_version",
            "which_strategy",
            "market_time",
            "knowledge_time",
            "what_evidence_supported_it",
            "what_build_context",
            "what_market_state",
            "what_authorised_it",
            "what_order_configuration",
            "why_did_the_fill_occur",
            "what_changed_in_the_account",
        ):
            with self.subTest(question=question):
                self.assertIn(question, answers)
                self.assertIsNotNone(answers[question])

    def test_the_signal_and_its_version_are_both_recorded(self) -> None:
        chain, _ = _filled_chain()
        answers = chain.answers()
        self.assertEqual(answers["which_signal_fired"], "PUT_OI_SURGE")
        self.assertEqual(answers["which_signal_version"], 1)

    def test_the_strategy_version_is_traceable(self) -> None:
        chain, _ = _filled_chain()
        self.assertEqual(chain.answers()["which_strategy"], "TEST_STRATEGY@v2")

    def test_the_build_context_is_traceable(self) -> None:
        chain, _ = _filled_chain()
        self.assertEqual(chain.answers()["what_build_context"], "bc-test")

    def test_the_fill_explains_its_own_price(self) -> None:
        chain, _ = _filled_chain()
        explanation = chain.answers()["why_did_the_fill_occur"][0]
        for field in ("price", "reference_price", "price_source", "slippage_model"):
            self.assertIn(field, explanation)

    def test_the_order_history_is_part_of_the_answer(self) -> None:
        chain, _ = _filled_chain()
        history = chain.answers()["order_history"]
        self.assertEqual([e["to_state"] for e in history], ["ACCEPTED", "FILLED"])

    def test_a_missing_reference_reads_as_not_recorded_not_a_guess(self) -> None:
        """A plausible reconstruction in an audit trail is worse than a gap."""
        rows = fx.observations()
        rt = fx.runtime()
        import dataclasses

        bare = dataclasses.replace(
            fx.intent(), signal_id="", signal_version=0, build_context_id="", reason=""
        )
        result = fx.submit(rt, rows, bare)
        chain = build_audit_chain(
            result.orders[0],
            bare,
            risk_decisions=rt.decisions_for(bare.intent_id),
            fills=rt.fills_for_order(result.orders[0].order_id),
            execution_assumptions={},
        )
        answers = chain.answers()
        self.assertEqual(answers["which_signal_fired"], NOT_RECORDED)
        self.assertEqual(answers["what_build_context"], NOT_RECORDED)

    def test_a_mismatched_order_and_intent_is_refused(self) -> None:
        """Refusing beats explaining a trade that never happened."""
        rows = fx.observations()
        rt = fx.runtime()
        result = fx.submit(rt, rows, fx.intent())
        with self.assertRaises(ValueError) as caught:
            build_audit_chain(
                result.orders[0],
                fx.intent(quantity=99),
                risk_decisions=(),
                fills=(),
                execution_assumptions={},
            )
        self.assertIn("refusing to assemble a chain", str(caught.exception))

    def test_the_chain_reports_that_no_risk_engine_evaluated_it(self) -> None:
        chain, _ = _filled_chain()
        self.assertFalse(chain.risk_evaluated)
        self.assertFalse(chain.answers()["risk_evaluated"])

    def test_the_risk_decision_sequence_is_present_even_when_unevaluated(self) -> None:
        """`11` §3: decisions append from the first intent, so Phase 9 slots in."""
        chain, _ = _filled_chain()
        decisions = chain.answers()["what_authorised_it"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["sequence_no"], 1)
        self.assertFalse(decisions[0]["evaluated"])
        self.assertIn("no risk engine", decisions[0]["reason"])


class TestSerialisationEnvelopes(unittest.TestCase):
    """Pure envelope shapes, testable with no web stack installed."""

    def test_every_envelope_states_paper_mode(self) -> None:
        """Brief §19. Checked over all of them, not one by one."""
        rows = fx.observations()
        rt = fx.runtime()
        result = fx.submit(rt, rows, fx.intent())
        snapshot = rt.ledger.snapshot(as_of=at(3), marks=fx.marks(rows, 3))
        chain, _ = _filled_chain()

        envelopes = {
            "account": account_to_dict(rt.account),
            "intent": intent_to_dict(result.intent),
            "order": order_to_dict(result.orders[0]),
            "orders": orders_to_dict(rt.orders()),
            "fills": fills_to_dict(rt.fills()),
            "positions": positions_to_dict(snapshot),
            "pnl": snapshot_to_dict(snapshot),
            "audit": audit_to_dict(chain),  # type: ignore[arg-type]
        }
        for name, envelope in envelopes.items():
            with self.subTest(envelope=name):
                self.assertEqual(envelope["meta"]["mode"], "PAPER")
                self.assertFalse(envelope["meta"]["live_execution_available"])

    def test_the_account_envelope_carries_the_unrisked_caveat(self) -> None:
        rt = fx.runtime()
        envelope = account_to_dict(rt.account, risk_evaluated=rt.risk_evaluated)
        self.assertFalse(envelope["meta"]["risk_evaluated"])
        self.assertTrue(any("no risk engine" in c for c in envelope["meta"]["caveats"]))

    def test_the_intent_envelope_carries_the_full_decision_sequence(self) -> None:
        rows = fx.observations()
        rt = fx.runtime()
        result = fx.submit(rt, rows, fx.intent())
        envelope = intent_to_dict(
            result.intent, decisions=rt.decisions_for(result.intent.intent_id)
        )
        self.assertEqual(len(envelope["meta"]["risk_decisions"]), 1)

    def test_the_orders_envelope_counts_rejections(self) -> None:
        """A list that hid rejections would make a failing strategy look healthy."""
        rt = fx.runtime(acct=fx.account(cfg=fx.config(starting_cash=Decimal(100))))
        fx.submit(rt, fx.observations(), fx.intent())
        envelope = orders_to_dict(rt.orders())
        self.assertEqual(envelope["meta"]["rejected"], 1)
        self.assertEqual(len(envelope["data"]), 1)

    def test_the_fills_envelope_counts_assumption_based_fills(self) -> None:
        rt = fx.runtime()
        fx.submit(rt, fx.observations(with_quotes=False), fx.intent())
        self.assertEqual(fills_to_dict(rt.fills())["meta"]["assumption_based_fills"], 1)

    def test_the_pnl_envelope_keeps_the_components_separate(self) -> None:
        rows = fx.observations()
        rt = fx.runtime()
        fx.submit(rt, rows, fx.intent())
        body = snapshot_to_dict(rt.ledger.snapshot(as_of=at(3), marks=fx.marks(rows, 3)))
        for field in ("gross_pnl", "fees", "net_pnl", "realized_pnl", "unrealized_pnl"):
            self.assertIn(field, body["data"])

    def test_the_positions_envelope_names_unmarked_instruments(self) -> None:
        rt = fx.runtime()
        fx.submit(rt, fx.observations(), fx.intent())
        body = positions_to_dict(rt.ledger.snapshot(as_of=at(3), marks={}))
        self.assertEqual(body["meta"]["unmarked_instruments"], [fx.TARGET])


class TestApiSurface(unittest.TestCase):
    """Parsed as source: fastapi is not installed in the development sandbox."""

    SOURCE = (REPO / "oipulse/api/paper_trading.py").read_text(encoding="utf-8")

    def _routes(self) -> list[str]:
        tree = ast.parse(self.SOURCE)
        routes: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "router"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                routes.append(str(node.args[0].value))
        return routes

    def test_the_spec_required_surfaces_exist(self) -> None:
        """`12` §199: accounts, intents, orders, order events, fills, positions, cancel."""
        routes = " ".join(self._routes())
        for surface in (
            "/accounts",
            "/intents",
            "/orders",
            "/events",
            "/fills",
            "/positions",
            "/cancel",
        ):
            with self.subTest(surface=surface):
                self.assertIn(surface, routes)

    def test_no_route_offers_a_live_surface(self) -> None:
        for route in self._routes():
            for banned in ("live", "broker", "real", "submit_order"):
                with self.subTest(route=route, banned=banned):
                    self.assertNotIn(banned, route.lower())

    def test_no_endpoint_takes_a_mode_parameter(self) -> None:
        """Brief §19: there must be no `mode=live` escape hatch."""
        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                names = {a.arg for a in [*node.args.args, *node.args.kwonlyargs]}
                with self.subTest(function=node.name):
                    self.assertNotIn("mode", names)

    def test_creating_a_non_paper_account_is_refused_explicitly(self) -> None:
        """Refused, not coerced: a caller must not keep believing it went live."""
        self.assertIn("HTTP_422_UNPROCESSABLE_ENTITY", self.SOURCE)
        self.assertIn("is not available", self.SOURCE)
        self.assertIn("rather than silently downgraded", self.SOURCE)

    def test_there_is_no_live_trading_router(self) -> None:
        self.assertFalse(
            (REPO / "oipulse/api/trading.py").exists(),
            "`/trading/*` is Phase 10 and must not exist while no live adapter does",
        )


class TestPersistenceShape(unittest.TestCase):
    """Read as source: SQLAlchemy is absent in the development sandbox.

    The live schema is asserted against a real PostgreSQL in
    `tests/integration/test_migrations_postgres.py`, which CI fails on skip.
    """

    SOURCE = (REPO / "oipulse/persistence/trading_tables.py").read_text(encoding="utf-8")
    MIGRATION = (REPO / "oipulse/migrations/versions/0008_phase8_paper_trading.py").read_text(
        encoding="utf-8"
    )

    def test_the_seven_phase_8_tables_are_declared(self) -> None:
        tree = ast.parse(self.SOURCE)
        declared: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "TRADING_TABLES"
                and isinstance(node.value, ast.List)
            ):
                declared = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        self.assertEqual(
            sorted(declared),
            [
                "journal_entries",
                "portfolio_positions",
                "trade_accounts",
                "trade_fills",
                "trade_intents",
                "trade_order_events",
                "trade_orders",
            ],
        )

    def test_the_account_mode_is_constrained_to_paper(self) -> None:
        self.assertIn("ck_trade_accounts_paper_only", self.MIGRATION)
        self.assertIn("mode = 'PAPER'", self.MIGRATION)

    def test_identity_is_unique_in_the_database(self) -> None:
        """The durable half of idempotency."""
        for constraint in (
            "uq_trade_intents_intent_id",
            "uq_trade_orders_order_id",
            "uq_trade_fills_fill_key",
            "uq_trade_order_events_sequence",
        ):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, self.MIGRATION)

    def test_a_rejected_order_must_carry_a_reason(self) -> None:
        self.assertIn("ck_trade_orders_rejection_has_reason", self.MIGRATION)

    def test_no_phase_9_or_later_table_is_created(self) -> None:
        """Checks the tables actually created, not the word anywhere in the file.

        The migration docstring states that no such table is created, and a
        substring search would flag that disclaimer — the same distinction as the
        Phase 6 and Phase 7 checks.
        """
        tree = ast.parse(self.MIGRATION)
        created: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                created.append(str(node.args[0].value))
        for table in created:
            for banned in ("risk_", "reconcil", "attribution", "snapshot"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())
        self.assertEqual(len(created), 7)

    def test_positions_record_the_provenance_of_the_fold(self) -> None:
        """`11` §8 forbids a running total whose derivation cannot be checked."""
        self.assertIn("fills_applied", self.MIGRATION)
        self.assertIn("last_fill_key", self.MIGRATION)

    def test_nothing_is_partitioned(self) -> None:
        self.assertNotIn("postgresql_partition_by", self.MIGRATION)
        self.assertNotIn("PARTITION OF", self.MIGRATION)


class TestArchitectureGuards(unittest.TestCase):
    def _guard(self, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / name)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_paper_trading_safety_guard_passes(self) -> None:
        result = self._guard("check_paper_trading_safety.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_import_guard_passes_with_the_phase_8_contracts_armed(self) -> None:
        result = self._guard("check_import_boundaries.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for contract in (
            "paper-trading-is-pure",
            "paper-trading-cannot-reach-a-broker",
            "paper-trading-imports-no-later-phase",
        ):
            self.assertIn(contract, result.stdout)

    def test_no_wall_clock_access(self) -> None:
        result = self._guard("check_clock_access.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_trading_imports_no_broker_credential_or_database(self) -> None:
        """Asserted against the import graph, not against intent."""
        banned = (
            "sqlalchemy",
            "httpx",
            "requests",
            "aiohttp",
            "asyncpg",
            "redis",
            "fastapi",
            "oipulse.persistence",
            "oipulse.api",
            "oipulse.marketdata.providers",
            "oipulse.marketdata.upstox",
            "oipulse.marketdata.auth",
            "oipulse.core.config",
            "oipulse.core.clock",
        )
        for path in sorted((REPO / "oipulse/trading").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            for module in imported:
                for bad in banned:
                    with self.subTest(file=path.name, module=module):
                        self.assertFalse(
                            module == bad or module.startswith(bad + "."),
                            f"{path.name} imports {module}",
                        )

    def test_no_phase_9_or_later_package_exists(self) -> None:
        for package in ("portfolio", "oms", "reconciliation", "terminal"):
            with self.subTest(package=package):
                self.assertFalse((REPO / "oipulse" / package).exists())

    def test_no_live_broker_adapter_exists_anywhere_in_the_codebase(self) -> None:
        """The strongest form of the paper-only claim: absence, not a disabled flag."""
        for path in sorted((REPO / "oipulse").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    with self.subTest(file=path.name, cls=node.name):
                        self.assertNotIn(
                            "upstoxbroker",
                            node.name.lower().replace("_", ""),
                            f"{path.name} defines {node.name}; Phase 10 owns the live "
                            f"adapter and it must not land without its gates",
                        )

    def test_the_risk_seam_does_not_import_what_it_constrains(self) -> None:
        """`11` §3: the component that says 'no' must not depend on the components
        it constrains."""
        source = (REPO / "oipulse/trading/risk.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        for forbidden in (
            "oipulse.trading.orders",
            "oipulse.trading.brokers",
            "oipulse.trading.execution",
            "oipulse.trading.runtime",
            "oipulse.trading.ledger",
        ):
            with self.subTest(module=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_there_is_no_phase_9_risk_engine(self) -> None:
        """The seam only. A stand-in would approve what the real engine refuses."""
        import oipulse.trading.risk as risk

        self.assertTrue(hasattr(risk, "RiskGate"))
        self.assertTrue(hasattr(risk, "UNEVALUATED_RISK"))
        self.assertFalse(hasattr(risk, "RiskEngine"))
        self.assertFalse(risk.UNEVALUATED_RISK.evaluates_risk)


class TestObservability(unittest.TestCase):
    def test_every_phase_8_metric_is_prefixed_paper(self) -> None:
        """Brief §21: no misleading metric for broker execution.

        `orders_submitted_total` on a dashboard reads as broker traffic;
        `paper_orders_submitted_total` cannot.
        """
        import oipulse.observability.metrics as metrics

        phase8 = [
            value
            for name, value in vars(metrics).items()
            if name.startswith("PAPER_") and isinstance(value, str)
        ]
        self.assertTrue(phase8)
        for name in phase8:
            with self.subTest(metric=name):
                self.assertTrue(name.startswith("paper_"), name)

    def test_the_metrics_record_the_qualifying_flags(self) -> None:
        from oipulse.observability.metrics import (
            METRICS,
            PAPER_LIVE_EXECUTION_REFUSALS,
            PAPER_UNRISKED_ACCOUNTS,
            record_paper_account,
            record_paper_intent,
        )

        record_paper_account("a-metric", status="ACTIVE", risk_evaluated=False)
        record_paper_intent("a-metric", accepted=False, reject_reason="INSUFFICIENT_CASH")
        rendered = METRICS.render() if hasattr(METRICS, "render") else str(METRICS.snapshot())
        self.assertIn(PAPER_UNRISKED_ACCOUNTS, rendered)
        self.assertIn("paper_intents_rejected_total", rendered)
        self.assertIsInstance(PAPER_LIVE_EXECUTION_REFUSALS, str)


class TestPaperTradingApiRuntime(unittest.TestCase):
    """Runtime FastAPI HTTP tests against `/paper-trading` endpoints."""

    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from oipulse.api.paper_trading import router
        from oipulse.trading.audit import build_audit_chain
        from oipulse.trading.execution import PaperExecutionModel

        self.rows = fx.observations()
        self.rt = fx.runtime()

        class MockPaperTradingManager:
            def __init__(self, default_rt: Any, rows: list[object]) -> None:
                self._runtimes: dict[str, Any] = {default_rt.account.account_id: default_rt}
                self._rows = rows

            def list(self) -> list[Any]:
                return list(self._runtimes.values())

            def get(self, account_id: str) -> Any | None:
                return self._runtimes.get(account_id)

            def create(self, body: dict[str, Any]) -> Any:
                acct_id = body.get("account_id", f"acc-{len(self._runtimes) + 1}")
                starting_cash = Decimal(str(body.get("starting_cash", "500000")))
                acct = fx.account(account_id=acct_id, cfg=fx.config(starting_cash=starting_cash))
                rt = fx.runtime(acct=acct)
                self._runtimes[acct_id] = rt
                return rt

            def submit(self, account_id: str, body: dict[str, Any]) -> Any:
                rt = self.get(account_id)
                if rt is None:
                    raise KeyError(f"no account {account_id}")
                intent = fx.intent(
                    account_id=account_id,
                    quantity=int(body.get("quantity", 50)),
                    client_order_intent_id=body.get("intent_id", ""),
                )
                return fx.submit(rt, self._rows, intent)

            def cancel(self, account_id: str, order_id: str, body: dict[str, Any]) -> Any:
                rt = self.get(account_id)
                if rt is None:
                    raise KeyError(f"no account {account_id}")
                return rt.cancel(order_id, at(2), reason=body.get("reason", "API cancel"))

            def snapshot(self, account_id: str) -> Any:
                rt = self.get(account_id)
                if rt is None:
                    raise KeyError(f"no account {account_id}")
                return rt.ledger.snapshot(as_of=at(3), marks=fx.marks(self._rows, 3))

            def audit(self, account_id: str, order_id: str) -> Any | None:
                rt = self.get(account_id)
                if rt is None:
                    return None
                order = rt.order(order_id)
                if order is None:
                    return None
                intent = rt.intent(order.intent_id)
                if intent is None:
                    return None
                return build_audit_chain(
                    order,
                    intent,
                    risk_decisions=rt.decisions_for(intent.intent_id),
                    fills=rt.fills_for_order(order.order_id),
                    execution_assumptions=PaperExecutionModel(
                        fill_model=fx.fill_model()
                    ).assumptions,
                )

        self.manager = MockPaperTradingManager(self.rt, self.rows)
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.state.paper_trading = self.manager
        self.client = TestClient(self.app)

    def test_service_unavailable_when_unconfigured(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from oipulse.api.paper_trading import router

        empty_app = FastAPI()
        empty_app.include_router(router)
        client = TestClient(empty_app)
        res = client.get("/paper-trading/accounts")
        self.assertEqual(res.status_code, 503)

    def test_create_account_live_mode_rejected(self) -> None:
        res = self.client.post("/paper-trading/accounts", json={"mode": "LIVE"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("not available", res.json()["detail"])

    def test_create_account_paper_mode_success(self) -> None:
        res = self.client.post(
            "/paper-trading/accounts",
            json={"account_id": "acc-new", "mode": "PAPER", "starting_cash": "250000"},
        )
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["meta"]["mode"], "PAPER")
        self.assertEqual(body["data"]["account_id"], "acc-new")

    def test_list_and_get_accounts(self) -> None:
        res = self.client.get("/paper-trading/accounts")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["meta"]["mode"], "PAPER")

        res = self.client.get(f"/paper-trading/accounts/{self.rt.account.account_id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["account_id"], self.rt.account.account_id)

        res = self.client.get("/paper-trading/accounts/nonexistent")
        self.assertEqual(res.status_code, 404)

    def test_intent_submission_order_lifecycle_and_audit(self) -> None:
        # Submit intent
        res = self.client.post(
            f"/paper-trading/accounts/{self.rt.account.account_id}/intents",
            json={"intent_id": "int-101", "quantity": 50},
        )
        self.assertEqual(res.status_code, 201)
        sub_data = res.json()
        self.assertEqual(sub_data["meta"]["mode"], "PAPER")
        self.assertFalse(sub_data["meta"]["duplicate"])
        intent_id = sub_data["data"]["intent"]["intent_id"]

        # Duplicate submission is idempotent
        res_dup = self.client.post(
            f"/paper-trading/accounts/{self.rt.account.account_id}/intents",
            json={"intent_id": "int-101", "quantity": 50},
        )
        self.assertEqual(res_dup.status_code, 201)
        self.assertTrue(res_dup.json()["meta"]["duplicate"])

        # Query intent
        res_intent = self.client.get(
            f"/paper-trading/accounts/{self.rt.account.account_id}/intents/{intent_id}"
        )
        self.assertEqual(res_intent.status_code, 200)
        self.assertEqual(res_intent.json()["data"]["intent_id"], intent_id)

        # List orders
        res_orders = self.client.get(f"/paper-trading/accounts/{self.rt.account.account_id}/orders")
        self.assertEqual(res_orders.status_code, 200)
        orders = res_orders.json()["data"]
        self.assertTrue(len(orders) >= 1)
        order_id = orders[0]["order_id"]

        # Get single order
        res_order = self.client.get(
            f"/paper-trading/accounts/{self.rt.account.account_id}/orders/{order_id}"
        )
        self.assertEqual(res_order.status_code, 200)
        self.assertEqual(res_order.json()["data"]["order_id"], order_id)

        # Get order events
        res_events = self.client.get(
            f"/paper-trading/accounts/{self.rt.account.account_id}/orders/{order_id}/events"
        )
        self.assertEqual(res_events.status_code, 200)
        events = res_events.json()["data"]
        self.assertTrue(len(events) >= 2)

        # Get fills
        res_fills = self.client.get(f"/paper-trading/accounts/{self.rt.account.account_id}/fills")
        self.assertEqual(res_fills.status_code, 200)
        self.assertTrue(len(res_fills.json()["data"]) >= 1)

        # Get positions & pnl
        res_pos = self.client.get(f"/paper-trading/accounts/{self.rt.account.account_id}/positions")
        self.assertEqual(res_pos.status_code, 200)
        res_pnl = self.client.get(f"/paper-trading/accounts/{self.rt.account.account_id}/pnl")
        self.assertEqual(res_pnl.status_code, 200)

        # Get audit chain
        res_audit = self.client.get(
            f"/paper-trading/accounts/{self.rt.account.account_id}/audit/{order_id}"
        )
        self.assertEqual(res_audit.status_code, 200)
        self.assertIn("answers", res_audit.json()["data"])


if __name__ == "__main__":
    unittest.main()
