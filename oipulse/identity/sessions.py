"""Server-side sessions. `17-SECURITY.md` §3.

The specification's record, field for field:

```
identity_sessions
  id (opaque, random)  user_id  created_at  last_seen_at  expires_at
  revoked_at  user_agent  ip  permissions_snapshot
```

and the property that motivates it:

> The legacy design signs the literal string `user:{id}` and stores nothing, so
> logout clears a cookie while the signed value remains valid until expiry. There is
> no revocation and no "log out everywhere". ... The cookie carries only the opaque
> id. Server-side lookup means revocation is immediate and per-session.

So the cookie is not a token to be verified — it is a lookup key, and every request
resolves it against stored state. There is no signed form of a session anywhere in
this module, deliberately: a signature would make a revoked session still verify,
which is exactly the legacy defect.

### Four ways a session fails, kept apart

`validate_session` returns which one. They are different facts about the world — a
revoked session means somebody logged out, an expired one means time passed, an
idle-timed-out one means the operator walked away, and an absent one means the
cookie is forged or the record was purged. Collapsing them into a boolean makes the
audit trail unable to say what happened, and `17` §9 requires session revocation to
be an audited event distinguishable from expiry.

The caller never learns which: `gate` maps all four to the same 401 with the same
body, because telling an attacker that a session id existed but was revoked
confirms the id was real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from oipulse.identity.permissions import Permission, parse_permissions

__all__ = [
    "DEFAULT_IDLE_TIMEOUT",
    "DEFAULT_SESSION_TTL",
    "SESSION_COOKIE",
    "SessionRecord",
    "SessionStatus",
    "SessionStore",
    "validate_session",
]

#: Host-scoped, `HttpOnly`, `Secure` in production, `SameSite=Lax` (`17` §3). The
#: `__Host-` prefix is not used: it forbids a `Domain` attribute and requires
#: `Secure`, which would make the cookie unusable over plain HTTP in development,
#: and a cookie that only works in production is a cookie nobody tests.
SESSION_COOKIE = "oipulse_session"

#: `17` §3 requires "a documented TTL and idle timeout" without giving numbers.
#: These are an implementation choice, stated here rather than buried, and
#: overridable through `Settings`. Twelve hours covers a full trading session
#: including pre-open and settlement; two hours of inactivity is long enough not to
#: interrupt research and short enough that an unattended terminal closes itself.
DEFAULT_SESSION_TTL = timedelta(hours=12)
DEFAULT_IDLE_TIMEOUT = timedelta(hours=2)


class SessionStatus(StrEnum):
    """Why a session is or is not usable."""

    ACTIVE = "ACTIVE"
    #: No record with that id. A forged cookie, or a purged record.
    NOT_FOUND = "NOT_FOUND"
    #: `revoked_at` is set. Logout, or "log out everywhere".
    REVOKED = "REVOKED"
    #: Past `expires_at`.
    EXPIRED = "EXPIRED"
    #: `last_seen_at` is older than the idle timeout.
    IDLE_TIMEOUT = "IDLE_TIMEOUT"

    @property
    def is_usable(self) -> bool:
        return self is SessionStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One row of `identity_sessions`. The field list is `17` §3's, unchanged."""

    id: str
    user_id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    user_agent: str | None = None
    ip: str | None = None
    #: The permissions in force when the session was issued (`17` §3).
    permissions_snapshot: frozenset[Permission] = frozenset()
    #: Server side of the double-submit pair (`17` §7.3). Compared against the
    #: header; never sent anywhere except as the cookie the browser echoes.
    csrf_token: str = ""

    @classmethod
    def from_row(cls, row: dict[str, object]) -> SessionRecord:
        """Build from a database row, tolerating an unknown permission string."""
        return cls(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            created_at=_as_datetime(row["created_at"]),
            last_seen_at=_as_datetime(row["last_seen_at"]),
            expires_at=_as_datetime(row["expires_at"]),
            revoked_at=_as_optional_datetime(row.get("revoked_at")),
            user_agent=_as_optional_str(row.get("user_agent")),
            ip=_as_optional_str(row.get("ip")),
            permissions_snapshot=parse_permissions(row.get("permissions_snapshot")),
            csrf_token=str(row.get("csrf_token") or ""),
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        """Redacted. The id and the CSRF token are both bearer credentials."""
        return (
            f"SessionRecord(id=<redacted>, user_id={self.user_id!r}, "
            f"expires_at={self.expires_at.isoformat()}, "
            f"revoked={self.revoked_at is not None}, csrf_token=<redacted>)"
        )


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError(f"expected a datetime, got {type(value).__name__}")


def _as_optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def validate_session(
    record: SessionRecord | None,
    *,
    at: datetime,
    idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
) -> SessionStatus:
    """Decide whether a session may be used, and say why if not.

    The order is deliberate. Revocation is checked before expiry so a session that
    was revoked *and* has since expired reports the revocation — that is the
    security-relevant fact, and `17` §9 audits it as its own event.

    `at` is passed in rather than read from the clock. Every module in this
    repository takes its time as an argument (`tools/check_clock_access.py`), and it
    is what makes "this session expires in one second" a test rather than a sleep.
    """
    if record is None:
        return SessionStatus.NOT_FOUND
    if record.revoked_at is not None:
        return SessionStatus.REVOKED
    if at >= record.expires_at:
        return SessionStatus.EXPIRED
    if at - record.last_seen_at >= idle_timeout:
        return SessionStatus.IDLE_TIMEOUT
    return SessionStatus.ACTIVE


class SessionStore(Protocol):
    """What the gate needs from persistence, and nothing more.

    A protocol rather than a class so the pure decision has no database import, and
    so a test can supply a dict. The real implementation lives behind the API layer.
    """

    def get(self, session_id: str) -> SessionRecord | None:
        """The record, or `None`. Must not raise for an unknown id."""
        ...

    def touch(self, session_id: str, *, at: datetime) -> None:
        """Advance `last_seen_at`. Called only after a session validates."""
        ...

    def revoke(self, session_id: str, *, at: datetime) -> None:
        """Set `revoked_at`. Idempotent."""
        ...
