"""Reconciliation, provider-state mapping, recovery, API shape and the guards.

Phase 10 brief §11, §12, §13, §14, §20, §21, §22, §24, §25, §26, §29.
`11-TRADING.md` §6's acceptance criterion is the one that matters most:

> Reconciliation must be able to rebuild local state from broker truth. Wipe local
> order and position state, run reconciliation, and the result must match the broker.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from oipulse.trading.brokers import (
    LiveExecutionDisabled,
    PaperVenueFaults,
    ProviderOrderStatus,
    UpstoxBrokerAdapter,
)
from oipulse.trading.oms import OrderManager
from oipulse.trading.oms.serialisation import (
    oms_order_to_dict,
    oms_orders_to_dict,
    reconciliation_run_to_dict,
    submission_outcome_to_dict,
)
from oipulse.trading.orders import OrderState
from oipulse.trading.reconciliation import (
    DiscrepancyKind,
    Reconciler,
    Resolution,
    canonical_state_for,
    reconciled_state,
    startup_gate,
)
from tests.phase10 import _fixtures as fx
from tests.phase10._fixtures import at

REPO = Path(__file__).resolve().parents[2]


def _lost_ack_setup() -> tuple[OrderManager, object, object]:
    """An order at the venue whose acknowledgement we lost. The canonical case."""
    order = fx.order()
    venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
    manager = OrderManager(adapter=venue)
    fx.run(fx.submit_one(manager, ord_=order))
    manager.escalate_to_reconciliation(order.order_id, at=at(2))
    return manager, venue, order


class TestProviderStateMapping(unittest.TestCase):
    """Brief §11. Every provider status, and the one that maps to nothing."""

    def test_every_provider_status_is_mapped(self) -> None:
        for status in ProviderOrderStatus:
            with self.subTest(status=status):
                canonical_state_for(status)  # must not raise: the table is total

    def test_the_mappings_are_what_the_design_expects(self) -> None:
        self.assertIs(canonical_state_for(ProviderOrderStatus.OPEN), OrderState.OPEN)
        self.assertIs(canonical_state_for(ProviderOrderStatus.FILLED), OrderState.FILLED)
        self.assertIs(canonical_state_for(ProviderOrderStatus.CANCELLED), OrderState.CANCELLED)
        self.assertIs(canonical_state_for(ProviderOrderStatus.REJECTED), OrderState.REJECTED)
        self.assertIs(canonical_state_for(ProviderOrderStatus.EXPIRED), OrderState.EXPIRED)

    def test_an_unknown_provider_status_maps_to_no_state(self) -> None:
        """Brief §11: do not force provider truth in without evidence."""
        self.assertIsNone(canonical_state_for(ProviderOrderStatus.UNKNOWN))

    def test_quantities_narrow_the_status_but_never_invent_one(self) -> None:
        """A venue saying OPEN on a partly-filled order is describing a partial."""
        partial = fx.provider_state(status=ProviderOrderStatus.OPEN, filled=20)
        self.assertIs(reconciled_state(partial), OrderState.PARTIALLY_FILLED)

        complete = fx.provider_state(status=ProviderOrderStatus.OPEN, filled=50)
        self.assertIs(reconciled_state(complete), OrderState.FILLED)

        # But an UNKNOWN status with quantities still maps to nothing.
        unknown = fx.provider_state(status=ProviderOrderStatus.UNKNOWN, filled=20)
        self.assertIsNone(reconciled_state(unknown))

    def test_the_vocabularies_are_separate_types(self) -> None:
        """A shared enum would hide the mapping and its assumption."""
        self.assertNotEqual({s.value for s in ProviderOrderStatus}, {s.value for s in OrderState})


class TestReconciliationOutcomes(unittest.TestCase):
    """Brief §12's classification, each reached by a real scenario."""

    def test_a_matching_order_is_a_match(self) -> None:
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        fx.run(fx.submit_one(manager))
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        kinds = {d.kind for d in run.discrepancies}
        self.assertIn(DiscrepancyKind.PROVIDER_AHEAD, kinds | {DiscrepancyKind.MATCH})
        self.assertTrue(run.outcome.is_clean)

    def test_provider_ahead_is_applied(self) -> None:
        """A lost acknowledgement whose order was in fact accepted."""
        manager, venue, order = _lost_ack_setup()
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        applied = [d for d in run.discrepancies if d.resolution is Resolution.ORDER_STATE_APPLIED]
        self.assertEqual(len(applied), 1)
        self.assertIs(applied[0].kind, DiscrepancyKind.PROVIDER_AHEAD)
        self.assertIs(manager.order(order.order_id).state, OrderState.OPEN)

    def test_reconciliation_recovers_the_provider_identity(self) -> None:
        """Without this the order could never be cancelled."""
        manager, venue, order = _lost_ack_setup()
        self.assertIsNone(manager.order(order.order_id).provider_order_id)
        fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        self.assertIsNotNone(manager.order(order.order_id).provider_order_id)

    def test_missing_at_provider_is_recorded_not_resubmitted(self) -> None:
        """A dropped request: nothing reached the venue.

        The evidence supports concluding the order was never accepted, but
        reconciliation does not act on that — resubmitting is an explicit decision
        for the caller, not a side effect of reconciling.
        """
        order = fx.order()
        venue = fx.venue(faults=fx.drop_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        fx.run(fx.submit_one(manager, ord_=order))
        manager.escalate_to_reconciliation(order.order_id, at=at(2))

        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        missing = [d for d in run.discrepancies if d.kind is DiscrepancyKind.MISSING_AT_PROVIDER]
        self.assertEqual(len(missing), 1)
        self.assertIs(missing[0].resolution, Resolution.RECORDED_ONLY)
        self.assertEqual(venue.venue_order_count(), 0, "nothing was resubmitted")

    def test_missing_locally_is_recorded_never_cancelled(self) -> None:
        """`11` §6's "manual broker-side change". Brief §12 forbids acting on it."""
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        # An order appears at the venue that the OMS knows nothing about.
        fx.run(
            venue.place_order(
                __import__(
                    "oipulse.trading.brokers.protocol", fromlist=["BrokerOrderRequest"]
                ).BrokerOrderRequest(
                    client_order_attempt_id="stranger",
                    order_id="not-ours",
                    instrument_id=fx.TARGET,
                    side=fx.order().side,
                    quantity=10,
                    order_type=fx.order().order_type,
                    requested_at=at(1),
                )
            )
        )
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        stray = [d for d in run.discrepancies if d.kind is DiscrepancyKind.MISSING_LOCALLY]
        self.assertEqual(len(stray), 1)
        self.assertIs(stray[0].resolution, Resolution.RECORDED_ONLY)
        self.assertTrue(stray[0].needs_attention)
        self.assertFalse(run.outcome.is_clean, "a stray broker order is not clean")

    def test_a_venue_side_cancellation_is_picked_up(self) -> None:
        """`11` §6: broker-side cancellation, discovered rather than pushed."""
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None

        venue.faults = PaperVenueFaults(venue_cancelled=frozenset({order.provider_order_id}))
        fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        self.assertIs(manager.order(order.order_id).state, OrderState.CANCELLED)

    def test_an_unknown_provider_status_leaves_the_order_unresolved(self) -> None:
        from dataclasses import replace

        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        venue._book[order.provider_order_id] = replace(
            venue._book[order.provider_order_id],
            status=ProviderOrderStatus.UNKNOWN,
        )
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        unknown = [d for d in run.discrepancies if d.kind is DiscrepancyKind.UNKNOWN]
        self.assertEqual(len(unknown), 1)
        self.assertIs(unknown[0].resolution, Resolution.UNRESOLVED)
        self.assertFalse(run.outcome.is_clean)

    def test_a_hidden_order_reads_as_missing_at_provider(self) -> None:
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        venue.faults = PaperVenueFaults(hide_from_queries=frozenset({order.provider_order_id}))
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        self.assertIn(DiscrepancyKind.MISSING_AT_PROVIDER, {d.kind for d in run.discrepancies})


class TestFillIngestion(unittest.TestCase):
    """Brief §14. Duplicates must not double-apply anything."""

    def _filled(self) -> tuple[OrderManager, object, object, object]:
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        rec = Reconciler(manager=manager, adapter=venue)
        venue.venue_fill(
            order.provider_order_id,
            quantity=20,
            price=Decimal("106.40"),
            at=at(4),
            provider_fill_id="X1",
        )
        return manager, venue, order, rec

    def test_a_provider_fill_is_ingested(self) -> None:
        manager, _, order, rec = self._filled()
        run = fx.run(fx.reconcile_once(rec, at_minute=5))
        self.assertEqual(run.outcome.fills_inserted, 1)
        applied = manager.order(order.order_id)
        self.assertEqual(applied.filled_quantity, 20)
        self.assertIs(applied.state, OrderState.PARTIALLY_FILLED)

    def test_a_duplicate_fill_does_not_double_apply(self) -> None:
        manager, _, order, rec = self._filled()
        fx.run(fx.reconcile_once(rec, run_id="r1", at_minute=5))
        before = manager.order(order.order_id).filled_quantity
        second = fx.run(fx.reconcile_once(rec, run_id="r2", at_minute=5))
        self.assertEqual(second.outcome.fills_inserted, 0)
        self.assertEqual(manager.order(order.order_id).filled_quantity, before)

    def test_fills_are_applied_before_the_status(self) -> None:
        """Otherwise a terminal status makes its own evidence unapplicable.

        Applying FILLED first drives the order terminal, after which `apply_fill`
        correctly refuses — leaving an order claiming FILLED with a filled
        quantity of zero. This asserts the coherent outcome.
        """
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        venue.venue_fill(
            order.provider_order_id,
            quantity=50,
            price=Decimal("106.40"),
            at=at(4),
            provider_fill_id="F1",
        )
        rec = Reconciler(manager=manager, adapter=venue)
        run = fx.run(fx.reconcile_once(rec, at_minute=5))
        applied = manager.order(order.order_id)
        self.assertIs(applied.state, OrderState.FILLED)
        self.assertEqual(applied.filled_quantity, 50)
        self.assertEqual(run.outcome.fills_inserted, 1)
        self.assertTrue(run.outcome.is_clean)

    def test_a_fill_with_no_provider_id_uses_a_weaker_dedup_and_says_so(self) -> None:
        """Brief §14 and §28: never fabricate a provider identifier."""
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        fill = venue.venue_fill(
            order.provider_order_id, quantity=10, price=Decimal("106.40"), at=at(4)
        )
        self.assertIsNone(fill.provider_fill_id)
        self.assertFalse(fill.has_provider_identity)
        self.assertTrue(fill.dedup_key.startswith("digest:"))

        rec = Reconciler(manager=manager, adapter=venue)
        run = fx.run(fx.reconcile_once(rec, at_minute=5))
        inserted = [d for d in run.discrepancies if d.resolution is Resolution.FILL_INSERTED]
        self.assertIn("content digest", inserted[0].detail)

    def test_a_fill_for_an_unknown_order_is_recorded_not_applied(self) -> None:
        """Applying it would create a position with no traceable cause."""
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        from oipulse.trading.brokers.protocol import BrokerOrderRequest

        ack = fx.run(
            venue.place_order(
                BrokerOrderRequest(
                    client_order_attempt_id="stranger",
                    order_id="not-ours",
                    instrument_id=fx.TARGET,
                    side=fx.order().side,
                    quantity=10,
                    order_type=fx.order().order_type,
                    requested_at=at(1),
                )
            )
        )
        assert ack.provider_order_id is not None
        venue.venue_fill(
            ack.provider_order_id,
            quantity=10,
            price=Decimal("100"),
            at=at(2),
            provider_fill_id="S1",
        )
        rec = Reconciler(manager=manager, adapter=venue)
        run = fx.run(fx.reconcile_once(rec))
        self.assertEqual(run.outcome.fills_inserted, 0)
        self.assertTrue(any(d.needs_attention for d in run.discrepancies))


class TestReconciliationIdempotency(unittest.TestCase):
    """Brief §13: twice over unchanged evidence, identical semantic results."""

    def test_two_runs_over_settled_evidence_are_identical(self) -> None:
        manager, venue, _ = _lost_ack_setup()
        rec = Reconciler(manager=manager, adapter=venue)
        fx.run(fx.reconcile_once(rec, run_id="converge"))

        a = fx.run(fx.reconcile_once(rec, run_id="a"))
        b = fx.run(fx.reconcile_once(rec, run_id="b"))
        self.assertEqual(a.content_digest, b.content_digest)
        self.assertEqual(a.outcome.as_dict(), b.outcome.as_dict())

    def test_a_second_run_makes_no_transition(self) -> None:
        manager, venue, order = _lost_ack_setup()
        rec = Reconciler(manager=manager, adapter=venue)
        fx.run(fx.reconcile_once(rec, run_id="first"))
        events_after_first = len(manager.order(order.order_id).events)

        fx.run(fx.reconcile_once(rec, run_id="second"))
        self.assertEqual(
            len(manager.order(order.order_id).events),
            events_after_first,
            "a second run over unchanged evidence must append no event",
        )

    def test_the_run_digest_excludes_execution_metadata(self) -> None:
        from dataclasses import replace

        manager, venue, _ = _lost_ack_setup()
        rec = Reconciler(manager=manager, adapter=venue)
        run = fx.run(fx.reconcile_once(rec))
        later = replace(run, run_id="different", started_at=at(9), completed_at=at(9))
        self.assertEqual(run.content_digest, later.content_digest)


class TestRecoveryAndRestart(unittest.TestCase):
    """Brief §20 and §21, and `11` §6's acceptance criterion."""

    def test_wiping_local_state_and_reconciling_matches_the_broker(self) -> None:
        """`11` §6: "wipe local order and position state, run reconciliation, and
        the result must match the broker"."""
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        venue.venue_fill(
            order.provider_order_id,
            quantity=50,
            price=Decimal("106.40"),
            at=at(4),
            provider_fill_id="F1",
        )
        fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue), at_minute=5))
        original = manager.order(order.order_id)

        # A fresh process: the same order shell, nothing else.
        from dataclasses import replace

        restarted = OrderManager(adapter=venue)
        restarted.track(
            replace(
                order,
                state=OrderState.SUBMITTED,
                filled_quantity=0,
                average_fill_price=None,
                events=(),
            )
        )
        fx.run(
            fx.reconcile_once(
                Reconciler(manager=restarted, adapter=venue), run_id="rebuild", at_minute=5
            )
        )
        rebuilt = restarted.order(order.order_id)
        self.assertIs(rebuilt.state, original.state)
        self.assertEqual(rebuilt.filled_quantity, original.filled_quantity)

    def test_a_restart_mid_submit_converges(self) -> None:
        """Brief §20: the same state as an uninterrupted run, given the same evidence."""
        order = fx.order()
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        crashed = OrderManager(adapter=venue)
        fx.run(fx.submit_one(crashed, ord_=order))
        crashed.escalate_to_reconciliation(order.order_id, at=at(2))
        fx.run(fx.reconcile_once(Reconciler(manager=crashed, adapter=venue)))

        # A fresh manager recovering the same order from the same venue.
        from dataclasses import replace

        restarted = OrderManager(adapter=venue)
        restarted.track(
            replace(
                order,
                state=OrderState.PENDING_RECONCILIATION,
                client_order_attempt_id=crashed.order(order.order_id).client_order_attempt_id,
                events=(),
            )
        )
        fx.run(
            fx.reconcile_once(Reconciler(manager=restarted, adapter=venue), run_id="after-restart")
        )
        self.assertIs(restarted.order(order.order_id).state, crashed.order(order.order_id).state)

    def test_duplicate_provider_observations_are_absorbed(self) -> None:
        """Brief §21: a reconnect replays events; nothing may be re-applied."""
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        order = fx.run(fx.submit_one(manager)).order
        assert order.provider_order_id is not None
        venue.venue_fill(
            order.provider_order_id,
            quantity=25,
            price=Decimal("106.40"),
            at=at(4),
            provider_fill_id="D1",
        )
        rec = Reconciler(manager=manager, adapter=venue)
        for run_id in ("a", "b", "c"):
            fx.run(fx.reconcile_once(rec, run_id=run_id, at_minute=5))
        self.assertEqual(manager.order(order.order_id).filled_quantity, 25)
        self.assertEqual(rec.applied_fill_count, 1)


class TestStartupGate(unittest.TestCase):
    """`18` Phase 10: trader is not ready until reconciliation is clean."""

    def test_no_run_is_not_ready(self) -> None:
        ready, reason = startup_gate(None)
        self.assertFalse(ready)
        self.assertIn("no reconciliation has run", reason)

    def test_a_dirty_run_is_not_ready(self) -> None:
        order = fx.order()
        venue = fx.venue(faults=fx.drop_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        fx.run(fx.submit_one(manager, ord_=order))
        manager.escalate_to_reconciliation(order.order_id, at=at(2))
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        self.assertFalse(startup_gate(run)[0])

    def test_a_clean_run_is_ready(self) -> None:
        venue = fx.venue()
        manager = OrderManager(adapter=venue)
        fx.run(fx.submit_one(manager))
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        self.assertTrue(startup_gate(run)[0], run.outcome.as_dict())


class TestSerialisation(unittest.TestCase):
    """Pure envelopes, testable with no web stack installed."""

    def test_every_envelope_states_the_execution_posture(self) -> None:
        manager = fx.oms()
        outcome = fx.run(fx.submit_one(manager))
        venue = manager.adapter
        run = fx.run(fx.reconcile_once(Reconciler(manager=manager, adapter=venue)))
        envelopes = {
            "order": oms_order_to_dict(outcome.order),
            "orders": oms_orders_to_dict(manager.orders()),
            "submission": submission_outcome_to_dict(outcome),
            "run": reconciliation_run_to_dict(run),
        }
        for name, envelope in envelopes.items():
            with self.subTest(envelope=name):
                self.assertFalse(envelope["meta"]["live_execution_enabled"])
                self.assertEqual(envelope["meta"]["execution_mode"], "PAPER")

    def test_absent_provider_identity_is_stated_not_implied(self) -> None:
        manager, _, order = _lost_ack_setup()
        body = oms_order_to_dict(manager.order(order.order_id))
        self.assertIsNone(body["data"]["provider_order_id"])
        self.assertFalse(body["meta"]["provider_identity_known"])

    def test_an_ambiguous_submission_says_resubmission_is_not_permitted(self) -> None:
        order = fx.order()
        venue = fx.venue(faults=fx.lose_ack_for_first_attempt(order.order_id))
        manager = OrderManager(adapter=venue)
        outcome = fx.run(fx.submit_one(manager, ord_=order))
        body = submission_outcome_to_dict(outcome)
        self.assertTrue(body["meta"]["is_ambiguous"])
        self.assertFalse(body["meta"]["resubmission_permitted"])


class TestApiSafety(unittest.TestCase):
    """Brief §24 and §25, parsed from source: fastapi is absent in this sandbox.

    These are **static** checks. They are not equivalent to exercising the router
    over HTTP, and the report says so — real HTTP tests for this surface are
    environment-blocked here and run in CI.
    """

    SOURCE = (REPO / "oipulse/api/reconciliation.py").read_text(encoding="utf-8")

    def _routes(self) -> list[str]:
        tree = ast.parse(self.SOURCE)
        return [
            str(node.args[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "router"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]

    def test_the_spec_required_surfaces_exist(self) -> None:
        routes = " ".join(self._routes())
        for surface in ("/runs", "/trigger", "/orders", "/cancel", "/provider-state"):
            with self.subTest(surface=surface):
                self.assertIn(surface, routes)

    def test_no_route_submits(self) -> None:
        for route in self._routes():
            for banned in ("submit", "place", "live", "broker"):
                with self.subTest(route=route, banned=banned):
                    self.assertNotIn(banned, route.lower())

    def test_the_trigger_endpoint_refuses_supplied_provider_state(self) -> None:
        """Brief §25: provider truth enters only through the adapter."""
        self.assertIn("provider_orders", self.SOURCE)
        self.assertIn("may not be supplied", self.SOURCE)
        self.assertIn("would let a caller forge", self.SOURCE)

    def test_no_endpoint_accepts_a_target_state(self) -> None:
        """Arbitrary state transitions must be impossible from the API."""
        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                names = {a.arg for a in [*node.args.args, *node.args.kwonlyargs]}
                for banned in ("state", "target_state", "status"):
                    with self.subTest(function=node.name):
                        self.assertNotIn(banned, names)


class TestArchitectureGuards(unittest.TestCase):
    def _guard(self, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / name)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_live_execution_barrier_guard_passes(self) -> None:
        result = self._guard("check_live_execution_barrier.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_risk_authorization_guard_still_passes(self) -> None:
        result = self._guard("check_risk_authorization.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_wall_clock_access(self) -> None:
        result = self._guard("check_clock_access.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_adapter_grants_live_submit(self) -> None:
        from oipulse.trading.brokers import ExecutionCapability

        for adapter in (UpstoxBrokerAdapter(), fx.venue()):
            with self.subTest(adapter=adapter.name):
                self.assertNotIn(ExecutionCapability.LIVE_SUBMIT, adapter.capabilities)

    def test_the_upstox_adapter_refuses_every_venue_method(self) -> None:
        adapter = UpstoxBrokerAdapter()
        calls = [
            adapter.place_order(None),  # type: ignore[arg-type]
            adapter.cancel_order("x"),
            adapter.modify_order("x", {}),
            adapter.get_order("x"),
            adapter.list_orders(since=at(0)),
            adapter.list_trades(since=at(0)),
            adapter.get_positions(),
        ]
        for coro in calls:
            with self.subTest(coro=coro), self.assertRaises(LiveExecutionDisabled):
                fx.run(coro)

    def test_no_phase_11_package_exists(self) -> None:
        for package in ("portfolio", "attribution", "terminal"):
            with self.subTest(package=package):
                self.assertFalse((REPO / "oipulse" / package).exists())

    def test_trading_imports_no_network_module(self) -> None:
        banned = ("httpx", "requests", "aiohttp", "urllib", "socket", "websockets")
        for path in sorted((REPO / "oipulse/trading").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    with self.subTest(file=path.name, module=module):
                        self.assertNotIn(module.split(".")[0], banned)


class TestObservability(unittest.TestCase):
    def test_no_metric_implies_successful_live_trading(self) -> None:
        """Brief §26."""
        import oipulse.observability.metrics as metrics

        names = [
            value
            for name, value in vars(metrics).items()
            if name.startswith(("OMS_", "RECONCILIATION_", "LIVE_")) and isinstance(value, str)
        ]
        self.assertTrue(names)
        for name in names:
            for banned in ("live_orders", "orders_placed", "live_fills", "live_trades"):
                with self.subTest(metric=name):
                    self.assertNotIn(banned, name)

    def test_submissions_are_labelled_by_venue_and_outcome(self) -> None:
        """An acknowledgement from the paper venue must be legible as such."""
        import oipulse.observability.metrics as metrics

        metrics.record_oms_submission(venue="PAPER", outcome="ACKNOWLEDGED")
        metrics.record_oms_submission(
            venue="PAPER", outcome="NOT_AUTHORIZED", refusal="DECISION_EXPIRED"
        )
        self.assertEqual(
            metrics.METRICS.counter(
                metrics.OMS_SUBMIT_ATTEMPTS, {"venue": "PAPER", "outcome": "ACKNOWLEDGED"}
            ),
            1.0,
        )
        self.assertEqual(
            metrics.METRICS.counter(
                metrics.OMS_SUBMISSIONS_BLOCKED,
                {"venue": "PAPER", "reason": "DECISION_EXPIRED"},
            ),
            1.0,
        )

    def test_reconciliation_discrepancies_are_labelled_by_kind_and_resolution(
        self,
    ) -> None:
        """A resolved PROVIDER_AHEAD and an unresolvable one are not one number."""
        import oipulse.observability.metrics as metrics

        metrics.record_reconciliation_run(
            trigger="MANUAL",
            duration_seconds=0.01,
            is_clean=False,
            discrepancies={
                ("PROVIDER_AHEAD", "ORDER_STATE_APPLIED"): 1,
                ("MISSING_LOCALLY", "RECORDED_ONLY"): 2,
            },
            fills_inserted=0,
            needs_attention=2,
        )
        self.assertEqual(
            metrics.METRICS.counter(
                metrics.RECONCILIATION_DISCREPANCIES,
                {
                    "trigger": "MANUAL",
                    "kind": "MISSING_LOCALLY",
                    "resolution": "RECORDED_ONLY",
                },
            ),
            2.0,
        )


class TestLiveExecutionBarrierGuardMutation(unittest.TestCase):
    """Mutation tests for tools/check_live_execution_barrier.py."""

    def test_non_literal_flag_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_flag_is_a_literal_false

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/brokers"
            target.mkdir(parents=True)
            # Mutate to os.getenv(...)
            (target / "capability.py").write_text(
                "import os\nLIVE_EXECUTION_ENABLED = bool(os.getenv('LIVE', '0'))\n",
                encoding="utf-8",
            )
            findings = _check_flag_is_a_literal_false(tmp)
            self.assertTrue(
                any("not the literal False" in f for f in findings),
                f"Expected literal False violation, got {findings}",
            )

    def test_true_flag_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_flag_is_a_literal_false

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/brokers"
            target.mkdir(parents=True)
            (target / "capability.py").write_text(
                "LIVE_EXECUTION_ENABLED = True\n",
                encoding="utf-8",
            )
            findings = _check_flag_is_a_literal_false(tmp)
            self.assertTrue(
                any("not the literal False" in f for f in findings),
                f"Expected literal False violation, got {findings}",
            )

    def test_network_import_in_trading_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_no_network_or_credentials

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading"
            target.mkdir(parents=True)
            (target / "bad_client.py").write_text(
                "import httpx\n",
                encoding="utf-8",
            )
            findings = _check_no_network_or_credentials(tmp)
            self.assertTrue(
                any("imports httpx" in f for f in findings),
                f"Expected httpx network violation, got {findings}",
            )

    def test_credential_import_in_trading_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_no_network_or_credentials

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading"
            target.mkdir(parents=True)
            (target / "bad_secret.py").write_text(
                "from oipulse.core.secrets import get_secret\n",
                encoding="utf-8",
            )
            findings = _check_no_network_or_credentials(tmp)
            self.assertTrue(
                any("imports oipulse.core.secrets" in f for f in findings),
                f"Expected credential violation, got {findings}",
            )

    def test_upstox_missing_capability_gate_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_upstox_cannot_submit

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/brokers"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/brokers/upstox.py").read_text(encoding="utf-8")
            # Remove require_capability call from place_order
            mutated = original.replace(
                "require_capability(self.capabilities, ExecutionCapability.LIVE_SUBMIT, who=self.name)",
                "pass",
            )
            (target / "upstox.py").write_text(mutated, encoding="utf-8")
            findings = _check_upstox_cannot_submit(tmp)
            self.assertTrue(
                any("does not call require_capability" in f for f in findings),
                f"Expected missing capability gate violation, got {findings}",
            )

    def test_upstox_with_await_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_upstox_cannot_submit

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/brokers"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/brokers/upstox.py").read_text(encoding="utf-8")
            # Add await to place_order
            mutated = original.replace(
                "require_capability(self.capabilities, ExecutionCapability.LIVE_SUBMIT, who=self.name)",
                "require_capability(self.capabilities, ExecutionCapability.LIVE_SUBMIT, who=self.name)\n        await something()",
            )
            (target / "upstox.py").write_text(mutated, encoding="utf-8")
            findings = _check_upstox_cannot_submit(tmp)
            self.assertTrue(
                any("awaits something" in f for f in findings),
                f"Expected await violation, got {findings}",
            )

    def test_submit_route_in_api_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_no_api_submit_route

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/api"
            target.mkdir(parents=True)
            (target / "bad_route.py").write_text(
                "from fastapi import APIRouter\nrouter = APIRouter()\n"
                "@router.post('/submit')\ndef submit_order(): pass\n",
                encoding="utf-8",
            )
            findings = _check_no_api_submit_route(tmp)
            self.assertTrue(
                any("names a submission surface" in f for f in findings),
                f"Expected submission surface violation, got {findings}",
            )

    def test_forged_provider_param_in_api_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_no_api_submit_route

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/api"
            target.mkdir(parents=True)
            (target / "bad_route.py").write_text(
                "from fastapi import APIRouter\nrouter = APIRouter()\n"
                "@router.post('/trigger')\ndef trigger(provider_orders: list): pass\n",
                encoding="utf-8",
            )
            findings = _check_no_api_submit_route(tmp)
            self.assertTrue(
                any("takes 'provider_orders'" in f for f in findings),
                f"Expected provider param violation, got {findings}",
            )

    def test_submission_before_authorization_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_authorization_precedes_submission

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/oms"
            target.mkdir(parents=True)
            (target / "manager.py").write_text(
                "class OrderManager:\n"
                "    async def submit(self):\n"
                "        await self.adapter.place_order(None)\n"
                "        self.authorize_submission()\n",
                encoding="utf-8",
            )
            findings = _check_authorization_precedes_submission(tmp)
            self.assertTrue(
                any("place_order is called before the authorization" in f for f in findings),
                f"Expected inverted authorization violation, got {findings}",
            )

    def test_unknown_transition_to_open_fails_guard(self) -> None:
        from tools.check_live_execution_barrier import _check_unknown_has_no_resubmit_edge

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/orders.py").read_text(encoding="utf-8")
            mutated = original.replace(
                "OrderState.UNKNOWN: frozenset({OrderState.PENDING_RECONCILIATION})",
                "OrderState.UNKNOWN: frozenset({OrderState.PENDING_RECONCILIATION, OrderState.OPEN})",
            )
            (target / "orders.py").write_text(mutated, encoding="utf-8")
            findings = _check_unknown_has_no_resubmit_edge(tmp)
            self.assertTrue(
                any("UNKNOWN permits" in f for f in findings),
                f"Expected UNKNOWN transition violation, got {findings}",
            )


if __name__ == "__main__":
    unittest.main()
