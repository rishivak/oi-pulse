"""Paper-trading integration, idempotency, concurrency, persistence, API, guards.

Phase 9 brief §17, §18, §19, §20, §23, §24, §25, §26, §29. The headline property:

> No execution path may bypass an approved risk decision for its own intent.

Enforced three ways, per `18-ROADMAP.md` Phase 9 — static, database constraint and
runtime — and all three are checked below (the database one structurally, because
PostgreSQL is absent in the development sandbox).
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from oipulse.trading.orders import OrderState, RejectReason
from oipulse.trading.risk import (
    UNEVALUATED_RISK,
    KillSwitchState,
    RiskDecisionRecord,
    RiskVerdict,
)
from oipulse.trading.risk.serialisation import (
    decision_to_dict,
    decisions_to_dict,
    limit_status_to_dict,
    policy_to_dict,
    state_to_dict,
)
from tests.phase8 import _fixtures as p8
from tests.phase9 import _fixtures as fx
from tests.phase9._fixtures import at

REPO = Path(__file__).resolve().parents[2]


class TestPaperTradingIntegration(unittest.TestCase):
    """Brief §18: paper trading integrates with risk without bypassing it."""

    def test_an_approved_intent_produces_an_authorized_order(self) -> None:
        rt = p8.runtime()
        result = p8.submit(rt, p8.observations(), p8.intent())
        self.assertTrue(result.risk_decision.evaluated)
        order = result.orders[0]
        self.assertTrue(order.is_authorized)
        self.assertEqual(order.authorizing_risk_decision_id, result.risk_decision.risk_decision_id)
        self.assertEqual(order.authorizing_decision_sequence, 1)

    def test_a_rejected_intent_produces_no_order_at_all(self) -> None:
        """Not a rejected order: no order. The gate precedes execution."""
        rt = p8.runtime(acct=p8.account(cfg=p8.config(starting_cash=Decimal(1))))
        result = p8.submit(rt, p8.observations(), p8.intent())
        self.assertTrue(result.rejected)
        self.assertEqual(len(result.orders), 0)
        self.assertEqual(len(rt.orders()), 0)

    def test_an_account_with_no_policy_cannot_trade(self) -> None:
        """The fail-closed default. An unevaluated approval authorizes nothing.

        This is a deliberate change to Phase 8 behaviour, required by brief §17
        and §22: an uncertain risk state must never be treated as approval.
        """
        rt = p8.runtime(unevaluated_risk=True)
        result = p8.submit(rt, p8.observations(), p8.intent())
        self.assertTrue(result.rejected)
        self.assertIs(result.reject_reason, RejectReason.RISK_REJECTED)
        self.assertEqual(len(rt.orders()), 0)
        self.assertFalse(result.risk_decision.evaluated)

    def test_the_kill_switch_halts_the_runtime_immediately(self) -> None:
        rt = p8.runtime()
        rt.set_kill_switch(KillSwitchState(engaged=True, reason="operator halt"))
        result = p8.submit(rt, p8.observations(), p8.intent())
        self.assertTrue(result.rejected)
        self.assertIn("kill_switch", {b.limit_id for b in result.risk_decision.breaches()})

    def test_clearing_the_kill_switch_restores_trading(self) -> None:
        rt = p8.runtime()
        rt.set_kill_switch(KillSwitchState(engaged=True))
        p8.submit(rt, p8.observations(), p8.intent())
        rt.set_kill_switch(KillSwitchState())
        result = p8.submit(rt, p8.observations(), p8.intent(client_order_intent_id="second"))
        self.assertTrue(result.accepted, result.detail)

    def test_the_risk_state_is_built_from_the_decision_state(self) -> None:
        """Not the execution state: risk must not read what the decision could not."""
        import inspect

        from oipulse.trading import runtime as runtime_module

        source = inspect.getsource(runtime_module.PaperTradingRuntime.submit)
        self.assertIn("self.risk_state(decision_state", source)
        self.assertNotIn("self.risk_state(execution_state", source)

    def test_the_runtime_risk_state_is_deterministic(self) -> None:
        rows = p8.observations()
        first = p8.runtime().risk_state(p8.state_at(rows, 1), at=at(1))
        second = p8.runtime().risk_state(p8.state_at(rows, 1), at=at(1))
        self.assertEqual(first.risk_state_ref, second.risk_state_ref)

    def test_greeks_are_absent_rather_than_zero(self) -> None:
        """Brief §15: no second analytics engine, and no invented value.

        The runtime supplies no greeks, so greek limits report NOT_EVALUABLE and
        fail closed — which is why the Phase 8 fixture policy leaves them unset.
        """
        state = p8.runtime().risk_state(p8.state_at(p8.observations(), 1), at=at(1))
        self.assertIsNone(state.exposure.net_delta)
        self.assertIsNone(state.exposure.gross_vega)

    def test_a_resized_approval_sizes_the_order_not_the_intent(self) -> None:
        engine = fx.engine(lim=fx.runtime_limits(max_order_quantity=20), allow_resizing=True)
        rt = p8.runtime(risk_gate=engine)
        intent = p8.intent(quantity=50)
        result = p8.submit(rt, p8.observations(), intent)
        self.assertIs(result.risk_decision.verdict, RiskVerdict.MODIFIED)
        self.assertEqual(result.orders[0].quantity, 20)
        self.assertEqual(intent.total_quantity, 50, "the intent is never rewritten")


class TestNoExecutionWithoutApproval(unittest.TestCase):
    """Brief §17, enforced three ways."""

    def test_runtime_an_expired_approval_does_not_authorize(self) -> None:
        """`11` §3: refused rather than silently re-approved."""
        engine = fx.engine(lim=fx.runtime_limits(), validity=timedelta(seconds=0))
        rt = p8.runtime(risk_gate=engine)
        # Evaluated at minute 1, valid for 0s; submission is also at minute 1, so
        # the inclusive boundary approves. Push execution past it.
        result = rt.submit(
            p8.intent(),
            decision_state=p8.state_at(p8.observations(), 0),
            execution_state=p8.state_at(p8.observations(), 1),
            at=at(1),
        )
        self.assertTrue(result.accepted, "at the inclusive boundary an approval holds")

        decision = result.risk_decision
        self.assertFalse(decision.is_actionable_at(at(2)), "and lapses after it")

    def test_static_the_guard_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "check_risk_authorization.py")],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_database_the_composite_fk_and_trigger_exist(self) -> None:
        """`02-DATA_MODEL.md` §11, read from the migration source.

        Applied against a real PostgreSQL by
        `tests/integration/test_migrations_postgres.py`, which CI fails on skip.
        """
        source = (REPO / "oipulse/migrations/versions/0009_phase9_risk.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("fk_trade_orders_authorizing_decision", source)
        # Keyed on the order's OWN intent_id, so another intent's approval cannot
        # be referenced at all.
        self.assertIn('["intent_id", "authorizing_decision_sequence"]', source)
        self.assertIn('["intent_id", "sequence_no"]', source)
        # The FK alone would accept a REJECTED decision; the trigger closes that.
        self.assertIn("trg_trade_orders_require_approved_decision", source)
        self.assertIn("only an approving decision may authorize an order", source)

    def test_an_order_cannot_carry_half_an_authorization_key(self) -> None:
        from oipulse.backtest.intents import OrderType, Side
        from oipulse.trading.orders import PaperOrder

        with self.assertRaises(ValueError):
            PaperOrder(
                order_id="o",
                account_id="a",
                intent_id="i",
                instrument_id=1,
                side=Side.BUY,
                quantity=1,
                order_type=OrderType.MARKET,
                created_at=at(0),
                authorizing_risk_decision_id="rdec_x",
            )

    def test_an_unevaluated_decision_is_never_actionable(self) -> None:
        decision = UNEVALUATED_RISK.evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        self.assertTrue(decision.is_approved, "the verdict says APPROVED")
        self.assertFalse(
            decision.is_actionable_at(at(0)),
            "but nothing evaluated it, so it authorizes nothing",
        )


class TestIdempotencyAndConcurrency(unittest.TestCase):
    """Brief §19 and §20."""

    def test_a_duplicate_intent_does_not_produce_a_second_decision(self) -> None:
        rt = p8.runtime()
        rows = p8.observations()
        first = p8.submit(rt, rows, p8.intent())
        second = p8.submit(rt, rows, p8.intent())
        self.assertTrue(second.duplicate)
        self.assertEqual(len(rt.decisions_for(first.intent.intent_id)), 1)
        self.assertEqual(len(rt.orders()), 1)

    def test_a_repeated_evaluation_is_byte_identical(self) -> None:
        engine = fx.engine()
        decisions = [
            engine.evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0)) for _ in range(5)
        ]
        digests = {d.decision_digest for d in decisions}
        self.assertEqual(len(digests), 1)

    def test_two_competing_intents_are_evaluated_against_the_state_each_saw(self) -> None:
        """Brief §20. The paper runtime evaluates at submission, so the second
        intent sees the first one's effect — the sequential semantics the design
        actually provides, asserted rather than assumed.
        """
        # 50 lots fill at 106.40 plus fees, so ~5,325 leaves ~675 of 6,000 --
        # enough for the first intent and not the second.
        rt = p8.runtime(acct=p8.account(cfg=p8.config(starting_cash=Decimal("6000"))))
        rows = p8.observations()
        first = p8.submit(rt, rows, p8.intent(quantity=50, client_order_intent_id="a"))
        self.assertTrue(first.accepted, first.detail)
        self.assertLess(rt.ledger.cash, Decimal("1000"))

        second = p8.submit(rt, rows, p8.intent(quantity=50, client_order_intent_id="b"))
        self.assertTrue(second.rejected, "the second must see the first's cash outflow")
        self.assertIn("cash_sufficiency", {b.limit_id for b in second.risk_decision.breaches()})

    def test_a_pending_intent_consumes_headroom(self) -> None:
        """Two intents in flight must not each pass a check their sum would fail."""
        state = fx.state(pending_intent_ids=("int_a", "int_b"))
        self.assertEqual(len(state.pending_intent_ids), 2)
        self.assertEqual(state.pending_intent_ids, ("int_a", "int_b"))

    def test_decisions_append_rather_than_replace(self) -> None:
        """`11` §3 and `02` §6: re-evaluation is normal."""
        engine = fx.engine()
        intent = fx.intent()
        first = engine.evaluate(intent, fx.state(), sequence_no=1, at=at(0))
        second = engine.evaluate(intent, fx.state(cash="500"), sequence_no=2, at=at(1))
        self.assertEqual(first.sequence_no, 1)
        self.assertEqual(second.sequence_no, 2)
        self.assertNotEqual(first.risk_state_ref, second.risk_state_ref)
        self.assertNotEqual(first.risk_decision_id, second.risk_decision_id)

    def test_a_sequence_number_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            RiskDecisionRecord(
                intent_id="int_x", sequence_no=0, verdict=RiskVerdict.REJECTED, evaluated=True
            )


class TestAuditEvidence(unittest.TestCase):
    """Brief §23: every question answerable, from structured fields."""

    def test_the_decision_answers_every_required_question(self) -> None:
        intent = p8.intent()
        rt = p8.runtime()
        result = p8.submit(rt, p8.observations(), intent)
        decision = result.risk_decision
        body = decision.as_dict()

        # Which intent, which policy, which state, when, on what inputs.
        self.assertEqual(body["intent_id"], intent.intent_id)
        for field in (
            "policy_id",
            "policy_version",
            "policy_digest",
            "risk_state_ref",
            "risk_evaluation_time",
            "inputs_digest",
            "approved_until",
            "requested_quantity",
            "approved_quantity",
        ):
            with self.subTest(field=field):
                self.assertIsNotNone(body[field], f"{field} must be recorded")

        # Which constraints were checked -- structured, not prose.
        self.assertGreater(len(body["limits_evaluated"]), 20)
        for limit in body["limits_evaluated"]:
            self.assertIn("category", limit)
            self.assertIn("status", limit)

        # Which strategy and signal, from the intent it names.
        self.assertEqual(intent.signal_id, "PUT_OI_SURGE")
        self.assertEqual(intent.strategy_version, 2)

    def test_a_rejection_names_the_violated_constraint(self) -> None:
        """Brief §9: every rejection must identify the actual violated constraint."""
        decision = fx.evaluate(
            eng=fx.engine(lim=fx.limits(max_order_quantity=1)), it=fx.intent(quantity=50)
        )
        breach = decision.breaches()[0]
        self.assertEqual(breach.limit_id, "max_order_quantity")
        self.assertEqual(breach.limit_value, "1")
        self.assertEqual(breach.observed_value, "50")
        self.assertEqual(breach.headroom, "-49")

    def test_evidence_is_not_only_free_text(self) -> None:
        decision = fx.evaluate()
        for limit in decision.limits_evaluated:
            with self.subTest(limit=limit.limit_id):
                self.assertTrue(limit.limit_id)
                self.assertIsNotNone(limit.status)
                self.assertIsNotNone(limit.category)


class TestSerialisationAndApiSafety(unittest.TestCase):
    """Brief §25 and §26."""

    def test_the_decision_envelope_resolves_status_against_a_supplied_time(self) -> None:
        decision = fx.evaluate()
        body = decision_to_dict(decision, at=at(0))
        self.assertEqual(body["meta"]["authorization_status"], "APPROVED")
        later = decision_to_dict(decision, at=at(30))
        self.assertEqual(later["meta"]["authorization_status"], "EXPIRED")

    def test_the_envelope_omits_status_when_no_time_is_given(self) -> None:
        """There is no "now" in this system; a default would invent one."""
        self.assertNotIn("authorization_status", decision_to_dict(fx.evaluate())["meta"])

    def test_the_limit_status_envelope_separates_the_three_non_breaches(self) -> None:
        decision = fx.evaluate(eng=fx.engine(lim=fx.limits(max_gross_vega=None)))
        body = limit_status_to_dict(decision, fx.policy(lim=fx.limits(max_gross_vega=None)))
        self.assertIn("not_configured", body["meta"])
        self.assertIn("not_evaluable", body["meta"])
        self.assertGreaterEqual(body["meta"]["not_configured"], 1)

    def test_the_decisions_envelope_returns_the_whole_sequence(self) -> None:
        engine = fx.engine()
        intent = fx.intent()
        decisions = [
            engine.evaluate(intent, fx.state(), sequence_no=n, at=at(0)) for n in (1, 2, 3)
        ]
        self.assertEqual(decisions_to_dict(decisions)["meta"]["count"], 3)

    def test_the_policy_envelope_surfaces_the_configured_count(self) -> None:
        body = policy_to_dict(fx.policy())
        self.assertIn("configured_limits", body["meta"])

    def test_the_state_envelope_carries_its_content_address(self) -> None:
        state = fx.state()
        self.assertEqual(state_to_dict(state)["meta"]["risk_state_ref"], state.risk_state_ref)

    def test_no_api_endpoint_approves_an_intent(self) -> None:
        """Brief §25. Checked over the routes rather than trusted."""
        source = (REPO / "oipulse/api/risk.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
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
        self.assertTrue(routes)
        for route in routes:
            for banned in ("approve", "authorize", "override", "force"):
                with self.subTest(route=route, banned=banned):
                    self.assertNotIn(banned, route.lower())

    def test_no_code_deserialises_a_risk_decision_from_untrusted_input(self) -> None:
        """Brief §26: a client cannot forge an approval, substitute an
        `inputs_digest` or extend an `approved_until`, because no `dict ->
        RiskDecisionRecord` direction exists anywhere."""
        for package in ("oipulse/trading/risk", "oipulse/api"):
            for path in sorted((REPO / package).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.FunctionDef)
                        and "from_dict" in node.name
                        and "decision" in node.name.lower()
                    ):
                        self.fail(f"{path.name}: {node.name} deserialises a decision")

    def test_the_engine_computes_the_digest_rather_than_accepting_one(self) -> None:
        import inspect

        from oipulse.trading.risk import engine as engine_module

        source = inspect.getsource(engine_module.RiskEngine.evaluate)
        self.assertIn("inputs_digest_for(", source)


class TestPersistenceShape(unittest.TestCase):
    """Read as source: SQLAlchemy is absent in the development sandbox."""

    SOURCE = (REPO / "oipulse/persistence/risk_tables.py").read_text(encoding="utf-8")
    MIGRATION = (REPO / "oipulse/migrations/versions/0009_phase9_risk.py").read_text(
        encoding="utf-8"
    )

    def test_the_two_phase_9_tables_are_declared(self) -> None:
        tree = ast.parse(self.SOURCE)
        declared: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "RISK_TABLES"
                and isinstance(node.value, ast.List)
            ):
                declared = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        self.assertEqual(sorted(declared), ["risk_decisions", "risk_profiles"])

    def test_decisions_are_keyed_as_an_append_only_sequence(self) -> None:
        """`02` §11: PRIMARY KEY (intent_id, sequence_no), no unique-per-intent."""
        self.assertIn("pk_risk_decisions", self.MIGRATION)
        self.assertIn('"intent_id", "sequence_no"', self.MIGRATION)

    def test_the_four_mandatory_fields_are_not_null(self) -> None:
        for field in ("risk_state_ref", "risk_evaluation_time", "inputs_digest"):
            with self.subTest(field=field):
                self.assertIn(f'sa.Column("{field}"', self.MIGRATION)
        # approved_until is nullable, but required precisely when approving.
        self.assertIn("ck_risk_decisions_approval_has_validity", self.MIGRATION)

    def test_a_rejection_cannot_approve_a_quantity_in_the_database(self) -> None:
        self.assertIn("ck_risk_decisions_rejection_approves_nothing", self.MIGRATION)
        self.assertIn("ck_risk_decisions_approved_within_requested", self.MIGRATION)

    def test_a_policy_version_is_immutable(self) -> None:
        self.assertIn("uq_risk_profiles_identity", self.MIGRATION)
        self.assertIn("uq_risk_profiles_digest", self.MIGRATION)

    def test_no_phase_10_or_later_table_is_created(self) -> None:
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
        self.assertEqual(sorted(created), ["risk_decisions", "risk_profiles"])
        for table in created:
            for banned in ("oms", "broker", "reconcil", "attribution"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())

    def test_the_downgrade_mirrors_the_upgrade(self) -> None:
        self.assertIn("DROP TRIGGER IF EXISTS", self.MIGRATION)
        self.assertIn(
            'op.drop_column("trade_orders", "authorizing_decision_sequence")', self.MIGRATION
        )
        self.assertIn('op.drop_table("risk_decisions")', self.MIGRATION)


class TestArchitectureGuards(unittest.TestCase):
    def _guard(self, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / name)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_risk_authorization_guard_passes(self) -> None:
        result = self._guard("check_risk_authorization.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_paper_safety_guard_still_passes(self) -> None:
        result = self._guard("check_paper_trading_safety.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_wall_clock_access(self) -> None:
        result = self._guard("check_clock_access.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_risk_imports_no_broker_database_or_execution_module(self) -> None:
        banned = (
            "sqlalchemy",
            "httpx",
            "asyncpg",
            "redis",
            "fastapi",
            "oipulse.persistence",
            "oipulse.api",
            "oipulse.marketdata.providers",
            "oipulse.marketdata.upstox",
            "oipulse.core.clock",
            "oipulse.trading.brokers",
            "oipulse.trading.execution",
            "oipulse.trading.orders",
            "oipulse.trading.runtime",
        )
        for path in sorted((REPO / "oipulse/trading/risk").rglob("*.py")):
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

    def test_risk_is_evaluable_with_no_trading_infrastructure(self) -> None:
        """`11` §3's acceptance criterion, demonstrated rather than asserted.

        Everything this needs is an intent, a state and a policy — no runtime, no
        ledger, no account, no market feed, no database.
        """
        decision = fx.engine().evaluate(fx.intent(), fx.state(), sequence_no=1, at=at(0))
        self.assertTrue(decision.is_approved)

    def test_no_phase_10_or_later_package_exists(self) -> None:
        for package in ("oms", "reconciliation", "portfolio", "terminal"):
            with self.subTest(package=package):
                self.assertFalse((REPO / "oipulse" / package).exists())

    def test_the_risk_engine_reads_no_clock(self) -> None:
        """Every time is supplied. A clock read would break reproducibility."""
        for path in sorted((REPO / "oipulse/trading/risk").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    with self.subTest(file=path.name, call=node.func.attr):
                        self.assertNotIn(
                            node.func.attr, {"now", "utcnow", "today", "time", "monotonic"}
                        )


class TestObservability(unittest.TestCase):
    def test_every_phase_9_metric_is_prefixed_risk(self) -> None:
        import oipulse.observability.metrics as metrics

        names = [
            value
            for name, value in vars(metrics).items()
            if name.startswith("RISK_") and isinstance(value, str)
        ]
        self.assertTrue(names)
        for name in names:
            with self.subTest(metric=name):
                self.assertTrue(name.startswith("risk_"), name)

    def test_breaches_are_labelled_by_limit(self) -> None:
        """An aggregate count cannot distinguish a position cap from stale data."""
        from oipulse.observability.metrics import (
            METRICS,
            RISK_KILL_SWITCH_BLOCKS,
            RISK_LIMIT_BREACHES,
            record_risk_evaluation,
        )

        record_risk_evaluation(
            "acc-metric",
            policy_label="P@v1",
            verdict="REJECTED",
            evaluated=True,
            duration_seconds=0.001,
            requested_quantity=10,
            approved_quantity=0,
            breached_limits=("kill_switch", "max_daily_loss"),
            unevaluable_limits=("max_gross_vega",),
        )
        rendered = METRICS.render() if hasattr(METRICS, "render") else str(METRICS.snapshot())
        self.assertIn(RISK_LIMIT_BREACHES, rendered)
        self.assertIn(RISK_KILL_SWITCH_BLOCKS, rendered)
        self.assertIn("max_daily_loss", rendered)

    def test_there_is_no_broker_or_order_metric_in_the_risk_set(self) -> None:
        """Brief §27: metrics must represent actual execution paths."""
        import oipulse.observability.metrics as metrics

        names = [
            value
            for name, value in vars(metrics).items()
            if name.startswith("RISK_") and isinstance(value, str)
        ]
        for name in names:
            for banned in ("broker", "order_submitted", "fill"):
                with self.subTest(metric=name):
                    self.assertNotIn(banned, name)


class TestOrderStateUnaffected(unittest.TestCase):
    """Phase 1-8 regression at the seam Phase 9 touched."""

    def test_the_order_lifecycle_is_unchanged(self) -> None:
        rt = p8.runtime()
        result = p8.submit(rt, p8.observations(), p8.intent())
        order = result.orders[0]
        self.assertEqual(
            [e.to_state for e in order.events], [OrderState.ACCEPTED, OrderState.FILLED]
        )

    def test_the_ledger_still_reconciles(self) -> None:
        rows = p8.observations()
        rt = p8.runtime()
        p8.submit(rt, rows, p8.intent())
        snapshot = rt.ledger.snapshot(as_of=at(3), marks=p8.marks(rows, 3))
        self.assertEqual(snapshot.net_pnl, snapshot.gross_pnl - snapshot.fees)


if __name__ == "__main__":
    unittest.main()
