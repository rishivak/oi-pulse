#!/usr/bin/env python3
"""Guard: every observation kind has a table, in both the schema and the migration.

Motivated by a real failure. `DepthObservation`, `OHLCObservation` and
`IndexObservation` existed as canonical dataclasses and were required by
`docs/design/02-DATA_MODEL.md` §3, but `obs_depth`, `obs_ohlc` and `obs_index` were
absent from both `store/schema.py` and migration 0002 — so the normalizer could produce
observations with nowhere to land. `instrument_options` and `instrument_futures` were
likewise absent despite appearing in the §2 ERD.

Nothing caught it: the tests exercised quotes and greeks, and no check compared the set
of observation kinds against the set of tables.

This compares three sources by name and reports any disagreement:

1. observation dataclasses in `marketdata/observations.py`
2. tables in `marketdata/store/schema.py`
3. `op.create_table` / `op.drop_table` in the migrations

It also asserts upgrade and downgrade are mirror images, since a downgrade that forgets
a table leaves a half-dropped schema that the next upgrade cannot recreate.

Pure AST, no database and no `sqlalchemy` install required.

Exit 0 clean, 1 on any disagreement.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

#: Observation kind -> the table it must land in.
#: `MarketObservation` is the abstract envelope and has no table of its own.
KIND_TO_TABLE: dict[str, str] = {
    "QuoteObservation": "obs_quotes",
    "GreeksObservation": "obs_greeks",
    "DepthObservation": "obs_depth",
    "OHLCObservation": "obs_ohlc",
    "IndexObservation": "obs_index",
    "HistoricalDailyOI": "obs_historical_oi",
}

#: Tables required by the design that are not observation kinds.
REQUIRED_SUPPORT_TABLES: frozenset[str] = frozenset(
    {
        "instrument_instruments",
        "instrument_versions",
        "instrument_vendor_mappings",
        "instrument_expiries",
        "instrument_options",
        "instrument_futures",
        "instrument_universes",
        "chain_snapshots",
        "dq_issues",
        "feed_sessions",
    }
)

ABSTRACT = frozenset({"MarketObservation"})


def observation_classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "MarketObservation" in bases and node.name not in ABSTRACT:
            found.add(node.name)
    return found


def schema_tables(path: Path) -> set[str]:
    """Names passed as the first argument to `sa.Table(...)`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def migration_tables(paths: list[Path]) -> tuple[set[str], set[str]]:
    """Return (created, dropped) across every migration."""
    created: set[str] = set()
    dropped: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            name = node.args[0].value
            if not isinstance(name, str):
                continue
            if node.func.attr == "create_table":
                created.add(name)
            elif node.func.attr == "drop_table":
                dropped.add(name)
    return created, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root)

    observations = root / "oipulse/marketdata/observations.py"
    schema = root / "oipulse/marketdata/store/schema.py"
    migrations = sorted((root / "oipulse/migrations/versions").glob("0*.py"))

    for required in (observations, schema):
        if not required.exists():
            print(f"check_schema_parity: missing {required}", file=sys.stderr)
            return 2

    kinds = observation_classes(observations)
    tables = schema_tables(schema)
    created, dropped = migration_tables(migrations)

    problems: list[str] = []

    unmapped = kinds - set(KIND_TO_TABLE)
    if unmapped:
        problems.append(
            f"observation kinds with no table mapping: {', '.join(sorted(unmapped))}. "
            f"Add them to KIND_TO_TABLE and create their tables."
        )

    for kind in sorted(kinds & set(KIND_TO_TABLE)):
        table = KIND_TO_TABLE[kind]
        if table not in tables:
            problems.append(f"{kind} has no table in store/schema.py (expected {table})")
        if table not in created:
            problems.append(f"{kind} has no table in any migration (expected {table})")

    for table in sorted(REQUIRED_SUPPORT_TABLES):
        if table not in created:
            problems.append(f"required table {table} is not created by any migration")

    # Phase 2 tables only: earlier revisions legitimately create their own.
    phase2_created = {
        t for t in created if t.startswith(("obs_", "instrument_", "chain_", "dq_", "feed_"))
    }
    not_dropped = phase2_created - dropped
    if not_dropped:
        problems.append(
            f"created but never dropped, so downgrade leaves a half-dropped schema: "
            f"{', '.join(sorted(not_dropped))}"
        )
    dropped_not_created = {
        t for t in dropped if t.startswith(("obs_", "instrument_", "chain_", "dq_", "feed_"))
    } - created
    if dropped_not_created:
        problems.append(f"dropped but never created: {', '.join(sorted(dropped_not_created))}")

    if problems:
        print("FAIL  schema parity:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(
        f"PASS  schema parity: {len(kinds)} observation kinds, "
        f"{len(created)} tables created, upgrade/downgrade mirrored"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
