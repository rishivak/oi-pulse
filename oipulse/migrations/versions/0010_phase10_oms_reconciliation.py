"""Phase 10: reconciliation runs, discrepancies, and the OMS columns on trade_orders.

`18-ROADMAP.md` Phase 10 schema: `trade_reconciliations`,
`trade_reconciliation_discrepancies`. `11-TRADING.md` §4, §5 and §6.

### What the new `trade_orders` columns are for

Phase 8 created `trade_orders` for a paper venue that had no network. Phase 10 adds
the columns a venue relationship needs:

* `venue` — PAPER or BROKER. Brief §18 requires the two to stay distinguishable, and
  a stored order must still say which it was long after the process that made it.
* `client_order_attempt_id` — **our** id for one submission try. `06` §10: it gives
  local dedup and audit and does **not** oblige a provider to reject a duplicate.
  UNIQUE, because sending the same attempt twice is our bug, not the venue's.
* `provider_order_id` — the venue's id, **nullable**. After a lost acknowledgement we
  may hold an order at the venue whose id we never learned; that is the normal case
  `UNKNOWN` exists for, and a `NOT NULL` here would force a fabricated placeholder.
* `provider_status`, `provider_event_time`, `received_at` — the venue's own word and
  its two clocks. Brief §16: provider receipt time is not market time, and the two
  are stored separately rather than collapsed.

### Constraints worth reading

* `uq_trade_orders_client_attempt` makes a repeated submission attempt a database
  conflict. This is the durable half of *local* idempotency; there is deliberately no
  claim that it prevents a duplicate at the broker.
* `ck_trade_orders_provider_status_needs_id` — a provider status with no provider id
  describes an order we cannot address, which means something mapped a response to
  the wrong order.
* `uq_trade_reconciliations_digest` makes a re-run over unchanged evidence collide
  rather than accumulate, which is brief §13 at the storage layer.

**No Phase 11 table is created.** No portfolio snapshot, no attribution.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0010_phase10_oms_reconciliation
Revises: 0009_phase9_risk
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_phase10_oms_reconciliation"
down_revision = "0009_phase9_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_reconciliations",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("content_digest", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("discrepancy_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("resolved", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("needs_attention", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("fills_inserted", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("orders_updated", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("unresolved_orders", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("is_clean", sa.Boolean, nullable=False),
        # `11` §6 step 6: the broker snapshot is part of the run.
        sa.Column("provider_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("local_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", name="uq_trade_reconciliations_run_id"),
        # Brief §13 at the storage layer: unchanged evidence collides.
        sa.UniqueConstraint("content_digest", name="uq_trade_reconciliations_digest"),
        sa.CheckConstraint(
            "matched >= 0 AND discrepancy_count >= 0 AND needs_attention >= 0",
            name="ck_trade_reconciliations_counts_non_negative",
        ),
        # A clean run is one with nothing outstanding. Encoding it stops a row
        # claiming cleanliness while recording work left to do.
        sa.CheckConstraint(
            "is_clean = (needs_attention = 0 AND unresolved_orders = 0)",
            name="ck_trade_reconciliations_clean_means_nothing_outstanding",
        ),
    )

    op.create_table(
        "trade_reconciliation_discrepancies",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("resolution", sa.Text, nullable=False),
        sa.Column("order_id", sa.Text, nullable=True),
        sa.Column("provider_order_id", sa.Text, nullable=True),
        sa.Column("local_state", sa.Text, nullable=True),
        sa.Column("provider_status", sa.Text, nullable=True),
        sa.Column("local_value", sa.Text, nullable=True),
        sa.Column("provider_value", sa.Text, nullable=True),
        sa.Column("detail", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("needs_attention", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "order_id IS NOT NULL OR provider_order_id IS NOT NULL",
            name="ck_reconciliation_discrepancy_has_a_subject",
        ),
    )

    # --- OMS columns on the Phase 8 order table -----------------------------
    op.add_column(
        "trade_orders",
        sa.Column("venue", sa.Text, nullable=False, server_default=sa.text("'PAPER'")),
    )
    op.add_column("trade_orders", sa.Column("client_order_attempt_id", sa.Text, nullable=True))
    # Nullable on purpose: after a lost acknowledgement we may hold an order at the
    # venue whose id we never learned. A placeholder would be a fabricated provider
    # identifier, which brief §28 forbids.
    op.add_column("trade_orders", sa.Column("provider_order_id", sa.Text, nullable=True))
    op.add_column("trade_orders", sa.Column("provider_status", sa.Text, nullable=True))
    op.add_column(
        "trade_orders",
        sa.Column("provider_event_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trade_orders", sa.Column("received_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_check_constraint(
        "ck_trade_orders_venue", "trade_orders", "venue IN ('PAPER', 'BROKER')"
    )
    # A provider status we cannot attribute to a provider order means a response was
    # mapped onto the wrong order.
    op.create_check_constraint(
        "ck_trade_orders_provider_status_needs_id",
        "trade_orders",
        "provider_status IS NULL OR provider_order_id IS NOT NULL",
    )
    # The durable half of *local* submission idempotency. Not a claim about the
    # broker: `06` §10 is explicit that our key does not bind the provider.
    op.create_unique_constraint(
        "uq_trade_orders_client_attempt", "trade_orders", ["client_order_attempt_id"]
    )

    # Indexes only after every table and column exists.
    op.create_index("ix_trade_reconciliations_as_of", "trade_reconciliations", ["as_of"])
    op.create_index(
        "ix_trade_reconciliations_clean", "trade_reconciliations", ["is_clean", "as_of"]
    )
    op.create_index(
        "ix_reconciliation_discrepancies_run",
        "trade_reconciliation_discrepancies",
        ["run_id"],
    )
    op.create_index(
        "ix_reconciliation_discrepancies_kind",
        "trade_reconciliation_discrepancies",
        ["kind", "resolution"],
    )
    op.create_index(
        "ix_reconciliation_discrepancies_order",
        "trade_reconciliation_discrepancies",
        ["order_id"],
    )
    op.create_index("ix_trade_orders_provider", "trade_orders", ["provider_order_id"])
    op.create_index("ix_trade_orders_venue_state", "trade_orders", ["venue", "state"])


def downgrade() -> None:
    # Reverse creation order. Only Phase 10 objects are touched.
    op.drop_index("ix_trade_orders_venue_state", table_name="trade_orders")
    op.drop_index("ix_trade_orders_provider", table_name="trade_orders")
    op.drop_constraint("uq_trade_orders_client_attempt", "trade_orders", type_="unique")
    op.drop_constraint("ck_trade_orders_provider_status_needs_id", "trade_orders", type_="check")
    op.drop_constraint("ck_trade_orders_venue", "trade_orders", type_="check")
    op.drop_column("trade_orders", "received_at")
    op.drop_column("trade_orders", "provider_event_time")
    op.drop_column("trade_orders", "provider_status")
    op.drop_column("trade_orders", "provider_order_id")
    op.drop_column("trade_orders", "client_order_attempt_id")
    op.drop_column("trade_orders", "venue")
    op.drop_table("trade_reconciliation_discrepancies")
    op.drop_table("trade_reconciliations")
