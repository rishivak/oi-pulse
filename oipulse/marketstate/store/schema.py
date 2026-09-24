"""SQLAlchemy tables for state materialization.

`docs/design/02-DATA_MODEL.md` §5. These are **prunable**: deleting every checkpoint
loses no history, only recomputation time, because a `MarketState` is reconstructable
from `obs_*`.

Two constraints carry the architecture:

* `state_build_contexts.configuration_digest` is `UNIQUE`, and the table is immutable —
  a build context is content-addressable, so the same configuration must map to exactly
  one row on every machine.
* `state_checkpoints` is `UNIQUE (underlying_id, observed_at, knowledge_horizon,
  build_context_id)` — the **full** identity tuple. A key omitting `knowledge_horizon`
  would collapse `MarketState(NIFTY, 11:42, K=11:42)` and `MarketState(NIFTY, 11:42,
  K=11:50)`, which are different and equally valid states.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "STATE_TABLES",
    "state_build_contexts",
    "state_checkpoint_expiries",
    "state_checkpoint_legs",
    "state_checkpoints",
]

METADATA = sa.MetaData()

STATE_TABLES: list[str] = [
    "state_build_contexts",
    "state_checkpoints",
    "state_checkpoint_legs",
    "state_checkpoint_expiries",
]


def _money() -> Any:
    """Prices as NUMERIC, never float. A float rupee price is a rounding bug waiting."""
    return sa.Numeric(18, 4)


state_build_contexts = sa.Table(
    "state_build_contexts",
    METADATA,
    # Not a surrogate key: the id IS the content address, so the same configuration
    # yields the same id in every process without coordination.
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("builder_version", sa.Text, nullable=False),
    sa.Column("staleness_policy_version", sa.Text, nullable=False),
    sa.Column("feature_set_version", sa.Text, nullable=False),
    sa.Column("configuration_digest", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("configuration_digest", name="uq_state_build_contexts_digest"),
)

state_checkpoints = sa.Table(
    "state_checkpoints",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    sa.Column("underlying_id", sa.BigInteger, nullable=False),
    #: Market time. Named `observed_at` to match every other temporal column (`05` §2).
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    #: Identity, NOT metadata.
    sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=False),
    sa.Column("build_context_id", sa.Text, nullable=False),
    #: When assembly ran. Deliberately not part of identity and not part of the digest.
    sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("session_phase", sa.Text, nullable=False),
    sa.Column("spot", _money(), nullable=True),
    sa.Column("spot_observed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("quality_status", sa.Text, nullable=False),
    sa.Column("coherence_mode", sa.Text, nullable=False),
    sa.Column("coverage_ratio", sa.Numeric(6, 5), nullable=False),
    sa.Column("trigger", sa.Text, nullable=False),
    #: Lets deduplication skip a byte-identical successor without re-reading the legs.
    sa.Column("content_digest", sa.Text, nullable=False),
    sa.Column(
        "observation_refs",
        postgresql.JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
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

state_checkpoint_legs = sa.Table(
    "state_checkpoint_legs",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    sa.Column("checkpoint_id", sa.BigInteger, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("instrument_id", sa.BigInteger, nullable=False),
    sa.Column("expiry_id", sa.BigInteger, nullable=False),
    sa.Column("strike", _money(), nullable=False),
    sa.Column("option_type", sa.Text, nullable=False),
    # Every observed value is nullable. Absence is not zero, and a NOT NULL column
    # here would force a fabricated value for a leg we could not see.
    sa.Column("ltp", _money(), nullable=True),
    sa.Column("bid", _money(), nullable=True),
    sa.Column("ask", _money(), nullable=True),
    sa.Column("volume", sa.BigInteger, nullable=True),
    sa.Column("oi", sa.BigInteger, nullable=True),
    sa.Column("provider_prev_oi", sa.BigInteger, nullable=True),
    sa.Column("iv", sa.Numeric(12, 6), nullable=True),
    sa.Column("delta", sa.Numeric(12, 6), nullable=True),
    sa.Column("gamma", sa.Numeric(12, 6), nullable=True),
    sa.Column("theta", sa.Numeric(12, 6), nullable=True),
    sa.Column("vega", sa.Numeric(12, 6), nullable=True),
    sa.Column("quote_observed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("greeks_observed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("quote_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("oi_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("greeks_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.PrimaryKeyConstraint("id", "observed_at", name="pk_state_checkpoint_legs"),
    postgresql_partition_by="RANGE (observed_at)",
)

state_checkpoint_expiries = sa.Table(
    "state_checkpoint_expiries",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
    sa.Column("checkpoint_id", sa.BigInteger, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expiry_id", sa.BigInteger, nullable=False),
    sa.Column("expiry_date", sa.Date, nullable=False),
    sa.Column("total_call_oi", sa.BigInteger, nullable=True),
    sa.Column("total_put_oi", sa.BigInteger, nullable=True),
    sa.Column("atm_strike", _money(), nullable=True),
    sa.Column("missing_leg_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.PrimaryKeyConstraint("id", "observed_at", name="pk_state_checkpoint_expiries"),
    postgresql_partition_by="RANGE (observed_at)",
)
