"""The access decision, composed. `17-SECURITY.md` §3, §4 and §7.

The Phase 12 remediation brief §2 states the required order:

```
Request
  ↓
Session extraction
  ↓
identity_sessions lookup/validation
  ↓
identity
  ↓
route authorization
```

`evaluate` is that pipeline as one pure function. It takes `RequestFacts` — the
method, path, cookies and headers, already extracted — and returns an
`AccessDecision`. It touches no framework, no clock and no database: the session
lookup arrives as a callable, and `at` arrives as an argument.

That shape is the point. `fastapi` cannot be installed in this environment, so an
authorization rule expressed as a FastAPI dependency would ship unexercised. Here
every rule in §3, §4 and §7 is a test that runs on a bare interpreter, and
`oipulse/api/security.py` is the adapter that builds the facts and renders the
decision. The HTTP-level tests still exist and run in CI; they check the wiring,
not the rules.

### What a refusal says

Little. A 401 does not distinguish an absent session from a revoked or expired one
— telling an attacker that an id was real is a small oracle, but a free one. A 403
names the missing permission, because the caller is already authenticated and
needs to know what to ask for.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from oipulse.identity.csrf import (
    CSRF_HEADER,
    CsrfOutcome,
    CsrfResult,
    evaluate_csrf,
)
from oipulse.identity.permissions import (
    Permission,
    RoutePolicy,
    is_public,
    policy_for,
)
from oipulse.identity.principal import Principal, PrincipalKind
from oipulse.identity.sessions import (
    DEFAULT_IDLE_TIMEOUT,
    SESSION_COOKIE,
    SessionRecord,
    SessionStatus,
    validate_session,
)

__all__ = [
    "AccessDecision",
    "AccessOutcome",
    "RequestFacts",
    "evaluate",
]


class AccessOutcome(StrEnum):
    """What the gate decided."""

    #: A route on the public allow-list. Two container probes, and nothing else.
    PUBLIC = "PUBLIC"
    ALLOWED = "ALLOWED"
    #: No session, or a session that is not usable. 401.
    UNAUTHENTICATED = "UNAUTHENTICATED"
    #: Authenticated, but the permission is absent. 403.
    FORBIDDEN = "FORBIDDEN"
    #: Authenticated and permitted, but the resource belongs to another identity. 403.
    WRONG_OWNER = "WRONG_OWNER"
    #: CSRF refused the request. 403.
    CSRF_FAILED = "CSRF_FAILED"
    #: The route has no policy entry. Closed by default. 403.
    NO_POLICY = "NO_POLICY"
    #: An account-scoped route was reached with no way to resolve ownership. 503.
    OWNERSHIP_UNAVAILABLE = "OWNERSHIP_UNAVAILABLE"

    @property
    def is_allowed(self) -> bool:
        return self in (AccessOutcome.PUBLIC, AccessOutcome.ALLOWED)


#: What each outcome becomes on the wire, and the `12-API_SPEC.md` §4 error code.
_STATUS: dict[AccessOutcome, tuple[int, str]] = {
    AccessOutcome.PUBLIC: (200, ""),
    AccessOutcome.ALLOWED: (200, ""),
    AccessOutcome.UNAUTHENTICATED: (401, "PERMISSION_DENIED"),
    AccessOutcome.FORBIDDEN: (403, "PERMISSION_DENIED"),
    AccessOutcome.WRONG_OWNER: (403, "PERMISSION_DENIED"),
    AccessOutcome.CSRF_FAILED: (403, "PERMISSION_DENIED"),
    AccessOutcome.NO_POLICY: (403, "PERMISSION_DENIED"),
    AccessOutcome.OWNERSHIP_UNAVAILABLE: (503, "PERMISSION_DENIED"),
}


@dataclass(frozen=True, slots=True)
class RequestFacts:
    """Everything the decision needs from a request, and nothing else.

    Header names are expected lower-cased by the adapter; `header()` lower-cases
    again so a test can pass them either way.
    """

    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    #: Resolved by the adapter from the path, when the route is account-scoped.
    account_id: str | None = None

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None

    @property
    def session_id(self) -> str | None:
        value = self.cookies.get(SESSION_COOKIE)
        return value if value else None


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """The answer, with enough detail to audit and to render."""

    outcome: AccessOutcome
    principal: Principal | None = None
    policy: RoutePolicy | None = None
    #: Set when the session lookup ran; `None` on a public route.
    session_status: SessionStatus | None = None
    csrf: CsrfResult | None = None
    #: Operator-facing reason. Never echoed verbatim to an unauthenticated caller.
    detail: str = ""

    @property
    def is_allowed(self) -> bool:
        return self.outcome.is_allowed

    @property
    def http_status(self) -> int:
        return _STATUS[self.outcome][0]

    @property
    def error_code(self) -> str:
        return _STATUS[self.outcome][1]

    @property
    def client_detail(self) -> str:
        """What the caller is told.

        An unauthenticated caller is told only that authentication is required: the
        four session failures are indistinguishable from outside, so a probe cannot
        learn that a session id it guessed was real but revoked.
        """
        if self.outcome is AccessOutcome.UNAUTHENTICATED:
            return "authentication required"
        return self.detail


SessionLookup = Callable[[str], SessionRecord | None]
#: Given an account id, the identity that owns it, or `None` if unknown.
OwnerLookup = Callable[[str], str | None]


def evaluate(
    facts: RequestFacts,
    *,
    at: datetime,
    lookup: SessionLookup,
    allowed_origins: frozenset[str],
    idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
    owner_lookup: OwnerLookup | None = None,
) -> AccessDecision:
    """Run the pipeline and return one decision."""
    method = facts.method.upper()

    # ---------------------------------------------------------------- public
    if is_public(method, facts.path):
        return AccessDecision(AccessOutcome.PUBLIC, detail="public route")

    # -------------------------------------------- route policy, closed by default
    policy = policy_for(method, facts.path)
    if policy is None:
        return AccessDecision(
            AccessOutcome.NO_POLICY,
            detail=(
                f"no authorization policy covers {method} {facts.path}. A route "
                f"without a policy is refused, not allowed: see "
                f"oipulse/identity/permissions.py."
            ),
        )

    # --------------------------------------- session extraction, lookup, validation
    session_id = facts.session_id
    record = lookup(session_id) if session_id else None
    status = validate_session(record, at=at, idle_timeout=idle_timeout)
    if not status.is_usable or record is None:
        return AccessDecision(
            AccessOutcome.UNAUTHENTICATED,
            policy=policy,
            session_status=status,
            detail=f"session {status.value.lower().replace('_', ' ')}",
        )

    # --------------------------------------------------------------- identity
    principal = Principal(
        identity_id=record.user_id,
        kind=PrincipalKind.SESSION,
        permissions=record.permissions_snapshot,
        session_id=record.id,
    )

    # -------------------------------------------------------- route authorization
    if not principal.has(policy.required) and not principal.is_admin:
        return AccessDecision(
            AccessOutcome.FORBIDDEN,
            principal=principal,
            policy=policy,
            session_status=status,
            detail=f"{policy.required.value} is required for {method} {facts.path}",
        )

    # ------------------------------------------------------------- ownership
    # Permission is not access to somebody else's account (`17` §4, defence in
    # depth). ADMIN is the documented exception: §4 grants it operational reach.
    if policy.account_scoped and not principal.is_admin:
        if owner_lookup is None:
            return AccessDecision(
                AccessOutcome.OWNERSHIP_UNAVAILABLE,
                principal=principal,
                policy=policy,
                session_status=status,
                detail=(
                    "this process cannot resolve account ownership, so an "
                    "account-scoped route is refused rather than served without "
                    "the check"
                ),
            )
        if facts.account_id is None:
            return AccessDecision(
                AccessOutcome.OWNERSHIP_UNAVAILABLE,
                principal=principal,
                policy=policy,
                session_status=status,
                detail="the account could not be read from the path",
            )
        owner = owner_lookup(facts.account_id)
        if owner is None or owner != principal.identity_id:
            return AccessDecision(
                AccessOutcome.WRONG_OWNER,
                principal=principal,
                policy=policy,
                session_status=status,
                detail="this account belongs to another identity",
            )

    # ------------------------------------------------------------------ CSRF
    csrf = evaluate_csrf(
        method=method,
        principal_kind=principal.kind,
        origin=facts.header("origin"),
        referer=facts.header("referer"),
        header_token=facts.header(CSRF_HEADER),
        session_token=record.csrf_token,
        allowed_origins=allowed_origins,
    )
    if not csrf.is_allowed:
        return AccessDecision(
            AccessOutcome.CSRF_FAILED,
            principal=principal,
            policy=policy,
            session_status=status,
            csrf=csrf,
            detail=csrf.detail or csrf.outcome.value,
        )

    return AccessDecision(
        AccessOutcome.ALLOWED,
        principal=principal,
        policy=policy,
        session_status=status,
        csrf=csrf,
    )


def required_permission(method: str, path: str) -> Permission | None:
    """Convenience for callers that only need the permission."""
    policy = policy_for(method, path)
    return None if policy is None else policy.required


def csrf_outcome_of(decision: AccessDecision) -> CsrfOutcome | None:
    return None if decision.csrf is None else decision.csrf.outcome
