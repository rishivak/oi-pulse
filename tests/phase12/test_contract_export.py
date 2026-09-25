"""The generated API contract. `tools/export_api_contract.py`.

The contract is the load-bearing artifact of Phase 12: the screen registry, the
endpoint builders and the DTOs are all checked against it, so if it is wrong every
check above it is wrong in the same direction and silently.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from export_api_contract import (
    build,
    collect_models,
    collect_routes,
    collect_serializers,
    to_typescript,
)

from tests.phase12._workspace import edit, workspace

CONTRACT_JSON = REPO / "frontend" / "lib" / "api" / "contract.generated.json"
CONTRACT_TS = REPO / "frontend" / "lib" / "api" / "contract.generated.ts"


class RouteExtraction(unittest.TestCase):
    def setUp(self) -> None:
        self.routes = collect_routes(REPO)
        self.keys = {f"{r['method']} {r['path']}" for r in self.routes}

    def test_finds_every_router(self) -> None:
        """One route from each of the eleven mounted routers, at minimum."""
        for expected in (
            "GET /market/state",
            "GET /features",
            "GET /signals",
            "GET /alerts/rules",
            "GET /research/studies",
            "GET /replay/sessions",
            "GET /backtest/runs",
            "GET /paper-trading/accounts",
            "GET /risk/profiles",
            "GET /reconciliation/status",
            "GET /portfolio",
            "GET /ops/health",
        ):
            self.assertIn(expected, self.keys)

    def test_prefix_is_joined_not_guessed(self) -> None:
        """`APIRouter(prefix="/signals")` + `@router.get("")` is `/signals`."""
        self.assertIn("GET /signals", self.keys)
        self.assertNotIn("GET /signals/", self.keys)

    def test_path_parameters_are_preserved_verbatim(self) -> None:
        self.assertIn("GET /signals/{signal_id}/history", self.keys)

    def test_no_live_trading_route_exists(self) -> None:
        """The whole safety argument rests on this being empty."""
        live = [k for k in self.keys if "/trading/" in k and "/paper-trading/" not in k]
        self.assertEqual(live, [])

    def test_the_stream_surface_is_still_absent(self) -> None:
        """`/stream` is in `12-API_SPEC.md` §3 and is not served.

        The terminal polls instead of streaming because of exactly this. If it
        appears, `lib/terminal/realtime.ts` should be revisited -- and a test there
        asserts the constant and the contract agree, so the two cannot drift.
        """
        self.assertEqual([k for k in self.keys if "/stream" in k], [])

    def test_the_journal_read_contract_is_served(self) -> None:
        """Added by the Phase 12 remediation; the screen ships against it."""
        self.assertIn("GET /journal/entries", self.keys)
        self.assertIn("GET /journal/entries/{entry_id}", self.keys)
        # Read only: `12` §3 says CRUD, and writing is a domain action no phase
        # specifies an authoring path for.
        self.assertEqual([k for k in self.keys if "/journal" in k and not k.startswith("GET ")], [])

    def test_meta_keys_are_extracted_where_the_return_is_literal(self) -> None:
        signals = next(r for r in self.routes if r["path"] == "/signals" and r["method"] == "GET")
        self.assertIn("semantics", signals["meta_keys"])
        self.assertIn("decision_time", signals["meta_keys"])
        self.assertFalse(signals["meta_partial"])


class ModelExtraction(unittest.TestCase):
    def setUp(self) -> None:
        self.models = collect_models(REPO)
        self.serializers = collect_serializers(REPO)

    def test_attribution_keys_include_the_residual(self) -> None:
        keys = self.models["AttributionResult"]["keys"]
        for field in ("total_pnl", "explained", "residual", "reconciles"):
            self.assertIn(field, keys)

    def test_order_state_is_called_state_not_status(self) -> None:
        """The exact confusion the DTO check exists to catch."""
        keys = self.models["PaperOrder"]["keys"]
        self.assertIn("state", keys)
        self.assertNotIn("status", keys)
        self.assertIn("provider_status", keys)

    def test_a_name_collision_is_merged_and_marked_partial(self) -> None:
        """`TradeIntent` exists in both `backtest` and `trading`.

        The union is a lower bound for either one, and `partial` is what stops the
        DTO check treating a lower bound as a closed set.
        """
        self.assertTrue(self.models["TradeIntent"]["partial"])
        self.assertGreater(len(self.models["TradeIntent"]["sources"]), 1)

    def test_a_serializer_that_mutates_its_dict_is_marked_partial(self) -> None:
        """`signal_to_dict` adds evidence conditionally after the literal."""
        self.assertTrue(self.serializers["signal_to_dict"]["partial"])
        self.assertIn("contradiction_assessment", self.serializers["signal_to_dict"]["keys"])

    def test_a_closed_serializer_is_not_marked_partial(self) -> None:
        self.assertFalse(self.serializers["rule_to_dict"]["partial"])


class Determinism(unittest.TestCase):
    def test_two_runs_agree(self) -> None:
        self.assertEqual(build(REPO), build(REPO))

    def test_committed_artifacts_match_the_generator(self) -> None:
        contract = build(REPO)
        self.assertEqual(
            json.loads(CONTRACT_JSON.read_text(encoding="utf-8")),
            contract,
            "contract.generated.json is stale; run tools/export_api_contract.py",
        )
        self.assertEqual(
            CONTRACT_TS.read_text(encoding="utf-8"),
            to_typescript(contract),
            "contract.generated.ts is stale; run tools/export_api_contract.py",
        )

    def test_a_backend_change_changes_the_contract(self) -> None:
        """Otherwise the drift check would pass over a renamed route."""
        with workspace() as root:
            before = build(root)
            edit(
                root / "oipulse" / "api" / "signals.py",
                '@router.get("/types")',
                '@router.get("/catalogue")',
            )
            after = build(root)
            self.assertNotEqual(before["routes"], after["routes"])
            self.assertNotEqual(before["source_digest"], after["source_digest"])


if __name__ == "__main__":
    unittest.main()


class DigestStability(unittest.TestCase):
    """The digest must describe the content, not the checkout.

    This repository has `core.autocrlf=true` and no `.gitattributes`, so a checkout
    rewrites every `.py` file to CRLF while git's stored blobs stay LF. Hashing raw
    bytes made the same commit produce two different digests on two machines, and
    `--check` failed on a tree where `git diff` was empty — which is exactly what
    happened during a rebase.
    """

    def test_line_endings_do_not_change_the_digest(self) -> None:
        import shutil
        import tempfile

        from export_api_contract import _source_digest

        with tempfile.TemporaryDirectory(prefix="digest-eol-") as raw:
            root = Path(raw)
            shutil.copytree(
                REPO / "oipulse",
                root / "oipulse",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            converted = 0
            for path in (root / "oipulse").rglob("*.py"):
                original = path.read_bytes()
                flipped = (
                    original.replace(b"\r\n", b"\n")
                    if b"\r\n" in original
                    else original.replace(b"\n", b"\r\n")
                )
                if flipped != original:
                    converted += 1
                path.write_bytes(flipped)
            self.assertGreater(converted, 50, "no files were converted; the test is inert")
            self.assertEqual(_source_digest(REPO), _source_digest(root))
