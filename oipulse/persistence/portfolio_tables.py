"""Tables for portfolio snapshots, attribution and position reconciliation.

`18-ROADMAP.md` Phase 11 schema: `portfolio_snapshots`, `portfolio_attribution`.
`02-DATA_MODEL.md` §6 sketches the second as `bucket, component, amount`. Phase 11
adds `portfolio_position_reconciliations` for the Phase 10 deferral this phase
discharges.

`portfolio_positions` already exists — Phase 8 created it. Phase 11 adds the columns
the portfolio layer needs and does not recreate the table.

**Why these live in `persistence/`.** `trading/*` is pure so the portfolio layer is
computable and testable with no database. The import contract enforces it.

Three properties carry weight.

**The residual is a column, not a derivation.** `portfolio_attribution.residual` is
`NOT NULL`, and a CHECK asserts
`total_pnl = explained + residual`. A row cannot exist that hides an unexplained
amount by omitting it, and `18` Phase 11's risk — "attribution residual large enough
to be meaningless, mitigated by reporting it prominently" — is enforced at the
storage layer rather than trusted to the writer.

**Snapshots are content-addressed and unique on it.** Two snapshots of the same book
at the same `(T, K, BuildContext)` are one row. Execution metadata is excluded from
the digest, so re-computing a historical snapshot collides instead of accumulating.

**Both times are stored, separately.** `market_time` and `knowledge_time` are
distinct `NOT NULL` columns. A snapshot at T on knowledge to K is not the same object
as one at T on latest knowledge, and a schema with one timestamp could not tell them
apart.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "PORTFOLIO_TABLES",
    "portfolio_attribution",
    "portfolio_position_reconciliations",
    "portfolio_snapshots",
]

METADATA = sa.MetaData()

PORTFOLIO_TABLES: list[str] = [
    "portfolio_snapshots",
    "portfolio_attribution",
    "portfolio_position_reconciliations",
]

portfolio_snapshots = sa.Table(
    "portfolio_snapshots",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("snapshot_id", sa.Text, nullable=False),
    # Semantic identity: valuation, positions, P&L and the configuration needed to
    # reproduce them. Excludes computed_at (brief §24).
    sa.Column("content_digest", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("portfolio_id", sa.Text, nullable=False),
    # Both times, separately. Brief §10: valuation-at-T is not latest-known.
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
    # Every greek nullable and separately absent: one missing greek must not make
    # the others unreportable.
    sa.Column("delta", sa.Numeric(24, 8), nullable=True),
    sa.Column("gamma", sa.Numeric(24, 8), nullable=True),
    sa.Column("vega", sa.Numeric(24, 8), nullable=True),
    sa.Column("theta", sa.Numeric(24, 8), nullable=True),
    # NULL when no risk policy supplied a capital limit. Never 0 — that would read
    # as "no margin used" rather than "not known" (brief §22).
    sa.Column("margin_utilisation", sa.Numeric(12, 8), nullable=True),
    sa.Column("margin_basis", sa.Text, nullable=False, server_default=sa.text("'NOT_AVAILABLE'")),
    sa.Column("drawdown", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
    sa.Column("peak_equity", sa.Numeric(20, 4), nullable=True),
    sa.Column(
        "concentration", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    sa.Column("positions", postgresql.JSONB, nullable=False),
    sa.Column("returns", postgresql.JSONB, nullable=False),
    # The honesty flag. A partially-valued book must never read as a complete one.
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
        "knowledge_time >= market_time", name="ck_portfolio_snapshots_knowledge_after_market"
    ),
    sa.CheckConstraint(
        "margin_utilisation IS NULL OR margin_utilisation >= 0",
        name="ck_portfolio_snapshots_margin_non_negative",
    ),
    sa.CheckConstraint("drawdown >= 0", name="ck_portfolio_snapshots_drawdown_non_negative"),
    sa.Index("ix_portfolio_snapshots_account", "account_id", "portfolio_id", "market_time"),
    sa.Index("ix_portfolio_snapshots_times", "market_time", "knowledge_time"),
)

portfolio_attribution = sa.Table(
    "portfolio_attribution",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("snapshot_id", sa.Text, nullable=False),
    sa.Column("content_digest", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("portfolio_id", sa.Text, nullable=False),
    # `02` §6's shape: bucket, component, amount -- normalised, so slicing is a
    # query rather than a JSON scan.
    sa.Column("bucket", sa.Text, nullable=False),
    sa.Column("bucket_id", sa.Text, nullable=False),
    # The versioned method (`11` §8). Stored per row so a decomposition stays
    # interpretable after the formula changes.
    sa.Column("method", sa.Text, nullable=False),
    sa.Column("method_version", sa.Integer, nullable=False),
    sa.Column("total_pnl", sa.Numeric(20, 4), nullable=False),
    sa.Column("explained", sa.Numeric(20, 4), nullable=False),
    # NOT NULL and CHECKed. The residual cannot be omitted to make a row look tidy.
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
    # `18` Phase 11's risk, enforced by the database: the decomposition must add up
    # with the residual included, so no row can hide an unexplained amount.
    sa.CheckConstraint(
        "total_pnl = explained + residual", name="ck_portfolio_attribution_reconciles"
    ),
    sa.UniqueConstraint(
        "snapshot_id", "bucket", "bucket_id", name="uq_portfolio_attribution_slice"
    ),
    sa.Index("ix_portfolio_attribution_bucket", "bucket", "bucket_id"),
    sa.Index("ix_portfolio_attribution_snapshot", "snapshot_id"),
)

portfolio_position_reconciliations = sa.Table(
    "portfolio_position_reconciliations",
    METADATA,
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
    # `11` §6 step 6: persist the broker snapshot with the run. A discrepancy
    # without its evidence cannot be re-examined.
    sa.Column("provider_snapshot", postgresql.JSONB, nullable=False),
    sa.Column("local_snapshot", postgresql.JSONB, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("run_id", name="uq_portfolio_position_recon_run_id"),
    # Brief §19 at the storage layer: unchanged evidence collides.
    sa.UniqueConstraint("content_digest", name="uq_portfolio_position_recon_digest"),
    sa.CheckConstraint(
        "is_clean = (needs_attention = 0)", name="ck_portfolio_position_recon_clean"
    ),
    sa.Index("ix_portfolio_position_recon_account", "account_id", "portfolio_id", "as_of"),
)
