"""Phase 1: system tables — outbox, transactional inbox, retention locks.

Schema per `docs/design/18-ROADMAP.md` Phase 1 and `02-DATA_MODEL.md` §9, §11.

Three invariants are expressed as constraints rather than conventions:

* `sys_outbox` carries `(aggregate_type, aggregate_id, aggregate_sequence)` with a UNIQUE
  index — aggregate events are totally ordered, and ordering does not depend on a
  best-effort lock (`03-EVENT_MODEL.md` §4).
* `sys_event_inbox` is keyed `(subscriber, event_id)` and is written in the **same
  transaction** as the business mutation, giving exactly-once *database application* per
  subscriber. Not global exactly-once — Postgres cannot make an external side effect
  idempotent.
* `sys_retention_locks` pins any row a decision artifact references, so pruning can never
  silently break the audit chain (`02-DATA_MODEL.md` §8).

Revision ID: 0001_phase1_sys_tables
Revises: 002 (the legacy backend chain head)

**Why this chains off a legacy revision.** The live database is at
`alembic_version = '002'` from `backend/alembic` (001 -> 002), and both chains share the
default `alembic_version` table. Starting the v2 chain at `down_revision = None` created
a second root, so Alembic could not locate '002' and every upgrade failed.

Continuing from '002' gives one linear history for both a fresh database and the existing
one, preserves all legacy data, and needs no manual edit of `alembic_version`. Legacy
tables are left untouched — AD-20 keeps the legacy application running until cutover.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_phase1_sys_tables"
#: The legacy backend chain head. See the module docstring.
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sys_outbox",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
            unique=True,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        # Ordering identity. Explicit, not incidental.
        sa.Column("aggregate_type", sa.Text, nullable=False),
        sa.Column("aggregate_id", sa.Text, nullable=False),
        sa.Column("aggregate_sequence", sa.BigInteger, nullable=False),
        sa.Column("correlation_id", sa.Text, nullable=True),
        sa.Column("causation_id", sa.Text, nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','published','failed')", name="ck_sys_outbox_status"
        ),
        sa.CheckConstraint(
            "aggregate_sequence >= 1", name="ck_sys_outbox_sequence_positive"
        ),
    )

    # Aggregate events are totally ordered. This is the constraint that makes the
    # sequence check meaningful; without it two producers could allocate the same slot.
    op.create_unique_constraint(
        "uq_sys_outbox_aggregate_sequence",
        "sys_outbox",
        ["aggregate_type", "aggregate_id", "aggregate_sequence"],
    )

    # Dispatcher claims an aggregate and delivers its pending events in sequence order.
    op.create_index(
        "ix_sys_outbox_aggregate_pending",
        "sys_outbox",
        ["aggregate_type", "aggregate_id", "aggregate_sequence"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_sys_outbox_pending",
        "sys_outbox",
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "sys_event_inbox",
        sa.Column("subscriber", sa.Text, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("subscriber", "event_id", name="pk_sys_event_inbox"),
    )

    op.create_table(
        "sys_retention_locks",
        sa.Column("subject_kind", sa.Text, nullable=False),
        sa.Column("subject_id", sa.BigInteger, nullable=False),
        sa.Column("locked_by_kind", sa.Text, nullable=False),
        sa.Column("locked_by_id", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "subject_kind",
            "subject_id",
            "locked_by_kind",
            "locked_by_id",
            name="pk_sys_retention_locks",
        ),
    )

    # Pruning jobs join this table; the index serves the "is this row pinned?" lookup.
    op.create_index(
        "ix_sys_retention_locks_subject",
        "sys_retention_locks",
        ["subject_kind", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_sys_retention_locks_subject", table_name="sys_retention_locks")
    op.drop_table("sys_retention_locks")
    op.drop_table("sys_event_inbox")
    op.drop_index("ix_sys_outbox_pending", table_name="sys_outbox")
    op.drop_index("ix_sys_outbox_aggregate_pending", table_name="sys_outbox")
    op.drop_constraint(
        "uq_sys_outbox_aggregate_sequence", "sys_outbox", type_="unique"
    )
    op.drop_table("sys_outbox")
