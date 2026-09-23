#!/usr/bin/env python3
"""Guard: no wall-clock access outside `oipulse.core.clock`.

`docs/design/00-OVERVIEW.md` §5 and `15-TESTING.md` §5. Determinism in replay and
backtesting depends on every timestamp coming from the injected `Clock`. A single stray
`datetime.now()` reintroduces real time into a supposedly deterministic path, and it does
so silently — the run still completes, it just is not reproducible.

AST-based rather than grep-based: a grep for `datetime.now` misses `from datetime import
datetime as dt; dt.now()` and misses `getattr(datetime, "now")()`, while flagging the
string "datetime.now()" inside a docstring.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# The single module permitted to read the host clock.
ALLOWED: frozenset[str] = frozenset({"oipulse/core/clock.py"})

# Callables that return real time.
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "now",  # datetime.now, pendulum.now
        "utcnow",  # datetime.utcnow
        "today",  # date.today, datetime.today
        "time",  # time.time
        "monotonic",  # time.monotonic
        "perf_counter",
        "time_ns",
        "monotonic_ns",
    }
)

FORBIDDEN_ROOTS: frozenset[str] = frozenset({"datetime", "date", "time", "pendulum", "arrow"})


class ClockVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[tuple[int, str]] = []
        # Track `from datetime import datetime as dt` so `dt.now()` is caught.
        self.aliases: dict[str, str] = {}

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in FORBIDDEN_ROOTS:
            for alias in node.names:
                self.aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in FORBIDDEN_ROOTS:
                self.aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ATTRS:
            root = self._root_name(func.value)
            if root is not None and (root in FORBIDDEN_ROOTS or root in self.aliases):
                self.violations.append((node.lineno, f"{root}.{func.attr}()"))
        # getattr(datetime, "now")() — rare, but the guard should not be trivially evaded.
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in FORBIDDEN_ATTRS
        ):
            root = self._root_name(node.args[0])
            if root is not None and (root in FORBIDDEN_ROOTS or root in self.aliases):
                self.violations.append((node.lineno, f'getattr({root}, "{node.args[1].value}")()'))
        self.generic_visit(node)

    @staticmethod
    def _root_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return ClockVisitor._root_name(node.value)
        return None


def scan(root: Path, allowed: frozenset[str]) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).as_posix()
        if rel in allowed or "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(f"{rel}:{exc.lineno}: syntax error, cannot verify: {exc.msg}")
            continue
        visitor = ClockVisitor(path)
        visitor.visit(tree)
        for lineno, what in visitor.violations:
            findings.append(
                f"{rel}:{lineno}: forbidden wall-clock access {what} — "
                f"inject oipulse.core.clock.Clock instead"
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", default=["oipulse"])
    args = parser.parse_args()

    all_findings: list[str] = []
    for root_name in args.roots or ["oipulse"]:
        root = Path(root_name)
        if not root.exists():
            print(f"check_clock_access: no such path {root}", file=sys.stderr)
            return 2
        all_findings.extend(scan(root, ALLOWED))

    if all_findings:
        print("FAIL  wall-clock access outside oipulse/core/clock.py:")
        for f in all_findings:
            print(f"  {f}")
        return 1

    print("PASS  no wall-clock access outside oipulse/core/clock.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
