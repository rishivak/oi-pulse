"""Phase 11: portfolio snapshots, attribution, position reconciliation.

`18-ROADMAP.md` Phase 11 schema: `portfolio_snapshots`, `portfolio_attribution`.
`11-TRADING.md` §8; `02-DATA_MODEL.md` §6.

### The constraint that matters most

`ck_portfolio_attribution_reconciles` asserts `total_pnl = explained + residual`, and
`residual` is `NOT NULL`. `18` Phase 11 names the risk this addresses:

> Attribution residual large enough to be meaningless -- mitigated by reporting it
> prominently; a large residual is information, not something to hide.

With this constraint a row physically cannot exist that hides an unexplained amount
by omitting it or by having been "balanced" into a component. A writer that tried
would get an integrity error rather than a tidy-looking row.

### Columns added to the Phase 8 `portfolio_positions`

Phase 8 created that table for a paper account. Phase 11 adds what the portfolio
layer needs: the portfolio scope, the contract economics in force, the cost-basis
method, and fees. `lot_size` and `contract_multiplier` are stored **per position
snapshot**, not looked up at read time, because `07-ANALYTICS.md` §4.3 requires a
lot-size revision not to retroactively rewrite historical exposure -- and a join to
the current instrument version would do exactly that.

### Nullability is meaning, not laxity

`margin_utilisation` is nullable and must never be written as `0`: no capital limit
supplied means *not known*, and zero would read as no margin used (brief §22). Every
greek is nullable and separately absent, so one missing greek does not make the
others unreportable.

**No Phase 12 terminal table is created.** No layouts, no saved views, no user
preferences.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0011_phase11_portfolio_attribution
Revises: 0010_phase10_oms_reconciliation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_phase11_portfolio_attribution"
down_revision = "0010_phase10_oms_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("snapshot_id", sa.Text, nullable=False),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("portfolio_id", sa.Text, nullable=False),
        sa.Column("market_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_context_id", sa.Text, nullable=False),
        sa.Column("market_state_ref", sa.Text, nullable=True),
        sa.Column("cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("equity", sa.Numeric(20, 4), nullable=False),
        sa.Column("market_value", sa.Numeric(20, 4), nullable=False),
        sa.Column("gross_exposure", sa.Numeric(20, 4), nullable=False),
        sa.Column("net_exposure", sa.Numeric(20, 4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(20, 4), nullable=False),
        sa.Column("fees", sa.Numeric(20, 4), nullable=False),
        sa.Column("delta", sa.Numeric(24, 8), nullable=True),
        sa.Column("gamma", sa.Numeric(24, 8), nullable=True),
        sa.Column("vega", sa.Numeric(24, 8), nullable=True),
        sa.Column("theta", sa.Numeric(24, 8), nullable=True),
        # NULL means not known. Never 0, which would read as no margin used.
        sa.Column("margin_utilisation", sa.Numeric(12, 8), nullable=True),
        sa.Column(
            "margin_basis",
            sa.Text,
            nullable=False,
            server_default=sa.text("'NOT_AVAILABLE'"),
        ),
        sa.Column("drawdown", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("peak_equity", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "concentration",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("positions", postgresql.JSONB, nullable=False),
        sa.Column("returns", postgresql.JSONB, nullable=False),
        sa.Column("is_complete", sa.Boolean, nullable=False),
        sa.Column(
            "unvalued_instruments",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("content_digest", name="uq_portfolio_snapshots_digest"),
        sa.UniqueConstraint("snapshot_id", name="uq_portfolio_snapshots_snapshot_id"),
        sa.CheckConstraint(
            "knowledge_time >= market_time",
            name="ck_portfolio_snapshots_knowledge_after_market",
        ),
        sa.CheckConstraint(
            "margin_utilisation IS NULL OR margin_utilisation >= 0",
            name="ck_portfolio_snapshots_margin_non_negative",
        ),
        sa.CheckConstraint("drawdown >= 0", name="ck_portfolio_snapshots_drawdown_non_negative"),
    )

    op.create_table(
        "portfolio_attribution",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("snapshot_id", sa.Text, nullable=False),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("portfolio_id", sa.Text, nullable=False),
        sa.Column("bucket", sa.Text, nullable=False),
        sa.Column("bucket_id", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("method_version", sa.Integer, nullable=False),
        sa.Column("total_pnl", sa.Numeric(20, 4), nullable=False),
        sa.Column("explained", sa.Numeric(20, 4), nullable=False),
        # NOT NULL. The residual is never optional.
        sa.Column("residual", sa.Numeric(20, 4), nullable=False),
        sa.Column("residual_fraction", sa.Numeric(12, 8), nullable=True),
        sa.Column("components", postgresql.JSONB, nullable=False),
        sa.Column(
            "uncomputed_components",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("market_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
        # The load-bearing constraint: a row cannot hide an unexplained amount.
        sa.CheckConstraint(
            "total_pnl = explained + residual", name="ck_portfolio_attribution_reconciles"
        ),
        sa.UniqueConstraint(
            "snapshot_id", "bucket", "bucket_id", name="uq_portfolio_attribution_slice"
        ),
    )

    op.create_table(
        "portfolio_position_reconciliations",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("portfolio_id", sa.Text, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("mismatched", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("corrections_applied", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("needs_attention", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("is_clean", sa.Boolean, nullable=False),
        sa.Column("discrepancies", postgresql.JSONB, nullable=False),
        sa.Column("provider_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("local_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", name="uq_portfolio_position_recon_run_id"),
        sa.UniqueConstraint("content_digest", name="uq_portfolio_position_recon_digest"),
        sa.CheckConstraint(
            "is_clean = (needs_attention = 0)", name="ck_portfolio_position_recon_clean"
        ),
    )

    # --- columns the portfolio layer needs on the Phase 8 positions table ------
    op.add_column(
        "portfolio_positions",
        sa.Column("portfolio_id", sa.Text, nullable=False, server_default=sa.text("'default'")),
    )
    op.add_column("portfolio_positions", sa.Column("underlying_id", sa.BigInteger, nullable=True))
    op.add_column("portfolio_positions", sa.Column("expiry_id", sa.BigInteger, nullable=True))
    op.add_column(
        "portfolio_positions",
        sa.Column("fees", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
    )
    # Stored per position, not joined from the current instrument version: `07`
    # §4.3 requires a lot-size revision not to rewrite historical exposure.
    op.add_column("portfolio_positions", sa.Column("lot_size", sa.Integer, nullable=True))
    op.add_column(
        "portfolio_positions", sa.Column("contract_multiplier", sa.Numeric(18, 8), nullable=True)
    )
    op.add_column("portfolio_positions", sa.Column("multiplier_source", sa.Text, nullable=True))
    op.add_column(
        "portfolio_positions",
        sa.Column(
            "cost_basis_method",
            sa.Text,
            nullable=False,
            server_default=sa.text("'WEIGHTED_AVERAGE'"),
        ),
    )
    op.create_check_constraint(
        "ck_portfolio_positions_lot_size_positive",
        "portfolio_positions",
        "lot_size IS NULL OR lot_size > 0",
    )
    op.create_check_constraint(
        "ck_portfolio_positions_multiplier_positive",
        "portfolio_positions",
        "contract_multiplier IS NULL OR contract_multiplier > 0",
    )

    # Indexes only after every table and column exists.
    op.create_index(
        "ix_portfolio_snapshots_account",
        "portfolio_snapshots",
        ["account_id", "portfolio_id", "market_time"],
    )
    op.create_index(
        "ix_portfolio_snapshots_times", "portfolio_snapshots", ["market_time", "knowledge_time"]
    )
    op.create_index(
        "ix_portfolio_attribution_bucket", "portfolio_attribution", ["bucket", "bucket_id"]
    )
    op.create_index("ix_portfolio_attribution_snapshot", "portfolio_attribution", ["snapshot_id"])
    op.create_index(
        "ix_portfolio_position_recon_account",
        "portfolio_position_reconciliations",
        ["account_id", "portfolio_id", "as_of"],
    )
    op.create_index(
        "ix_portfolio_positions_portfolio", "portfolio_positions", ["account_id", "portfolio_id"]
    )


def downgrade() -> None:
    # Reverse creation order. Only Phase 11 objects are touched.
    op.drop_index("ix_portfolio_positions_portfolio", table_name="portfolio_positions")
    op.drop_constraint(
        "ck_portfolio_positions_multiplier_positive", "portfolio_positions", type_="check"
    )
    op.drop_constraint(
        "ck_portfolio_positions_lot_size_positive", "portfolio_positions", type_="check"
    )
    op.drop_column("portfolio_positions", "cost_basis_method")
    op.drop_column("portfolio_positions", "multiplier_source")
    op.drop_column("portfolio_positions", "contract_multiplier")
    op.drop_column("portfolio_positions", "lot_size")
    op.drop_column("portfolio_positions", "fees")
    op.drop_column("portfolio_positions", "expiry_id")
    op.drop_column("portfolio_positions", "underlying_id")
    op.drop_column("portfolio_positions", "portfolio_id")
    op.drop_table("portfolio_position_reconciliations")
    op.drop_table("portfolio_attribution")
    op.drop_table("portfolio_snapshots")
