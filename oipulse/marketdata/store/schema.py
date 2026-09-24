"""Observation store — SQLAlchemy Core table definitions.

`docs/design/02-DATA_MODEL.md` §2, §3, §10, §11.

NOT EXECUTABLE IN THE DEVELOPMENT SANDBOX: requires `sqlalchemy`. The DDL these tables
describe is applied by `oipulse/migrations/versions/0002_phase2_market_data.py`, which
requires a live PostgreSQL.

Three structural commitments are expressed here rather than left to convention:

1. **Identity is tiered, not timestamp-based.** Three *partial* unique indexes, one per
   tier. `(instrument_id, observed_at, source)` is deliberately **not** unique — a WS
   feed may legitimately deliver several distinct events for one instrument at the same
   timestamp resolution, and collapsing them destroys real information.
2. **Bitemporal.** `observed_at` and `ingested_at` are separate columns, separately
   indexed, never interchangeable.
3. **Append-only.** Corrections are new rows carrying `supersedes_observation_id`.
   Immutability is enforced by withholding UPDATE/DELETE from the application role, not
   by discipline.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "chain_snapshots",
    "obs_depth",
    "obs_greeks",
    "obs_historical_oi",
    "obs_index",
    "obs_ohlc",
    "obs_quotes",
]

METADATA = sa.MetaData()


def _identity_columns() -> list[sa.Column[object]]:
    """Columns shared by every observation table.

    Factored so a new observation kind cannot accidentally omit one — an observation
    table missing `ingested_at` would silently break every knowledge-time query against
    it.
    """
    return [
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("instrument_id", sa.BigInteger, nullable=False, index=True),
        # --- bitemporal axes, never conflated -------------------------------------
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        # --- identity (06 §6, 03 §2) ----------------------------------------------
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("identity_tier", sa.Text, nullable=False),
        sa.Column("identity_confidence", sa.Text, nullable=False),
        sa.Column("provider_event_id", sa.Text, nullable=True),
        sa.Column("feed_session_id", sa.Text, nullable=True),
        sa.Column("channel", sa.Text, nullable=True),
        sa.Column("channel_sequence", sa.BigInteger, nullable=True),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("received_seq", sa.BigInteger, nullable=True),
        # --- corrections ----------------------------------------------------------
        sa.Column("supersedes_observation_id", sa.BigInteger, nullable=True),
        # --- vendor fields we do not model yet (06 §8) -----------------------------
        sa.Column(
            "raw_extra",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    ]


def _identity_indexes(table: str) -> list[sa.Index]:
    """Tiered partial unique indexes — the idempotency guarantee.

    Evaluated in priority order rather than as one composite key, because which tier
    applies depends on what the provider actually supplied for that row.
    """
    return [
        # A-13: scoped by feed session. Whether an Upstox event id is globally unique
        # or restarts per session is unverified, and the failure modes are asymmetric —
        # not scoping, when ids are session-scoped, silently discards live data.
        #
        # COALESCE because Postgres treats NULLs as distinct in a unique index by
        # default: REST rows carry no session, so two genuine duplicates would both be
        # admitted without it. (`NULLS NOT DISTINCT` needs PG15+; COALESCE works
        # everywhere and states the intent at the index.)
        sa.Index(
            f"uq_{table}_provider_event",
            "provider_event_id",
            sa.text("COALESCE(feed_session_id, '')"),
            unique=True,
            postgresql_where=sa.text("provider_event_id IS NOT NULL"),
        ),
        sa.Index(
            f"uq_{table}_feed_seq",
            "feed_session_id",
            "channel",
            "channel_sequence",
            unique=True,
            postgresql_where=sa.text(
                "feed_session_id IS NOT NULL AND channel_sequence IS NOT NULL"
            ),
        ),
        sa.Index(
            f"uq_{table}_content",
            "instrument_id",
            "observed_at",
            "source",
            "content_digest",
            unique=True,
            postgresql_where=sa.text("provider_event_id IS NULL AND feed_session_id IS NULL"),
        ),
        # Point-in-time lookup: latest observation per instrument under a bound.
        sa.Index(f"ix_{table}_pit", "instrument_id", sa.text("observed_at DESC"), "ingested_at"),
        # knowledge_at scans filter on ingestion time independently.
        sa.Index(f"ix_{table}_ingested", "ingested_at"),
        # Within a daily partition, rows correlate with insertion order: BRIN gives most
        # of a B-tree's benefit at a fraction of the size.
        sa.Index(f"ix_{table}_brin", "observed_at", postgresql_using="brin"),
    ]


obs_quotes = sa.Table(
    "obs_quotes",
    METADATA,
    *_identity_columns(),
    sa.Column("ltp", sa.Numeric(18, 4)),
    sa.Column("bid", sa.Numeric(18, 4)),
    sa.Column("ask", sa.Numeric(18, 4)),
    sa.Column("bid_qty", sa.BigInteger),
    sa.Column("ask_qty", sa.BigInteger),
    sa.Column("volume", sa.BigInteger),
    sa.Column("oi", sa.BigInteger),
    # The provider's assertion of previous OI — itself a raw observation. Distinct from
    # *our* previous OI, which is never stored (01 §4).
    sa.Column("provider_prev_oi", sa.BigInteger),
    sa.Column("prev_close", sa.Numeric(18, 4)),
    sa.CheckConstraint("oi IS NULL OR oi >= 0", name="ck_obs_quotes_oi_non_negative"),
    sa.CheckConstraint("volume IS NULL OR volume >= 0", name="ck_obs_quotes_volume_non_negative"),
    *_identity_indexes("obs_quotes"),
    postgresql_partition_by="RANGE (observed_at)",
)

obs_greeks = sa.Table(
    "obs_greeks",
    METADATA,
    *_identity_columns(),
    # All five persisted. The legacy pipeline kept only iv and discarded the rest.
    sa.Column("iv", sa.Numeric(12, 6)),
    sa.Column("delta", sa.Numeric(12, 6)),
    sa.Column("gamma", sa.Numeric(16, 10)),
    sa.Column("theta", sa.Numeric(14, 6)),
    sa.Column("vega", sa.Numeric(14, 6)),
    sa.Column("rho", sa.Numeric(14, 6)),
    *_identity_indexes("obs_greeks"),
    postgresql_partition_by="RANGE (observed_at)",
)

obs_historical_oi = sa.Table(
    "obs_historical_oi",
    METADATA,
    *_identity_columns(),
    # Date granularity, explicit and unmistakable (AD-26). A daily figure can never be
    # served as live intraday state because the interval says what it covers.
    sa.Column("observation_kind", sa.Text, nullable=False, server_default="historical_daily_oi"),
    sa.Column("observation_date", sa.Date, nullable=False),
    sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
    sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
    sa.Column("oi", sa.BigInteger),
    sa.Column("close_spot", sa.Numeric(18, 4)),
    sa.CheckConstraint("valid_to > valid_from", name="ck_hist_oi_interval"),
    sa.CheckConstraint("oi IS NULL OR oi >= 0", name="ck_hist_oi_non_negative"),
    *_identity_indexes("obs_historical_oi"),
)


# --------------------------------------------------------------------------------
# The remaining observation kinds. `02-DATA_MODEL.md` §3 lists these as siblings of
# obs_quotes; each canonical observation dataclass has a table, so a kind cannot be
# produced by the normalizer with nowhere to land.
#
# Retention differs by kind (`02` §9): depth is high-volume and low research value
# (90 days), while OHLC, index and historical OI are monthly-partitioned and kept.
# --------------------------------------------------------------------------------

obs_depth = sa.Table(
    "obs_depth",
    METADATA,
    *_identity_columns(),
    # JSONB rather than a levels table: depth is written and read whole, never joined
    # level-by-level, and a row-per-level design would multiply the highest-volume
    # table in the system by its depth count.
    sa.Column("bids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("asks", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("level_count", sa.Integer),
    *_identity_indexes("obs_depth"),
    postgresql_partition_by="RANGE (observed_at)",
)

obs_ohlc = sa.Table(
    "obs_ohlc",
    METADATA,
    *_identity_columns(),
    sa.Column("interval", sa.Text, nullable=False),
    sa.Column("open", sa.Numeric(18, 4)),
    sa.Column("high", sa.Numeric(18, 4)),
    sa.Column("low", sa.Numeric(18, 4)),
    sa.Column("close", sa.Numeric(18, 4)),
    sa.Column("volume", sa.BigInteger),
    sa.Column("oi", sa.BigInteger),
    sa.CheckConstraint("high IS NULL OR low IS NULL OR high >= low", name="ck_obs_ohlc_high_low"),
    sa.CheckConstraint("volume IS NULL OR volume >= 0", name="ck_obs_ohlc_volume"),
    *_identity_indexes("obs_ohlc"),
)

obs_index = sa.Table(
    "obs_index",
    METADATA,
    *_identity_columns(),
    sa.Column("ltp", sa.Numeric(18, 4)),
    sa.Column("prev_close", sa.Numeric(18, 4)),
    sa.Column("open", sa.Numeric(18, 4)),
    sa.Column("high", sa.Numeric(18, 4)),
    sa.Column("low", sa.Numeric(18, 4)),
    *_identity_indexes("obs_index"),
)

chain_snapshots = sa.Table(
    "chain_snapshots",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("underlying_id", sa.BigInteger, nullable=False),
    sa.Column("expiry_id", sa.BigInteger, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("request_id", sa.Text),
    sa.Column("leg_count", sa.Integer, nullable=False),
    sa.Column("expected_leg_count", sa.Integer),
    # A snapshot is a CROSS-SECTIONAL CONSISTENCY SET: all legs as the venue reported
    # them in one response. Completeness is recorded because a partial chain is usable
    # but must not masquerade as a full one.
    sa.Column("is_complete", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("coherence_mode", sa.Text, nullable=False),
    sa.Index(
        "ix_chain_snapshots_lookup", "underlying_id", "expiry_id", sa.text("observed_at DESC")
    ),
)
