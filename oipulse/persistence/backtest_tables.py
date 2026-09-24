"""Tables for replay runs, backtest results, trades and replay-scoped events.

`docs/design/10-REPLAY.md` §2, §6 and §8; `18-ROADMAP.md` Phase 7.

**Why these live in `persistence/`.** `replay/*` and `backtest/*` are pure for the same
reason `analytics/*` and `research/*` are: a table declaration in the pure layer puts a
database one import away from the engine, and the engine must be replayable without one.
The import-boundary contract enforces it.

Three properties of the schema carry architectural weight.

**`backtest_results.content_hash` is `UNIQUE`.** Re-running the same strategy over the
same period under the same fill model yields the same artifact, not a second row. A
differing hash means an input differed, and finding out *which* is the point of storing
the digests beside it.

**The assumption set is a column, not a comment.** `fill_model_digest`, `assumptions`,
`assumption_based` and `risk_evaluated` are stored with every result, because §6
requires the assumptions to be printed alongside every number and a number whose
assumptions were lost is not interpretable later.

**Replay events are namespaced by `run_id` and cannot reach the live outbox** (§8).
`replay_events` is a separate table rather than a flag on the live outbox: a flag can be
forgotten in a `WHERE` clause, and the consequence of forgetting would be a replay
firing a real alert. A separate table makes the mistake require a deliberate join.

Nothing here is partitioned. These are decision artifacts -- immutable, small and
retained (`02` §9) -- and a time partition would imply a pruning story that must not
exist for them.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "BACKTEST_TABLES",
    "METADATA",
    "backtest_results",
    "backtest_trades",
    "replay_events",
    "replay_runs",
]

METADATA = sa.MetaData()

BACKTEST_TABLES: list[str] = [
    "replay_runs",
    "backtest_results",
    "backtest_trades",
    "replay_events",
]

replay_runs = sa.Table(
    "replay_runs",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Text, nullable=False, unique=True),
    # The replay context's content digest: period, universe, build context, step and
    # knowledge mode. Excludes run_id and speed, so two runs differing only in those
    # share a digest -- which is what makes "same configuration" checkable.
    sa.Column("content_digest", sa.Text, nullable=False),
    sa.Column("build_context_id", sa.Text, nullable=False),
    sa.Column("underlying_ids", postgresql.JSONB, nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("step_mode", sa.Text, nullable=False),
    sa.Column("knowledge_mode", sa.Text, nullable=False),
    # NULL under lockstep, where K is derived per step rather than pinned.
    sa.Column("pinned_knowledge_horizon", sa.DateTime(timezone=True), nullable=True),
    sa.Column("feature_versions", postgresql.JSONB, nullable=False),
    sa.Column("rule_versions", postgresql.JSONB, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    # Resume support (brief §17): where the run stopped and what it carried.
    sa.Column("last_step_index", sa.Integer, nullable=True),
    sa.Column("last_market_time", sa.DateTime(timezone=True), nullable=True),
    sa.Column("carried_state_digest", sa.Text, nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("period_end >= period_start", name="ck_replay_runs_period_ordered"),
    sa.Index("ix_replay_runs_digest", "content_digest"),
)

backtest_results = sa.Table(
    "backtest_results",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Text, nullable=False),
    # Semantic identity: excludes execution metadata, includes every assumption.
    sa.Column("content_hash", sa.Text, nullable=False, unique=True),
    sa.Column("strategy_id", sa.Text, nullable=False),
    sa.Column("strategy_version", sa.Integer, nullable=False),
    sa.Column("strategy_digest", sa.Text, nullable=False),
    sa.Column("replay_digest", sa.Text, nullable=False),
    sa.Column("build_context_id", sa.Text, nullable=False),
    sa.Column("fill_model_digest", sa.Text, nullable=False),
    # The printable assumption set (§6). Stored whole so a result read years later
    # still states what it rests on.
    sa.Column("assumptions", postgresql.JSONB, nullable=False),
    sa.Column("statistics", postgresql.JSONB, nullable=False),
    sa.Column("final_ledger", postgresql.JSONB, nullable=False),
    # Flags that must never be inferred from the numbers.
    sa.Column("assumption_based", sa.Boolean, nullable=False),
    sa.Column("risk_evaluated", sa.Boolean, nullable=False),
    sa.Column("coverage_warnings", postgresql.JSONB, nullable=False),
    sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("duration_seconds", sa.Numeric(18, 6), nullable=True),
    sa.Index("ix_backtest_results_strategy", "strategy_id", "strategy_version"),
    sa.Index("ix_backtest_results_run", "run_id"),
)

backtest_trades = sa.Table(
    "backtest_trades",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Text, nullable=False),
    sa.Column("intent_id", sa.Text, nullable=False),
    sa.Column("instrument_id", sa.BigInteger, nullable=False),
    sa.Column("side", sa.Text, nullable=False),
    sa.Column("requested_quantity", sa.Integer, nullable=False),
    sa.Column("filled_quantity", sa.Integer, nullable=False),
    sa.Column("price", sa.Numeric(18, 4), nullable=True),
    # Kept beside the fill price so slippage paid stays recoverable rather than
    # having to be re-derived from a quote nobody stored.
    sa.Column("reference_price", sa.Numeric(18, 4), nullable=True),
    sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("price_source", sa.Text, nullable=True),
    sa.Column("slippage_model", sa.Text, nullable=True),
    # Per-trade, not just per-run: one assumed-spread fill in an otherwise
    # full-fidelity run must remain identifiable.
    sa.Column("assumption_based", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("rejection_reason", sa.Text, nullable=True),
    sa.Column("costs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint(
        "filled_quantity >= 0 AND filled_quantity <= requested_quantity",
        name="ck_backtest_trades_fill_within_request",
    ),
    sa.CheckConstraint(
        "(filled_quantity = 0) = (filled_at IS NULL)",
        name="ck_backtest_trades_unfilled_has_no_fill_time",
    ),
    sa.UniqueConstraint(
        "run_id", "intent_id", "filled_quantity", "price", name="uq_backtest_trades_fill"
    ),
    sa.Index("ix_backtest_trades_run", "run_id", "decision_time"),
)

replay_events = sa.Table(
    "replay_events",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    # Mandatory, and the reason this table exists: a replay-derived event is scoped
    # by run and can never be picked up by the live outbox dispatcher (§8).
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
    sa.Index("ix_replay_events_run", "run_id", "market_time"),
)
