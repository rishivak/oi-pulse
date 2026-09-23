#!/usr/bin/env python3
"""Guard: no repository read without a temporal bound.

`docs/design/05-DATA_LIFECYCLE_PIT.md` §3 — *"There is no unbounded query for callers to
reach for."* `15-TESTING.md` §5 lists this as a contract test, and `18-ROADMAP.md`
Phase 1 acceptance requires that *"a repository method without a time mode does not
compile past lint."*

Python has no way to make it a compile error, so this is the lint. Any class whose name
ends in `Repository` (or which subclasses `TemporalRepository`) must accept a
`TemporalBound` on every public read method.

Write methods are exempt: a write records `ingested_at` at the moment of writing and has
no temporal bound to honour.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

BOUND_TYPES = {
    "TemporalBound",
    "MarketTruthAt",
    "KnowledgeAt",
    "TradableInformationAt",
    "ResolvedBound",
}

BOUND_PARAM_NAMES = {"bound", "as_of", "at", "temporal_bound"}

# Verbs that read. Anything else on a repository is presumed a write or a helper.
READ_PREFIXES = (
    "fetch",
    "get",
    "list",
    "find",
    "load",
    "read",
    "select",
    "query",
    "count",
    "exists",
    "latest",
    "iter",
    "stream",
    "search",
)

EXEMPT_METHODS = {"__init__", "__repr__", "__str__", "__enter__", "__exit__"}


def _is_repository(node: ast.ClassDef) -> bool:
    if node.name.endswith("Repository"):
        return True
    for base in node.bases:
        name = (
            base.id
            if isinstance(base, ast.Name)
            else (base.attr if isinstance(base, ast.Attribute) else None)
        )
        if name and name.endswith("Repository"):
            return True
    return False


def _annotation_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            # string annotation, e.g. "TemporalBound | None"
            names.update(part.strip(" |[]'\"") for part in sub.value.split())
    return names


def _takes_bound(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = list(fn.args.posonlyargs) + list(fn.args.args) + list(fn.args.kwonlyargs)
    for arg in args:
        if arg.arg in ("self", "cls"):
            continue
        if arg.arg in BOUND_PARAM_NAMES:
            return True
        if _annotation_names(arg.annotation) & BOUND_TYPES:
            return True
    return False


def _is_read(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if fn.name in EXEMPT_METHODS or fn.name.startswith("_"):
        return False
    return fn.name.split("_")[0] in READ_PREFIXES or fn.name in READ_PREFIXES


def check(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(f"{path}:{exc.lineno}: syntax error, cannot verify")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_repository(node):
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_read(item):
                    continue
                if not _takes_bound(item):
                    findings.append(
                        f"{path}:{item.lineno}: {node.name}.{item.name}() is a read "
                        f"without a temporal bound — accept a TemporalBound "
                        f"(market_truth_at / knowledge_at / tradable_information_at)"
                    )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", default=["oipulse"])
    args = parser.parse_args()

    findings: list[str] = []
    for name in args.roots or ["oipulse"]:
        root = Path(name)
        if not root.exists():
            print(f"check_temporal_repository: no such path {root}", file=sys.stderr)
            return 2
        findings.extend(check(root))

    if findings:
        print("FAIL  unbounded repository reads:")
        for f in findings:
            print(f"  {f}")
        return 1

    print("PASS  every repository read accepts a temporal bound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
