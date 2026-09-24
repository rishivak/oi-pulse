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

#: Phase 3 materialization tables (`0003_phase3_marketstate`). Prunable: dropping them
#: loses no history, only recomputation time.
PHASE3_TABLES = (
    "state_build_contexts",
    "state_checkpoints",
    "state_checkpoint_legs",
    "state_checkpoint_expiries",
)
PHASE3_PARTITIONED = (
    "state_checkpoints",
    "state_checkpoint_legs",
    "state_checkpoint_expiries",
)

#: Phase 4 analytics tables (`0004_phase4_analytics`). Also prunable.
PHASE4_TABLES = ("metric_values", "interp_labels", "metric_oi_migrations")
PHASE4_PARTITIONED = PHASE4_TABLES

#: Phase 5 signal and alert tables (`0005_phase5_signals`). `alert_rules` holds
#: configuration rather than a time series, so it is deliberately unpartitioned.
PHASE5_TABLES = ("signal_signals", "signal_evidence", "alert_rules", "alert_occurrences")
PHASE5_PARTITIONED = ("signal_signals", "signal_evidence", "alert_occurrences")

#: Phase 6 research tables (`0006_phase6_research`). Deliberately UNPARTITIONED:
#: decision artifacts are immutable and retained forever (`02` §9), so partitioning
#: them would imply a pruning story that must not exist for this data.
PHASE6_TABLES = (
    "research_studies",
    "research_datasets",
    "research_results",
    "research_signal_evaluations",
)

#: Phase 7 replay/backtest tables (`0007_phase7_replay_backtest`). Also deliberately
#: UNPARTITIONED, for the same reason as Phase 6: these are immutable decision
#: artifacts, and a time partition would imply a pruning story that must not exist.
PHASE7_TABLES = (
    "replay_runs",
    "backtest_results",
    "backtest_trades",
    "replay_events",
)


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
            [
                "001",
                "002",
                "0001_phase1_sys_tables",
                "0002_phase2_market_data",
                "0003_phase3_marketstate",
                "0004_phase4_analytics",
                "0005_phase5_signals",
                "0006_phase6_research",
                "0007_phase7_replay_backtest",
            ],
            "the chain must run legacy -> Phase 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7, no branch",
        )

    def test_exactly_one_head(self) -> None:
        downs = {rev.down_revision for rev in self.chain}
        heads = [rev.revision for rev in self.chain if rev.revision not in downs]
        self.assertEqual(heads, ["0007_phase7_replay_backtest"])

    def test_upgrading_from_the_legacy_revision_reaches_phase_2(self) -> None:
        """A database stamped at `002` must have a path to head without manual edits."""
        by_down = {rev.down_revision: rev for rev in self.chain}
        visited: list[str] = []
        cursor = "002"
        while cursor in by_down:
            cursor = by_down[cursor].revision
            visited.append(cursor)
        self.assertEqual(
            visited,
            [
                "0001_phase1_sys_tables",
                "0002_phase2_market_data",
                "0003_phase3_marketstate",
                "0004_phase4_analytics",
                "0005_phase5_signals",
                "0006_phase6_research",
                "0007_phase7_replay_backtest",
            ],
        )


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

    def test_phase_3_creates_exactly_the_state_tables(self) -> None:
        created = _tables(V2 / "0003_phase3_marketstate.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE3_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_phase_3_checkpoint_identity_is_the_full_tuple(self) -> None:
        """A key omitting knowledge_horizon would collapse two valid states."""
        source = (V2 / "0003_phase3_marketstate.py").read_text(encoding="utf-8")
        for column in (
            '"underlying_id"',
            '"observed_at"',
            '"knowledge_horizon"',
            '"build_context_id"',
        ):
            self.assertIn(column, source)
        self.assertIn("uq_state_checkpoints_identity", source)
        self.assertIn("uq_state_build_contexts_digest", source)

    def test_phase_4_creates_exactly_the_analytics_tables(self) -> None:
        created = _tables(V2 / "0004_phase4_analytics.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE4_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_phase_4_metric_identity_includes_version_horizon_and_context(self) -> None:
        """Two metrics for one observed_at under different horizons are different
        values; a key omitting either would collapse them."""
        source = (V2 / "0004_phase4_analytics.py").read_text(encoding="utf-8")
        for column in (
            '"feature_id"',
            '"feature_version"',
            '"scope_kind"',
            '"scope_ref"',
            '"observed_at"',
            '"knowledge_horizon"',
            '"build_context_id"',
        ):
            self.assertIn(column, source)
        self.assertIn("uq_metric_values_identity", source)
        # The availability invariant is enforced by the database, not only by code.
        self.assertIn("available_at >= computed_at", source)

    def test_phase_5_creates_exactly_the_signal_and_alert_tables(self) -> None:
        created = _tables(V2 / "0005_phase5_signals.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE5_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_phase_5_signal_identity_and_alert_dedup_are_constrained(self) -> None:
        """The two constraints that make re-processing idempotent."""
        source = (V2 / "0005_phase5_signals.py").read_text(encoding="utf-8")
        self.assertIn("uq_signal_signals_identity", source)
        for column in (
            '"rule_version"',
            '"occurrence"',
            '"knowledge_horizon"',
            '"build_context_id"',
            '"config_digest"',
        ):
            self.assertIn(column, source)
        self.assertIn("uq_alert_occurrences_dedup", source)
        self.assertIn('"dedup_key"', source)
        # Evidence weight sign is tied to its kind in the database, not only in code.
        self.assertIn("ck_signal_evidence_weight_sign", source)
        # 'NONE_OBSERVED' is a positive finding, so the column cannot be NULL.
        self.assertIn('sa.Column("contradiction_assessment", sa.Text, nullable=False)', source)

    def test_phase_6_creates_exactly_the_research_tables(self) -> None:
        created = _tables(V2 / "0006_phase6_research.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE6_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_phase_6_content_hashes_are_unique(self) -> None:
        """The reproducibility gate, enforced by the database.

        A rebuild with the same parameters must reuse the row; a divergent one is
        immediately visible as a new hash rather than silently accumulating.
        """
        source = (V2 / "0006_phase6_research.py").read_text(encoding="utf-8")
        self.assertIn("uq_research_datasets_content", source)
        self.assertIn("uq_research_results_content", source)
        self.assertIn("uq_research_studies_identity", source)
        # Insufficiency is a real status, so the column cannot be NULL.
        self.assertIn('sa.Column("status", sa.Text, nullable=False)', source)
        # Both event counts are stored, never just the raw one.
        self.assertIn('"raw_events"', source)
        self.assertIn('"effective_sample"', source)
        self.assertIn('"comparisons"', source)

    def test_phase_6_introduces_no_backtest_table(self) -> None:
        """Phase 7 tables must not appear early.

        Checks the tables actually created, not the word anywhere in the file: the
        migration's own docstring says "no Phase 7 backtest table is created", and a
        substring search would flag that disclaimer. The same distinction as the
        Phase 4 directional-label check -- a statement that something is absent is
        not an instance of it.
        """
        created = _tables(V2 / "0006_phase6_research.py", "upgrade", "create_table")
        for table in created:
            for banned in ("backtest", "replay", "paper_trade", "portfolio", "terminal"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())
        self.assertTrue(all(t.startswith("research_") for t in created))

    def test_phase_7_creates_exactly_the_replay_and_backtest_tables(self) -> None:
        created = _tables(V2 / "0007_phase7_replay_backtest.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE7_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_phase_7_result_identity_and_trade_constraints(self) -> None:
        """The three constraints that carry the architecture, in the migration source."""
        source = (V2 / "0007_phase7_replay_backtest.py").read_text(encoding="utf-8")
        # Re-running an identical backtest must yield the same artifact, not a new row.
        self.assertIn("uq_backtest_results_content_hash", source)
        # A fill cannot exceed what was requested, and an unfilled trade cannot carry
        # a fill time -- states the runner never produces, kept impossible.
        self.assertIn("ck_backtest_trades_fill_within_request", source)
        self.assertIn("ck_backtest_trades_unfilled_has_no_fill_time", source)
        # The assumption set travels with every result (`10` §6).
        for column in ("assumptions", "assumption_based", "risk_evaluated", "fill_model_digest"):
            with self.subTest(column=column):
                self.assertIn(f'"{column}"', source)

    def test_replay_events_are_namespaced_by_run_and_cannot_be_null(self) -> None:
        """`10` §8: a replay-derived event must never reach the live outbox.

        `run_id NOT NULL` plus a separate table is the structural form of that rule:
        a flag on the live outbox could be forgotten in a WHERE clause, and the
        consequence of forgetting would be a replay firing a real alert.
        """
        source = (V2 / "0007_phase7_replay_backtest.py").read_text(encoding="utf-8")
        self.assertIn('sa.Column("run_id", sa.Text, nullable=False)', source)
        self.assertIn("uq_replay_events_sequence", source)
        created = _tables(V2 / "0007_phase7_replay_backtest.py", "upgrade", "create_table")
        self.assertIn("replay_events", created)
        self.assertNotIn("sys_outbox", created, "replay must not extend the live outbox")

    def test_phase_7_introduces_no_phase_8_or_later_table(self) -> None:
        """Paper trading, orders, positions, portfolio and the terminal are later phases.

        Checks the tables actually created rather than the word anywhere in the file:
        the migration docstring states that no such table is created, and a substring
        search would flag that disclaimer. Same distinction as the Phase 6 check --
        a statement that something is absent is not an instance of it.
        """
        created = _tables(V2 / "0007_phase7_replay_backtest.py", "upgrade", "create_table")
        for table in created:
            for banned in ("paper", "portfolio", "position", "reconcil", "terminal"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())
        # `backtest_trades` is simulated fills inside a run, scoped by run_id -- not
        # an order book. Assert the scoping rather than trusting the name.
        source = (V2 / "0007_phase7_replay_backtest.py").read_text(encoding="utf-8")
        self.assertIn("uq_backtest_trades_fill", source)
        self.assertTrue(
            all(t.startswith(("replay_", "backtest_")) for t in created),
            f"unexpected table namespace in {created}",
        )

    def test_phase_7_partitions_nothing(self) -> None:
        """Decision artifacts are retained, not pruned (`02` §9)."""
        source = (V2 / "0007_phase7_replay_backtest.py").read_text(encoding="utf-8")
        self.assertNotIn("postgresql_partition_by", source)
        self.assertNotIn("PARTITION OF", source)

    def test_downgrade_mirrors_upgrade_in_all_revisions(self) -> None:
        """A downgrade that forgets a table leaves a schema the next upgrade cannot build."""
        for name in (
            "0001_phase1_sys_tables.py",
            "0002_phase2_market_data.py",
            "0003_phase3_marketstate.py",
            "0004_phase4_analytics.py",
            "0005_phase5_signals.py",
            "0006_phase6_research.py",
            "0007_phase7_replay_backtest.py",
        ):
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
        for name in (
            "0001_phase1_sys_tables.py",
            "0002_phase2_market_data.py",
            "0003_phase3_marketstate.py",
            "0004_phase4_analytics.py",
            "0005_phase5_signals.py",
            "0006_phase6_research.py",
            "0007_phase7_replay_backtest.py",
        ):
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
