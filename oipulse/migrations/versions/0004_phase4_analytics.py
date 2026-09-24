"""Phase 4: metric values, interpretations and tracked OI migrations.

`docs/design/02-DATA_MODEL.md` §6.

The identity constraint on `metric_values` is the load-bearing one:

    UNIQUE (feature_id, feature_version, scope_kind, scope_ref,
            observed_at, knowledge_horizon, build_context_id)

`knowledge_horizon` and `build_context_id` are identity, not metadata, and
`feature_version` is part of the key so v1 and v2 of a formula coexist rather than one
silently redefining the other's stored meaning.

A CHECK constraint enforces `available_at >= computed_at` in the database as well as in
the code that writes it, so a row breaking the availability invariant cannot exist.

Ordering follows PostgreSQL's requirement and the Phase 2/3 guard: parent table ->
constraints -> partitions -> indexes.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0004_phase4_analytics
Revises: 0003_phase3_marketstate
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_phase4_analytics"
down_revision = "0003_phase3_marketstate"
branch_labels = None
depends_on = None

#: Fixed, never `date.today()`: a migration whose output depends on the day it runs is
#: not deterministic. Matches Phase 2 and Phase 3.
_PARTITION_ANCHOR = date(2026, 1, 1)
_INITIAL_PARTITION_DAYS = 120
_PARTITIONED = ("metric_values", "interp_labels", "metric_oi_migrations")
#: `metric_oi_migrations` partitions on when a migration began, not on `observed_at`.
_PARTITION_KEY = {
    "metric_values": "observed_at",
    "interp_labels": "observed_at",
    "metric_oi_migrations": "first_observed_at",
}


def _partition_anchor() -> date:
    override = os.environ.get("OIPULSE_PARTITION_ANCHOR")
    return date.fromisoformat(override) if override else _PARTITION_ANCHOR


def _partition_days() -> int:
    override = os.environ.get("OIPULSE_PARTITION_DAYS")
    return int(override) if override else _INITIAL_PARTITION_DAYS


def _create_daily_partitions(table: str, start: date, days: int) -> None:
    for offset in range(days):
        day = start + timedelta(days=offset)
        nxt = day + timedelta(days=1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {table}_{day:%Y%m%d} "
            f"PARTITION OF {table} "
            f"FOR VALUES FROM ('{day.isoformat()}') TO ('{nxt.isoformat()}')"
        )


def upgrade() -> None:
    op.create_table(
        "metric_values",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("state_checkpoint_id", sa.BigInteger, nullable=True),
        sa.Column("feature_id", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Integer, nullable=False),
        sa.Column("scope_kind", sa.Text, nullable=False),
        sa.Column("scope_ref", sa.Text, nullable=False),
        # Text plus an optional numeric projection: a value may be a number, a
        # category or a per-strike profile, and forcing all three into one numeric
        # column would drop the categorical features entirely.
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("value_numeric", sa.Numeric(24, 8), nullable=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("inputs_digest", sa.Text, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_context_id", sa.Text, nullable=False),
        sa.Column("quality_status", sa.Text, nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_metric_values"),
        sa.UniqueConstraint(
            "feature_id",
            "feature_version",
            "scope_kind",
            "scope_ref",
            "observed_at",
            "knowledge_horizon",
            "build_context_id",
            name="uq_metric_values_identity",
        ),
        sa.CheckConstraint(
            "available_at >= computed_at", name="ck_metric_values_available_after_computed"
        ),
        postgresql_partition_by="RANGE (observed_at)",
    )

    op.create_table(
        "interp_labels",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_refs", postgresql.JSONB, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("convention_version", sa.Text, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_interp_labels"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    op.create_table(
        "metric_oi_migrations",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("expiry_id", sa.BigInteger, nullable=False),
        sa.Column("option_type", sa.Text, nullable=False),
        sa.Column("origin_strike", sa.Numeric(18, 4), nullable=False),
        sa.Column("destination_strike", sa.Numeric(18, 4), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("magnitude", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("windows", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column(
            "evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.PrimaryKeyConstraint("id", "first_observed_at", name="pk_metric_oi_migrations"),
        sa.CheckConstraint(
            "last_observed_at >= first_observed_at",
            name="ck_oi_migration_duration_non_negative",
        ),
        postgresql_partition_by="RANGE (first_observed_at)",
    )

    # Partitions and indexes only after every parent exists.
    anchor = _partition_anchor()
    days = _partition_days()
    for table in _PARTITIONED:
        _create_daily_partitions(table, anchor, days)

    op.create_index(
        "ix_metric_values_lookup",
        "metric_values",
        ["feature_id", "feature_version", "scope_kind", "scope_ref", "observed_at"],
    )
    # Availability-filtered reads are the common research query
    # (`available_at <= decision_time`), so they get their own index.
    op.create_index(
        "ix_metric_values_availability", "metric_values", ["available_at", "observed_at"]
    )
    op.create_index(
        "ix_oi_migrations_expiry",
        "metric_oi_migrations",
        ["expiry_id", "status", "first_observed_at"],
    )


def downgrade() -> None:
    # Reverse creation order. Only Phase 4 tables are touched; no observation,
    # checkpoint or legacy table is affected.
    op.drop_table("metric_oi_migrations")
    op.drop_table("interp_labels")
    op.drop_table("metric_values")
