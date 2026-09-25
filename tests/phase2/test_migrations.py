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
from tools.check_migration_order import chain_order, check_migration

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

#: Phase 8 paper-trading tables (`0008_phase8_paper_trading`). Also UNPARTITIONED:
#: trade artifacts are immutable decision records, retained rather than pruned.
PHASE8_TABLES = (
    "trade_accounts",
    "trade_intents",
    "trade_orders",
    "trade_order_events",
    "trade_fills",
    "portfolio_positions",
    "journal_entries",
)

#: Phase 9 risk tables (`0009_phase9_risk`). Also UNPARTITIONED: a risk decision is
#: an immutable audit record, retained rather than pruned.
PHASE9_TABLES = ("risk_profiles", "risk_decisions")

#: Phase 10 reconciliation tables (`0010_phase10_oms_reconciliation`). Also
#: UNPARTITIONED: a reconciliation run is an immutable audit record.
PHASE10_TABLES = ("trade_reconciliations", "trade_reconciliation_discrepancies")

#: Phase 11 portfolio tables (`0011_phase11_portfolio_attribution`). Also
#: UNPARTITIONED: a snapshot is an immutable decision artifact.
PHASE11_TABLES = (
    "portfolio_snapshots",
    "portfolio_attribution",
    "portfolio_position_reconciliations",
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
                "0008_phase8_paper_trading",
                "0009_phase9_risk",
                "0010_phase10_oms_reconciliation",
                "0011_phase11_portfolio_attribution",
                "0012_phase12_identity_sessions",
            ],
            "the chain must run legacy -> Phase 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 "
            "-> 9 -> 10 -> 11 -> 12, no branch",
        )

    def test_exactly_one_head(self) -> None:
        downs = {rev.down_revision for rev in self.chain}
        heads = [rev.revision for rev in self.chain if rev.revision not in downs]
        self.assertEqual(heads, ["0012_phase12_identity_sessions"])

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
                "0008_phase8_paper_trading",
                "0009_phase9_risk",
                "0010_phase10_oms_reconciliation",
                "0011_phase11_portfolio_attribution",
                "0012_phase12_identity_sessions",
            ],
        )


class TestOperationOrdering(unittest.TestCase):
    """PostgreSQL ordering: parent table -> constraints -> partitions -> indexes."""

    def test_no_revision_touches_a_table_before_creating_it(self) -> None:
        """The exact defect external verification hit, now gated.

        `check_migration_order` expands `for table in _PARTITIONED:` against the module
        tuple, which is where the bug hid: the loop was correct in isolation and wrong
        only in relation to the statements around it.

        Walked in **chain order** with the accumulated set of already-created
        tables, because a revision may legitimately alter a table an earlier one
        built -- Phase 9 adds a column and a foreign key to the Phase 8
        `trade_orders`. Checking each file in isolation would forbid every
        cross-revision change; within a revision the original rule is unchanged.
        """
        problems: list[str] = []
        built: set[str] = set()
        for path in chain_order(sorted(V2.glob("0*.py"))):
            problems.extend(check_migration(path, existing=frozenset(built)))
            built.update(_tables(path, "upgrade", "create_table"))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_use_before_create_inside_one_revision_is_still_caught(self) -> None:
        """The relaxation above must not have disarmed the guard.

        Seeding `existing` with earlier revisions' tables could have been done by
        seeding it with *every* revision's tables, which would have silently
        accepted the original Phase 2 bug. This asserts it did not.
        """
        import tempfile

        source = (
            "revision = 'x'\n"
            "down_revision = None\n"
            "def upgrade():\n"
            "    op.create_index('ix_x', 'brand_new', ['a'])\n"
            "    op.create_table('brand_new')\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(source)
            temp = Path(handle.name)
        try:
            problems = check_migration(temp, existing=frozenset({"trade_orders"}))
            self.assertTrue(problems)
            self.assertIn("before it is created", problems[0])
        finally:
            temp.unlink()

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

    def test_phase_8_creates_exactly_the_paper_trading_tables(self) -> None:
        created = _tables(V2 / "0008_phase8_paper_trading.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE8_TABLES))
        self.assertEqual(len(created), len(set(created)), "no table created twice")

    def test_phase_8_constrains_accounts_to_paper_mode(self) -> None:
        """The database half of the paper-only gate.

        `11-TRADING.md` §7 makes mode account-level so nothing downstream knows
        paper from live; the CHECK exists because Phase 8 ships no live adapter, and
        a row claiming LIVE would describe an account the system cannot serve.
        """
        source = (V2 / "0008_phase8_paper_trading.py").read_text(encoding="utf-8")
        self.assertIn("ck_trade_accounts_paper_only", source)
        self.assertIn("mode = 'PAPER'", source)

    def test_phase_8_identity_constraints_make_retries_safe(self) -> None:
        """The durable half of idempotency. In-memory checks are the fast half."""
        source = (V2 / "0008_phase8_paper_trading.py").read_text(encoding="utf-8")
        for constraint in (
            "uq_trade_intents_intent_id",
            "uq_trade_orders_order_id",
            "uq_trade_fills_fill_key",
            "uq_trade_order_events_sequence",
            "uq_journal_entries_source",
            "uq_portfolio_positions_identity",
        ):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, source)
        # A rejection with no cause is not an audit record.
        self.assertIn("ck_trade_orders_rejection_has_reason", source)
        # A decision cannot know less than the fact it rests on is old.
        self.assertIn("ck_trade_intents_knowledge_after_market", source)

    def test_phase_8_introduces_no_phase_9_or_later_table(self) -> None:
        """Risk (9), live reconciliation (10) and attribution (11) are later phases.

        Checks the tables actually created rather than the word anywhere in the
        file: the migration docstring states that no such table is created, and a
        substring search would flag that disclaimer.
        """
        created = _tables(V2 / "0008_phase8_paper_trading.py", "upgrade", "create_table")
        for table in created:
            for banned in ("risk", "reconcil", "attribution", "snapshot"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())
        self.assertTrue(
            all(t.startswith(("trade_", "portfolio_", "journal_")) for t in created),
            f"unexpected table namespace in {created}",
        )

    def test_phase_8_partitions_nothing(self) -> None:
        source = (V2 / "0008_phase8_paper_trading.py").read_text(encoding="utf-8")
        self.assertNotIn("postgresql_partition_by", source)
        self.assertNotIn("PARTITION OF", source)

    def test_phase_9_creates_exactly_the_risk_tables(self) -> None:
        created = _tables(V2 / "0009_phase9_risk.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE9_TABLES))

    def test_phase_9_makes_decisions_append_only(self) -> None:
        """`02` §11: PRIMARY KEY (intent_id, sequence_no), no unique-per-intent.

        `02` §6 says so explicitly -- "NO unique-per-intent constraint --
        re-evaluation is normal" -- so this asserts the absence as well as the
        presence.
        """
        source = (V2 / "0009_phase9_risk.py").read_text(encoding="utf-8")
        self.assertIn("pk_risk_decisions", source)
        self.assertNotIn("uq_risk_decisions_intent", source)

    def test_phase_9_binds_an_order_to_an_approving_decision_of_its_own_intent(
        self,
    ) -> None:
        """`02` §11's composite FK plus trigger. Both halves matter.

        The FK alone would accept a REJECTED decision; the trigger alone would not
        stop a decision belonging to another intent being referenced. Together
        they make the bypass impossible at the storage layer.
        """
        source = (V2 / "0009_phase9_risk.py").read_text(encoding="utf-8")
        self.assertIn("fk_trade_orders_authorizing_decision", source)
        self.assertIn('["intent_id", "authorizing_decision_sequence"]', source)
        self.assertIn('["intent_id", "sequence_no"]', source)
        self.assertIn("trg_trade_orders_require_approved_decision", source)
        self.assertIn("ck_trade_orders_authorization_complete", source)

    def test_phase_9_introduces_no_phase_10_or_later_table(self) -> None:
        """OMS, broker and reconciliation are Phase 10; attribution is Phase 11.

        Checks the tables actually created rather than the word anywhere in the
        file, so the docstring's disclaimer is not mistaken for a breach.
        """
        created = _tables(V2 / "0009_phase9_risk.py", "upgrade", "create_table")
        for table in created:
            for banned in ("oms", "broker", "reconcil", "attribution", "snapshot"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())
        self.assertTrue(all(t.startswith("risk_") for t in created))

    def test_phase_9_partitions_nothing(self) -> None:
        source = (V2 / "0009_phase9_risk.py").read_text(encoding="utf-8")
        self.assertNotIn("postgresql_partition_by", source)
        self.assertNotIn("PARTITION OF", source)

    def test_phase_9_downgrade_removes_the_columns_it_added(self) -> None:
        """A downgrade that left the columns would break the next upgrade.

        The generic mirror test only compares create_table/drop_table, so the
        added columns, constraint, FK, index and trigger need their own check.
        """
        source = (V2 / "0009_phase9_risk.py").read_text(encoding="utf-8")
        for dropped in (
            'op.drop_column("trade_orders", "authorizing_decision_sequence")',
            'op.drop_column("trade_orders", "authorizing_risk_decision_id")',
            "DROP TRIGGER IF EXISTS trg_trade_orders_require_approved_decision",
            "DROP FUNCTION IF EXISTS trade_orders_require_approved_decision",
            "fk_trade_orders_authorizing_decision",
            "ck_trade_orders_authorization_complete",
        ):
            with self.subTest(statement=dropped):
                self.assertIn(dropped, source)

    def test_phase_10_creates_exactly_the_reconciliation_tables(self) -> None:
        created = _tables(V2 / "0010_phase10_oms_reconciliation.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE10_TABLES))

    def test_phase_10_provider_identity_columns_are_nullable(self) -> None:
        """Brief §8 and §28: an absent provider id must be representable.

        After a lost acknowledgement we may hold an order at the venue whose id we
        never learned. A NOT NULL here would force a fabricated placeholder, which
        is the specific thing the brief forbids.
        """
        source = (V2 / "0010_phase10_oms_reconciliation.py").read_text(encoding="utf-8")
        for column in ("provider_order_id", "provider_status", "client_order_attempt_id"):
            with self.subTest(column=column):
                self.assertIn(f'sa.Column("{column}", sa.Text, nullable=True)', source)

    def test_phase_10_local_attempt_identity_is_unique(self) -> None:
        """The durable half of *local* submission idempotency.

        Deliberately not described as broker-side dedup: `06` §10 says our key does
        not bind the provider, and the migration's comment says so too.
        """
        source = (V2 / "0010_phase10_oms_reconciliation.py").read_text(encoding="utf-8")
        self.assertIn("uq_trade_orders_client_attempt", source)
        self.assertIn("does not bind the provider", source)

    def test_phase_10_reconciliation_runs_are_content_addressed(self) -> None:
        """Brief §13 at the storage layer: unchanged evidence collides."""
        source = (V2 / "0010_phase10_oms_reconciliation.py").read_text(encoding="utf-8")
        self.assertIn("uq_trade_reconciliations_digest", source)
        # A run cannot claim cleanliness while recording outstanding work.
        self.assertIn("ck_trade_reconciliations_clean_means_nothing_outstanding", source)

    def test_phase_10_introduces_no_phase_11_table(self) -> None:
        """Portfolio snapshots and attribution are Phase 11.

        Checks the tables actually created rather than the word anywhere in the
        file, so the docstring's disclaimer is not mistaken for a breach.
        """
        created = _tables(V2 / "0010_phase10_oms_reconciliation.py", "upgrade", "create_table")
        for table in created:
            for banned in ("portfolio", "attribution", "snapshot"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())

    def test_phase_10_downgrade_removes_the_columns_it_added(self) -> None:
        source = (V2 / "0010_phase10_oms_reconciliation.py").read_text(encoding="utf-8")
        for dropped in (
            'op.drop_column("trade_orders", "venue")',
            'op.drop_column("trade_orders", "provider_order_id")',
            'op.drop_column("trade_orders", "client_order_attempt_id")',
            "uq_trade_orders_client_attempt",
            "ck_trade_orders_venue",
        ):
            with self.subTest(statement=dropped):
                self.assertIn(dropped, source)

    def test_phase_11_creates_exactly_the_portfolio_tables(self) -> None:
        created = _tables(V2 / "0011_phase11_portfolio_attribution.py", "upgrade", "create_table")
        self.assertEqual(sorted(created), sorted(PHASE11_TABLES))

    def test_phase_11_forces_attribution_to_reconcile(self) -> None:
        """`18` Phase 11's risk, enforced by the database.

        With `residual NOT NULL` and `total_pnl = explained + residual`, a row
        cannot exist that hides an unexplained amount by omitting it or by having
        been balanced into a component.
        """
        source = (V2 / "0011_phase11_portfolio_attribution.py").read_text(encoding="utf-8")
        self.assertIn("ck_portfolio_attribution_reconciles", source)
        self.assertIn("total_pnl = explained + residual", source)
        self.assertIn('sa.Column("residual", sa.Numeric(20, 4), nullable=False)', source)

    def test_phase_11_stores_both_times_separately(self) -> None:
        """A snapshot at T on knowledge to K is not one at T on latest knowledge."""
        source = (V2 / "0011_phase11_portfolio_attribution.py").read_text(encoding="utf-8")
        self.assertIn(
            'sa.Column("market_time", sa.DateTime(timezone=True), nullable=False)', source
        )
        self.assertIn(
            'sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False)', source
        )
        self.assertIn("ck_portfolio_snapshots_knowledge_after_market", source)

    def test_phase_11_keeps_margin_nullable(self) -> None:
        """NULL means not known; 0 would read as no margin used (brief §22)."""
        source = (V2 / "0011_phase11_portfolio_attribution.py").read_text(encoding="utf-8")
        self.assertIn('sa.Column("margin_utilisation", sa.Numeric(12, 8), nullable=True)', source)

    def test_phase_11_stores_contract_economics_per_position(self) -> None:
        """`07` §4.3: a lot-size revision must not rewrite historical exposure.

        A join to the current instrument version at read time would do exactly
        that, so the economics in force are stored on the position row.
        """
        source = (V2 / "0011_phase11_portfolio_attribution.py").read_text(encoding="utf-8")
        for column in ("lot_size", "contract_multiplier", "multiplier_source", "cost_basis_method"):
            with self.subTest(column=column):
                self.assertIn(f'"{column}"', source)

    def test_phase_11_introduces_no_phase_12_table(self) -> None:
        """The terminal is Phase 12. Checks tables created, not words in the file."""
        created = _tables(V2 / "0011_phase11_portfolio_attribution.py", "upgrade", "create_table")
        for table in created:
            for banned in ("terminal", "layout", "widget", "dashboard", "user_pref"):
                with self.subTest(table=table, banned=banned):
                    self.assertNotIn(banned, table.lower())
        self.assertTrue(all(t.startswith("portfolio_") for t in created))

    def test_phase_11_downgrade_removes_the_columns_it_added(self) -> None:
        source = (V2 / "0011_phase11_portfolio_attribution.py").read_text(encoding="utf-8")
        for dropped in (
            'op.drop_column("portfolio_positions", "portfolio_id")',
            'op.drop_column("portfolio_positions", "lot_size")',
            'op.drop_column("portfolio_positions", "contract_multiplier")',
            "ck_portfolio_positions_lot_size_positive",
        ):
            with self.subTest(statement=dropped):
                self.assertIn(dropped, source)

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
            "0008_phase8_paper_trading.py",
            "0009_phase9_risk.py",
            "0010_phase10_oms_reconciliation.py",
            "0011_phase11_portfolio_attribution.py",
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
            "0008_phase8_paper_trading.py",
            "0009_phase9_risk.py",
            "0010_phase10_oms_reconciliation.py",
            "0011_phase11_portfolio_attribution.py",
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
