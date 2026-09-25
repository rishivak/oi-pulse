"""`identity_sessions`. `17-SECURITY.md` §3.

The specification's record, field for field:

```
identity_sessions
  id (opaque, random)  user_id  created_at  last_seen_at  expires_at
  revoked_at  user_agent  ip  permissions_snapshot
```

`csrf_token` is the one addition, and §7.3 requires it: the double-submit token is
"issued per session ... compared against the session record server-side". There is
nowhere else for it to live, and deriving it from the session id would defeat the
purpose — anything that could read the id could compute the token.

### Why the id is the primary key

`17` §3 calls it "opaque, random", and the cookie carries only it. A surrogate
integer key with the opaque id in a second column would mean a lookup by cookie hits
a secondary index while the primary key is meaningless to every query anyone writes.
The opaque id is 256 bits of `secrets.token_urlsafe`, so it is not guessable and it
is not enumerable.

### Why `permissions_snapshot` is on the row

Also §3. A permission granted after a session was issued does not take effect on
that session; revoking it is what applies a change. Resolving permissions live would
mean a privilege change has no single moment at which it becomes true, and no
session to revoke in order to undo it.

### Retention

Rows are kept after expiry and after revocation rather than deleted. `17` §9 audits
session revocation, and a revoked session whose row is gone cannot be distinguished
from one that never existed — which is exactly the question an incident asks.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from oipulse.persistence.trading_tables import METADATA

__all__ = ["IDENTITY_TABLES", "identity_sessions"]

identity_sessions = sa.Table(
    "identity_sessions",
    METADATA,
    # Opaque and random (`17` §3). The cookie carries this and nothing else.
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("user_id", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    # NULL means live. Set once, never cleared: un-revoking is not an operation.
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("user_agent", sa.Text, nullable=True),
    sa.Column("ip", sa.Text, nullable=True),
    # The five permissions in force when the session was issued (`17` §3, §4).
    sa.Column(
        "permissions_snapshot",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    ),
    # Server side of the double submit (`17` §7.3).
    sa.Column("csrf_token", sa.Text, nullable=False),
    # A session that expired before it was created, or was revoked before it was
    # created, is a clock or a code error. The database refuses either.
    sa.CheckConstraint("expires_at > created_at", name="ck_identity_sessions_expiry_after_start"),
    sa.CheckConstraint(
        "revoked_at IS NULL OR revoked_at >= created_at",
        name="ck_identity_sessions_revoked_after_start",
    ),
    sa.CheckConstraint("length(csrf_token) >= 32", name="ck_identity_sessions_csrf_length"),
    # "log out everywhere" is a scan by user; expiry sweeps are a scan by time.
    sa.Index("ix_identity_sessions_user", "user_id", "created_at"),
    sa.Index("ix_identity_sessions_expiry", "expires_at"),
)

IDENTITY_TABLES = ("identity_sessions",)
