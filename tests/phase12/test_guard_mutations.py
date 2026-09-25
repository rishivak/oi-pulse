"""Mutation tests for the two Phase 12 guards.

Phase 12 brief §30 asks for guards. A guard that has only ever been run against
correct code proves nothing about what it would catch, and the Phase 10 brief §29
draws the distinction directly: "safety guard PASS" is not "mutation-tested safety".

Each test below breaks exactly one thing in a disposable copy of the tree and
asserts the guard names it. `edit` raises when its anchor is missing, so a test
cannot quietly stop mutating and start passing for the wrong reason — which is a
failure mode this project has hit before.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from check_frontend_contract import check as contract_check
from check_terminal_boundary import check as boundary_check

from tests.phase12._workspace import append, edit, workspace

TERMINAL = Path("frontend") / "lib" / "terminal"
COMPONENTS = Path("frontend") / "components" / "terminal"
PAGES = Path("frontend") / "app" / "terminal"


class Baseline(unittest.TestCase):
    """Both guards pass on the committed tree, and on an unmodified copy."""

    def test_contract_guard_passes_here(self) -> None:
        self.assertEqual(contract_check(REPO), [])

    def test_boundary_guard_passes_here(self) -> None:
        self.assertEqual(boundary_check(REPO), [])

    def test_guards_pass_on_an_untouched_copy(self) -> None:
        """Establishes that a later failure came from the mutation, not the copy."""
        with workspace() as root:
            self.assertEqual(contract_check(root), [])
            self.assertEqual(boundary_check(root), [])


class ContractGuardMutations(unittest.TestCase):
    def assertCaught(self, findings: list[str], fragment: str) -> None:
        self.assertTrue(findings, "the guard reported nothing")
        joined = "\n".join(findings)
        self.assertIn(fragment, joined, f"expected {fragment!r} in:\n{joined}")

    def test_an_endpoint_the_backend_does_not_serve(self) -> None:
        with workspace() as root:
            edit(
                root / TERMINAL / "endpoints.ts",
                'endpoint("GET", "/signals/types")',
                'endpoint("GET", "/signals/catalogue")',
            )
            self.assertCaught(contract_check(root), "GET /signals/catalogue")

    def test_a_screen_declaring_a_route_that_does_not_exist(self) -> None:
        """This is the no-stub rule: a screen promising a capability it lacks."""
        with workspace() as root:
            edit(
                root / TERMINAL / "screens.ts",
                '"GET /portfolio/attribution"',
                '"GET /portfolio/sharpe-ratio"',
            )
            self.assertCaught(contract_check(root), "GET /portfolio/sharpe-ratio")

    def test_a_dto_field_the_backend_does_not_send(self) -> None:
        """`order.status` when the backend sends `order.state`."""
        with workspace() as root:
            edit(
                root / "frontend" / "lib" / "api" / "dto.ts",
                "  readonly state: string;\n  readonly venue: string;",
                "  readonly status: string;\n  readonly venue: string;",
            )
            self.assertCaught(contract_check(root), "'status'")

    def test_an_untagged_dto_interface(self) -> None:
        with workspace() as root:
            append(
                root / "frontend" / "lib" / "api" / "dto.ts",
                "\nexport interface SmuggledDto {\n  readonly whatever: string;\n}\n",
            )
            self.assertCaught(contract_check(root), "SmuggledDto has no @contract tag")

    def test_a_dto_citing_a_model_that_does_not_exist(self) -> None:
        with workspace() as root:
            edit(
                root / "frontend" / "lib" / "api" / "dto.ts",
                "/** @contract model PortfolioGreeks */",
                "/** @contract model MadeUpModel */",
            )
            self.assertCaught(contract_check(root), "MadeUpModel")

    def test_a_component_building_its_own_request(self) -> None:
        with workspace() as root:
            append(
                root / COMPONENTS / "ProvenanceTrail.tsx",
                '\nexport async function sneak() {\n  return fetch("/api/v2/signals");\n}\n',
            )
            self.assertCaught(contract_check(root), "builds its own request")

    def test_a_stale_contract(self) -> None:
        """The backend changed and nobody regenerated."""
        with workspace() as root:
            edit(
                root / "oipulse" / "api" / "portfolio.py",
                '@router.get("/attribution")',
                '@router.get("/attribution-v2")',
            )
            self.assertCaught(contract_check(root), "stale")

    def test_the_endpoint_parser_noticing_it_has_stopped_matching(self) -> None:
        """A parser that matches nothing would otherwise pass every path."""
        with workspace() as root:
            path = root / TERMINAL / "endpoints.ts"
            path.write_text("export const endpoint = null;\n", encoding="utf-8")
            self.assertCaught(contract_check(root), "endpoint() calls found")


class BoundaryGuardMutations(unittest.TestCase):
    def assertCaught(self, findings: list[str], fragment: str) -> None:
        self.assertTrue(findings, "the guard reported nothing")
        joined = "\n".join(findings)
        self.assertIn(fragment, joined, f"expected {fragment!r} in:\n{joined}")

    def test_a_database_client_import(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                '\nimport { Pool } from "pg";\nexport const pool = Pool;\n',
            )
            self.assertCaught(boundary_check(root), "imports pg")

    def test_a_provider_client_import(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                '\nimport { Upstox } from "upstox-client";\nexport const u = Upstox;\n',
            )
            self.assertCaught(boundary_check(root), "imports upstox-client")

    def test_reading_configuration_in_the_client(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                "\nexport const base = process.env.DATABASE_URL;\n",
            )
            self.assertCaught(boundary_check(root), "process.env")

    def test_a_credential_identifier(self) -> None:
        with workspace() as root:
            append(root / TERMINAL / "quality.ts", "\nexport const accessToken = 1;\n")
            self.assertCaught(boundary_check(root), "accessToken")

    def test_an_invented_stream(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "realtime.ts",
                '\nexport const stream = new EventSource("/api/v2/stream/events");\n',
            )
            self.assertCaught(boundary_check(root), "EventSource")

    def test_widening_the_live_trading_return_type(self) -> None:
        """A `boolean` return would let a caller write a live branch."""
        with workspace() as root:
            edit(
                root / TERMINAL / "mode.ts",
                "export function liveTradingRenderable(): false {",
                "export function liveTradingRenderable(): boolean {",
            )
            self.assertCaught(boundary_check(root), "literal `false`")

    def test_arithmetic_in_a_component(self) -> None:
        """Recomputing an analytic in the UI (13-FRONTEND_IA.md §1.3)."""
        with workspace() as root:
            append(
                root / COMPONENTS / "ProvenanceTrail.tsx",
                "\nexport function pcr(calls: number, puts: number) {\n  return puts / calls;\n}\n",
            )
            self.assertCaught(boundary_check(root), "performs arithmetic")

    def test_deriving_the_residual_in_the_frontend(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "attribution.ts",
                "\nexport function badResidual(total: number, explained: number) {\n"
                "  return total - explained;\n}\n",
            )
            self.assertCaught(boundary_check(root), "subtracts an attribution field")

    def test_syntax_node_cannot_strip(self) -> None:
        """An enum would break the only test runner available here."""
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                "\nexport enum Tone {\n  Good,\n  Bad,\n}\n",
            )
            self.assertCaught(boundary_check(root), "Node cannot")

    def test_a_reference_to_a_later_phase(self) -> None:
        with workspace() as root:
            append(root / TERMINAL / "quality.ts", "\nexport const PHASE_13 = true;\n")
            self.assertCaught(boundary_check(root), "phase after 12")

    def test_a_page_that_bypasses_the_frame(self) -> None:
        with workspace() as root:
            (root / PAGES / "orphan").mkdir(parents=True)
            (root / PAGES / "orphan" / "page.tsx").write_text(
                "export default function Orphan() {\n  return null;\n}\n",
                encoding="utf-8",
            )
            findings = boundary_check(root)
            self.assertCaught(findings, "does not render through ScreenFrame")
            self.assertCaught(findings, "not in the screen registry")

    def test_a_registry_entry_with_no_page(self) -> None:
        with workspace() as root:
            edit(
                root / TERMINAL / "screens.ts",
                'route: "/terminal/volatility"',
                'route: "/terminal/vol-surface"',
            )
            findings = boundary_check(root)
            self.assertCaught(findings, "/terminal/vol-surface")

    def test_a_component_missing_the_use_client_directive(self) -> None:
        """Next would render it on the server, where hooks do not exist."""
        with workspace() as root:
            path = root / COMPONENTS / "primitives.tsx"
            edit(path, '"use client";\n\n', "")
            self.assertCaught(boundary_check(root), '"use client" directive')

    def test_a_raw_table_without_semantics(self) -> None:
        with workspace() as root:
            edit(
                root / PAGES / "signals" / "page.tsx",
                '<div className="space-y-4">',
                '<div className="space-y-4"><table />',
            )
            self.assertCaught(boundary_check(root), "renders a raw <table>")

    def test_the_file_scanner_noticing_it_has_lost_the_tree(self) -> None:
        """A guard pointed at nothing passes; this makes that a failure."""
        with workspace() as root:
            import shutil

            shutil.rmtree(root / TERMINAL)
            shutil.rmtree(root / COMPONENTS)
            shutil.rmtree(root / PAGES)
            self.assertCaught(boundary_check(root), "wrong place")


if __name__ == "__main__":
    unittest.main()
