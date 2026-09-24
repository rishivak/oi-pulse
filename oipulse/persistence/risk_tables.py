"""Tables for risk profiles and the immutable decision sequence.

`docs/design/02-DATA_MODEL.md` §6 and §11; `11-TRADING.md` §3; `18-ROADMAP.md`
Phase 9: `risk_profiles`, `risk_decisions`, `trade_orders.authorizing_risk_decision_id`.

**Why these live in `persistence/`.** `trading/risk` is pure for the same reason
`analytics`, `research`, `backtest` and the rest of `trading` are: the gate must be
evaluable with no database present, which is what `11` §3 means by "independently
testable with no trading infrastructure". The import contract enforces it.

Four properties of the schema carry architectural weight.

**Decisions are an append-only sequence.** `PRIMARY KEY (intent_id, sequence_no)`,
per `02` §11. `11` §3: risk is re-evaluated on modification, retry or changed
conditions, and each evaluation **appends**. There is deliberately **no**
unique-per-intent constraint — `02` §6 says so explicitly — and no UPDATE is
expected: a decision is a historical fact.

**An order cannot exist without an approved decision from its own intent.** `02` §11
specifies a composite FK to `risk_decisions(intent_id, sequence_no)` plus a trigger
asserting the referenced decision is `APPROVED` and belongs to the order's intent.
Both are created by migration 0009. This is the database-level expression of the
brief's §17, and it is what makes "no execution without authorization" a property of
the storage rather than a convention in the application.

**The four mandatory fields are `NOT NULL` where they can be.** `risk_state_ref`,
`risk_evaluation_time` and `inputs_digest` are required on every decision;
`approved_until` is nullable because a rejection has no validity window, and a CHECK
requires it to be present precisely when the decision approves something.

**Policy identity travels with the decision.** `policy_digest` is stored on the row,
not merely referenced, so a decision remains interpretable after the profile it used
has been edited. `11` §3: historical decision semantics must never silently change.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

__all__ = [
    "METADATA",
    "RISK_TABLES",
    "risk_decisions",
    "risk_profiles",
]

METADATA = sa.MetaData()

RISK_TABLES: list[str] = [
    "risk_profiles",
    "risk_decisions",
]

risk_profiles = sa.Table(
    "risk_profiles",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("policy_id", sa.Text, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    # Content address of the whole limit set. A limit edit produces a new digest,
    # so two decisions under different limits can never look comparable.
    sa.Column("policy_digest", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("limits", postgresql.JSONB, nullable=False),
    sa.Column("scope", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    # How many limits are actually in force. Stored so an auditor can see a
    # relaxation as a number, without re-parsing the limit blob.
    sa.Column("configured_limits", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("account_id", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    # A policy version is immutable: editing one would silently change what every
    # historical decision meant.
    sa.UniqueConstraint("policy_id", "version", name="uq_risk_profiles_identity"),
    sa.UniqueConstraint("policy_digest", name="uq_risk_profiles_digest"),
    sa.CheckConstraint("version >= 1", name="ck_risk_profiles_version_positive"),
    sa.Index("ix_risk_profiles_account", "account_id"),
)

risk_decisions = sa.Table(
    "risk_decisions",
    METADATA,
    sa.Column("intent_id", sa.Text, nullable=False),
    sa.Column("sequence_no", sa.Integer, nullable=False),
    # Deterministic, derived from (intent_id, sequence_no).
    sa.Column("risk_decision_id", sa.Text, nullable=False),
    sa.Column("decision", sa.Text, nullable=False),
    sa.Column("evaluated", sa.Boolean, nullable=False),
    # --- the four mandatory traceability fields (`11` §3) --------------------
    sa.Column("risk_state_ref", sa.Text, nullable=False),
    sa.Column("risk_evaluation_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column("inputs_digest", sa.Text, nullable=False),
    sa.Column("approved_until", sa.DateTime(timezone=True), nullable=True),
    # --- policy identity ------------------------------------------------------
    sa.Column("policy_id", sa.Text, nullable=False),
    sa.Column("policy_version", sa.Integer, nullable=False),
    sa.Column("policy_digest", sa.Text, nullable=False),
    # --- requested vs approved (brief §11) ------------------------------------
    sa.Column("requested_quantity", sa.Integer, nullable=False),
    sa.Column("approved_quantity", sa.Integer, nullable=False),
    sa.Column(
        "approved_legs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    # --- structured evidence (brief §23) --------------------------------------
    sa.Column("limits_evaluated", postgresql.JSONB, nullable=False),
    sa.Column("reasons", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("reason", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("knowledge_horizon", sa.DateTime(timezone=True), nullable=True),
    sa.Column("decision_digest", sa.Text, nullable=False),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    # `02` §11: an append-only sequence. No unique-per-intent constraint --
    # re-evaluation is normal and each evaluation appends.
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
    # A rejection approves nothing; an approval must carry a validity window.
    sa.CheckConstraint(
        "decision <> 'REJECTED' OR approved_quantity = 0",
        name="ck_risk_decisions_rejection_approves_nothing",
    ),
    sa.CheckConstraint(
        "decision = 'REJECTED' OR approved_until IS NOT NULL",
        name="ck_risk_decisions_approval_has_validity",
    ),
    sa.Index("ix_risk_decisions_policy", "policy_id", "policy_version"),
    sa.Index("ix_risk_decisions_state", "risk_state_ref"),
)
