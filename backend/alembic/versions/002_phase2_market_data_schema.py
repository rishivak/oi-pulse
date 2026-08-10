"""Phase 2 market-data events and timeframe bars.

Revision ID: 002
Revises: 001
Create Date: 2026-08-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_data_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument_type", sa.String(length=20), nullable=False),
        sa.Column("instrument_key", sa.String(length=120), nullable=False),
        sa.Column("trading_symbol", sa.String(length=120), nullable=True),
        sa.Column("underlying", sa.String(length=50), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(12, 2), nullable=True),
        sa.Column("option_type", sa.String(length=2), nullable=True),
        sa.Column("ltp", sa.Numeric(14, 4), nullable=True),
        sa.Column("oi", sa.BigInteger(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="REST"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_mde_event_ts", "market_data_events", ["event_ts"])
    op.create_index("ix_mde_instrument_ts", "market_data_events", ["instrument_key", "event_ts"])
    op.create_index("ix_mde_underlying_expiry_ts", "market_data_events", ["underlying", "expiry_date", "event_ts"])
    op.create_index("ix_mde_option_lookup", "market_data_events", ["underlying", "expiry_date", "strike", "option_type", "event_ts"])

    op.create_table(
        "oi_time_bars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument_type", sa.String(length=20), nullable=False),
        sa.Column("instrument_key", sa.String(length=120), nullable=False),
        sa.Column("trading_symbol", sa.String(length=120), nullable=True),
        sa.Column("underlying", sa.String(length=50), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(12, 2), nullable=True),
        sa.Column("option_type", sa.String(length=2), nullable=True),
        sa.Column("timeframe", sa.String(length=4), nullable=False),
        sa.Column("bucket_start_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_ltp", sa.Numeric(14, 4), nullable=True),
        sa.Column("close_ltp", sa.Numeric(14, 4), nullable=True),
        sa.Column("ltp_change", sa.Numeric(14, 4), nullable=True),
        sa.Column("open_oi", sa.BigInteger(), nullable=True),
        sa.Column("close_oi", sa.BigInteger(), nullable=True),
        sa.Column("oi_change", sa.BigInteger(), nullable=True),
        sa.Column("oi_high", sa.BigInteger(), nullable=True),
        sa.Column("oi_low", sa.BigInteger(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("interpretation", sa.String(length=40), nullable=True),
        sa.Column("points_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="DERIVED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_key", "timeframe", "bucket_start_ts", name="uq_oi_time_bar"),
    )

    op.create_index("ix_otb_underlying_tf_bucket", "oi_time_bars", ["underlying", "timeframe", "bucket_start_ts"])
    op.create_index("ix_otb_expiry_tf_bucket", "oi_time_bars", ["underlying", "expiry_date", "timeframe", "bucket_start_ts"])
    op.create_index("ix_otb_option_lookup", "oi_time_bars", ["underlying", "expiry_date", "strike", "option_type", "timeframe", "bucket_start_ts"])


def downgrade() -> None:
    op.drop_index("ix_otb_option_lookup", table_name="oi_time_bars")
    op.drop_index("ix_otb_expiry_tf_bucket", table_name="oi_time_bars")
    op.drop_index("ix_otb_underlying_tf_bucket", table_name="oi_time_bars")
    op.drop_table("oi_time_bars")

    op.drop_index("ix_mde_option_lookup", table_name="market_data_events")
    op.drop_index("ix_mde_underlying_expiry_ts", table_name="market_data_events")
    op.drop_index("ix_mde_instrument_ts", table_name="market_data_events")
    op.drop_index("ix_mde_event_ts", table_name="market_data_events")
    op.drop_table("market_data_events")
