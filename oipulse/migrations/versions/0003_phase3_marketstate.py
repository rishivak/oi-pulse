"""Phase 3: state build contexts and state checkpoints.

`docs/design/02-DATA_MODEL.md` §5 and `04-MARKETSTATE.md` §5.

Checkpoints are a **materialization artifact, not truth**. Every table here is prunable:
dropping the lot loses no history, only recomputation time, because a `MarketState` is
reconstructable from `obs_*`. The exception is retention-locked rows (`02` §8), which is
why nothing here cascades.

Two constraints carry the architecture:

* `UNIQUE (configuration_digest)` on `state_build_contexts` — a build context is
  content-addressable, so one configuration maps to one row everywhere.
* `UNIQUE (underlying_id, observed_at, knowledge_horizon, build_context_id)` on
  `state_checkpoints` — the **full** identity tuple. A key omitting `knowledge_horizon`
  would collapse `MarketState(NIFTY, 11:42, K=11:42)` and `MarketState(NIFTY, 11:42,
  K=11:50)`, which are different, equally valid states.

Ordering follows PostgreSQL's requirement and the Phase 2 guard: parent table ->
constraints -> partitions -> indexes. `tools/check_migration_order.py` enforces it.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0003_phase3_marketstate
Revises: 0002_phase2_market_data
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_phase3_marketstate"
down_revision = "0002_phase2_market_data"
branch_labels = None
depends_on = None

#: Fixed, never `date.today()`: a migration whose output depends on the day it runs is
#: not deterministic, and two environments migrated a week apart would end up with
#: different partition sets from the same revision. Matches Phase 2's anchor.
_PARTITION_ANCHOR = date(2026, 1, 1)
_INITIAL_PARTITION_DAYS = 120
_PARTITIONED = (
    "state_checkpoints",
    "state_checkpoint_legs",
    "state_checkpoint_expiries",
)


def _partition_anchor() -> date:
    override = os.environ.get("OIPULSE_PARTITION_ANCHOR")
    return date.fromisoformat(override) if override else _PARTITION_ANCHOR


def _create_daily_partitions(table: str, start: date, days: int) -> None:
    for offset in range(days):
        day = start + timedelta(days=offset)
        nxt = day + timedelta(days=1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {table}_{day:%Y%m%d} "
            f"PARTITION OF {table} "
            f"FOR VALUES FROM ('{day.isoformat()}') TO ('{nxt.isoformat()}')"
        )


def _money() -> sa.Numeric:
    return sa.Numeric(18, 4)


def upgrade() -> None:
    # ------------------------------------------------------- build contexts
    # `id` is the content address, not a surrogate key: the same configuration must
    # produce the same id in every process, without coordination.
    op.create_table(
        "state_build_contexts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("builder_version", sa.Text, nullable=False),
        sa.Column("staleness_policy_version", sa.Text, nullable=False),
        sa.Column("feature_set_version", sa.Text, nullable=False),
        sa.Column("configuration_digest", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("configuration_digest", name="uq_state_build_contexts_digest"),
    )

    # ---------------------------------------------------------- checkpoints
    op.create_table(
        "state_checkpoints",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("underlying_id", sa.BigInteger, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_context_id", sa.Text, nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_phase", sa.Text, nullable=False),
        sa.Column("spot", _money(), nullable=True),
        sa.Column("spot_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_status", sa.Text, nullable=False),
        sa.Column("coherence_mode", sa.Text, nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(6, 5), nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column(
            "observation_refs",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # The partition key must be in every unique constraint on a partitioned table.
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_state_checkpoints"),
        sa.UniqueConstraint(
            "underlying_id",
            "observed_at",
            "knowledge_horizon",
            "build_context_id",
            name="uq_state_checkpoints_identity",
        ),
        postgresql_partition_by="RANGE (observed_at)",
    )

    op.create_table(
        "state_checkpoint_legs",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("checkpoint_id", sa.BigInteger, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument_id", sa.BigInteger, nullable=False),
        sa.Column("expiry_id", sa.BigInteger, nullable=False),
        sa.Column("strike", _money(), nullable=False),
        sa.Column("option_type", sa.Text, nullable=False),
        # Every observed value is nullable: absence is not zero, and NOT NULL here
        # would force a fabricated number for a leg that was never seen.
        sa.Column("ltp", _money()),
        sa.Column("bid", _money()),
        sa.Column("ask", _money()),
        sa.Column("volume", sa.BigInteger),
        sa.Column("oi", sa.BigInteger),
        sa.Column("provider_prev_oi", sa.BigInteger),
        sa.Column("iv", sa.Numeric(12, 6)),
        sa.Column("delta", sa.Numeric(12, 6)),
        sa.Column("gamma", sa.Numeric(12, 6)),
        sa.Column("theta", sa.Numeric(12, 6)),
        sa.Column("vega", sa.Numeric(12, 6)),
        sa.Column("quote_observed_at", sa.DateTime(timezone=True)),
        sa.Column("greeks_observed_at", sa.DateTime(timezone=True)),
        sa.Column("quote_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("oi_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("greeks_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_state_checkpoint_legs"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    op.create_table(
        "state_checkpoint_expiries",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("checkpoint_id", sa.BigInteger, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiry_id", sa.BigInteger, nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False),
        sa.Column("total_call_oi", sa.BigInteger),
        sa.Column("total_put_oi", sa.BigInteger),
        sa.Column("atm_strike", _money()),
        sa.Column("missing_leg_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_state_checkpoint_expiries"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    # Partitions and indexes only after every parent exists. PostgreSQL requires
    # parent -> constraints -> partitions -> indexes, and getting this order wrong is
    # exactly what broke migration 0002 in external verification.
    anchor = _partition_anchor()
    for table in _PARTITIONED:
        _create_daily_partitions(table, anchor, _INITIAL_PARTITION_DAYS)

    op.create_index(
        "ix_state_checkpoints_lookup",
        "state_checkpoints",
        ["underlying_id", "observed_at", "knowledge_horizon", "build_context_id"],
    )
    op.create_index(
        "ix_state_checkpoint_legs_checkpoint",
        "state_checkpoint_legs",
        ["checkpoint_id", "observed_at"],
    )
    op.create_index(
        "ix_state_checkpoint_expiries_checkpoint",
        "state_checkpoint_expiries",
        ["checkpoint_id", "observed_at"],
    )


def downgrade() -> None:
    # Reverse creation order, or dependent objects block the drop. Only Phase 3 tables
    # are touched; no observation or legacy table is affected.
    op.drop_table("state_checkpoint_expiries")
    op.drop_table("state_checkpoint_legs")
    op.drop_table("state_checkpoints")
    op.drop_table("state_build_contexts")
