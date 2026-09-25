"""Who is making the request.

`17-SECURITY.md` §3 describes two kinds of authenticated caller: a browser session
and an API key. They differ in exactly one security-relevant way, and §7 states it:

> API-key principals are exempt from CSRF (no ambient credential to abuse) but remain
> subject to origin checks where a browser is involved.

So the kind is modelled even though only the session path can currently issue a
principal. Leaving it out would mean the CSRF rule had no way to express its own
exemption, and the exemption would arrive later as a special case somewhere else.

**API keys are not implemented.** `PrincipalKind.API_KEY` exists so the rule is
expressible and tested; nothing in this build constructs one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from oipulse.identity.permissions import Permission

__all__ = ["Principal", "PrincipalKind"]


class PrincipalKind(StrEnum):
    """How the caller authenticated."""

    #: A browser session cookie carrying an opaque `identity_sessions.id`.
    SESSION = "SESSION"
    #: A programmatic key (`17` §3). Declared, not implemented.
    API_KEY = "API_KEY"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller and what it may do.

    `permissions` is a snapshot taken from the session record rather than looked up
    per request. `17` §3 stores `permissions_snapshot` on the session for exactly
    this reason: a permission granted after a session was issued does not silently
    take effect on that session, and revoking the session is what applies a change.
    The alternative — resolving permissions live — means a privilege change has no
    single point at which it becomes true.
    """

    identity_id: str
    kind: PrincipalKind
    permissions: frozenset[Permission]
    #: The session this principal came from, for audit and revocation. `None` for
    #: an API key.
    session_id: str | None = None

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def is_admin(self) -> bool:
        return Permission.ADMIN in self.permissions

    def __repr__(self) -> str:  # pragma: no cover - trivial
        """Never prints the session id.

        `17` §2 requires credentials to be structurally redacted rather than
        redacted by discipline. The session id is a bearer credential: anything
        holding it can act as this principal, so it must not reach a log line
        through an accidental interpolation of the principal.
        """
        return (
            f"Principal(identity_id={self.identity_id!r}, kind={self.kind.value}, "
            f"permissions={sorted(p.value for p in self.permissions)}, session_id=<redacted>)"
        )
