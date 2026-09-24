"""Tables for metric values, interpretations and tracked OI migrations.

`docs/design/02-DATA_MODEL.md` §6.

**Why these live in `persistence/` and not in `analytics/`.** The `analytics-is-pure`
contract forbids `analytics/*` from importing SQLAlchemy at all, and it caught the
first draft of this module sitting under `analytics/store/`. The contract is right:
analytics never writes. A table declaration inside the pure layer would put a database
dependency one import away from every feature, and the next person adding a "quick
lookup" would find the session already reachable. The rows are written by whichever
caller owns the transaction; the shape lives here, beside the temporal repository base.

The identity constraint is the one that carries the architecture:

    UNIQUE (feature_id, feature_version, scope_kind, scope_ref,
            observed_at, knowledge_horizon, build_context_id)

`knowledge_horizon` and `build_context_id` are **identity, not metadata** — two metrics
computed for the same `observed_at` under different knowledge horizons are legitimately
different values, and a key omitting either would collapse them.

`feature_version` is part of the key too, so v1 and v2 of a formula coexist as separate
rows rather than one overwriting the other's meaning.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "ANALYTICS_TABLES",
    "METADATA",
    "interp_labels",
    "metric_oi_migrations",
    "metric_values",
]

METADATA = sa.MetaData()

ANALYTICS_TABLES: list[str] = ["metric_values", "interp_labels", "metric_oi_migrations"]

metric_values = sa.Table(
    "metric_values",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    # Nullable: a value may be computed ad hoc rather than against a checkpoint.
    sa.Column("state_checkpoint_id", sa.BigInteger, nullable=True),
    sa.Column("feature_id", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Integer, nullable=False),
    sa.Column("scope_kind", sa.Text, nullable=False),
    sa.Column("scope_ref", sa.Text, nullable=False),
    # Text, not NUMERIC: a value may be a number, a category (BUILDUP_CLASSIFICATION)
    # or a profile (GEX_BY_STRIKE). Coercing all three to a float column would lose
    # the categorical features entirely.
    sa.Column("value", sa.Text, nullable=True),
    sa.Column("value_numeric", sa.Numeric(24, 8), nullable=True),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("inputs_digest", sa.Text, nullable=False),
    # --- the four-time model, all four distinct (`05` §2) -----------------------
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
    sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("build_context_id", sa.Text, nullable=False),
    sa.Column("quality_status", sa.Text, nullable=False),
    sa.Column("evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
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
    # The availability invariant, enforced by the database and not only by the code
    # that writes it: a row that violates it cannot exist.
    sa.CheckConstraint(
        "available_at >= computed_at", name="ck_metric_values_available_after_computed"
    ),
    postgresql_partition_by="RANGE (observed_at)",
)

interp_labels = sa.Table(
    "interp_labels",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("metric_refs", postgresql.JSONB, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("convention_version", sa.Text, nullable=False),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("id", "observed_at", name="pk_interp_labels"),
    postgresql_partition_by="RANGE (observed_at)",
)

metric_oi_migrations = sa.Table(
    "metric_oi_migrations",
    METADATA,
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
    sa.Column("evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.PrimaryKeyConstraint("id", "first_observed_at", name="pk_metric_oi_migrations"),
    sa.CheckConstraint(
        "last_observed_at >= first_observed_at", name="ck_oi_migration_duration_non_negative"
    ),
    postgresql_partition_by="RANGE (first_observed_at)",
)
