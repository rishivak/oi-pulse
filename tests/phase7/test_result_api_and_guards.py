"""Result identity, research compatibility, persistence, API shape, guards, performance.

Brief §18 to §32. The recurring theme is that a number must never travel without the
assumptions that produced it, and that Phase 7 must not build any part of Phase 8+.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from oipulse.backtest.result import BacktestExecutionMetadata
from oipulse.backtest.risk import UNCONSTRAINED_RISK, RiskGate, RiskVerdict
from oipulse.backtest.runner import BacktestRunner
from oipulse.backtest.serialisation import (
    equity_curve_to_dict,
    result_to_dict,
    trades_to_dict,
)
from oipulse.replay.serialisation import (
    context_to_dict,
    progress_to_dict,
    step_to_dict,
    timeline_to_dict,
)
from oipulse.research.access import PointInTimeAccessor
from tests.phase3._fixtures import at
from tests.phase7 import _fixtures as fx

REPO = Path(__file__).resolve().parents[2]


def _run(
    *,
    rows: list[object] | None = None,
    risk_gate: RiskGate | None = None,
    executed_at=None,
    latency: timedelta = timedelta(seconds=60),
):
    rows = rows if rows is not None else fx.observations()
    return BacktestRunner(
        fx.engine(rows),
        fill_model=fx.fill_model(latency=latency),
        opening_cash=Decimal(500000),
        risk_gate=risk_gate,
    ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor(), executed_at=executed_at)


class TestResultIdentity(unittest.TestCase):
    def test_two_identical_runs_share_a_content_hash(self) -> None:
        self.assertEqual(_run().content_hash, _run().content_hash)

    def test_execution_metadata_is_excluded_from_identity(self) -> None:
        """When a run happened is not part of what it found."""
        early = _run(executed_at=at(0))
        late = _run(executed_at=at(100))
        self.assertEqual(early.content_hash, late.content_hash)
        self.assertNotEqual(early.execution.executed_at, late.execution.executed_at)

    def test_the_exclusion_is_structural_not_a_maintained_list(self) -> None:
        """`ExecutionMetadata` is a type the hash function never receives.

        A field added to it later cannot leak into semantic identity by accident.

        Checks the dict keys the digest is actually built from, not the word
        "execution" anywhere in the method: the docstring says the hash *excludes*
        execution metadata, and a substring search would flag that disclaimer. A
        statement that something is absent is not an instance of it.
        """
        import inspect
        import textwrap

        source = textwrap.dedent(
            inspect.getsource(type(_run()).content_hash.fget)  # type: ignore[union-attr]
        )
        tree = ast.parse(source)
        keys: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys += [k.value for k in node.keys if isinstance(k, ast.Constant)]
        self.assertTrue(keys, "no digest payload parsed")
        for banned in ("execution", "executed_at", "duration", "host", "run_id"):
            with self.subTest(key=banned):
                self.assertNotIn(banned, keys)
        # And the things that must be in it.
        for required in ("strategy_digest", "assumptions", "statistics", "risk_evaluated"):
            with self.subTest(key=required):
                self.assertIn(required, keys)

    def test_changing_a_fill_assumption_changes_the_result_identity(self) -> None:
        """Two runs under different assumptions are not comparable, and say so."""
        rows = fx.observations()
        base = BacktestRunner(
            fx.engine(rows), fill_model=fx.fill_model(), opening_cash=Decimal(500000)
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())
        slipped = BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(slippage_parameter=Decimal("0.9")),
            opening_cash=Decimal(500000),
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())
        self.assertNotEqual(base.content_hash, slipped.content_hash)

    def test_the_assumption_set_travels_with_the_result(self) -> None:
        result = _run()
        for key in (
            "latency_seconds",
            "slippage_model",
            "assumed_spread_fraction",
            "rejection_rate",
            "cost_model",
        ):
            self.assertIn(key, result.assumptions)

    def test_no_expected_return_or_annualised_projection_is_reported(self) -> None:
        """`10` §9: a model's output is never presented as what would have happened."""
        fields = set(_run().statistics.as_dict())
        for banned in ("expected_return", "annualised_return", "annualized_return", "sharpe"):
            self.assertNotIn(banned, fields)


class TestRiskSeam(unittest.TestCase):
    def test_a_run_with_no_risk_engine_is_flagged_and_says_why(self) -> None:
        result = _run()
        self.assertFalse(result.risk_evaluated)
        self.assertTrue(any("no risk engine" in c for c in result.caveats()))

    def test_the_default_gate_is_named_so_it_cannot_pass_for_a_risk_engine(self) -> None:
        self.assertEqual(UNCONSTRAINED_RISK.name, "UNCONSTRAINED")
        self.assertFalse(UNCONSTRAINED_RISK.evaluates_risk)

    def test_a_rejecting_gate_prevents_the_fill(self) -> None:
        """The seam is real: a gate that says no is obeyed."""

        class RejectAll:
            name = "REJECT_ALL"
            evaluates_risk = True

            def evaluate(self, intent):  # type: ignore[no-untyped-def]
                from oipulse.backtest.risk import RiskDecisionStub

                return RiskDecisionStub(
                    intent_id=intent.intent_id,
                    verdict=RiskVerdict.REJECTED,
                    reason="test",
                    evaluated=True,
                )

        result = _run(risk_gate=RejectAll())  # type: ignore[arg-type]
        self.assertEqual(result.statistics.intents_rejected_by_risk, 1)
        self.assertEqual(len(result.fills), 0)
        self.assertTrue(result.risk_evaluated)

    def test_the_real_risk_decision_name_is_not_taken_by_the_stub(self) -> None:
        """Phase 9 owns `RiskDecision`; a weaker type must not occupy the name."""
        import oipulse.backtest.risk as risk

        self.assertFalse(hasattr(risk, "RiskDecision"))
        self.assertTrue(hasattr(risk, "RiskDecisionStub"))


class TestSerialisationContracts(unittest.TestCase):
    """Pure envelope shapes, testable with no web stack installed."""

    def test_every_replay_step_carries_both_times(self) -> None:
        tl = fx.timeline(fx.observations())
        for step in tl.steps:
            body = step_to_dict(step)
            self.assertIn("market_time", body)
            self.assertIn("knowledge_time", body)

    def test_the_replay_context_envelope_badges_hindsight(self) -> None:
        body = context_to_dict(fx.context())
        self.assertIn("is_hindsight", body)
        self.assertFalse(body["is_hindsight"])

    def test_a_result_envelope_carries_its_assumptions_and_flags_in_meta(self) -> None:
        body = result_to_dict(_run())
        for key in ("assumptions", "assumption_based", "risk_evaluated", "caveats"):
            self.assertIn(key, body["meta"])

    def test_the_trades_envelope_includes_rejections(self) -> None:
        """A trades list that hid rejections would flatter the strategy."""
        rows = fx.observations()
        result = BacktestRunner(
            fx.engine(rows),
            fill_model=fx.fill_model(rejection_rate=Decimal("1")),
            opening_cash=Decimal(500000),
        ).run(fx.BuyOnceStrategy(), fx.timeline(rows), PointInTimeAccessor())
        body = trades_to_dict(result)
        self.assertEqual(body["meta"]["rejections"], 1)
        self.assertEqual(len(body["data"]), 1)

    def test_the_equity_curve_declares_that_it_is_not_interpolated(self) -> None:
        body = equity_curve_to_dict((), run_id="r")
        self.assertFalse(body["meta"]["interpolated"])

    def test_checkpoint_reuse_is_reported_without_a_division_error(self) -> None:
        from oipulse.replay.engine import ReplayProgress

        self.assertEqual(progress_to_dict(ReplayProgress())["checkpoint_reuse_ratio"], 0.0)

    def test_the_timeline_envelope_reports_counts_not_the_whole_series_by_default(self) -> None:
        body = timeline_to_dict(fx.timeline(fx.observations()))
        self.assertIn("step_count", body)
        self.assertNotIn("steps", body)


class TestPersistenceShape(unittest.TestCase):
    """Read as source, not imported.

    `oipulse/persistence/backtest_tables.py` imports SQLAlchemy, which is absent in
    the development sandbox. Parsing the declaration keeps the contract checkable
    here; the live schema is asserted against a real PostgreSQL in
    `tests/integration/test_migrations_postgres.py`, which CI fails on skip.
    """

    SOURCE = (REPO / "oipulse/persistence/backtest_tables.py").read_text(encoding="utf-8")

    def test_the_four_phase_7_tables_are_declared(self) -> None:
        tree = ast.parse(self.SOURCE)
        declared: list[str] = []
        for node in ast.walk(tree):
            # Declared as `BACKTEST_TABLES: list[str] = [...]`, so an AnnAssign.
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "BACKTEST_TABLES"
                and isinstance(node.value, ast.List)
            ):
                declared = [
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant)
                ]
        self.assertEqual(
            sorted(declared),
            ["backtest_results", "backtest_trades", "replay_events", "replay_runs"],
        )

    def test_replay_events_are_a_separate_table_from_the_live_outbox(self) -> None:
        """`10` §8: a flag can be forgotten in a WHERE clause; a table cannot."""
        self.assertIn("replay_events", self.SOURCE)
        self.assertNotIn("sys_outbox", self.SOURCE)


class TestArchitectureGuards(unittest.TestCase):
    """The guards must pass, and the Phase 7 contracts must actually be armed."""

    def _guard(self, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / name)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_strategy_surface_guard_passes(self) -> None:
        result = self._guard("check_strategy_surface.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_import_boundary_guard_passes_with_the_phase_7_contracts_armed(self) -> None:
        result = self._guard("check_import_boundaries.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for contract in (
            "replay-is-pure",
            "replay-never-reaches-a-broker-or-a-provider",
            "backtest-is-pure-and-cannot-trade",
            "no-phase-8-or-later-leakage",
        ):
            self.assertIn(contract, result.stdout)

    def test_no_wall_clock_access(self) -> None:
        result = self._guard("check_clock_access.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_neither_package_imports_a_broker_provider_or_database(self) -> None:
        """Asserted against the import graph, not against intent."""
        banned = (
            "sqlalchemy",
            "httpx",
            "asyncpg",
            "redis",
            "fastapi",
            "oipulse.persistence",
            "oipulse.api",
            "oipulse.alerts",
            "oipulse.marketdata.providers",
        )
        for package in ("replay", "backtest"):
            for path in sorted((REPO / "oipulse" / package).rglob("*.py")):
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

    def test_phase_7_does_not_depend_on_any_later_phase(self) -> None:
        """Replay and backtest must not import a later layer.

        This assertion previously read "no later-phase package exists at all",
        which was the right check while Phase 7 was the frontier. Phase 8 has since
        landed `oipulse/trading`, so the *existence* form is now false for a
        legitimate reason and would have to be deleted or weakened to pass.

        It is replaced with the stronger property it was actually protecting:
        Phase 7 code must not **depend** on a later phase. Existence was only ever
        a proxy for that, and the dependency check keeps biting as later phases
        arrive, where the existence check would have to be relaxed at each one.

        The packages that do not yet exist are still listed: naming a package that
        is absent costs nothing and arms the rule before the code it governs.
        """
        later_phases = ("trading", "paper", "portfolio", "terminal", "oms")
        for package in ("replay", "backtest"):
            for path in sorted((REPO / "oipulse" / package).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                imported: list[str] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported += [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.append(node.module)
                for module in imported:
                    for later in later_phases:
                        target = f"oipulse.{later}"
                        with self.subTest(file=path.name, module=module):
                            self.assertFalse(
                                module == target or module.startswith(target + "."),
                                f"{package}/{path.name} imports {module}, which "
                                f"belongs to a later phase",
                            )

    def test_no_api_route_implies_live_trading(self) -> None:
        for name in ("replay.py", "backtest.py"):
            source = (REPO / "oipulse/api" / name).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name)
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
            self.assertTrue(routes, f"no routes parsed from {name}")
            for route in routes:
                for banned in ("order", "cancel", "submit", "broker", "live"):
                    with self.subTest(route=route, banned=banned):
                        self.assertNotIn(banned, route.lower())


class TestObservability(unittest.TestCase):
    def test_the_phase_7_metrics_record_the_qualifying_flags(self) -> None:
        from oipulse.observability.metrics import (
            BACKTEST_ASSUMPTION_BASED_FILLS,
            BACKTEST_UNRISKED_RUNS,
            METRICS,
            record_backtest_run,
        )

        record_backtest_run(
            "run-metric-test",
            "S",
            status="ok",
            duration_seconds=0.1,
            intents=1,
            fills=1,
            partial_fills=0,
            assumption_based_fills=1,
            rejection_reasons={},
            risk_evaluated=False,
        )
        rendered = METRICS.render() if hasattr(METRICS, "render") else str(METRICS.snapshot())
        self.assertIn(BACKTEST_ASSUMPTION_BASED_FILLS, rendered)
        self.assertIn(BACKTEST_UNRISKED_RUNS, rendered)

    def test_replay_checkpoint_hits_and_misses_are_both_recorded(self) -> None:
        from oipulse.observability.metrics import (
            METRICS,
            REPLAY_CHECKPOINT_HITS,
            REPLAY_CHECKPOINT_MISSES,
            record_replay_run,
        )

        record_replay_run(
            "run-metric-test",
            status="ok",
            duration_seconds=0.1,
            steps=5,
            states_built=5,
            checkpoint_hits=0,
            checkpoint_misses=5,
        )
        rendered = METRICS.render() if hasattr(METRICS, "render") else str(METRICS.snapshot())
        self.assertIn(REPLAY_CHECKPOINT_HITS, rendered)
        self.assertIn(REPLAY_CHECKPOINT_MISSES, rendered)


class TestPerformanceObservations(unittest.TestCase):
    """Measured, not asserted against a target.

    These record what the synthetic fixture costs on this machine. A hard threshold
    would fail on a slower CI box for no architectural reason, so the test asserts
    only that the work completes and prints the figures for the report.
    """

    def test_replay_throughput_is_measured(self) -> None:
        rows = fx.observations(minutes=6)
        tl = fx.timeline(rows)
        started = time.perf_counter()
        results = fx.engine(rows).run(tl)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(results), len(tl.steps))
        print(
            f"\n  replay: {len(tl.steps)} steps, {len(tl.observations)} observations "
            f"in {elapsed * 1000:.1f} ms "
            f"({len(tl.observations) / max(elapsed, 1e-9):,.0f} obs/sec)"
        )

    def test_backtest_end_to_end_is_measured(self) -> None:
        started = time.perf_counter()
        result = _run()
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(result.content_hash)
        print(f"  backtest end-to-end: {elapsed * 1000:.1f} ms")

    def test_ledger_application_is_measured(self) -> None:
        from oipulse.backtest.costs import CostBreakdown
        from oipulse.backtest.fills import Fill, PriceSource, SlippageModel
        from oipulse.backtest.intents import Side
        from oipulse.backtest.ledger import Ledger

        book = Ledger(opening_cash=Decimal(10_000_000))
        fills = [
            Fill(
                intent_id=f"ti_{i}",
                instrument_id=fx.TARGET,
                side=Side.BUY if i % 2 == 0 else Side.SELL,
                quantity=1,
                requested_quantity=1,
                price=Decimal(100 + i % 7),
                reference_price=Decimal(100),
                filled_at=at(1),
                price_source=PriceSource.OBSERVED_QUOTE,
                slippage_model=SlippageModel.MID,
                costs=CostBreakdown(),
                assumption_based=False,
            )
            for i in range(10000)
        ]
        started = time.perf_counter()
        applied = book.apply_all(fills)
        elapsed = time.perf_counter() - started
        self.assertEqual(applied, 10000)
        print(f"  ledger: 10,000 fills in {elapsed * 1000:.1f} ms")


class TestResearchCompatibility(unittest.TestCase):
    """Brief §19. One availability implementation, used by research and backtest alike."""

    def test_the_strategy_context_uses_the_research_accessor_not_a_copy(self) -> None:
        """A second availability filter would drift from the first, silently.

        `09-RESEARCH.md` §2 owns the refusal semantics; the backtest surface wraps
        that object rather than reimplementing the comparison.
        """
        import inspect

        from oipulse.backtest import strategy as strategy_module

        source = inspect.getsource(strategy_module)
        self.assertIn("PointInTimeAccessor", source)

        # No local re-derivation of availability. Checked as an AST comparison
        # rather than as the text "available_at <=", because the module docstring
        # *describes* the rule in exactly those words -- a substring search would
        # flag the documentation of the contract as a breach of it.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for operand in operands:
                    if isinstance(operand, ast.Attribute) and operand.attr == "available_at":
                        self.fail(
                            f"line {node.lineno}: the strategy surface compares "
                            f"available_at itself; availability must be decided in "
                            f"one place (oipulse/research/access.py)"
                        )

    def test_research_and_backtest_refuse_the_same_unavailable_feature(self) -> None:
        from oipulse.analytics.values import MetricValue, Scope, ScopeRef
        from oipulse.backtest.strategy import FeatureView
        from oipulse.marketstate.staleness import QualityStatus
        from oipulse.research.access import FeatureAccessError

        metric = MetricValue(
            feature_id="PCR",
            feature_version=1,
            scope=ScopeRef(Scope.UNDERLYING, "100"),
            value=Decimal(1),
            unit="ratio",
            observed_at=at(3),
            knowledge_horizon=at(3),
            computed_at=at(3),
            available_at=at(3),
            build_context_id=fx.BUILD_CONTEXT,
            inputs_digest="d",
            quality_status=QualityStatus.OK,
        )
        accessor = PointInTimeAccessor(metrics=(metric,))

        with self.assertRaises(FeatureAccessError):
            accessor.get_feature("PCR", 1, at(2))
        with self.assertRaises(FeatureAccessError):
            FeatureView(accessor, at(2)).get("PCR", 1)
        # And both allow it once it is available, so the test is not merely
        # asserting that everything raises.
        self.assertIsNotNone(accessor.get_feature("PCR", 1, at(4)))
        self.assertIsNotNone(FeatureView(accessor, at(4)).get("PCR", 1))

    def test_a_backtest_result_is_reproducible_from_its_recorded_identity(self) -> None:
        """Brief §18: the artifact records what is needed to reproduce it."""
        result = _run()
        for field in ("strategy_digest", "replay_digest", "build_context_id", "fill_model_digest"):
            with self.subTest(field=field):
                self.assertTrue(getattr(result, field), f"{field} is empty")


class TestExecutionMetadataDefault(unittest.TestCase):
    def test_metadata_defaults_are_empty_rather_than_a_wall_clock_reading(self) -> None:
        """Nothing in Phase 7 may read the wall clock, including a default."""
        metadata = BacktestExecutionMetadata()
        self.assertIsNone(metadata.executed_at)
        self.assertIsNone(metadata.duration)


class MockReplaySession:
    def __init__(self, session_id: str, context: Any, status_str: str = "created"):
        from oipulse.replay.engine import ReplayProgress
        from oipulse.replay.timeline import ReplayStep

        self.session_id = session_id
        self.context = context
        self.status = status_str
        self.timeline = fx.timeline(fx.observations())
        self.progress = ReplayProgress()
        self.current_step = ReplayStep(1, at(1), at(1))
        from tests.phase3._fixtures import UNDERLYING

        self._state = fx.service(fx.observations()).get_state(UNDERLYING, at(1), at(1))

    def state_at(self, step: Any, underlying_id: int) -> Any:
        from oipulse.replay.engine import SteppedState

        return SteppedState(
            step=step,
            underlying_id=underlying_id,
            state=self._state,
            from_checkpoint=False,
        )

    def current_state(self, underlying_id: int | None = None) -> Any:
        from oipulse.replay.engine import SteppedState
        from oipulse.replay.timeline import ReplayStep

        step = ReplayStep(1, at(1), at(1))
        return SteppedState(
            step=step,
            underlying_id=100,
            state=self._state,
            from_checkpoint=False,
        )

    def control(self, action: str, body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.status = action
        return {"action": action, "status": self.status}


class MockReplaySessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, MockReplaySession] = {}

    def list(self) -> list[MockReplaySession]:
        return list(self._sessions.values())

    def get(self, session_id: str) -> MockReplaySession | None:
        return self._sessions.get(session_id)

    def create(self, body: dict[str, Any]) -> MockReplaySession:
        if "universe" not in body or "period" not in body:
            raise KeyError("missing required field")
        ctx = fx.context(run_id=body.get("run_id", "test-replay-run"))
        sess = MockReplaySession(ctx.run_id, ctx)
        self._sessions[sess.session_id] = sess
        return sess


class MockBacktestRunItem:
    def __init__(self, run_id: str, result_obj: Any = None):
        self.run_id = run_id
        self.status = "completed" if result_obj else "running"
        self.assumptions = result_obj.assumptions if result_obj else {}
        self.result = result_obj
        self.steps_completed = 10
        self.steps_total = 10
        self.equity_curve: tuple[Any, ...] = ()


class MockBacktestRunManager:
    def __init__(self) -> None:
        self._runs: dict[str, MockBacktestRunItem] = {}

    def list(self) -> list[MockBacktestRunItem]:
        return list(self._runs.values())

    def get(self, run_id: str) -> MockBacktestRunItem | None:
        return self._runs.get(run_id)

    def create(self, body: dict[str, Any]) -> MockBacktestRunItem:
        if "strategy_id" not in body or "fill_model" not in body:
            raise KeyError("missing required field")
        r_obj = _run()
        item = MockBacktestRunItem(body.get("run_id", "bt-run-1"), r_obj)
        self._runs[item.run_id] = item
        return item


class TestReplayAndBacktestFastAPIEndpoints(unittest.TestCase):
    """End-to-end HTTP tests of /replay and /backtest using FastAPI TestClient."""

    def setUp(self) -> None:
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

    def test_replay_unconfigured_manager_returns_503(self) -> None:
        self.app.state.replay_sessions = None
        self.assertEqual(self.client.get("/replay/sessions").status_code, 503)

    def test_backtest_unconfigured_manager_returns_503(self) -> None:
        self.app.state.backtest_runs = None
        self.assertEqual(self.client.get("/backtest/runs").status_code, 503)

    def test_replay_full_flow(self) -> None:
        mgr = MockReplaySessionManager()
        self.app.state.replay_sessions = mgr

        # Create
        create_resp = self.client.post(
            "/replay/sessions",
            json={
                "run_id": "replay-sess-1",
                "universe": [100],
                "period": {
                    "start": at(0).isoformat(),
                    "end": at(10).isoformat(),
                },
            },
        )
        self.assertEqual(create_resp.status_code, 201)

        # List
        list_resp = self.client.get("/replay/sessions")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()["data"]), 1)

        # Get
        get_resp = self.client.get("/replay/sessions/replay-sess-1")
        self.assertEqual(get_resp.status_code, 200)

        # Control - valid
        ctrl_resp = self.client.post(
            "/replay/sessions/replay-sess-1/control",
            json={"action": "step"},
        )
        self.assertEqual(ctrl_resp.status_code, 200)

        # Control - invalid action
        bad_ctrl = self.client.post(
            "/replay/sessions/replay-sess-1/control",
            json={"action": "buy_market_order"},
        )
        self.assertEqual(bad_ctrl.status_code, 422)

        # State
        state_resp = self.client.get(
            "/replay/sessions/replay-sess-1/state", params={"underlying_id": 100}
        )
        self.assertEqual(state_resp.status_code, 200)
        self.assertIn("data", state_resp.json())

    def test_backtest_full_flow(self) -> None:
        mgr = MockBacktestRunManager()
        self.app.state.backtest_runs = mgr

        # Create
        create_resp = self.client.post(
            "/backtest/runs",
            json={
                "run_id": "bt-run-1",
                "strategy_id": "BUY_ONCE",
                "fill_model": {"latency_seconds": 1.0},
            },
        )
        self.assertEqual(create_resp.status_code, 201)

        # List
        list_resp = self.client.get("/backtest/runs")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()["data"]), 1)

        # Get
        get_resp = self.client.get("/backtest/runs/bt-run-1")
        self.assertEqual(get_resp.status_code, 200)

        # Results
        res_resp = self.client.get("/backtest/runs/bt-run-1/results")
        self.assertEqual(res_resp.status_code, 200)
        self.assertIn("meta", res_resp.json())
        self.assertIn("assumptions", res_resp.json()["meta"])

        # Trades
        trades_resp = self.client.get("/backtest/runs/bt-run-1/trades")
        self.assertEqual(trades_resp.status_code, 200)

        # Equity curve
        eq_resp = self.client.get("/backtest/runs/bt-run-1/equity-curve")
        self.assertEqual(eq_resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
