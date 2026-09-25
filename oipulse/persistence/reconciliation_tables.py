"""Tables for reconciliation runs and their discrepancies, plus the OMS order columns.

`18-ROADMAP.md` Phase 10 schema: `trade_reconciliations`,
`trade_reconciliation_discrepancies`. `02-DATA_MODEL.md` §6 sketches the first as
`run_id, scope, discrepancies jsonb, resolution, broker_snap`; Phase 10 normalises the
discrepancies into their own table so a pattern across runs is a query rather than a
JSON scan — `11-TRADING.md` §6 requires that "a pattern of them is visible rather than
absorbed", and a blob makes that hard.

**Why these live in `persistence/`.** `trading/*` is pure, for the same reason every
other layer is: the reconciler must be runnable and testable with no database, which
is how `11` §6's "first-class subsystem with its own suite" is achievable at all.

Three properties carry weight.

**A run is content-addressed and unique on it.** Two reconciliations over identical
evidence produce one row, not two, which is the storage-level expression of brief
§13's idempotency requirement.

**The broker snapshot is stored with the run.** `11` §6 step 6 requires it. A
discrepancy without the evidence that produced it cannot be re-examined, and
re-examination is the only reason to persist a conclusion rather than just act on it.

**Provider identity is nullable everywhere.** `provider_order_id`, `provider_fill_id`
and `client_order_attempt_id` are all `NULL`-able, because each is genuinely absent in
real situations — most importantly after a lost acknowledgement, which is the case the
whole subsystem exists for. A `NOT NULL` here would force a placeholder, and a
fabricated provider id is worse than an honest gap (brief §8 and §28).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "RECONCILIATION_TABLES",
    "trade_reconciliation_discrepancies",
    "trade_reconciliations",
]

METADATA = sa.MetaData()

RECONCILIATION_TABLES: list[str] = [
    "trade_reconciliations",
    "trade_reconciliation_discrepancies",
]

trade_reconciliations = sa.Table(
    "trade_reconciliations",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Text, nullable=False),
    # Semantic identity: trigger, scope, evidence and conclusions. Excludes
    # run_id and the execution timestamps, so a repeat over unchanged evidence
    # collides instead of accumulating.
    sa.Column("content_digest", sa.Text, nullable=False),
    sa.Column("trigger", sa.Text, nullable=False),
    sa.Column("scope", sa.Text, nullable=False),
    #: Market time the run reconciled as of. Not the wall clock it ran at.
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    sa.Column("matched", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("discrepancy_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("resolved", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("needs_attention", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("fills_inserted", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("orders_updated", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("unresolved_orders", sa.Integer, nullable=False, server_default=sa.text("0")),
    #: `18` Phase 10 acceptance: "trader is not ready until reconciliation is clean".
    sa.Column("is_clean", sa.Boolean, nullable=False),
    # `11` §6 step 6: persist the broker snapshot with the run.
    sa.Column("provider_snapshot", postgresql.JSONB, nullable=False),
    sa.Column("local_snapshot", postgresql.JSONB, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("run_id", name="uq_trade_reconciliations_run_id"),
    sa.UniqueConstraint("content_digest", name="uq_trade_reconciliations_digest"),
    sa.Index("ix_trade_reconciliations_as_of", "as_of"),
    sa.Index("ix_trade_reconciliations_clean", "is_clean", "as_of"),
)

trade_reconciliation_discrepancies = sa.Table(
    "trade_reconciliation_discrepancies",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("resolution", sa.Text, nullable=False),
    # Both nullable, and both genuinely absent in real cases: MISSING_LOCALLY has
    # no local order, MISSING_AT_PROVIDER has no provider order, and a lost
    # acknowledgement has no provider id at all.
    sa.Column("order_id", sa.Text, nullable=True),
    sa.Column("provider_order_id", sa.Text, nullable=True),
    sa.Column("local_state", sa.Text, nullable=True),
    sa.Column("provider_status", sa.Text, nullable=True),
    sa.Column("local_value", sa.Text, nullable=True),
    sa.Column("provider_value", sa.Text, nullable=True),
    sa.Column("detail", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("needs_attention", sa.Boolean, nullable=False, server_default=sa.text("false")),
    # A discrepancy names at least one side. One that named neither would describe
    # a difference between two things that do not exist.
    sa.CheckConstraint(
        "order_id IS NOT NULL OR provider_order_id IS NOT NULL",
        name="ck_reconciliation_discrepancy_has_a_subject",
    ),
    sa.Index("ix_reconciliation_discrepancies_run", "run_id"),
    # `11` §6: a *pattern* of discrepancies must be visible. This index is what
    # makes "how often does this kind happen?" a cheap query.
    sa.Index("ix_reconciliation_discrepancies_kind", "kind", "resolution"),
    sa.Index("ix_reconciliation_discrepancies_order", "order_id"),
)
