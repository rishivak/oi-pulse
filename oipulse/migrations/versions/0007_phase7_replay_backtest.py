"""Phase 7: replay runs, backtest results, trades and replay-scoped events.

`docs/design/10-REPLAY.md` §2, §6 and §8; `18-ROADMAP.md` Phase 7.

Three constraints carry the architecture rather than merely tidying the schema.

* `backtest_results.content_hash` is `UNIQUE`. Re-running the same strategy over the
  same period under the same fill model produces the same artifact, not a second row.
* `backtest_trades` is checked so an unfilled trade cannot carry a fill time and a
  fill cannot exceed the quantity requested. Both are states the runner never
  produces; the constraint is there so a future writer cannot introduce them quietly.
* `replay_events.run_id` is `NOT NULL`. Replay-derived events live in their own
  namespace and are structurally incapable of reaching the live outbox (§8), so a
  replay can never fire a real alert or a real order.

**Nothing here is partitioned.** These are decision artifacts -- immutable, small and
retained (`02` §9). A time partition would imply a pruning story that must not exist.

**No paper-trading, order, position or portfolio table is created.** Those belong to
Phases 8, 10 and 11. `backtest_trades` records simulated fills inside a backtest run
and is scoped by `run_id`; it is not an order book.

Ordering follows PostgreSQL's requirement and the Phase 2/3 guard: parent tables ->
constraints -> indexes. No partitions exist at this revision.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0007_phase7_replay_backtest
Revises: 0006_phase6_research
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_phase7_replay_backtest"
down_revision = "0006_phase6_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "replay_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("build_context_id", sa.Text, nullable=False),
        sa.Column("underlying_ids", postgresql.JSONB, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step_mode", sa.Text, nullable=False),
        sa.Column("knowledge_mode", sa.Text, nullable=False),
        # NULL under lockstep, where K is derived per step rather than pinned.
        sa.Column("pinned_knowledge_horizon", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "feature_versions",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "rule_versions", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("last_step_index", sa.Integer, nullable=True),
        sa.Column("last_market_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carried_state_digest", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", name="uq_replay_runs_run_id"),
        sa.CheckConstraint("period_end >= period_start", name="ck_replay_runs_period_ordered"),
    )

    op.create_table(
        "backtest_results",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("strategy_id", sa.Text, nullable=False),
        sa.Column("strategy_version", sa.Integer, nullable=False),
        sa.Column("strategy_digest", sa.Text, nullable=False),
        sa.Column("replay_digest", sa.Text, nullable=False),
        sa.Column("build_context_id", sa.Text, nullable=False),
        sa.Column("fill_model_digest", sa.Text, nullable=False),
        sa.Column("assumptions", postgresql.JSONB, nullable=False),
        sa.Column("statistics", postgresql.JSONB, nullable=False),
        sa.Column("final_ledger", postgresql.JSONB, nullable=False),
        sa.Column("assumption_based", sa.Boolean, nullable=False),
        sa.Column("risk_evaluated", sa.Boolean, nullable=False),
        sa.Column(
            "coverage_warnings",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(18, 6), nullable=True),
        # Re-running an identical backtest yields the same artifact, not a new row.
        sa.UniqueConstraint("content_hash", name="uq_backtest_results_content_hash"),
    )

    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("intent_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.BigInteger, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("requested_quantity", sa.Integer, nullable=False),
        sa.Column("filled_quantity", sa.Integer, nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=True),
        sa.Column("reference_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_source", sa.Text, nullable=True),
        sa.Column("slippage_model", sa.Text, nullable=True),
        sa.Column("assumption_based", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("costs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= requested_quantity",
            name="ck_backtest_trades_fill_within_request",
        ),
        # An unfilled trade with a fill time, or a fill without one, is a state the
        # runner never produces. The constraint keeps it that way.
        sa.CheckConstraint(
            "(filled_quantity = 0) = (filled_at IS NULL)",
            name="ck_backtest_trades_unfilled_has_no_fill_time",
        ),
        sa.UniqueConstraint(
            "run_id", "intent_id", "filled_quantity", "price", name="uq_backtest_trades_fill"
        ),
    )

    op.create_table(
        "replay_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        # NOT NULL: this is the namespace separation from the live outbox (§8).
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("aggregate_type", sa.Text, nullable=False),
        sa.Column("aggregate_id", sa.Text, nullable=False),
        sa.Column("aggregate_sequence", sa.BigInteger, nullable=False),
        sa.Column("market_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_sequence",
            name="uq_replay_events_sequence",
        ),
    )

    # Indexes only after every parent table exists.
    op.create_index("ix_replay_runs_digest", "replay_runs", ["content_digest"])
    op.create_index(
        "ix_backtest_results_strategy", "backtest_results", ["strategy_id", "strategy_version"]
    )
    op.create_index("ix_backtest_results_run", "backtest_results", ["run_id"])
    op.create_index("ix_backtest_trades_run", "backtest_trades", ["run_id", "decision_time"])
    op.create_index("ix_replay_events_run", "replay_events", ["run_id", "market_time"])


def downgrade() -> None:
    # Reverse creation order. Only Phase 7 tables are touched; no observation,
    # checkpoint, metric, signal or research table is affected.
    op.drop_table("replay_events")
    op.drop_table("backtest_trades")
    op.drop_table("backtest_results")
    op.drop_table("replay_runs")
