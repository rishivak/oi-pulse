"""Structural facts about the Phase 12 terminal.

These are the claims the implementation report makes, checked here so the report
describes a tree that exists rather than one that was intended. Where a check is a
presence check over text it is named as one — `13-FRONTEND_IA.md` is prose, and a
grep over prose proves a phrase is present, nothing more.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
TERMINAL = FRONTEND / "lib" / "terminal"
PAGES = FRONTEND / "app" / "terminal"
SCREENS = (TERMINAL / "screens.ts").read_text(encoding="utf-8")
CONTRACT = json.loads((FRONTEND / "lib" / "api" / "contract.generated.json").read_text())


def _without_comments(text: str) -> str:
    """Drop `//` and `/* */` comments, keeping everything a user could see."""
    without_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


class ScreenInventory(unittest.TestCase):
    def test_all_fourteen_named_screens_ship(self) -> None:
        """`13-FRONTEND_IA.md` §2 names fourteen, and all of them now have backends.

        The roadmap's "twelve workflow screens" contradicts its own fourteen-name
        deliverable list, so the count is not the testable thing; the freeze's
        qualifier is. `20-ARCHITECTURE_FREEZE.md` §286 gates on "none ahead of its
        backend", and the Journal read contract added by the remediation is what
        makes that true for the last of them.
        """
        routes = re.findall(r'route:\s*"(/terminal[^"]*)"', SCREENS)
        self.assertEqual(len(routes), 14)
        self.assertIn("/terminal", routes)
        self.assertIn("/terminal/journal", routes)

    def test_every_declared_route_has_a_page(self) -> None:
        for route in re.findall(r'route:\s*"(/terminal[^"]*)"', SCREENS):
            suffix = route.removeprefix("/terminal").lstrip("/")
            page = PAGES / suffix / "page.tsx" if suffix else PAGES / "page.tsx"
            self.assertTrue(page.exists(), f"{route} has no page at {page}")

    def test_every_page_is_declared(self) -> None:
        routes = set(re.findall(r'route:\s*"(/terminal[^"]*)"', SCREENS))
        for page in PAGES.rglob("page.tsx"):
            suffix = page.parent.relative_to(PAGES).as_posix()
            route = "/terminal" if suffix == "." else f"/terminal/{suffix}"
            self.assertIn(route, routes)

    def test_the_route_group_has_error_loading_and_not_found_boundaries(self) -> None:
        """`13` §8: "Error boundaries and loading states per route — all absent in
        the legacy app"."""
        for name in ("error.tsx", "loading.tsx", "not-found.tsx", "layout.tsx"):
            self.assertTrue((PAGES / name).exists(), f"{name} is missing")

    def test_journal_ships_against_a_real_route(self) -> None:
        self.assertIn('id: "journal"', SCREENS)
        self.assertTrue((PAGES / "journal" / "page.tsx").exists())
        served = {
            f"{r['method']} {r['path']}" for r in CONTRACT["routes"] if "/journal" in r["path"]
        }
        self.assertEqual(served, {"GET /journal/entries", "GET /journal/entries/{entry_id}"})

    def test_nothing_is_withheld_and_the_list_remains(self) -> None:
        """The list outlives its one entry: the rule it serves is not Journal-specific."""
        self.assertIn("WITHHELD_SCREENS", SCREENS)
        self.assertIn("WITHHELD_SCREENS: readonly WithheldScreen[] = [] as const", SCREENS)

    def test_no_coming_soon_anywhere(self) -> None:
        """`13` §2 rejects stubs. A presence check over the *rendered* text.

        Comments are stripped first, and that is not a detail. A naive scan for
        "coming soon" finds `not-found.tsx`, whose comment says the design *forbids*
        a "coming soon" page — the prose that rules the thing out reads identically
        to the thing. A ban on a word catches its own disclaimer unless the check
        looks at code.
        """
        for path in list(PAGES.rglob("*.tsx")) + list(
            (FRONTEND / "components" / "terminal").rglob("*.tsx")
        ):
            text = _without_comments(path.read_text(encoding="utf-8")).lower()
            self.assertNotIn("coming soon", text, f"{path} promises a future screen")
            self.assertNotIn("todo:", text, f"{path} ships a TODO")


class SafetySurface(unittest.TestCase):
    def test_the_terminal_declares_no_non_paper_trading_route(self) -> None:
        declared = set(re.findall(r'"(?:GET|POST|PUT|PATCH|DELETE) (/[^"]*)"', SCREENS))
        for path in declared:
            if "/trading/" in path:
                self.assertIn("/paper-trading/", path, f"{path} is not a paper route")

    def test_live_trading_renderable_returns_the_literal_false(self) -> None:
        mode = (TERMINAL / "mode.ts").read_text(encoding="utf-8")
        self.assertIn("export function liveTradingRenderable(): false {", mode)
        self.assertIn("LIVE_TRADING_GATES", mode)
        for gate in ("LIVE_TRADING_ENABLED", "LIVE_TRADING_CONFIRMED", "LIVE_TRADE_PERMISSION"):
            self.assertIn(gate, mode)

    def test_the_stream_constant_agrees_with_the_backend(self) -> None:
        realtime = (TERMINAL / "realtime.ts").read_text(encoding="utf-8")
        served = any("/stream" in r["path"] for r in CONTRACT["routes"])
        self.assertEqual(
            served,
            "STREAM_ENDPOINT_IMPLEMENTED = true" in realtime,
            "the streaming constant and the backend disagree",
        )
        self.assertIn("STREAM_ENDPOINT_IMPLEMENTED = false", realtime)


class BuildWiring(unittest.TestCase):
    def test_the_frontend_declares_the_four_checks_ci_runs(self) -> None:
        scripts = json.loads((FRONTEND / "package.json").read_text())["scripts"]
        for name in ("build", "type-check", "lint", "test"):
            self.assertIn(name, scripts)

    def test_ci_runs_the_phase_12_guards_and_the_frontend_job(self) -> None:
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for command in (
            "tools/export_api_contract.py --check",
            "tools/check_frontend_contract.py",
            "tools/check_terminal_boundary.py",
        ):
            self.assertIn(command, ci)
        self.assertIn("npm run build", ci)
        self.assertIn("npm run type-check", ci)
        self.assertIn("npm test", ci)

    def test_the_v2_api_base_is_server_only(self) -> None:
        """`NEXT_PUBLIC_*` is inlined into the client bundle; this must not be."""
        config = (FRONTEND / "next.config.mjs").read_text(encoding="utf-8")
        self.assertIn("OIPULSE_V2_API_URL", config)
        self.assertNotIn("NEXT_PUBLIC_OIPULSE", config)
        self.assertIn("/api/v2/:path*", config)
        # The legacy v1 proxy is untouched.
        self.assertIn("NEXT_PUBLIC_API_URL", config)


class NodeSuite(unittest.TestCase):
    """Run the terminal's TypeScript tests as part of this suite.

    They are the tests that cover the temporal model, the quality substitutions, the
    residual and the no-live-trading path. Running them from here means a Python-only
    `unittest` run does not silently omit two thirds of Phase 12's coverage.
    """

    def test_the_terminal_test_suite_passes(self) -> None:
        try:
            result = subprocess.run(
                [
                    "node",
                    "--import",
                    "./tests/register.mjs",
                    "--test",
                    # TAP rather than the default reporter: the summary line format
                    # is specified, so parsing a count out of it will not break on a
                    # Node release that restyles the human-readable output.
                    "--test-reporter=tap",
                    "tests/**/*.test.ts",
                ],
                cwd=FRONTEND,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except FileNotFoundError:
            self.skipTest("node is not installed; the terminal suite runs in CI")
        self.assertEqual(
            result.returncode,
            0,
            f"the terminal test suite failed:\n{result.stdout}\n{result.stderr}",
        )
        match = re.search(r"^# pass (\d+)$", result.stdout, re.MULTILINE)
        self.assertIsNotNone(match, f"could not read a pass count from:\n{result.stdout}")
        assert match is not None
        self.assertGreaterEqual(
            int(match.group(1)),
            150,
            "the terminal suite reported far fewer tests than it contains; the "
            "runner has probably stopped discovering files",
        )


if __name__ == "__main__":
    unittest.main()
