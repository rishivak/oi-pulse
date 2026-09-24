"""Tables for paper accounts, intents, orders, order events, fills and positions.

`docs/design/11-TRADING.md` §2, §4, §8 and §10; `18-ROADMAP.md` Phase 8 schema:
`trade_accounts`, `trade_intents`, `trade_orders`, `trade_order_events`,
`trade_fills`, `portfolio_positions`, `journal_entries`.

**Why these live in `persistence/`.** `trading/*` is pure for the same reason
`analytics/*`, `research/*` and `backtest/*` are: a table declaration in the trading
layer puts a database one import away from the execution path, and the paper runtime
must be runnable — and testable — without one. The import contract enforces it.

Four properties of the schema carry architectural weight.

**Identity is deterministic, and the database enforces it.** `trade_intents.intent_id`
and `trade_orders.order_id` are both `UNIQUE` and both content-addressed. A retry
after a restart recomputes the same id, so the constraint turns a duplicate submission
into a conflict rather than a second order. That is the durable half of idempotency;
the in-memory check is only the fast half.

**Order history is append-only.** `trade_order_events` is unique on
`(order_id, sequence)`, so a redelivered transition cannot append twice and an
out-of-order one is detectable. `11` §4: the order aggregate is event-sourced because
its history is genuinely required for audit.

**Positions are derived, and the row records what derived them.** `11` §8 requires a
position to be a fold over its fills. `portfolio_positions` therefore carries
`fills_applied` and `last_fill_id` — a stored total whose provenance cannot be checked
is exactly the drifting running total the design forbids.

**The audit chain is a join path, not a log search.** Every table from the intent
downwards carries the signal, strategy, checkpoint and build-context references, so
`11` §10's eleven questions are answerable by traversal.

`trade_accounts.mode` has a CHECK constraining it to `'PAPER'`. The column exists
because `11` §7 makes mode account-level; the constraint exists because Phase 8 has no
live adapter, and a row claiming `LIVE` would describe an account the system cannot
serve. Phase 10 relaxes the constraint when it adds the adapter and the gates.

Nothing here is partitioned. These are decision artifacts -- immutable, small and
retained (`02` §9) -- and a time partition would imply a pruning story that must not
exist for them.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "TRADING_TABLES",
    "journal_entries",
    "portfolio_positions",
    "trade_accounts",
    "trade_fills",
    "trade_intents",
    "trade_order_events",
    "trade_orders",
]

METADATA = sa.MetaData()

TRADING_TABLES: list[str] = [
    "trade_accounts",
    "trade_intents",
    "trade_orders",
    "trade_order_events",
    "trade_fills",
    "portfolio_positions",
    "journal_entries",
]

trade_accounts = sa.Table(
    "trade_accounts",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("account_id", sa.Text, nullable=False, unique=True),
    sa.Column("owner", sa.Text, nullable=False),
    # `11` §7: mode is account-level so nothing downstream knows paper from live.
    # Constrained to PAPER because Phase 8 ships no live adapter.
    sa.Column("mode", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("label", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("starting_cash", sa.Numeric(20, 4), nullable=False),
    sa.Column("config", postgresql.JSONB, nullable=False),
    sa.Column("config_digest", sa.Text, nullable=False),
    sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("mode = 'PAPER'", name="ck_trade_accounts_paper_only"),
    sa.CheckConstraint("starting_cash >= 0", name="ck_trade_accounts_cash_non_negative"),
)

trade_intents = sa.Table(
    "trade_intents",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    # Content-addressed. UNIQUE, so a reprocessed decision conflicts rather than
    # creating a second intent.
    sa.Column("intent_id", sa.Text, nullable=False, unique=True),
    sa.Column("client_order_intent_id", sa.Text, nullable=True),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("source_ref", sa.Text, nullable=False),
    sa.Column("legs", postgresql.JSONB, nullable=False),
    sa.Column("time_in_force", sa.Text, nullable=False),
    sa.Column("constraints", postgresql.JSONB, nullable=False),
    # The three times, all stored, all distinct (`05` §1).
    sa.Column("market_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
    # The audit references (`11` §2, §10).
    sa.Column("rationale_ref", sa.Text, nullable=True),
    sa.Column("state_checkpoint_ref", sa.Text, nullable=True),
    sa.Column("build_context_id", sa.Text, nullable=True),
    sa.Column("signal_id", sa.Text, nullable=True),
    sa.Column("signal_version", sa.Integer, nullable=True),
    sa.Column("strategy_id", sa.Text, nullable=True),
    sa.Column("strategy_version", sa.Integer, nullable=True),
    sa.Column("reason", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column(
        "evidence_refs", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    ),
    sa.Column("content_digest", sa.Text, nullable=False),
    # A decision cannot know less than the fact it rests on is old.
    sa.CheckConstraint(
        "knowledge_time >= market_time", name="ck_trade_intents_knowledge_after_market"
    ),
    sa.CheckConstraint(
        "decision_time >= market_time", name="ck_trade_intents_decision_after_market"
    ),
    sa.Index("ix_trade_intents_account", "account_id", "decision_time"),
    sa.Index("ix_trade_intents_signal", "signal_id", "signal_version"),
)

trade_orders = sa.Table(
    "trade_orders",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    # Derived from (intent_id, leg_index, account_id). UNIQUE, so a retry after a
    # restart is a conflict rather than a second order.
    sa.Column("order_id", sa.Text, nullable=False, unique=True),
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
    # Which execution assumptions were in force. A fill price without them is not
    # interpretable later (`10` §6).
    sa.Column("config_digest", sa.Text, nullable=False),
    sa.Column("execution_digest", sa.Text, nullable=True),
    sa.CheckConstraint("quantity > 0", name="ck_trade_orders_quantity_positive"),
    sa.CheckConstraint(
        "filled_quantity >= 0 AND filled_quantity <= quantity",
        name="ck_trade_orders_fill_within_quantity",
    ),
    # A rejection must say why. A bare REJECTED is not an audit record.
    sa.CheckConstraint(
        "state <> 'REJECTED' OR reject_reason IS NOT NULL",
        name="ck_trade_orders_rejection_has_reason",
    ),
    sa.Index("ix_trade_orders_account_state", "account_id", "state"),
    sa.Index("ix_trade_orders_intent", "intent_id"),
)

trade_order_events = sa.Table(
    "trade_order_events",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("order_id", sa.Text, nullable=False),
    sa.Column("sequence", sa.Integer, nullable=False),
    sa.Column("from_state", sa.Text, nullable=True),
    sa.Column("to_state", sa.Text, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("trigger", sa.Text, nullable=False),
    sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint("sequence >= 1", name="ck_trade_order_events_sequence_positive"),
    # Append-only and idempotent: a redelivered transition cannot append twice.
    sa.UniqueConstraint("order_id", "sequence", name="uq_trade_order_events_sequence"),
    sa.Index("ix_trade_order_events_order", "order_id", "sequence"),
)

trade_fills = sa.Table(
    "trade_fills",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    # Content-addressed fill key. UNIQUE is what makes a duplicate fill event a
    # database conflict rather than a doubled position.
    sa.Column("fill_key", sa.Text, nullable=False, unique=True),
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
    # Per-fill, not just per-account: one assumed-spread fill in an otherwise
    # full-fidelity account must remain identifiable.
    sa.Column("assumption_based", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("costs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint("quantity > 0", name="ck_trade_fills_quantity_positive"),
    sa.CheckConstraint("price > 0", name="ck_trade_fills_price_positive"),
    sa.Index("ix_trade_fills_order", "order_id"),
    sa.Index("ix_trade_fills_account", "account_id", "filled_at"),
)

portfolio_positions = sa.Table(
    "portfolio_positions",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("instrument_id", sa.BigInteger, nullable=False),
    # Signed: positive long, negative short.
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("average_price", sa.Numeric(18, 4), nullable=False),
    sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
    sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_fill_at", sa.DateTime(timezone=True), nullable=True),
    # Provenance of the fold. `11` §8 forbids a running total whose derivation
    # cannot be checked; these two columns are what make it checkable.
    sa.Column("fills_applied", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("last_fill_key", sa.Text, nullable=True),
    sa.UniqueConstraint("account_id", "instrument_id", name="uq_portfolio_positions_identity"),
    sa.Index("ix_portfolio_positions_account", "account_id"),
)

journal_entries = sa.Table(
    "journal_entries",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("entry_type", sa.Text, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cash_delta", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
    sa.Column("realized_pnl_delta", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
    sa.Column("fees_delta", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
    sa.Column("cash_after", sa.Numeric(20, 4), nullable=False),
    sa.Column("order_id", sa.Text, nullable=True),
    sa.Column("fill_key", sa.Text, nullable=True),
    # Idempotency: one entry per (account, source event).
    sa.Column("source_event_key", sa.Text, nullable=False),
    sa.UniqueConstraint("account_id", "source_event_key", name="uq_journal_entries_source"),
    sa.Index("ix_journal_entries_account", "account_id", "occurred_at"),
)
