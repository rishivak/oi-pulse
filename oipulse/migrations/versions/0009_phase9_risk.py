"""Phase 9: risk profiles, the immutable decision sequence, and order authorization.

`docs/design/02-DATA_MODEL.md` §6 and §11; `11-TRADING.md` §3; `18-ROADMAP.md` Phase 9.

The load-bearing part of this revision is the last three statements, not the tables.

**`trade_orders.authorizing_risk_decision_id` + a composite FK + a trigger.** `02`
§11 specifies exactly this:

> **No order without an approved risk decision from its own intent** — Composite FK
> to `risk_decisions(intent_id, sequence_no)` + trigger asserting that decision's
> `decision = 'APPROVED'` and `intent_id` matches the order's

The FK alone is not enough: it would accept a *rejected* decision, and it would
accept a decision belonging to a different intent if the order's `intent_id` column
were not part of the key. Both holes are closed — the FK is composite on
`(intent_id, authorizing_decision_sequence)`, so the decision necessarily belongs to
the order's own intent, and the trigger rejects a non-approving decision.

This is the database-level expression of the Phase 9 brief §17. It is not possible
to insert an order row that skipped the gate, or one authorized by another intent's
approval, even by a caller that bypasses the application entirely.

**Decisions are append-only.** `PRIMARY KEY (intent_id, sequence_no)` with no
unique-per-intent constraint, because `11` §3 makes re-evaluation normal.

**Existing Phase 8 rows.** The new column is nullable and the FK is `NOT VALID` for
existing rows would be the usual trick; here it is simply nullable, because a Phase 8
order legitimately predates the risk engine. The trigger only fires when the column
is populated. New orders written by the Phase 9 runtime always populate it.

**No Phase 10+ table is created.** No OMS, no broker, no reconciliation, no
attribution.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0009_phase9_risk
Revises: 0008_phase8_paper_trading
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_phase9_risk"
down_revision = "0008_phase8_paper_trading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_profiles",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("policy_id", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("policy_digest", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("limits", postgresql.JSONB, nullable=False),
        sa.Column("scope", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("configured_limits", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("account_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        # A policy version is immutable: editing one would silently change what
        # every historical decision meant (`11` §3).
        sa.UniqueConstraint("policy_id", "version", name="uq_risk_profiles_identity"),
        sa.UniqueConstraint("policy_digest", name="uq_risk_profiles_digest"),
        sa.CheckConstraint("version >= 1", name="ck_risk_profiles_version_positive"),
    )

    op.create_table(
        "risk_decisions",
        sa.Column("intent_id", sa.Text, nullable=False),
        sa.Column("sequence_no", sa.Integer, nullable=False),
        sa.Column("risk_decision_id", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("evaluated", sa.Boolean, nullable=False),
        # The four mandatory traceability fields (`11` §3, brief §5).
        sa.Column("risk_state_ref", sa.Text, nullable=False),
        sa.Column("risk_evaluation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inputs_digest", sa.Text, nullable=False),
        sa.Column("approved_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_id", sa.Text, nullable=False),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("policy_digest", sa.Text, nullable=False),
        sa.Column("requested_quantity", sa.Integer, nullable=False),
        sa.Column("approved_quantity", sa.Integer, nullable=False),
        sa.Column(
            "approved_legs",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("limits_evaluated", postgresql.JSONB, nullable=False),
        sa.Column(
            "reasons", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("reason", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_digest", sa.Text, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        # `02` §11: append-only. Deliberately NO unique-per-intent constraint.
        sa.PrimaryKeyConstraint("intent_id", "sequence_no", name="pk_risk_decisions"),
        sa.CheckConstraint("sequence_no >= 1", name="ck_risk_decisions_sequence_positive"),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'MODIFIED')",
            name="ck_risk_decisions_verdict",
        ),
        sa.CheckConstraint(
            "approved_quantity >= 0 AND approved_quantity <= requested_quantity",
            name="ck_risk_decisions_approved_within_requested",
        ),
        sa.CheckConstraint(
            "decision <> 'REJECTED' OR approved_quantity = 0",
            name="ck_risk_decisions_rejection_approves_nothing",
        ),
        sa.CheckConstraint(
            "decision = 'REJECTED' OR approved_until IS NOT NULL",
            name="ck_risk_decisions_approval_has_validity",
        ),
    )

    # --- order authorization (`02` §11) --------------------------------------
    op.add_column(
        "trade_orders",
        sa.Column("authorizing_risk_decision_id", sa.Text, nullable=True),
    )
    op.add_column(
        "trade_orders",
        sa.Column("authorizing_decision_sequence", sa.Integer, nullable=True),
    )
    # Both halves of the composite key, or neither. Half of it identifies nothing.
    op.create_check_constraint(
        "ck_trade_orders_authorization_complete",
        "trade_orders",
        "(authorizing_risk_decision_id IS NULL) = (authorizing_decision_sequence IS NULL)",
    )
    # The composite FK. Keyed on the order's OWN intent_id, so a decision belonging
    # to a different intent cannot be referenced -- brief §6, enforced by the
    # database rather than by the application.
    op.create_foreign_key(
        "fk_trade_orders_authorizing_decision",
        "trade_orders",
        "risk_decisions",
        ["intent_id", "authorizing_decision_sequence"],
        ["intent_id", "sequence_no"],
    )

    # Indexes only after every table and column exists.
    op.create_index("ix_risk_profiles_account", "risk_profiles", ["account_id"])
    op.create_index("ix_risk_decisions_policy", "risk_decisions", ["policy_id", "policy_version"])
    op.create_index("ix_risk_decisions_state", "risk_decisions", ["risk_state_ref"])
    op.create_index(
        "ix_trade_orders_authorization", "trade_orders", ["authorizing_risk_decision_id"]
    )

    # The trigger the FK cannot express: the referenced decision must actually
    # approve. A composite FK proves the decision belongs to this intent; only a
    # trigger can prove it said yes.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trade_orders_require_approved_decision()
        RETURNS trigger AS $$
        DECLARE
            verdict text;
        BEGIN
            IF NEW.authorizing_decision_sequence IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT decision INTO verdict
              FROM risk_decisions
             WHERE intent_id = NEW.intent_id
               AND sequence_no = NEW.authorizing_decision_sequence;
            IF verdict IS NULL THEN
                RAISE EXCEPTION
                    'order % references risk decision (%, %) which does not exist',
                    NEW.order_id, NEW.intent_id, NEW.authorizing_decision_sequence;
            END IF;
            IF verdict NOT IN ('APPROVED', 'MODIFIED') THEN
                RAISE EXCEPTION
                    'order % is authorized by a % decision; only an approving decision may authorize an order',
                    NEW.order_id, verdict;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_trade_orders_require_approved_decision
        BEFORE INSERT OR UPDATE ON trade_orders
        FOR EACH ROW EXECUTE FUNCTION trade_orders_require_approved_decision();
        """
    )


def downgrade() -> None:
    # Reverse creation order. Only Phase 9 objects are touched; the Phase 8 columns
    # added here are dropped, and no observation, checkpoint, metric, signal,
    # research, replay or trade table is removed.
    op.execute("DROP TRIGGER IF EXISTS trg_trade_orders_require_approved_decision ON trade_orders")
    op.execute("DROP FUNCTION IF EXISTS trade_orders_require_approved_decision()")
    op.drop_index("ix_trade_orders_authorization", table_name="trade_orders")
    op.drop_constraint("fk_trade_orders_authorizing_decision", "trade_orders", type_="foreignkey")
    op.drop_constraint("ck_trade_orders_authorization_complete", "trade_orders", type_="check")
    op.drop_column("trade_orders", "authorizing_decision_sequence")
    op.drop_column("trade_orders", "authorizing_risk_decision_id")
    op.drop_table("risk_decisions")
    op.drop_table("risk_profiles")
