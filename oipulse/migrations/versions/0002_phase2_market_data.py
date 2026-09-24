"""Phase 2: instruments, universes and the bitemporal observation store.

`docs/design/02-DATA_MODEL.md` §2, §3, §4, §9, §10, §11.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.
See the external verification checklist in the Phase 2 report.

Structural commitments expressed as constraints rather than conventions:

* **Instrument identity, metadata version and vendor mapping are three tables.**
  Versions and mappings use GiST exclusion constraints so overlapping validity ranges
  are impossible — a lot-size revision cannot silently rewrite March's exposure.
* **Observation identity is tiered, never timestamp-based.** Three partial unique
  indexes. `(instrument_id, observed_at, source)` is deliberately not unique.
* **Bitemporal columns are separate and separately indexed.**
* **No `user_id` anywhere.** Market data is canonical (`02` §12).
* **Historical daily OI carries a validity interval**, so it can never be served as an
  intraday instant (AD-26).

Partitioning: `obs_quotes` and `obs_greeks` are declared `PARTITION BY RANGE
(observed_at)` with an initial set of daily partitions. A scheduled job extends them a
month ahead (`14-DEPLOYMENT.md` §4); a missing partition must never be discovered by a
failed insert.

Revision ID: 0002_phase2_market_data
Revises: 0001_phase1_sys_tables
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_phase2_market_data"
down_revision = "0001_phase1_sys_tables"
branch_labels = None
depends_on = None

#: Daily partitions created up front. The `jobs` role extends the window forward at
#: runtime using the injected clock (`14-DEPLOYMENT.md` §4).
#:
#: The anchor is a **fixed date, not today**. A migration whose output depends on the day
#: it runs is not deterministic: two environments migrated a week apart would end up with
#: different partition sets from the same revision, and a rebuilt staging database would
#: not match production. It is also a wall-clock read, which the Phase 1 guard forbids
#: outside `core.clock` — the guard caught this.
#:
#: Override with OIPULSE_PARTITION_ANCHOR=YYYY-MM-DD when bootstrapping an environment
#: whose collection starts outside the default window.
_PARTITION_ANCHOR = date(2026, 1, 1)
_INITIAL_PARTITION_DAYS = 120
_PARTITIONED = ("obs_quotes", "obs_greeks", "obs_depth")


def _partition_anchor() -> date:
    override = os.environ.get("OIPULSE_PARTITION_ANCHOR")
    return date.fromisoformat(override) if override else _PARTITION_ANCHOR


def _partition_days() -> int:
    override = os.environ.get("OIPULSE_PARTITION_DAYS")
    return int(override) if override else _INITIAL_PARTITION_DAYS


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("identity_tier", sa.Text, nullable=False),
        sa.Column("identity_confidence", sa.Text, nullable=False),
        sa.Column("provider_event_id", sa.Text),
        sa.Column("feed_session_id", sa.Text),
        sa.Column("channel", sa.Text),
        sa.Column("channel_sequence", sa.BigInteger),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("received_seq", sa.BigInteger),
        sa.Column("supersedes_observation_id", sa.BigInteger),
        sa.Column(
            "raw_extra",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    ]


def _create_identity_indexes(table: str, partitioned: bool) -> None:
    """Tiered partial unique indexes — the idempotency guarantee.

    On a partitioned table every unique index must include the partition key, so the
    tier columns are combined with `observed_at`. The guarantee is unchanged: two
    distinct events at one timestamp still produce two rows, because the tier columns
    differ.
    """
    suffix = ["observed_at"] if partitioned else []

    # A-13: scoped by feed session. Whether an Upstox event id is globally unique
    # or restarts per session is unverified, and the failure modes are asymmetric —
    # not scoping, when ids are session-scoped, silently discards live data.
    #
    # COALESCE because Postgres treats NULLs as distinct in a unique index by
    # default: REST rows carry no session, so two genuine duplicates would both be
    # admitted without it. (`NULLS NOT DISTINCT` needs PG15+; COALESCE works
    # everywhere and states the intent at the index.)
    op.create_index(
        f"uq_{table}_provider_event",
        table,
        ["provider_event_id", sa.text("COALESCE(feed_session_id, '')"), *suffix],
        unique=True,
        postgresql_where=sa.text("provider_event_id IS NOT NULL"),
    )
    op.create_index(
        f"uq_{table}_feed_seq",
        table,
        ["feed_session_id", "channel", "channel_sequence", *suffix],
        unique=True,
        postgresql_where=sa.text(
            "feed_session_id IS NOT NULL AND channel_sequence IS NOT NULL"
        ),
    )
    op.create_index(
        f"uq_{table}_content",
        table,
        ["instrument_id", "observed_at", "source", "content_digest"],
        unique=True,
        postgresql_where=sa.text("provider_event_id IS NULL AND feed_session_id IS NULL"),
    )
    op.create_index(f"ix_{table}_pit", table, ["instrument_id", "observed_at", "ingested_at"])
    op.create_index(f"ix_{table}_ingested", table, ["ingested_at"])
    op.create_index(f"ix_{table}_brin", table, ["observed_at"], postgresql_using="brin")


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
    # ---------------------------------------------------------------- instruments
    # Identity only. Contract attributes that *define* an instrument live in the
    # subtype tables below (`02-DATA_MODEL.md` §2); metadata that merely *describes*
    # it lives in instrument_versions. Carrying strike/option_type here would make
    # the identity row differ per instrument kind and put nullable columns on the
    # one table that must be uniform.
    op.create_table(
        "instrument_instruments",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("instrument_type", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "instrument_type IN ('index','equity','future','option')",
            name="ck_instrument_type",
        ),
    )

    op.create_table(
        "instrument_expiries",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("underlying_id", sa.BigInteger, sa.ForeignKey("instrument_instruments.id"), nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False),
        sa.Column("expiry_type", sa.Text, nullable=False),
        sa.Column("settlement_type", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("underlying_id", "expiry_date", name="uq_instrument_expiry"),
    )

    # Option and future contracts. Separate tables rather than nullable columns on the
    # identity row, per the 02 §2 ERD: a strike is not an optional property of every
    # instrument, it is what makes an option that option.
    op.create_table(
        "instrument_options",
        sa.Column(
            "instrument_id",
            sa.BigInteger,
            sa.ForeignKey("instrument_instruments.id"),
            primary_key=True,
        ),
        sa.Column("underlying_id", sa.BigInteger, sa.ForeignKey("instrument_instruments.id"), nullable=False),
        sa.Column("expiry_id", sa.BigInteger, sa.ForeignKey("instrument_expiries.id"), nullable=False),
        sa.Column("strike", sa.Numeric(18, 4), nullable=False),
        sa.Column("option_type", sa.Text, nullable=False),
        sa.CheckConstraint("strike > 0", name="ck_instrument_option_strike_positive"),
        sa.CheckConstraint("option_type IN ('CE','PE')", name="ck_instrument_option_type"),
        sa.UniqueConstraint(
            "underlying_id", "expiry_id", "strike", "option_type",
            name="uq_instrument_option_contract",
        ),
    )
    op.create_index(
        "ix_instrument_options_chain", "instrument_options",
        ["underlying_id", "expiry_id", "strike"],
    )

    op.create_table(
        "instrument_futures",
        sa.Column(
            "instrument_id",
            sa.BigInteger,
            sa.ForeignKey("instrument_instruments.id"),
            primary_key=True,
        ),
        sa.Column("underlying_id", sa.BigInteger, sa.ForeignKey("instrument_instruments.id"), nullable=False),
        sa.Column("expiry_id", sa.BigInteger, sa.ForeignKey("instrument_expiries.id"), nullable=False),
        sa.Column("contract_month", sa.Text),
        sa.UniqueConstraint("underlying_id", "expiry_id", name="uq_instrument_future_contract"),
    )

    op.create_table(
        "instrument_versions",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("instrument_id", sa.BigInteger, sa.ForeignKey("instrument_instruments.id"), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("trading_symbol", sa.Text),
        sa.Column("exchange", sa.Text, nullable=False),
        sa.Column("lot_size", sa.Integer, nullable=False),
        sa.Column("tick_size", sa.Numeric(12, 4), nullable=False),
        sa.Column("contract_attributes", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("lot_size > 0", name="ck_instrument_version_lot_size"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_instrument_version_range"),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    # Versions for one instrument may never overlap: history cannot be rewritten by a
    # metadata revision.
    op.execute(
        "ALTER TABLE instrument_versions ADD CONSTRAINT ex_instrument_version_no_overlap "
        "EXCLUDE USING gist (instrument_id WITH =, "
        "tstzrange(valid_from, valid_to, '[)') WITH &&)"
    )

    op.create_table(
        "instrument_vendor_mappings",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("instrument_id", sa.BigInteger, sa.ForeignKey("instrument_instruments.id"), nullable=False),
        sa.Column("vendor", sa.Text, nullable=False),
        sa.Column("vendor_key", sa.Text, nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_vendor_mapping_range"),
    )
    op.execute(
        "ALTER TABLE instrument_vendor_mappings ADD CONSTRAINT ex_vendor_mapping_no_overlap "
        "EXCLUDE USING gist (instrument_id WITH =, vendor WITH =, "
        "tstzrange(valid_from, valid_to, '[)') WITH &&)"
    )
    op.create_index(
        "ix_vendor_mapping_lookup", "instrument_vendor_mappings", ["vendor", "vendor_key", "valid_from"]
    )

    op.create_table(
        "instrument_universes",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("underlying_ids", postgresql.ARRAY(sa.BigInteger), nullable=False),
        sa.Column("expiry_selector", postgresql.JSONB, nullable=False),
        sa.Column("strike_selector", postgresql.JSONB, nullable=False),
        sa.Column("option_mode", sa.Text, nullable=False, server_default="greeks"),
        sa.Column("future_mode", sa.Text, nullable=False, server_default="greeks"),
        sa.Column("index_mode", sa.Text, nullable=False, server_default="ltpc"),
        sa.Column("include_futures", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        # System-level. Deliberately no user_id: the market is collected once.
    )

    # --------------------------------------------------------------- observations
    op.create_table(
        "obs_quotes",
        *_identity_columns(),
        sa.Column("ltp", sa.Numeric(18, 4)),
        sa.Column("bid", sa.Numeric(18, 4)),
        sa.Column("ask", sa.Numeric(18, 4)),
        sa.Column("bid_qty", sa.BigInteger),
        sa.Column("ask_qty", sa.BigInteger),
        sa.Column("volume", sa.BigInteger),
        sa.Column("oi", sa.BigInteger),
        sa.Column("provider_prev_oi", sa.BigInteger),
        sa.Column("prev_close", sa.Numeric(18, 4)),
        sa.PrimaryKeyConstraint("id", "observed_at"),
        sa.CheckConstraint("oi IS NULL OR oi >= 0", name="ck_obs_quotes_oi_non_negative"),
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="ck_obs_quotes_volume_non_negative"),
        postgresql_partition_by="RANGE (observed_at)",
    )
    op.create_table(
        "obs_greeks",
        *_identity_columns(),
        sa.Column("iv", sa.Numeric(12, 6)),
        sa.Column("delta", sa.Numeric(12, 6)),
        sa.Column("gamma", sa.Numeric(16, 10)),
        sa.Column("theta", sa.Numeric(14, 6)),
        sa.Column("vega", sa.Numeric(14, 6)),
        sa.Column("rho", sa.Numeric(14, 6)),
        sa.PrimaryKeyConstraint("id", "observed_at"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    # Depth is partitioned daily alongside quotes and greeks: same volume profile.
    # JSONB levels rather than a row per level — depth is written and read whole.
    op.create_table(
        "obs_depth",
        *_identity_columns(),
        sa.Column("bids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("asks", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("level_count", sa.Integer),
        sa.PrimaryKeyConstraint("id", "observed_at"),
        postgresql_partition_by="RANGE (observed_at)",
    )

    # ---------------------------------------------------------------------------
    # Partitions and indexes for EVERY partitioned parent, after ALL of them exist.
    #
    # PostgreSQL requires strict ordering: parent table -> columns/constraints ->
    # partitions -> indexes. An earlier revision of this file ran this loop before
    # `obs_depth` was created and then partitioned it a second time explicitly, so
    # the migration failed with `relation "obs_depth" does not exist` and would have
    # created duplicate indexes had it got past that.
    #
    # One loop, after all parents, driven by `_PARTITIONED`. Adding a partitioned
    # table now means creating it above and adding its name to that tuple — the
    # ordering is structural rather than something each new table has to remember,
    # and tools/check_migration_order.py fails the build if a parent is missing.
    # ---------------------------------------------------------------------------
    anchor = _partition_anchor()
    days = _partition_days()
    for table in _PARTITIONED:
        _create_daily_partitions(table, anchor, days)
        _create_identity_indexes(table, partitioned=True)

    # OHLC and index are monthly in the design (`02` §9) and low volume here, so they
    # are created unpartitioned in Phase 2. Partitioning them is a forward migration
    # when volume justifies it, not a guess made now.
    op.create_table(
        "obs_ohlc",
        *_identity_columns(),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("open", sa.Numeric(18, 4)),
        sa.Column("high", sa.Numeric(18, 4)),
        sa.Column("low", sa.Numeric(18, 4)),
        sa.Column("close", sa.Numeric(18, 4)),
        sa.Column("volume", sa.BigInteger),
        sa.Column("oi", sa.BigInteger),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("high IS NULL OR low IS NULL OR high >= low", name="ck_obs_ohlc_high_low"),
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="ck_obs_ohlc_volume"),
    )
    _create_identity_indexes("obs_ohlc", partitioned=False)

    op.create_table(
        "obs_index",
        *_identity_columns(),
        sa.Column("ltp", sa.Numeric(18, 4)),
        sa.Column("prev_close", sa.Numeric(18, 4)),
        sa.Column("open", sa.Numeric(18, 4)),
        sa.Column("high", sa.Numeric(18, 4)),
        sa.Column("low", sa.Numeric(18, 4)),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_identity_indexes("obs_index", partitioned=False)

    op.create_table(
        "obs_historical_oi",
        *_identity_columns(),
        sa.Column("observation_kind", sa.Text, nullable=False, server_default="historical_daily_oi"),
        sa.Column("observation_date", sa.Date, nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("oi", sa.BigInteger),
        sa.Column("close_spot", sa.Numeric(18, 4)),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("valid_to > valid_from", name="ck_hist_oi_interval"),
        sa.CheckConstraint("oi IS NULL OR oi >= 0", name="ck_hist_oi_non_negative"),
    )
    _create_identity_indexes("obs_historical_oi", partitioned=False)

    op.create_table(
        "chain_snapshots",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("underlying_id", sa.BigInteger, nullable=False),
        sa.Column("expiry_id", sa.BigInteger, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.Text),
        sa.Column("leg_count", sa.Integer, nullable=False),
        sa.Column("expected_leg_count", sa.Integer),
        sa.Column("is_complete", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("coherence_mode", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_chain_snapshots_lookup", "chain_snapshots", ["underlying_id", "expiry_id", "observed_at"]
    )

    # ------------------------------------------------------------- data quality
    op.create_table(
        "dq_issues",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument_id", sa.BigInteger),
        sa.Column("underlying_id", sa.BigInteger),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("detail", sa.Text),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_dq_issues_window", "dq_issues", ["type", "detected_at"])

    # Feed sessions: ordinals are assigned once and stored, never re-derived per run,
    # because replay ordering must not depend on what else is in the database
    # (10-REPLAY.md §3).
    op.create_table(
        "feed_sessions",
        sa.Column("session_id", sa.Text, primary_key=True),
        sa.Column("ordinal", sa.BigInteger, nullable=False, unique=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("message_count", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("identity_confidence", sa.Text, nullable=False, server_default="weak"),
    )


def downgrade() -> None:
    op.drop_table("feed_sessions")
    op.drop_index("ix_dq_issues_window", table_name="dq_issues")
    op.drop_table("dq_issues")
    op.drop_index("ix_chain_snapshots_lookup", table_name="chain_snapshots")
    op.drop_table("chain_snapshots")
    op.drop_table("obs_historical_oi")
    op.drop_table("obs_index")
    op.drop_table("obs_ohlc")
    op.drop_table("obs_depth")  # partitions drop with the parent
    op.drop_table("obs_greeks")
    op.drop_table("obs_quotes")
    op.drop_table("instrument_universes")
    op.drop_index("ix_vendor_mapping_lookup", table_name="instrument_vendor_mappings")
    op.drop_table("instrument_vendor_mappings")
    op.drop_table("instrument_versions")
    op.drop_table("instrument_futures")
    op.drop_index("ix_instrument_options_chain", table_name="instrument_options")
    op.drop_table("instrument_options")
    op.drop_table("instrument_expiries")
    op.drop_table("instrument_instruments")
