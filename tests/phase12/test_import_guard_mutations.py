"""Mutation tests for `tools/check_terminal_imports.py`.

This guard exists because `tsc --noEmit` and `next build` cannot run in this
environment, so the errors they normally catch — a mistyped path, a renamed export,
a symbol that was never exported — would otherwise reach CI unchallenged. A guard
standing in for a compiler has to be held to the same standard as one: each test
below introduces exactly one error a compiler would reject and asserts the guard
names it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from check_terminal_imports import check as import_check

from tests.phase12._workspace import append, edit, workspace

TERMINAL = Path("frontend") / "lib" / "terminal"
COMPONENTS = Path("frontend") / "components" / "terminal"
PAGES = Path("frontend") / "app" / "terminal"


class ImportGuard(unittest.TestCase):
    def assertCaught(self, findings: list[str], fragment: str) -> None:
        self.assertTrue(findings, "the guard reported nothing")
        joined = "\n".join(findings)
        self.assertIn(fragment, joined, f"expected {fragment!r} in:\n{joined}")

    def test_passes_on_the_committed_tree(self) -> None:
        self.assertEqual(import_check(REPO), [])

    def test_a_path_that_resolves_to_no_file(self) -> None:
        with workspace() as root:
            edit(
                root / COMPONENTS / "ScreenFrame.tsx",
                '"@/lib/terminal/realtime"',
                '"@/lib/terminal/realtimee"',
            )
            self.assertCaught(import_check(root), "resolves to no file")

    def test_a_named_import_the_module_does_not_export(self) -> None:
        """The renamed-export error, which is silent until the build."""
        with workspace() as root:
            edit(
                root / COMPONENTS / "ScreenFrame.tsx",
                "import { realtimeStatus }",
                "import { realtimeState }",
            )
            self.assertCaught(import_check(root), "does not export it")

    def test_a_relative_import_that_resolves_to_no_file(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                '\nimport { nothing } from "./does-not-exist";\nexport const x = nothing;\n',
            )
            self.assertCaught(import_check(root), "resolves to no file")

    def test_an_undeclared_package(self) -> None:
        """`npm ci` would not install it, so the build fails on a fresh checkout."""
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                '\nimport { chart } from "some-charting-lib";\nexport const c = chart;\n',
            )
            self.assertCaught(import_check(root), "not in frontend/package.json")

    def test_a_page_without_a_default_export(self) -> None:
        with workspace() as root:
            edit(
                root / PAGES / "signals" / "page.tsx",
                "export default function SignalsPage()",
                "export function SignalsPage()",
            )
            self.assertCaught(import_check(root), "no default export")

    def test_a_default_import_from_a_module_that_has_none(self) -> None:
        with workspace() as root:
            append(
                root / TERMINAL / "quality.ts",
                '\nimport time from "@/lib/terminal/time";\nexport const t = time;\n',
            )
            self.assertCaught(import_check(root), "no default export")

    def test_a_truncated_file(self) -> None:
        with workspace() as root:
            path = root / COMPONENTS / "ProvenanceTrail.tsx"
            text = path.read_text(encoding="utf-8")
            path.write_text(text[: len(text) // 2], encoding="utf-8")
            self.assertCaught(import_check(root), "unbalanced")

    def test_the_scanner_noticing_it_has_stopped_reading(self) -> None:
        with workspace() as root:
            import shutil

            shutil.rmtree(root / TERMINAL)
            shutil.rmtree(root / COMPONENTS)
            shutil.rmtree(root / PAGES)
            shutil.rmtree(root / "frontend" / "tests")
            self.assertCaught(import_check(root), "wrong place")

    def test_a_type_only_import_is_still_checked(self) -> None:
        """`import type { X }` is erased at build time but must still exist."""
        with workspace() as root:
            edit(
                root / COMPONENTS / "ScreenFrame.tsx",
                "import type { QualityBadge }",
                "import type { QualityBadgeX }",
            )
            self.assertCaught(import_check(root), "does not export it")

    def test_a_jsx_self_closing_tag_is_not_read_as_a_regex(self) -> None:
        """The false positive this guard produced on its second run.

        `<Foo bar={baz} />` ends with `} />`, and `}` precedes a regex elsewhere.
        Reading it as one swallowed the rest of the file and reported two intact
        pages as truncated.
        """
        with workspace() as root:
            append(
                root / COMPONENTS / "ProvenanceTrail.tsx",
                "\nexport function Tag() {\n  return <span title={String(1)} />;\n}\n",
            )
            self.assertEqual(import_check(root), [])

    def test_a_jsx_closing_tag_is_not_read_as_a_regex(self) -> None:
        with workspace() as root:
            append(
                root / COMPONENTS / "ProvenanceTrail.tsx",
                "\nexport function Wrap() {\n  return <div>{1}</div>;\n}\n",
            )
            self.assertEqual(import_check(root), [])


if __name__ == "__main__":
    unittest.main()
