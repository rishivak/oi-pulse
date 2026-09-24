"""Phase 5: signals, evidence, alert rules and alert occurrences.

`docs/design/02-DATA_MODEL.md` §6 and `18-ROADMAP.md` Phase 5.

Three constraints carry the architecture:

* `UNIQUE (type, rule_version, underlying_id, expiry_id, occurrence, observed_at,
  knowledge_horizon, build_context_id, config_digest)` on `signal_signals` — the full
  deterministic identity. Re-processing one source event updates a row rather than
  inserting a second, and a threshold change produces a distinguishable population
  instead of silently redefining the old one.
* `UNIQUE (dedup_key, observed_at)` on `alert_occurrences` — the alert idempotency key,
  enforced by the database and not only by the router that computes it.
* A `CHECK` on `signal_evidence` tying weight sign to evidence kind: supporting weights
  are non-negative, contradicting weights non-positive. A sign error would silently
  invert an item's contribution to strength.

`signal_evidence` stores the **full** `metric_values` identity tuple rather than a
surrogate id, because `metric_values` is partitioned by `observed_at` and a bare id
does not identify a row there. That tuple is what makes the audit chain
`signal -> evidence -> metric_values -> state_checkpoints -> obs_*` a join.

Ordering follows the Phase 2-4 guard: parent table -> constraints -> partitions ->
indexes.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0005_phase5_signals
Revises: 0004_phase4_analytics
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_phase5_signals"
down_revision = "0004_phase4_analytics"
branch_labels = None
depends_on = None

#: Fixed, never `date.today()`. Matches Phases 2 to 4.
_PARTITION_ANCHOR = date(2026, 1, 1)
_INITIAL_PARTITION_DAYS = 120
#: `alert_rules` is small and unpartitioned: it holds configuration, not a time series.
_PARTITIONED = ("signal_signals", "signal_evidence", "alert_occurrences")


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
        "signal_signals",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("signal_id", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Integer, nullable=False),
        sa.Column("underlying_id", sa.BigInteger, nullable=False),
        sa.Column("expiry_id", sa.BigInteger, nullable=True),
        sa.Column("occurrence", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("horizon_seconds", sa.Integer, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_context_id", sa.Text, nullable=False),
        sa.Column("config_digest", sa.Text, nullable=False),
        sa.Column("strength", sa.Numeric(6, 5), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("invalidation_condition", sa.Text, nullable=False),
        # NOT NULL: 'NONE_OBSERVED' is a positive finding, not an absent field.
        sa.Column("contradiction_assessment", sa.Text, nullable=False),
        sa.Column("quality_status", sa.Text, nullable=False),
        sa.Column("state_checkpoint_ref", sa.Text, nullable=True),
        sa.Column("provenance", postgresql.JSONB, nullable=False),
        sa.Column(
            "history", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_signal_signals"),
        sa.UniqueConstraint(
            "type",
            "rule_version",
            "underlying_id",
            "expiry_id",
            "occurrence",
            "observed_at",
            "knowledge_horizon",
            "build_context_id",
            "config_digest",
            name="uq_signal_signals_identity",
        ),
        sa.CheckConstraint(
            "available_at >= observed_at", name="ck_signal_available_after_observed"
        ),
        sa.CheckConstraint("strength >= 0 AND strength <= 1", name="ck_signal_strength_domain"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    op.create_table(
        "signal_evidence",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("signal_id", sa.Text, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("metric_feature_id", sa.Text, nullable=False),
        sa.Column("metric_feature_version", sa.Integer, nullable=False),
        sa.Column("metric_scope_kind", sa.Text, nullable=False),
        sa.Column("metric_scope_ref", sa.Text, nullable=False),
        sa.Column("metric_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_build_context_id", sa.Text, nullable=False),
        sa.Column("metric_inputs_digest", sa.Text, nullable=False),
        sa.Column("metric_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "observation_refs",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("weight", sa.Numeric(6, 4), nullable=False),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_signal_evidence"),
        sa.CheckConstraint(
            "(kind = 'SUPPORTING' AND weight >= 0) OR (kind = 'CONTRADICTING' AND weight <= 0)",
            name="ck_signal_evidence_weight_sign",
        ),
        postgresql_partition_by="RANGE (observed_at)",
    )

    # Configuration, not a time series: small, unpartitioned, keyed by its own id.
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("signal_type", sa.Text, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("on_statuses", postgresql.JSONB, nullable=False),
        sa.Column("min_strength", sa.Numeric(6, 5), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer, nullable=False),
        sa.Column("dedup_window_seconds", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("underlying_id", sa.BigInteger, nullable=True),
        sa.Column("destination", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("config_digest", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cooldown_seconds >= 0", name="ck_alert_rule_cooldown_non_negative"),
    )

    op.create_table(
        "alert_occurrences",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("occurrence_id", sa.Text, nullable=False),
        sa.Column("rule_id", sa.Text, nullable=False),
        sa.Column("rule_config_digest", sa.Text, nullable=False),
        sa.Column("signal_id", sa.Text, nullable=False),
        sa.Column("signal_type", sa.Text, nullable=False),
        sa.Column("underlying_id", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedup_key", sa.Text, nullable=False),
        sa.Column(
            "attempts", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("id", "observed_at", name="pk_alert_occurrences"),
        # The idempotency key, enforced by the database rather than only by the
        # router that computes it.
        sa.UniqueConstraint("dedup_key", "observed_at", name="uq_alert_occurrences_dedup"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    # Partitions and indexes only after every parent exists.
    anchor = _partition_anchor()
    days = _partition_days()
    for table in _PARTITIONED:
        _create_daily_partitions(table, anchor, days)

    op.create_index(
        "ix_signal_signals_lookup",
        "signal_signals",
        ["underlying_id", "type", "status", "observed_at"],
    )
    # Research filters on availability: `available_at <= decision_time`.
    op.create_index(
        "ix_signal_signals_availability", "signal_signals", ["available_at", "observed_at"]
    )
    op.create_index(
        "ix_signal_evidence_signal", "signal_evidence", ["signal_id", "observed_at"]
    )
    # The audit-chain join: evidence to the metric row that produced it.
    op.create_index(
        "ix_signal_evidence_metric",
        "signal_evidence",
        ["metric_feature_id", "metric_feature_version", "metric_observed_at"],
    )
    op.create_index(
        "ix_alert_occurrences_rule", "alert_occurrences", ["rule_id", "triggered_at"]
    )
    op.create_index(
        "ix_alert_occurrences_signal", "alert_occurrences", ["signal_id", "observed_at"]
    )
    op.create_index("ix_alert_rules_signal_type", "alert_rules", ["signal_type", "enabled"])


def downgrade() -> None:
    # Reverse creation order. Only Phase 5 tables are touched; no observation,
    # checkpoint, metric or legacy table is affected.
    op.drop_table("alert_occurrences")
    op.drop_table("alert_rules")
    op.drop_table("signal_evidence")
    op.drop_table("signal_signals")
