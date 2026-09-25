"""Attribution, the residual, roll-up, position reconciliation, API and guards.

Phase 11 brief §12, §13, §14, §15, §17, §18, §19, §24, §26, §27, §29.
`18-ROADMAP.md` Phase 11's acceptance criterion:

> Attribution decomposes P&L with the residual **reported**, and slices by regime.

and its named risk:

> Attribution residual large enough to be meaningless -- mitigated by reporting it
> prominently; a large residual is information, not something to hide.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from oipulse.trading.portfolio import (
    ATTRIBUTION_METHOD,
    ATTRIBUTION_METHOD_VERSION,
    UNATTRIBUTED,
    AttributionBucket,
    AttributionComponent,
    AttributionSlice,
    GreekInputs,
    PortfolioGreeks,
    PositionDiscrepancyKind,
    PositionResolution,
    ReturnInputs,
    attribute_position,
    drawdown_from,
    group_by,
    margin_utilisation_from,
    roll_up,
)
from oipulse.trading.portfolio.serialisation import (
    attribution_to_dict,
    pnl_to_dict,
    position_reconciliation_to_dict,
    slices_to_dict,
    snapshot_to_dict,
    valuation_to_dict,
)
from tests.phase11 import _fixtures as fx
from tests.phase11._fixtures import at

REPO = Path(__file__).resolve().parents[2]


def _slice(total: str, *, bucket_id: str = "S1", **greeks: object) -> AttributionSlice:
    result = attribute_position(
        total_pnl=Decimal(total),
        greeks=fx.greek_inputs(**greeks),
        bucket=AttributionBucket.STRATEGY,
        bucket_id=bucket_id,
    )
    return AttributionSlice(bucket=AttributionBucket.STRATEGY, bucket_id=bucket_id, result=result)


class TestDecomposition(unittest.TestCase):
    """Brief §12: only specification-defined categories."""

    def test_exactly_the_seven_components_11_section_8_defines(self) -> None:
        self.assertEqual(
            {c.value for c in AttributionComponent},
            {
                "DIRECTION",
                "VOLATILITY",
                "TIME_DECAY",
                "CONVEXITY",
                "EXECUTION",
                "SLIPPAGE",
                "COSTS",
            },
        )

    def test_each_greek_term_uses_the_documented_formula(self) -> None:
        result = attribute_position(
            total_pnl=Decimal(0), greeks=fx.greek_inputs(), costs=Decimal(0)
        )
        # delta * dS = 50 * 10
        self.assertEqual(result.component(AttributionComponent.DIRECTION), Decimal(500))
        # 0.5 * gamma * dS^2 = 0.5 * 2 * 100
        self.assertEqual(result.component(AttributionComponent.CONVEXITY), Decimal(100))
        # vega * dSigma = 100 * 0.01
        self.assertEqual(result.component(AttributionComponent.VOLATILITY), Decimal(1))
        # theta * dt = -20 * 1
        self.assertEqual(result.component(AttributionComponent.TIME_DECAY), Decimal(-20))

    def test_costs_reduce_pnl(self) -> None:
        result = attribute_position(total_pnl=Decimal(0), greeks=GreekInputs(), costs=Decimal(12))
        self.assertEqual(result.component(AttributionComponent.COSTS), Decimal(-12))

    def test_the_method_is_versioned(self) -> None:
        """A formula change must produce a distinguishable identity."""
        result = attribute_position(total_pnl=Decimal(0), greeks=GreekInputs())
        self.assertEqual(result.method, ATTRIBUTION_METHOD)
        self.assertEqual(result.method_version, ATTRIBUTION_METHOD_VERSION)
        self.assertIn("method_version", result.as_dict())


class TestResidual(unittest.TestCase):
    """Brief §13. The single most important property in the phase."""

    def test_the_decomposition_reconciles_with_the_residual(self) -> None:
        result = attribute_position(total_pnl=Decimal(1000), greeks=fx.greek_inputs())
        self.assertEqual(result.total_pnl, result.explained + result.residual)
        self.assertTrue(result.reconciles)

    def test_a_non_zero_residual_is_reported_not_absorbed(self) -> None:
        """Deliberately constructed so the components cannot explain the total."""
        result = attribute_position(total_pnl=Decimal(1000), greeks=fx.greek_inputs())
        self.assertEqual(result.explained, Decimal(581))
        self.assertEqual(result.residual, Decimal(419))
        self.assertGreater(result.residual_fraction, Decimal("0.4"))

    def test_no_component_absorbs_the_residual(self) -> None:
        """Every component equals its own formula, unchanged by the shortfall."""
        small = attribute_position(total_pnl=Decimal(581), greeks=fx.greek_inputs())
        large = attribute_position(total_pnl=Decimal(100000), greeks=fx.greek_inputs())
        for component in AttributionComponent:
            with self.subTest(component=component):
                self.assertEqual(
                    small.component(component),
                    large.component(component),
                    "a component changed with the total; it is absorbing the residual",
                )
        self.assertEqual(small.residual, Decimal(0))
        self.assertEqual(large.residual, Decimal(100000) - Decimal(581))

    def test_the_residual_is_a_derived_property_not_a_field(self) -> None:
        """Structural: a settable residual could be balanced to zero."""
        from oipulse.trading.portfolio.attribution import AttributionResult

        result = attribute_position(total_pnl=Decimal(1000), greeks=fx.greek_inputs())
        self.assertNotIn("residual", getattr(AttributionResult, "__slots__", ()))
        with self.assertRaises((AttributeError, TypeError)):
            result.residual = Decimal(0)  # type: ignore[misc]

    def test_a_missing_input_makes_a_component_uncomputed_and_names_it(self) -> None:
        """The residual's cause is stated rather than being unexplained twice."""
        result = attribute_position(total_pnl=Decimal(1000), greeks=GreekInputs(delta=Decimal(50)))
        self.assertIn(AttributionComponent.DIRECTION, result.uncomputed_components)
        detail = next(
            c.detail for c in result.components if c.component is AttributionComponent.DIRECTION
        )
        self.assertIn("appears in the residual", detail)

    def test_the_residual_fraction_is_none_on_a_zero_total(self) -> None:
        """A return on nothing is undefined, not infinite."""
        self.assertIsNone(
            attribute_position(total_pnl=Decimal(0), greeks=GreekInputs()).residual_fraction
        )

    def test_the_residual_appears_in_the_serialised_form(self) -> None:
        body = attribute_position(total_pnl=Decimal(1000), greeks=fx.greek_inputs()).as_dict()
        self.assertIn("residual", body)
        self.assertIn("residual_fraction", body)

    def test_the_envelope_promotes_the_residual_to_meta(self) -> None:
        """`18` Phase 11: report it prominently. Nested three levels down is not."""
        body = attribution_to_dict(
            attribute_position(total_pnl=Decimal(1000), greeks=fx.greek_inputs()),
            market_time=at(1),
            knowledge_time=at(1),
        )
        self.assertIn("residual", body["meta"])
        self.assertIn("residual_fraction", body["meta"])


class TestRollUp(unittest.TestCase):
    """Brief §14: children must roll up deterministically into parents."""

    def test_components_sum_across_children(self) -> None:
        parent = roll_up(
            [_slice("500", bucket_id="A"), _slice("300", bucket_id="B")],
            bucket=AttributionBucket.PORTFOLIO,
            bucket_id="pf-1",
        )
        self.assertEqual(parent.result.total_pnl, Decimal(800))
        self.assertEqual(parent.result.component(AttributionComponent.DIRECTION), Decimal(1000))

    def test_the_parent_residual_is_the_sum_of_the_children(self) -> None:
        """Not recomputed against an independent total, which could differ and
        would silently absorb the discrepancy."""
        children = [_slice("500", bucket_id="A"), _slice("300", bucket_id="B")]
        parent = roll_up(children, bucket=AttributionBucket.PORTFOLIO, bucket_id="pf-1")
        self.assertEqual(parent.result.residual, sum(c.result.residual for c in children))

    def test_the_roll_up_is_order_independent(self) -> None:
        children = [_slice("500", bucket_id="A"), _slice("300", bucket_id="B")]
        forward = roll_up(children, bucket=AttributionBucket.PORTFOLIO, bucket_id="p")
        backward = roll_up(
            list(reversed(children)), bucket=AttributionBucket.PORTFOLIO, bucket_id="p"
        )
        self.assertEqual(forward.result.content_digest, backward.result.content_digest)

    def test_an_uncomputed_child_component_makes_the_parent_partial(self) -> None:
        """Otherwise a gap vanishes at every level above where it happened."""
        partial = _slice("500", bucket_id="A", vega=None)
        parent = roll_up(
            [partial, _slice("300", bucket_id="B")],
            bucket=AttributionBucket.PORTFOLIO,
            bucket_id="p",
        )
        self.assertIn(AttributionComponent.VOLATILITY, parent.result.uncomputed_components)

    def test_grouping_collects_by_bucket_id(self) -> None:
        grouped = group_by(
            [
                _slice("100", bucket_id="A"),
                _slice("200", bucket_id="A"),
                _slice("50", bucket_id="B"),
            ],
            bucket=AttributionBucket.STRATEGY,
        )
        self.assertEqual(set(grouped), {"A", "B"})
        self.assertEqual(grouped["A"].result.total_pnl, Decimal(300))


class TestUnattributed(unittest.TestCase):
    """Brief §15: a trade with no strategy must not become 'Strategy A'."""

    def test_an_unnamed_slice_is_labelled_unattributed(self) -> None:
        result = attribute_position(total_pnl=Decimal(100), greeks=GreekInputs())
        self.assertEqual(result.bucket_id, UNATTRIBUTED)

    def test_grouping_collects_unnamed_slices_under_unattributed(self) -> None:
        unnamed = AttributionSlice(
            bucket=AttributionBucket.STRATEGY,
            bucket_id="",
            result=attribute_position(total_pnl=Decimal(75), greeks=GreekInputs()),
        )
        grouped = group_by(
            [unnamed, _slice("100", bucket_id="A")], bucket=AttributionBucket.STRATEGY
        )
        self.assertIn(UNATTRIBUTED, grouped)
        self.assertEqual(grouped[UNATTRIBUTED].result.total_pnl, Decimal(75))

    def test_unattributed_slices_are_counted_in_the_envelope(self) -> None:
        body = slices_to_dict(
            [
                AttributionSlice(
                    bucket=AttributionBucket.STRATEGY,
                    bucket_id=UNATTRIBUTED,
                    result=attribute_position(total_pnl=Decimal(1), greeks=GreekInputs()),
                )
            ],
            market_time=at(1),
            knowledge_time=at(1),
        )
        self.assertEqual(body["meta"]["unattributed_slices"], 1)


class TestCostAttribution(unittest.TestCase):
    """Brief §17: gross -> costs -> net must reconcile, with no double count."""

    def test_gross_fees_and_net_reconcile(self) -> None:
        returns = ReturnInputs(
            starting_capital=Decimal(100000),
            ending_equity=Decimal(101000),
            realized_pnl=Decimal(800),
            unrealized_pnl=Decimal(300),
            fees=Decimal(100),
        )
        self.assertEqual(returns.gross_pnl, Decimal(1100))
        self.assertEqual(returns.net_pnl, Decimal(1000))
        self.assertEqual(returns.net_pnl, returns.gross_pnl - returns.fees)

    def test_fees_are_not_double_counted_from_the_fill_price(self) -> None:
        """The Phase 7 fill keeps costs separate from price, so they never were
        embedded — there is nothing to double-count."""
        one = fx.fill(price="100", fees="25")
        self.assertEqual(one.price, Decimal(100))
        self.assertEqual(one.costs.total, Decimal(25))
        book = fx.book([one])
        self.assertEqual(book.positions()[0].average_price, Decimal(100))
        self.assertEqual(book.positions()[0].fees, Decimal(25))


class TestReturnMethodology(unittest.TestCase):
    """Brief §11: do not invent TWR or MWR where none is specified."""

    def test_the_return_is_labelled_simple_period(self) -> None:
        body = ReturnInputs(starting_capital=Decimal(1000), ending_equity=Decimal(1100)).as_dict()
        self.assertEqual(body["return_methodology"], "SIMPLE_PERIOD")
        self.assertEqual(body["simple_period_return"], "0.1")

    def test_cash_flows_are_excluded_from_the_return(self) -> None:
        returns = ReturnInputs(
            starting_capital=Decimal(1000),
            ending_equity=Decimal(2100),
            net_cash_flows=Decimal(1000),
        )
        self.assertEqual(returns.simple_period_return, Decimal("0.1"))

    def test_a_zero_starting_capital_gives_no_return(self) -> None:
        self.assertIsNone(
            ReturnInputs(
                starting_capital=Decimal(0), ending_equity=Decimal(100)
            ).simple_period_return
        )

    def test_no_twr_or_mwr_is_claimed_anywhere(self) -> None:
        import inspect

        from oipulse.trading.portfolio import snapshot as snapshot_module

        source = inspect.getsource(snapshot_module).lower()
        for claim in ("time_weighted", "money_weighted", "twr =", "irr"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, source)


class TestMarginAndDrawdown(unittest.TestCase):
    """Brief §22: use Phase 9 outputs; do not build a risk engine."""

    def test_margin_is_none_without_a_policy_limit(self) -> None:
        """Never zero, which would read as no margin used."""
        value, basis = margin_utilisation_from(Decimal(1000), None)
        self.assertIsNone(value)
        self.assertEqual(basis, "NOT_AVAILABLE")

    def test_margin_states_its_basis(self) -> None:
        value, basis = margin_utilisation_from(Decimal(500), Decimal(1000))
        self.assertEqual(value, Decimal("0.5"))
        self.assertEqual(basis, "RISK_POLICY_MAX_DEPLOYED_CAPITAL")

    def test_no_margin_model_is_implemented(self) -> None:
        """Brief §22: no new risk engine.

        Checks for a *definition* named after a margin model, not for the words
        anywhere in the file: the module docstring says margin is "not a SPAN
        calculation invented here", and a substring search would flag that
        disclaimer as a breach. A statement that something is absent is not an
        instance of it.
        """
        tree = ast.parse(
            (REPO / "oipulse/trading/portfolio/snapshot.py").read_text(encoding="utf-8")
        )
        defined = {
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        }
        for invented in ("span", "initial_margin", "maintenance_margin", "margin_model"):
            with self.subTest(term=invented):
                self.assertFalse(
                    any(invented in name for name in defined),
                    f"a definition named for {invented} exists; margin comes from "
                    f"Phase 9 outputs, not from a model implemented here",
                )

    def test_drawdown_is_a_positive_magnitude(self) -> None:
        self.assertEqual(drawdown_from(Decimal(1000), Decimal(900)), Decimal(100))
        self.assertEqual(drawdown_from(Decimal(1000), Decimal(1100)), Decimal(0))
        self.assertEqual(drawdown_from(None, Decimal(900)), Decimal(0))


class TestSnapshot(unittest.TestCase):
    """Brief §8 and §24."""

    def test_the_snapshot_carries_both_times(self) -> None:
        body = snapshot_to_dict(fx.snapshot())
        self.assertIn("market_time", body["meta"])
        self.assertIn("knowledge_time", body["meta"])

    def test_the_digest_excludes_execution_metadata(self) -> None:
        import dataclasses

        base = fx.snapshot()
        later = dataclasses.replace(base, computed_at=at(9), snapshot_id="other")
        self.assertEqual(base.content_digest, later.content_digest)

    def test_the_digest_changes_with_the_valuation(self) -> None:
        self.assertNotEqual(
            fx.snapshot(valuation_minute=1).content_digest,
            fx.snapshot(valuation_minute=3).content_digest,
        )

    def test_equity_is_cash_plus_market_value(self) -> None:
        snapshot = fx.snapshot(cash="500000")
        self.assertEqual(snapshot.equity, Decimal(500000) + snapshot.valuation.total_market_value)

    def test_concentration_is_a_fraction_of_gross(self) -> None:
        snapshot = fx.snapshot()
        total = sum(value for _, value in snapshot.concentration)
        self.assertEqual(total, Decimal(1))

    def test_partial_greeks_are_not_reported_as_complete(self) -> None:
        greeks = PortfolioGreeks(delta=Decimal(10), positions_included=1, positions_total=3)
        self.assertFalse(greeks.is_complete)

    def test_no_unsupported_metric_is_present(self) -> None:
        """Brief §8: do not introduce unsupported portfolio metrics."""
        body = fx.snapshot().as_dict()
        for invented in ("sharpe", "beta", "var", "sortino", "alpha"):
            with self.subTest(metric=invented):
                self.assertNotIn(invented, body)


class TestPositionReconciliation(unittest.TestCase):
    """Brief §4, §18, §19 — the Phase 10 deferral, discharged."""

    def _run(self, *, local_qty: int | None, provider_qty: int | None, **kwargs: object):
        reconciler = fx.reconciler()
        local = (
            fx.book([fx.fill(quantity=local_qty, price="100")]).positions(
                economics={fx.TARGET: fx.economics()}
            )
            if local_qty is not None
            else []
        )
        provider = [fx.broker_position(quantity=provider_qty)] if provider_qty is not None else []
        return reconciler, reconciler.reconcile(
            local,
            provider,
            account_id=fx.ACCOUNT_ID,
            portfolio_id=fx.PORTFOLIO_ID,
            at=at(3),
            run_id="rec-1",
            **kwargs,  # type: ignore[arg-type]
        )

    def test_agreement_is_a_match(self) -> None:
        _, (run, corrections) = self._run(local_qty=50, provider_qty=50)
        self.assertEqual(run.matched, 1)
        self.assertEqual(corrections, {})
        self.assertTrue(run.is_clean)

    def test_a_quantity_mismatch_is_corrected(self) -> None:
        """Unambiguous: same direction, different size. Broker authoritative."""
        _, (run, corrections) = self._run(local_qty=50, provider_qty=40)
        discrepancy = run.discrepancies[0]
        self.assertIs(discrepancy.kind, PositionDiscrepancyKind.QUANTITY_MISMATCH)
        self.assertIs(discrepancy.resolution, PositionResolution.POSITION_CORRECTED)
        self.assertEqual(list(corrections.values()), [40])

    def test_a_side_mismatch_is_recorded_not_corrected(self) -> None:
        """Long vs short is not a rounding difference."""
        _, (run, corrections) = self._run(local_qty=50, provider_qty=-50)
        discrepancy = run.discrepancies[0]
        self.assertIs(discrepancy.kind, PositionDiscrepancyKind.SIDE_MISMATCH)
        self.assertIs(discrepancy.resolution, PositionResolution.RECORDED_ONLY)
        self.assertEqual(corrections, {})
        self.assertFalse(run.is_clean)

    def test_a_position_missing_at_the_provider_is_recorded(self) -> None:
        """Zeroing it would make an untracked real position invisible."""
        _, (run, corrections) = self._run(local_qty=50, provider_qty=None)
        self.assertIs(run.discrepancies[0].kind, PositionDiscrepancyKind.MISSING_AT_PROVIDER)
        self.assertEqual(corrections, {})

    def test_a_position_missing_locally_is_never_adopted(self) -> None:
        """Adopting it would invent a cost basis nobody paid."""
        _, (run, corrections) = self._run(local_qty=None, provider_qty=25)
        discrepancy = run.discrepancies[0]
        self.assertIs(discrepancy.kind, PositionDiscrepancyKind.MISSING_LOCALLY)
        self.assertIs(discrepancy.resolution, PositionResolution.RECORDED_ONLY)
        self.assertEqual(corrections, {})
        self.assertIn("cost basis", discrepancy.detail)
        # The broker's average price is recorded but explicitly not adopted.
        self.assertIsNotNone(discrepancy.provider_average_price)

    def test_report_only_mode_corrects_nothing(self) -> None:
        _, (run, corrections) = self._run(local_qty=50, provider_qty=40, apply_corrections=False)
        self.assertEqual(corrections, {})
        self.assertIs(run.discrepancies[0].resolution, PositionResolution.RECORDED_ONLY)

    def test_running_twice_applies_the_correction_once(self) -> None:
        """Brief §19: no duplicate corrections."""
        reconciler = fx.reconciler()
        local = fx.book([fx.fill(quantity=50, price="100")]).positions(
            economics={fx.TARGET: fx.economics()}
        )
        provider = [fx.broker_position(quantity=40)]
        kwargs = {
            "account_id": fx.ACCOUNT_ID,
            "portfolio_id": fx.PORTFOLIO_ID,
            "at": at(3),
        }
        _, first = reconciler.reconcile(local, provider, run_id="a", **kwargs)  # type: ignore[arg-type]
        _, second = reconciler.reconcile(local, provider, run_id="b", **kwargs)  # type: ignore[arg-type]
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0, "the same correction must not reapply")
        self.assertEqual(reconciler.corrections_applied, 1)

    def test_the_run_digest_is_stable_over_unchanged_evidence(self) -> None:
        reconciler = fx.reconciler()
        local = fx.book().positions(economics={fx.TARGET: fx.economics()})
        provider = [fx.broker_position(quantity=50)]
        kwargs = {
            "account_id": fx.ACCOUNT_ID,
            "portfolio_id": fx.PORTFOLIO_ID,
            "at": at(3),
        }
        a, _ = reconciler.reconcile(local, provider, run_id="a", **kwargs)  # type: ignore[arg-type]
        b, _ = reconciler.reconcile(local, provider, run_id="b", **kwargs)  # type: ignore[arg-type]
        self.assertEqual(a.content_digest, b.content_digest)

    def test_the_run_is_order_independent(self) -> None:
        local = fx.book(
            [fx.fill(instrument_id=fx.TARGET, tag="a"), fx.fill(instrument_id=fx.OTHER, tag="b")]
        ).positions(
            economics={fx.TARGET: fx.economics(), fx.OTHER: fx.economics(instrument_id=fx.OTHER)}
        )
        provider = [
            fx.broker_position(instrument_id=fx.TARGET, quantity=50),
            fx.broker_position(instrument_id=fx.OTHER, quantity=50),
        ]
        kwargs = {
            "account_id": fx.ACCOUNT_ID,
            "portfolio_id": fx.PORTFOLIO_ID,
            "at": at(3),
        }
        a, _ = fx.reconciler().reconcile(list(local), provider, run_id="x", **kwargs)  # type: ignore[arg-type]
        b, _ = fx.reconciler().reconcile(
            list(reversed(local)),
            list(reversed(provider)),
            run_id="x",
            **kwargs,  # type: ignore[arg-type]
        )
        self.assertEqual(a.content_digest, b.content_digest)

    def test_the_envelope_surfaces_what_needs_attention(self) -> None:
        _, (run, _) = self._run(local_qty=50, provider_qty=-50)
        body = position_reconciliation_to_dict(run)
        self.assertFalse(body["meta"]["is_clean"])
        self.assertEqual(body["meta"]["needs_attention"], 1)


class TestSerialisationAndApi(unittest.TestCase):
    """Brief §26. AST checks; real HTTP tests live in `test_api_runtime.py`."""

    SOURCE = (REPO / "oipulse/api/portfolio.py").read_text(encoding="utf-8")

    def test_every_envelope_carries_both_times(self) -> None:
        snapshot = fx.snapshot()
        envelopes = {
            "snapshot": snapshot_to_dict(snapshot),
            "pnl": pnl_to_dict(snapshot),
            "valuation": valuation_to_dict(snapshot.valuation),
        }
        for name, envelope in envelopes.items():
            with self.subTest(envelope=name):
                self.assertIn("market_time", envelope["meta"])
                self.assertIn("knowledge_time", envelope["meta"])

    def test_the_spec_required_surfaces_exist(self) -> None:
        tree = ast.parse(self.SOURCE)
        routes = [
            str(node.args[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "router"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]
        joined = " ".join(routes)
        for surface in ("/pnl", "/greeks", "/exposure", "/attribution", "/snapshots", "/positions"):
            with self.subTest(surface=surface):
                self.assertIn(surface, joined)

    def test_a_historical_request_requires_a_knowledge_time(self) -> None:
        """Brief §26: no latest-knowledge shortcut for a historical request."""
        self.assertIn("_require_knowledge_time", self.SOURCE)
        self.assertIn("knowledge_time is required whenever market_time", self.SOURCE)

    def test_the_reconciliation_endpoint_refuses_supplied_positions(self) -> None:
        self.assertIn("provider_positions", self.SOURCE)
        self.assertIn("may not be supplied", self.SOURCE)


class TestArchitectureGuards(unittest.TestCase):
    def _guard(self, name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / name)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_portfolio_integrity_guard_passes(self) -> None:
        result = self._guard("check_portfolio_integrity.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_live_execution_barrier_still_passes(self) -> None:
        result = self._guard("check_live_execution_barrier.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_wall_clock_access(self) -> None:
        result = self._guard("check_clock_access.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_phase_12_package_exists(self) -> None:
        for package in ("terminal", "frontend", "ui", "dashboard"):
            with self.subTest(package=package):
                self.assertFalse((REPO / "oipulse" / package).exists())

    def test_no_corporate_action_handling_was_invented(self) -> None:
        """Brief §20: the design specifies none, so none is implemented."""
        for path in sorted((REPO / "oipulse/trading/portfolio").rglob("*.py")):
            source = path.read_text(encoding="utf-8").lower()
            for invented in (
                "def apply_split",
                "def apply_dividend",
                "def handle_assignment",
                "def handle_exercise",
                "corporate_action",
            ):
                with self.subTest(file=path.name, term=invented):
                    self.assertNotIn(invented, source)

    def test_the_portfolio_is_evaluable_with_no_infrastructure(self) -> None:
        """No database, no broker, no market feed — just fills and a state."""
        valuation = fx.valued()
        self.assertTrue(valuation.is_complete)


class TestObservability(unittest.TestCase):
    def test_metric_names_state_their_units(self) -> None:
        """Brief §27: names and units must be meaningful."""
        import oipulse.observability.metrics as metrics

        self.assertTrue(metrics.ATTRIBUTION_RESIDUAL_ABS.endswith("_currency"))
        self.assertTrue(metrics.ATTRIBUTION_RESIDUAL_FRACTION.endswith("_ratio"))

    def test_the_residual_is_recorded_both_absolutely_and_fractionally(self) -> None:
        """A residual of 500 is trivial on a 5,000,000 move and alarming on 600."""
        import oipulse.observability.metrics as metrics

        metrics.record_attribution_run(
            "acc-obs",
            bucket="PORTFOLIO",
            duration_seconds=0.01,
            residual_abs=419.0,
            residual_fraction=0.419,
            uncomputed_components=("VOLATILITY",),
        )
        labels = {"account": "acc-obs", "bucket": "PORTFOLIO"}
        self.assertEqual(
            metrics.METRICS.histogram(metrics.ATTRIBUTION_RESIDUAL_ABS, labels).count, 1
        )
        self.assertEqual(
            metrics.METRICS.histogram(metrics.ATTRIBUTION_RESIDUAL_FRACTION, labels).count,
            1,
        )

    def test_unvalued_positions_are_labelled_by_reason(self) -> None:
        import oipulse.observability.metrics as metrics

        metrics.record_portfolio_valuation(
            "acc-obs2",
            duration_seconds=0.01,
            positions_valued=1,
            unvalued_by_reason={"NO_PRICE_IN_STATE": 1, "NO_CONTRACT_ECONOMICS": 2},
        )
        self.assertEqual(
            metrics.METRICS.counter(
                metrics.PORTFOLIO_UNVALUED_POSITIONS,
                {"account": "acc-obs2", "reason": "NO_CONTRACT_ECONOMICS"},
            ),
            2.0,
        )


class TestPortfolioIntegrityGuardMutation(unittest.TestCase):
    """Mutation tests for tools/check_portfolio_integrity.py."""

    def test_settable_residual_field_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_residual_is_derived

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/portfolio/attribution.py").read_text(
                encoding="utf-8"
            )
            # Add residual field to AttributionResult class
            mutated = original.replace(
                "class AttributionResult:\n",
                "class AttributionResult:\n    residual: Decimal = Decimal(0)\n",
            )
            (target / "attribution.py").write_text(mutated, encoding="utf-8")
            findings = _check_residual_is_derived(tmp)
            self.assertTrue(
                any("declares `residual` as a field" in f for f in findings),
                f"Expected residual field violation, got {findings}",
            )

    def test_non_derived_residual_property_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_residual_is_derived

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/portfolio/attribution.py").read_text(
                encoding="utf-8"
            )
            # Replace residual property with constant 0
            mutated = original.replace(
                "return self.total_pnl - self.explained",
                "return Decimal(0)",
            )
            (target / "attribution.py").write_text(mutated, encoding="utf-8")
            findings = _check_residual_is_derived(tmp)
            self.assertTrue(
                any("does not derive from total_pnl and explained" in f for f in findings),
                f"Expected non-derived residual violation, got {findings}",
            )

    def test_component_amount_assignment_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_no_component_is_adjusted

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/portfolio/attribution.py").read_text(
                encoding="utf-8"
            )
            mutated = original + "\ndef adjust(c):\n    c.amount = Decimal(0)\n"
            (target / "attribution.py").write_text(mutated, encoding="utf-8")
            findings = _check_no_component_is_adjusted(tmp)
            self.assertTrue(
                any("assigns to a component's `amount`" in f for f in findings),
                f"Expected component amount assignment violation, got {findings}",
            )

    def test_runtime_metadata_in_hash_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_hash_excludes_runtime_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/portfolio/snapshot.py").read_text(encoding="utf-8")
            # Leak snapshot_id into as_dict()
            mutated = original.replace(
                '"account_id": self.account_id,',
                '"account_id": self.account_id, "snapshot_id": self.snapshot_id,',
            )
            (target / "snapshot.py").write_text(mutated, encoding="utf-8")
            findings = _check_hash_excludes_runtime_metadata(tmp)
            self.assertTrue(
                any("includes runtime metadata" in f for f in findings),
                f"Expected runtime metadata in hash violation, got {findings}",
            )

    def test_second_price_source_in_valuation_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_single_price_source

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            original = (REPO / "oipulse/trading/portfolio/valuation.py").read_text(encoding="utf-8")
            mutated = original + "\ndef bad_fetch(provider):\n    return provider.fetch_quotes()\n"
            (target / "valuation.py").write_text(mutated, encoding="utf-8")
            findings = _check_single_price_source(tmp)
            self.assertTrue(
                any("calls fetch_quotes" in f for f in findings),
                f"Expected price fetch violation, got {findings}",
            )

    def test_forbidden_module_import_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_imports

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            (target / "bad_import.py").write_text(
                "import oipulse.trading.oms\n",
                encoding="utf-8",
            )
            findings = _check_imports(tmp)
            self.assertTrue(
                any("imports oipulse.trading.oms" in f for f in findings),
                f"Expected forbidden import violation, got {findings}",
            )

    def test_network_module_import_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_imports

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            (target / "bad_socket.py").write_text(
                "import httpx\n",
                encoding="utf-8",
            )
            findings = _check_imports(tmp)
            self.assertTrue(
                any("imports httpx" in f for f in findings),
                f"Expected network import violation, got {findings}",
            )

    def test_upstream_mutation_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_no_mutation_of_upstream_truth

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            (target / "bad_mutator.py").write_text(
                "def mutate(order):\n    order.state = 'FILLED'\n",
                encoding="utf-8",
            )
            findings = _check_no_mutation_of_upstream_truth(tmp)
            self.assertTrue(
                any("assigns to order.state" in f for f in findings),
                f"Expected upstream mutation violation, got {findings}",
            )

    def test_clock_call_fails_guard(self) -> None:
        from tools.check_portfolio_integrity import _check_no_clock

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            target = tmp / "oipulse/trading/portfolio"
            target.mkdir(parents=True)
            (target / "bad_clock.py").write_text(
                "import datetime\ndef now():\n    return datetime.datetime.now()\n",
                encoding="utf-8",
            )
            findings = _check_no_clock(tmp)
            self.assertTrue(
                any("calls now()" in f for f in findings),
                f"Expected clock call violation, got {findings}",
            )


if __name__ == "__main__":
    unittest.main()

