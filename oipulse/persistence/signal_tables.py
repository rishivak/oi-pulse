"""Tables for signals, evidence, alert rules and alert occurrences.

`docs/design/02-DATA_MODEL.md` §6 and `18-ROADMAP.md` Phase 5.

**Why these live in `persistence/` rather than under `signals/`.** The
`signals-are-pure` contract forbids `signals/*` from importing SQLAlchemy at all, for
the same reason `analytics-is-pure` does: a table declaration inside the pure layer
puts a database dependency one import away from every rule.

The audit chain is a **foreign-key path**, which is what makes explainability a join
rather than a log search:

    signal_evidence.metric_value_* -> metric_values -> state_checkpoints -> obs_*

`signal_evidence` therefore stores the full `metric_values` identity tuple rather than
a surrogate id: `metric_values` is partitioned by `observed_at`, so a bare id would not
identify a row, and the tuple resolves by join without one.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "SIGNAL_TABLES",
    "alert_occurrences",
    "alert_rules",
    "signal_evidence",
    "signal_signals",
]

METADATA = sa.MetaData()

SIGNAL_TABLES: list[str] = [
    "signal_signals",
    "signal_evidence",
    "alert_rules",
    "alert_occurrences",
]

signal_signals = sa.Table(
    "signal_signals",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    # Content-addressable, deterministic. The same evaluation always yields the same
    # signal_id, which is what makes replaying a source event idempotent.
    sa.Column("signal_id", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("rule_version", sa.Integer, nullable=False),
    sa.Column("underlying_id", sa.BigInteger, nullable=False),
    sa.Column("expiry_id", sa.BigInteger, nullable=True),
    sa.Column("occurrence", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("horizon_seconds", sa.Integer, nullable=False),
    # --- the temporal model ---------------------------------------------------
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
    # Mandatory. An unfalsifiable signal cannot be evaluated against reality.
    sa.Column("invalidation_condition", sa.Text, nullable=False),
    # 'NONE_OBSERVED' is a positive finding, not an absent field, so NOT NULL.
    sa.Column("contradiction_assessment", sa.Text, nullable=False),
    sa.Column("quality_status", sa.Text, nullable=False),
    sa.Column("state_checkpoint_ref", sa.Text, nullable=True),
    sa.Column("provenance", postgresql.JSONB, nullable=False),
    sa.Column("history", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.PrimaryKeyConstraint("id", "observed_at", name="pk_signal_signals"),
    # Identity, so re-processing one source event updates rather than duplicates.
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
    # A signal can never be consumable before its market time.
    sa.CheckConstraint("available_at >= observed_at", name="ck_signal_available_after_observed"),
    sa.CheckConstraint("strength >= 0 AND strength <= 1", name="ck_signal_strength_domain"),
    postgresql_partition_by="RANGE (observed_at)",
)

signal_evidence = sa.Table(
    "signal_evidence",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    sa.Column("signal_id", sa.Text, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    # The full metric_values identity tuple: metric_values is partitioned, so a bare
    # id would not identify a row. This is the join that makes the trail walkable.
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

alert_rules = sa.Table(
    "alert_rules",
    METADATA,
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
    # Never a credential: a destination is a name resolved by deployment config, so a
    # webhook secret cannot end up in a row anybody can read.
    sa.Column("destination", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("config_digest", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("cooldown_seconds >= 0", name="ck_alert_rule_cooldown_non_negative"),
)

alert_occurrences = sa.Table(
    "alert_occurrences",
    METADATA,
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
    # The idempotency key. Unique within the partition, so reprocessing the same
    # source event cannot insert a second occurrence.
    sa.Column("dedup_key", sa.Text, nullable=False),
    sa.Column("attempts", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("acknowledged_by", sa.Text, nullable=True),
    sa.PrimaryKeyConstraint("id", "observed_at", name="pk_alert_occurrences"),
    sa.UniqueConstraint("dedup_key", "observed_at", name="uq_alert_occurrences_dedup"),
    postgresql_partition_by="RANGE (observed_at)",
)
