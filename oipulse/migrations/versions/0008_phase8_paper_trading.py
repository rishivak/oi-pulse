"""Phase 8: paper accounts, intents, orders, order events, fills, positions, journal.

`docs/design/11-TRADING.md` §2, §4, §8 and §10; `18-ROADMAP.md` Phase 8.

Four constraints carry the architecture rather than merely tidying the schema.

* `trade_accounts.mode` is CHECKed to `'PAPER'`. The column exists because `11` §7
  makes mode account-level so nothing downstream knows paper from live; the CHECK
  exists because Phase 8 ships no live adapter, and a row claiming `LIVE` would
  describe an account the system cannot serve. **Phase 10 relaxes this** when it adds
  the adapter and the three documented gates -- not before.
* `trade_intents.intent_id` and `trade_orders.order_id` are `UNIQUE` and both are
  content-addressed. A retry after a restart recomputes the same id, so the database
  turns a duplicate submission into a conflict rather than a second order.
* `trade_order_events` is `UNIQUE (order_id, sequence)`. The order aggregate is
  append-only, so a redelivered transition cannot append twice.
* `trade_fills.fill_key` is `UNIQUE`. This is what makes a duplicate fill event a
  database conflict rather than a doubled position -- the in-memory ledger check is
  the fast half of that guarantee; this is the durable half.

A rejected order must carry a reason: `ck_trade_orders_rejection_has_reason`. A bare
`REJECTED` with no cause is not an audit record.

**No Phase 9+ table is created.** No `risk_profiles`, no `risk_decisions`, no
`portfolio_snapshots`, no `portfolio_attribution`, no `reconciliation_runs`. Risk is
Phase 9, live reconciliation is Phase 10 and attribution is Phase 11.
`portfolio_positions` is positions only -- a fold over fills, required by Phase 8
brief §11 -- and carries no attribution column.

**Nothing here is partitioned.** These are decision artifacts, immutable and retained
(`02` §9); a time partition would imply a pruning story that must not exist.

Ordering follows PostgreSQL's requirement and the Phase 2/3 guard: parent tables ->
constraints -> indexes. No partitions exist at this revision.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0008_phase8_paper_trading
Revises: 0007_phase7_replay_backtest
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_phase8_paper_trading"
down_revision = "0007_phase7_replay_backtest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_accounts",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("owner", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("starting_cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("config_digest", sa.Text, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", name="uq_trade_accounts_account_id"),
        # Phase 8 is paper-only. Relaxed by Phase 10, with the adapter and the gates.
        sa.CheckConstraint("mode = 'PAPER'", name="ck_trade_accounts_paper_only"),
        sa.CheckConstraint("starting_cash >= 0", name="ck_trade_accounts_cash_non_negative"),
    )

    op.create_table(
        "trade_intents",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("intent_id", sa.Text, nullable=False),
        sa.Column("client_order_intent_id", sa.Text, nullable=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_ref", sa.Text, nullable=False),
        sa.Column("legs", postgresql.JSONB, nullable=False),
        sa.Column("time_in_force", sa.Text, nullable=False),
        sa.Column("constraints", postgresql.JSONB, nullable=False),
        sa.Column("market_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rationale_ref", sa.Text, nullable=True),
        sa.Column("state_checkpoint_ref", sa.Text, nullable=True),
        sa.Column("build_context_id", sa.Text, nullable=True),
        sa.Column("signal_id", sa.Text, nullable=True),
        sa.Column("signal_version", sa.Integer, nullable=True),
        sa.Column("strategy_id", sa.Text, nullable=True),
        sa.Column("strategy_version", sa.Integer, nullable=True),
        sa.Column("reason", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.UniqueConstraint("intent_id", name="uq_trade_intents_intent_id"),
        sa.CheckConstraint(
            "knowledge_time >= market_time", name="ck_trade_intents_knowledge_after_market"
        ),
        sa.CheckConstraint(
            "decision_time >= market_time", name="ck_trade_intents_decision_after_market"
        ),
    )

    op.create_table(
        "trade_orders",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("intent_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.BigInteger, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("filled_quantity", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("limit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("reject_reason", sa.Text, nullable=True),
        sa.Column("reject_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signal_id", sa.Text, nullable=True),
        sa.Column("signal_version", sa.Integer, nullable=True),
        sa.Column("strategy_id", sa.Text, nullable=True),
        sa.Column("strategy_version", sa.Integer, nullable=True),
        sa.Column("state_checkpoint_ref", sa.Text, nullable=True),
        sa.Column("build_context_id", sa.Text, nullable=True),
        sa.Column("config_digest", sa.Text, nullable=False),
        sa.Column("execution_digest", sa.Text, nullable=True),
        sa.UniqueConstraint("order_id", name="uq_trade_orders_order_id"),
        sa.CheckConstraint("quantity > 0", name="ck_trade_orders_quantity_positive"),
        sa.CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= quantity",
            name="ck_trade_orders_fill_within_quantity",
        ),
        sa.CheckConstraint(
            "state <> 'REJECTED' OR reject_reason IS NOT NULL",
            name="ck_trade_orders_rejection_has_reason",
        ),
    )

    op.create_table(
        "trade_order_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.Text, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("from_state", sa.Text, nullable=True),
        sa.Column("to_state", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column(
            "payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_trade_order_events_sequence_positive"),
        sa.UniqueConstraint("order_id", "sequence", name="uq_trade_order_events_sequence"),
    )

    op.create_table(
        "trade_fills",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("fill_key", sa.Text, nullable=False),
        sa.Column("order_id", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.BigInteger, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("reference_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_source", sa.Text, nullable=False),
        sa.Column("slippage_model", sa.Text, nullable=False),
        sa.Column("assumption_based", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("costs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # The durable half of fill idempotency.
        sa.UniqueConstraint("fill_key", name="uq_trade_fills_fill_key"),
        sa.CheckConstraint("quantity > 0", name="ck_trade_fills_quantity_positive"),
        sa.CheckConstraint("price > 0", name="ck_trade_fills_price_positive"),
    )

    op.create_table(
        "portfolio_positions",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("instrument_id", sa.BigInteger, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("average_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fill_at", sa.DateTime(timezone=True), nullable=True),
        # Provenance of the fold (`11` §8).
        sa.Column("fills_applied", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_fill_key", sa.Text, nullable=True),
        sa.UniqueConstraint("account_id", "instrument_id", name="uq_portfolio_positions_identity"),
    )

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("entry_type", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash_delta", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "realized_pnl_delta", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("fees_delta", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("cash_after", sa.Numeric(20, 4), nullable=False),
        sa.Column("order_id", sa.Text, nullable=True),
        sa.Column("fill_key", sa.Text, nullable=True),
        sa.Column("source_event_key", sa.Text, nullable=False),
        sa.UniqueConstraint("account_id", "source_event_key", name="uq_journal_entries_source"),
    )

    # Indexes only after every parent table exists.
    op.create_index("ix_trade_intents_account", "trade_intents", ["account_id", "decision_time"])
    op.create_index("ix_trade_intents_signal", "trade_intents", ["signal_id", "signal_version"])
    op.create_index("ix_trade_orders_account_state", "trade_orders", ["account_id", "state"])
    op.create_index("ix_trade_orders_intent", "trade_orders", ["intent_id"])
    op.create_index("ix_trade_order_events_order", "trade_order_events", ["order_id", "sequence"])
    op.create_index("ix_trade_fills_order", "trade_fills", ["order_id"])
    op.create_index("ix_trade_fills_account", "trade_fills", ["account_id", "filled_at"])
    op.create_index("ix_portfolio_positions_account", "portfolio_positions", ["account_id"])
    op.create_index("ix_journal_entries_account", "journal_entries", ["account_id", "occurred_at"])


def downgrade() -> None:
    # Reverse creation order. Only Phase 8 tables are touched; no observation,
    # checkpoint, metric, signal, research or replay table is affected.
    op.drop_table("journal_entries")
    op.drop_table("portfolio_positions")
    op.drop_table("trade_fills")
    op.drop_table("trade_order_events")
    op.drop_table("trade_orders")
    op.drop_table("trade_intents")
    op.drop_table("trade_accounts")
