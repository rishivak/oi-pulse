"""Initial schema — all core tables and indexes.

Revision ID: 001
Revises:
Create Date: 2026-08-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    # ── upstox_accounts ────────────────────────────────────────────────────────
    op.create_table(
        "upstox_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("upstox_user_id", sa.String(100), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("access_token_iv", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_iv", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    # ── oauth_states ───────────────────────────────────────────────────────────
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("state_token", sa.String(64), nullable=False),
        sa.Column("session_key", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_token"),
    )

    # ── user_preferences ───────────────────────────────────────────────────────
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("default_underlying", sa.String(50), nullable=False, server_default="NIFTY"),
        sa.Column("default_interval_min", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("default_expiry_type", sa.String(20), nullable=False, server_default="current"),
        sa.Column("theme", sa.String(20), nullable=False, server_default="dark"),
        sa.Column("strike_window", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    # ── instruments ────────────────────────────────────────────────────────────
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_key", sa.String(100), nullable=False),
        sa.Column("underlying_symbol", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("lot_size", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_key"),
    )
    op.create_index("ix_instruments_underlying", "instruments", ["underlying_symbol", "is_active"])

    # ── option_expiries ────────────────────────────────────────────────────────
    op.create_table(
        "option_expiries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("underlying", sa.String(50), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("underlying", "expiry_date", name="uq_expiry"),
    )
    op.create_index("ix_option_expiries_underlying_active", "option_expiries", ["underlying", "is_active"])

    # ── oi_snapshots ───────────────────────────────────────────────────────────
    op.create_table(
        "oi_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("underlying", sa.String(50), nullable=False),
        sa.Column("expiry_id", sa.Integer(), nullable=False),
        sa.Column("interval_min", sa.Integer(), nullable=False),
        sa.Column("bucket_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_call_oi", sa.BigInteger(), nullable=True),
        sa.Column("total_put_oi", sa.BigInteger(), nullable=True),
        sa.Column("pcr", sa.Numeric(10, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["expiry_id"], ["option_expiries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "underlying", "expiry_id", "interval_min", "bucket_ts", name="uq_snapshot"),
    )
    # Primary query pattern: user + underlying + expiry + time range, ordered desc
    op.create_index("ix_snapshots_user_underlying_ts", "oi_snapshots", ["user_id", "underlying", "expiry_id", "bucket_ts"])
    # For trending-OI queries: time-bucketed lookups across underlyings
    op.create_index("ix_snapshots_ts_underlying", "oi_snapshots", ["bucket_ts", "underlying"])

    # ── oi_strike_snapshots ────────────────────────────────────────────────────
    op.create_table(
        "oi_strike_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("strike", sa.Numeric(10, 2), nullable=False),
        sa.Column("call_oi", sa.BigInteger(), nullable=True),
        sa.Column("put_oi", sa.BigInteger(), nullable=True),
        sa.Column("call_ltp", sa.Numeric(10, 2), nullable=True),
        sa.Column("put_ltp", sa.Numeric(10, 2), nullable=True),
        sa.Column("call_volume", sa.BigInteger(), nullable=True),
        sa.Column("put_volume", sa.BigInteger(), nullable=True),
        sa.Column("call_iv", sa.Numeric(8, 4), nullable=True),
        sa.Column("put_iv", sa.Numeric(8, 4), nullable=True),
        sa.Column("call_prev_oi", sa.BigInteger(), nullable=True),
        sa.Column("put_prev_oi", sa.BigInteger(), nullable=True),
        sa.Column("call_oi_change", sa.BigInteger(), nullable=True),
        sa.Column("put_oi_change", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["oi_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "strike", name="uq_strike"),
    )
    op.create_index("ix_strikes_snapshot", "oi_strike_snapshots", ["snapshot_id"])
    # For top-N OI change queries across a snapshot batch
    op.create_index("ix_strikes_call_oi_change", "oi_strike_snapshots", ["snapshot_id", "call_oi_change"])
    op.create_index("ix_strikes_put_oi_change", "oi_strike_snapshots", ["snapshot_id", "put_oi_change"])

    # ── market_ticks ───────────────────────────────────────────────────────────
    op.create_table(
        "market_ticks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("underlying", sa.String(50), nullable=False),
        sa.Column("ltp", sa.Numeric(12, 2), nullable=False),
        sa.Column("tick_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_ticks_underlying_ts", "market_ticks", ["underlying", "tick_ts"])

    # ── alerts ─────────────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("underlying", sa.String(50), nullable=True),
        sa.Column("expiry_id", sa.Integer(), nullable=True),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("condition_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["expiry_id"], ["option_expiries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_user_enabled", "alerts", ["user_id", "is_enabled"])

    # ── collector_jobs ─────────────────────────────────────────────────────────
    op.create_table(
        "collector_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("underlying", sa.String(50), nullable=False),
        sa.Column("interval_min", sa.Integer(), nullable=False),
        sa.Column("is_running", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "underlying", "interval_min", name="uq_collector_job"),
    )

    # ── outbox_events ──────────────────────────────────────────────────────────
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=False), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_outbox_pending", "outbox_events", ["status", "created_at"], postgresql_where=sa.text("status = 'pending'"))

    # ── audit_logs ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_user_ts", "audit_logs", ["user_id", "created_at"])

    # ── Seed instrument master data ────────────────────────────────────────────
    op.execute("""
        INSERT INTO instruments (instrument_key, underlying_symbol, exchange, name, lot_size) VALUES
        ('NSE_INDEX|Nifty 50',   'NIFTY',     'NSE_INDEX', 'Nifty 50 Index',       50),
        ('NSE_INDEX|Nifty Bank', 'BANKNIFTY',  'NSE_INDEX', 'Nifty Bank Index',     15),
        ('BSE_INDEX|SENSEX',     'SENSEX',     'BSE_INDEX', 'BSE Sensex Index',     10)
        ON CONFLICT (instrument_key) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("outbox_events")
    op.drop_table("collector_jobs")
    op.drop_table("alerts")
    op.drop_table("market_ticks")
    op.drop_table("oi_strike_snapshots")
    op.drop_table("oi_snapshots")
    op.drop_table("option_expiries")
    op.drop_table("instruments")
    op.drop_table("user_preferences")
    op.drop_table("oauth_states")
    op.drop_table("upstox_accounts")
    op.drop_table("users")
