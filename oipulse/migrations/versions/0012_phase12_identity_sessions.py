"""Phase 12 remediation: server-side revocable sessions.

`17-SECURITY.md` §3 and §7.3. Independent verification of Phase 12 returned NOT
VERIFIED because the backend had no authentication boundary; this is the store that
makes one possible.

### What the table buys

The legacy design "signs the literal string `user:{id}` and stores nothing, so
logout clears a cookie while the signed value remains valid until expiry" (`17` §3).
A stored record inverts that: the cookie is a lookup key rather than a token, so
`revoked_at` takes effect on the next request rather than at the end of a TTL.

### Downgrade drops the table

Not the usual caution about losing data, because there is a specific reason it is
safe here: a session is ephemeral by construction and every row is re-creatable by
signing in again. Dropping it logs everyone out, which is the correct consequence of
removing the mechanism that tracks who is signed in.

### Repeatability

`upgrade` then `downgrade` then `upgrade` leaves the same schema: the table, its
three check constraints and its two indexes are created in one `create_table` and
dropped as one unit, so there is no partial state a second upgrade could trip over.

NOT APPLIED IN THE DEVELOPMENT SANDBOX: requires `alembic` and a live PostgreSQL.

Revision ID: 0012_phase12_identity_sessions
Revises: 0011_phase11_portfolio_attribution
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_phase12_identity_sessions"
down_revision = "0011_phase11_portfolio_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_sessions",
        # `17` §3: "The cookie carries only the opaque id."
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # NULL means live. Revocation is immediate and per-session.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip", sa.Text, nullable=True),
        sa.Column(
            "permissions_snapshot",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        # `17` §7.3: compared against the double-submit header server-side.
        sa.Column("csrf_token", sa.Text, nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_identity_sessions_expiry_after_start"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_identity_sessions_revoked_after_start",
        ),
        # 32 characters is the floor for a token that has to resist guessing; the
        # issuer mints 256 bits. A short token in this column is a bug, and the
        # database is the last place able to notice it.
        sa.CheckConstraint("length(csrf_token) >= 32", name="ck_identity_sessions_csrf_length"),
    )
    # "Log out everywhere" scans by user; the expiry sweep scans by time.
    op.create_index("ix_identity_sessions_user", "identity_sessions", ["user_id", "created_at"])
    op.create_index("ix_identity_sessions_expiry", "identity_sessions", ["expires_at"])


def downgrade() -> None:
    # Reverse creation order. Only Phase 12 objects are touched.
    op.drop_index("ix_identity_sessions_expiry", table_name="identity_sessions")
    op.drop_index("ix_identity_sessions_user", table_name="identity_sessions")
    op.drop_table("identity_sessions")
