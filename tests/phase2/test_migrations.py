"""Migration tests — P0-3.

External verification found that migration 0002 partitioned `obs_depth` before creating
it, so `alembic upgrade head` failed with `relation "obs_depth" does not exist`. The
existing tests could not have found it: they exercised the ORM schema and the in-memory
store, and nothing examined the migration as an ordered program.

**Two layers, and the split is the honest part.**

* This module is structural and runs on a bare interpreter with no database. It reads
  the migration source and asserts what can be asserted statically: the chain, the
  operation order, the table inventory, upgrade/downgrade symmetry, partition and index
  coverage, and agreement with `store/schema.py`.
* `tests/integration/test_migrations_postgres.py` actually applies the chain to a real
  PostgreSQL and introspects the result. It skips when `DATABASE_URL` is unset, which is
  the case in the development sandbox — no PostgreSQL is reachable here. CI runs it
  against a `postgres:16` service, where it does not skip.

Neither layer alone is sufficient and the report says so. Structural tests cannot prove
the DDL is accepted; the integration tests cannot run where there is no database.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.check_migration_chain import build_chain, load_version_locations
from tools.check_migration_order import check_migration

V2 = REPO / "oipulse/migrations/versions"
LEGACY = REPO / "backend/alembic/versions"

#: The inventory each revision owns. Written out rather than derived, so a table
#: appearing or disappearing is a deliberate edit to this list and shows up in review.
PHASE1_TABLES = ("sys_outbox", "sys_event_inbox", "sys_retention_locks")
PHASE2_TABLES = (
    "instrument_instruments",
    "instrument_expiries",
    "instrument_options",
    "instrument_futures",
    "instrument_versions",
    "instrument_vendor_mappings",
    "instrument_universes",
    "obs_quotes",
    "obs_greeks",
    "obs_depth",
    "obs_ohlc",
    "obs_index",
    "obs_historical_oi",
    "chain_snapshots",
    "dq_issues",
    "feed_sessions",
)
#: Declared `PARTITION BY RANGE (observed_at)`. A missing partition must never be
#: discovered by a failed insert during market hours (`14-DEPLOYMENT.md` §4).
PARTITIONED_TABLES = ("obs_quotes", "obs_greeks", "obs_depth")


def _tables(path: Path, function: str, call: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    out: list[str] = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == call
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            out.append(node.args[0].value)
    return out


class TestMigrationChain(unittest.TestCase):
    """One linear chain, one head, across both configured version locations."""

    def setUp(self) -> None:
        self.chain, problems = build_chain(load_version_locations(REPO / "alembic.ini"))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_chain_is_linear_from_the_legacy_root(self) -> None:
        """Upgrading an existing database must not require a second alembic history.

        The live database is already stamped at legacy `002`. A v2 chain rooted at
        `down_revision = None` would be a second root in the same `alembic_version`
        table, and alembic refuses to resolve two heads.
        """
        order = [rev.revision for rev in self.chain]
        self.assertEqual(
            order,
            ["001", "002", "0001_phase1_sys_tables", "0002_phase2_market_data"],
            "the chain must run legacy -> Phase 1 -> Phase 2 with no branch",
        )

    def test_exactly_one_head(self) -> None:
        downs = {rev.down_revision for rev in self.chain}
        heads = [rev.revision for rev in self.chain if rev.revision not in downs]
        self.assertEqual(heads, ["0002_phase2_market_data"])

    def test_upgrading_from_the_legacy_revision_reaches_phase_2(self) -> None:
        """A database stamped at `002` must have a path to head without manual edits."""
        by_down = {rev.down_revision: rev for rev in self.chain}
        visited: list[str] = []
        cursor = "002"
        while cursor in by_down:
            cursor = by_down[cursor].revision
            visited.append(cursor)
        self.assertEqual(visited, ["0001_phase1_sys_tables", "0002_phase2_market_data"])


class TestOperationOrdering(unittest.TestCase):
    """PostgreSQL ordering: parent table -> constraints -> partitions -> indexes."""

    def test_no_revision_touches_a_table_before_creating_it(self) -> None:
        """The exact defect external verification hit, now gated.

        `check_migration_order` expands `for table in _PARTITIONED:` against the module
        tuple, which is where the bug hid: the loop was correct in isolation and wrong
        only in relation to the statements around it.
        """
        problems: list[str] = []
        for path in sorted(V2.glob("0*.py")):
            problems.extend(check_migration(path))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_partitioned_parent_is_partitioned_exactly_once(self) -> None:
        source = (V2 / "0002_phase2_market_data.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        declared = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "_PARTITIONED"
        )
        self.assertEqual(tuple(declared), PARTITIONED_TABLES)
        for table in PARTITIONED_TABLES:
            self.assertIn(
                'postgresql_partition_by="RANGE (observed_at)"',
                source,
                f"{table} must be declared partitioned by observed_at",
            )

    def test_partitioned_tables_are_created_before_the_partition_loop(self) -> None:
        """Source-order check, because that is the order PostgreSQL executes."""
        source = (V2 / "0002_phase2_market_data.py").read_text(encoding="utf-8")
        loop_at = source.index("for table in _PARTITIONED:")
        for table in PARTITIONED_TABLES:
            created_at = source.index(f'op.create_table(\n        "{table}"')
            self.assertLess(
                created_at,
                loop_at,
                f"{table} is partitioned before it exists; PostgreSQL fails with "
                f'relation "{table}" does not exist',
            )


class TestTableInventory(unittest.TestCase):
    """The set of tables each revision owns, and upgrade/downgrade symmetry."""

    def test_phase_1_creates_exactly_the_sys_tables(self) -> None:
        created = _tables(V2 / "0001_phase1_sys_tables.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE1_TABLES))

    def test_phase_2_creates_exactly_the_expected_tables(self) -> None:
        created = _tables(V2 / "0002_phase2_market_data.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE2_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_downgrade_mirrors_upgrade_in_both_revisions(self) -> None:
        """A downgrade that forgets a table leaves a schema the next upgrade cannot build."""
        for name in ("0001_phase1_sys_tables.py", "0002_phase2_market_data.py"):
            with self.subTest(revision=name):
                created = _tables(V2 / name, "upgrade", "create_table")
                dropped = _tables(V2 / name, "downgrade", "drop_table")
                self.assertEqual(sorted(created), sorted(dropped))
                self.assertEqual(
                    dropped,
                    list(reversed(created)),
                    "drop in reverse creation order, or foreign keys block the drop",
                )

    def test_no_legacy_table_is_dropped_or_truncated(self) -> None:
        """Rule 19/20: historical market data is never deleted by a v2 migration."""
        legacy = set(_tables(LEGACY / "001_initial_schema.py", "upgrade", "create_table"))
        legacy |= set(
            _tables(LEGACY / "002_phase2_market_data_schema.py", "upgrade", "create_table")
        )
        for name in ("0001_phase1_sys_tables.py", "0002_phase2_market_data.py"):
            source = (V2 / name).read_text(encoding="utf-8")
            for table in legacy:
                self.assertNotIn(
                    f'drop_table("{table}")',
                    source,
                    f"{name} drops legacy table {table}",
                )
            for verb in ("TRUNCATE", "DROP TABLE", "DELETE FROM"):
                self.assertNotIn(verb, source.upper(), f"{name} contains a destructive {verb}")


class TestSchemaAgreesWithMigration(unittest.TestCase):
    """`store/schema.py` and migration 0002 must describe the same tables.

    Parsed rather than imported: SQLAlchemy is not installable in this environment, and
    skipping would remove the check entirely.
    """

    def test_every_schema_table_exists_in_the_migration(self) -> None:
        schema_src = (REPO / "oipulse/marketdata/store/schema.py").read_text(encoding="utf-8")
        tree = ast.parse(schema_src)
        declared = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "Table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        created = set(_tables(V2 / "0002_phase2_market_data.py", "upgrade", "create_table"))
        missing = declared - created
        self.assertEqual(missing, set(), f"declared in schema.py but never created: {missing}")


class TestIdentityIndexes(unittest.TestCase):
    """Tiered observation identity must be enforced by the database, not by convention."""

    def test_each_observation_table_gets_the_three_identity_indexes(self) -> None:
        source = (V2 / "0002_phase2_market_data.py").read_text(encoding="utf-8")
        self.assertIn("_create_identity_indexes", source)
        for tier in ("provider_event_id", "channel_sequence", "content_digest"):
            self.assertIn(tier, source, f"no index covers the {tier} identity tier")

    def test_tier_1_uniqueness_is_scoped_by_feed_session(self) -> None:
        """A-13 stays conservative in the DDL as well as in `identity.py`.

        If provider event ids turn out to be session-scoped and the index is not, the
        second session's events collide with the first's and are silently discarded as
        duplicates — live data lost with no error and no gap recorded.
        """
        source = (V2 / "0002_phase2_market_data.py").read_text(encoding="utf-8")
        self.assertIn("COALESCE(feed_session_id", source)

    def test_instrument_observed_at_source_is_not_unique(self) -> None:
        """A timestamp is not an identity; a feed may emit several events per instant."""
        source = (V2 / "0002_phase2_market_data.py").read_text(encoding="utf-8")
        self.assertNotIn("UNIQUE (instrument_id, observed_at, source)", source)


if __name__ == "__main__":
    unittest.main()
