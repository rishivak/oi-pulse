#!/usr/bin/env python3
"""Guard: migration operations respect PostgreSQL's dependency ordering.

Motivated by a real failure found in external verification. Migration 0002 ran its
partition loop before `obs_depth` was created, so PostgreSQL rejected it with
`relation "obs_depth" does not exist` — and had it got past that, `obs_depth` would
have been partitioned and indexed a second time by the explicit calls that followed.

PostgreSQL requires, for every table:

    parent table -> columns/constraints -> partitions -> indexes/dependent objects

This walks each migration's `upgrade()` in **source order** and asserts that every
operation naming a table happens after that table is created, and that no table is
partitioned or indexed twice. It resolves the `for table in _PARTITIONED:` loop by
reading the module-level tuple, which is exactly where the defect hid: the loop looked
correct in isolation and was wrong only in relation to the surrounding order.

Static analysis, so it runs with no database and no `alembic` install. It does **not**
prove the migration applies — that needs PostgreSQL and is an external step. It proves
the operation *order* is possible, which is what failed.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

#: Helpers that operate on an already-created table, mapped to what they need.
_REQUIRES_EXISTING_TABLE = {
    "_create_daily_partitions": "partition",
    "_create_identity_indexes": "index",
    "create_index": None,  # resolved from the second argument
    "create_foreign_key": None,
}

__all__ = ["check_migration"]


def _module_tuples(tree: ast.Module) -> dict[str, list[str]]:
    """Module-level tuple/list literals of strings, e.g. `_PARTITIONED`."""
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
            out[target.id] = list(value)
    return out


def _string_args(node: ast.Call) -> list[str]:
    return [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _revision_ids(path: Path) -> tuple[str | None, str | None]:
    """`(revision, down_revision)` declared at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, str | None] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                value = node.value
                found[target.id] = value.value if isinstance(value, ast.Constant) else None
    return found.get("revision"), found.get("down_revision")


def chain_order(paths: list[Path]) -> list[Path]:
    """Revisions in chain order, following `down_revision`.

    Filename order is not chain order: `0001_...` sorts before `001_...` because
    `'1' < '_'`, which would put the Phase 1 revision ahead of the legacy base it
    depends on. Accumulating "already created" tables across revisions only makes
    sense along the real chain, so the chain is what this follows.
    """
    by_down: dict[str | None, Path] = {}
    for path in paths:
        _, down = _revision_ids(path)
        by_down[down] = path

    ordered: list[Path] = []
    cursor: str | None = None
    seen: set[str] = set()
    while cursor in by_down:
        path = by_down[cursor]
        ordered.append(path)
        revision, _ = _revision_ids(path)
        if revision is None or revision in seen:
            break
        seen.add(revision)
        cursor = revision
    # Anything not reachable along the chain is still checked, just last -- a
    # detached revision is check_migration_chain's problem to report, not ours,
    # and silently skipping it here would hide its ordering bugs too.
    ordered.extend(p for p in paths if p not in ordered)
    return ordered


def check_migration(path: Path, *, existing: frozenset[str] = frozenset()) -> list[str]:
    """Check one revision.

    `existing` is the set of tables created by earlier revisions in the chain. A
    revision may legitimately alter a table an earlier one created -- Phase 9 adds
    a column and a foreign key to the Phase 8 `trade_orders` -- and flagging that
    would force every cross-revision change to be avoided. Within a revision the
    original rule is unchanged: a table must be created before it is used.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tuples = _module_tuples(tree)
    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if upgrade is None:
        return [f"{path.name}: no upgrade() function"]

    problems: list[str] = []
    #: Seeded with what earlier revisions built, so an ALTER of an existing table
    #: is permitted while a use-before-create inside this revision is still caught.
    created: set[str] = set(existing)
    created_here: set[str] = set()
    partitioned: set[str] = set()
    indexed: set[str] = set()
    name = path.name

    def resolve(node: ast.AST, loop_vars: dict[str, list[str]]) -> list[str]:
        """Table names a call refers to, expanding a loop variable when needed."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name) and node.id in loop_vars:
            return loop_vars[node.id]
        return []

    def walk(body: list[ast.stmt], loop_vars: dict[str, list[str]]) -> None:
        for stmt in body:
            # Expand `for table in _PARTITIONED:` so the loop body is checked against
            # the real table names rather than skipped.
            if isinstance(stmt, ast.For):
                bound = dict(loop_vars)
                if isinstance(stmt.target, ast.Name):
                    values: list[str] = []
                    if isinstance(stmt.iter, ast.Name):
                        values = tuples.get(stmt.iter.id, [])
                    elif isinstance(stmt.iter, ast.Tuple | ast.List):
                        values = [
                            e.value
                            for e in stmt.iter.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                    if values:
                        bound[stmt.target.id] = values
                walk(stmt.body, bound)
                continue

            for node in ast.walk(stmt):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                fname = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else (func.id if isinstance(func, ast.Name) else None)
                )
                if fname is None:
                    continue

                if fname == "create_table":
                    for table in _string_args(node)[:1] or resolve(
                        node.args[0] if node.args else ast.Constant(None), loop_vars
                    ):
                        if table in created_here:
                            problems.append(f"{name}:{node.lineno}: {table} created twice")
                        created.add(table)
                        created_here.add(table)
                    continue

                if fname not in _REQUIRES_EXISTING_TABLE:
                    continue

                # create_index(name, table, ...) -> the table is the second argument.
                targets: list[str] = []
                if fname in ("create_index", "create_foreign_key"):
                    if len(node.args) >= 2:
                        targets = resolve(node.args[1], loop_vars)
                elif node.args:
                    targets = resolve(node.args[0], loop_vars)

                kind = _REQUIRES_EXISTING_TABLE[fname]
                for table in targets:
                    if table not in created:
                        problems.append(
                            f"{name}:{node.lineno}: {fname}() targets {table!r} before it is "
                            f"created. PostgreSQL fails with 'relation \"{table}\" does not exist'."
                        )
                    if kind == "partition":
                        if table in partitioned:
                            problems.append(f"{name}:{node.lineno}: {table} partitioned twice")
                        partitioned.add(table)
                    elif kind == "index":
                        if table in indexed:
                            problems.append(
                                f"{name}:{node.lineno}: {table} indexed twice "
                                f"(duplicate index names will fail)"
                            )
                        indexed.add(table)

    walk(upgrade.body, {})

    # Every declared partitioned table must actually get partitions.
    for table in tuples.get("_PARTITIONED", []):
        if table in created and table not in partitioned:
            problems.append(
                f"{name}: {table} is in _PARTITIONED but never partitioned; "
                f"inserts outside any partition range will fail"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    versions = Path(args.root) / "oipulse/migrations/versions"
    if not versions.exists():
        print(f"check_migration_order: no such path {versions}", file=sys.stderr)
        return 2

    problems: list[str] = []
    checked = 0
    built: set[str] = set()
    for path in chain_order(sorted(versions.glob("0*.py"))):
        problems.extend(check_migration(path, existing=frozenset(built)))
        # Accumulate for the next revision in the chain.
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                built.add(str(node.args[0].value))
        checked += 1

    if problems:
        print("FAIL  migration operation ordering:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"PASS  migration operation ordering across {checked} revision(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
