#!/usr/bin/env python3
"""Guard: the strategy surface is closed.

`docs/design/10-REPLAY.md` §5:

> **A strategy cannot reach the database.** It receives a context and returns intents.
> This is what makes look-ahead impossible rather than merely discouraged — there is
> no API surface through which future data could be obtained.

That guarantee is a property of `StrategyContext`'s *fields*. Add a `store`, a
`session`, an `engine` or a `timeline` to it and the guarantee silently evaporates,
while every existing test keeps passing — the tests exercise strategies that do not
use the new field yet. So the field list itself is what has to be checked.

This guard parses `oipulse/backtest/strategy.py` and asserts:

1. every annotated field on `StrategyContext` is on the allow-list below;
2. no field's annotation names a forbidden type (a store, session, repository,
   engine, timeline or clock), whatever the field happens to be called;
3. `Strategy.on_state` takes the context and nothing else, so a caller cannot slip
   a second argument past the boundary.

Stdlib-only AST, so it runs with no dependencies installed and cannot be disabled by
an environment failure. Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path("oipulse/backtest/strategy.py")
CONTEXT_CLASS = "StrategyContext"
PROTOCOL_CLASS = "Strategy"

#: What a strategy may see (`10` §5's "Available" column), and nothing more.
ALLOWED_FIELDS = frozenset(
    {
        "run_id",
        "state",
        "features",
        "signals",
        "account",
        "now",
        "knowledge_horizon",
    }
)

#: Substrings that betray a reachable data source, matched against annotations.
FORBIDDEN_IN_ANNOTATION = (
    "Store",
    "Session",
    "Repository",
    "Repo",
    "Engine",
    "Timeline",
    "Clock",
    "Connection",
    "Provider",
    "Broker",
    "Accessor",
)


def check(path: Path) -> list[str]:
    findings: list[str] = []
    if not path.exists():
        return [f"{path}: missing -- the strategy surface guard has nothing to check"]

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    context = classes.get(CONTEXT_CLASS)
    if context is None:
        return [f"{path}: {CONTEXT_CLASS} not found"]

    for stmt in context.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        name = stmt.target.id
        annotation = ast.unparse(stmt.annotation)

        if name not in ALLOWED_FIELDS:
            findings.append(
                f"{path}:{stmt.lineno}: {CONTEXT_CLASS}.{name} is not on the declared "
                f"strategy surface. Adding a field here widens what a strategy can "
                f"reach; if it is genuinely part of the surface, add it to "
                f"ALLOWED_FIELDS in this guard and say why."
            )
        for bad in FORBIDDEN_IN_ANNOTATION:
            if bad in annotation:
                findings.append(
                    f"{path}:{stmt.lineno}: {CONTEXT_CLASS}.{name}: {annotation} exposes "
                    f"a {bad} to the strategy. Look-ahead must be impossible by "
                    f"construction, not merely unused (10-REPLAY.md §5)."
                )

    protocol = classes.get(PROTOCOL_CLASS)
    if protocol is None:
        findings.append(f"{path}: {PROTOCOL_CLASS} protocol not found")
    else:
        for stmt in protocol.body:
            if isinstance(stmt, ast.FunctionDef) and stmt.name == "on_state":
                args = [a.arg for a in stmt.args.args if a.arg != "self"]
                if args != ["ctx"]:
                    findings.append(
                        f"{path}:{stmt.lineno}: Strategy.on_state takes {args}; it must "
                        f"take exactly the context, or a caller can pass data around "
                        f"the boundary the context defines."
                    )
                if stmt.args.kwonlyargs or stmt.args.vararg or stmt.args.kwarg:
                    findings.append(
                        f"{path}:{stmt.lineno}: Strategy.on_state accepts extra "
                        f"arguments; the context must be the only input."
                    )
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = check(root / TARGET)
    if findings:
        print("FAIL  strategy surface violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(
        f"PASS  strategy surface closed: {len(ALLOWED_FIELDS)} declared fields, no store, "
        f"session, engine, timeline or clock reachable from a strategy"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
