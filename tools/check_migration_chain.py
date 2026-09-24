#!/usr/bin/env python3
"""Guard: the Alembic revision DAG is a single, valid, linear chain.

Motivated by a real failure. The live database carries `alembic_version = '002'` from the
legacy `backend/alembic` chain. The v2 chain was authored with
`down_revision = None`, creating a **second root in the same version table**, so
`alembic upgrade head` could not locate '002' and every upgrade failed. Nothing in the
test suite caught it, because nothing examined the DAG.

This does. It parses every revision file across all configured `version_locations` and
asserts:

* exactly one root (a revision with `down_revision = None`)
* exactly one head (a revision nothing depends on)
* every `down_revision` resolves to a revision that exists
* no duplicate revision ids
* no cycles

It needs no database and no `alembic` install, so it runs in CI on a bare interpreter
alongside the other Phase 1 guards. It does **not** prove the migrations apply — that
requires PostgreSQL and is an external verification step.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Revision", "build_chain", "load_version_locations"]


@dataclass(frozen=True)
class Revision:
    revision: str
    down_revision: str | None
    path: Path


def _literal(tree: ast.Module, name: str) -> object | None:
    """Read a module-level literal assignment, ignoring annotations."""
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name and value is not None:
                try:
                    return ast.literal_eval(value)
                except ValueError:
                    return None
    return None


def load_version_locations(ini_path: Path) -> list[Path]:
    """Read `version_locations`, falling back to `script_location/versions`."""
    parser = configparser.ConfigParser()
    parser.read(ini_path)
    root = ini_path.parent

    raw = parser.get("alembic", "version_locations", fallback="").strip()
    if raw:
        return [root / part for part in raw.replace(",", " ").split()]

    script = parser.get("alembic", "script_location", fallback="alembic").strip()
    return [root / script / "versions"]


def _collect(locations: list[Path]) -> tuple[list[Revision], list[str]]:
    revisions: list[Revision] = []
    problems: list[str] = []

    for location in locations:
        if not location.exists():
            problems.append(f"version_location does not exist: {location}")
            continue
        for path in sorted(location.glob("*.py")):
            if path.name.startswith("__"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                problems.append(f"{path}:{exc.lineno}: syntax error, cannot verify")
                continue
            rev = _literal(tree, "revision")
            if not isinstance(rev, str):
                problems.append(f"{path}: no module-level `revision` string")
                continue
            down = _literal(tree, "down_revision")
            if down is not None and not isinstance(down, str):
                problems.append(f"{path}: `down_revision` is neither a string nor None")
                down = None
            revisions.append(Revision(rev, down, path))

    return revisions, problems


def build_chain(locations: list[Path]) -> tuple[list[Revision], list[str]]:
    """Validate the DAG. Returns (ordered chain, problems)."""
    revisions, problems = _collect(locations)
    if not revisions:
        return [], [*problems, "no revision files found"]

    by_id: dict[str, Revision] = {}
    for rev in revisions:
        if rev.revision in by_id:
            problems.append(
                f"duplicate revision id {rev.revision!r}: "
                f"{by_id[rev.revision].path.name} and {rev.path.name}"
            )
            continue
        by_id[rev.revision] = rev

    for rev in by_id.values():
        if rev.down_revision is not None and rev.down_revision not in by_id:
            problems.append(
                f"{rev.path.name}: down_revision {rev.down_revision!r} does not exist. "
                f"If the database is already at that revision, upgrade will fail with "
                f'"Can\'t locate revision".'
            )

    roots = [r for r in by_id.values() if r.down_revision is None]
    if len(roots) != 1:
        names = ", ".join(sorted(r.revision for r in roots)) or "(none)"
        problems.append(
            f"expected exactly 1 root, found {len(roots)}: {names}. "
            f"Multiple roots in one alembic_version table is an ambiguous parallel chain."
        )

    depended_on = {r.down_revision for r in by_id.values() if r.down_revision}
    heads = [r for r in by_id.values() if r.revision not in depended_on]
    if len(heads) != 1:
        names = ", ".join(sorted(r.revision for r in heads)) or "(none)"
        problems.append(f"expected exactly 1 head, found {len(heads)}: {names}")

    # Walk from the root; a short walk means a cycle or a detached branch.
    ordered: list[Revision] = []
    if len(roots) == 1:
        children: dict[str | None, list[Revision]] = {}
        for rev in by_id.values():
            children.setdefault(rev.down_revision, []).append(rev)

        cursor: Revision | None = roots[0]
        seen: set[str] = set()
        while cursor is not None:
            if cursor.revision in seen:
                problems.append(f"cycle detected at revision {cursor.revision!r}")
                break
            seen.add(cursor.revision)
            ordered.append(cursor)
            nxt = children.get(cursor.revision, [])
            if len(nxt) > 1:
                branches = ", ".join(sorted(r.revision for r in nxt))
                problems.append(f"branch after {cursor.revision!r}: {branches}")
                break
            cursor = nxt[0] if nxt else None

        if len(ordered) != len(by_id) and not problems:
            problems.append(
                f"chain reaches {len(ordered)} of {len(by_id)} revisions; "
                f"some are detached from the root"
            )

    return ordered, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ini", default="alembic.ini")
    args = parser.parse_args()

    ini = Path(args.ini)
    if not ini.exists():
        print(f"check_migration_chain: no such file {ini}", file=sys.stderr)
        return 2

    locations = load_version_locations(ini)
    ordered, problems = build_chain(locations)

    if problems:
        print("FAIL  alembic revision chain:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"PASS  single alembic chain, {len(ordered)} revisions, one head")
    for index, rev in enumerate(ordered):
        arrow = "   " if index == 0 else "-> "
        print(f"  {arrow}{rev.revision:<26} {rev.path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
