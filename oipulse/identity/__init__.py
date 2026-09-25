"""Authentication and authorization. `17-SECURITY.md` §3, §4 and §7.

Deliberately a separate top-level package, and deliberately free of any web stack.

Phase 12's independent verification returned NOT VERIFIED with three security
blockers: no server-side session validation, no permission enforcement, and no CSRF
defence. This package is the remediation, and its shape follows from one constraint
that the earlier work got wrong — **the decision has to be testable without a web
framework installed.**

`fastapi` cannot be installed in this environment, so anything expressed as a
FastAPI dependency is unrunnable here and reaches CI unexercised. So the decision is
a pure function of a value object: `evaluate()` takes `RequestFacts` — method, path,
headers, cookie — and returns an `AccessDecision`. `oipulse/api/security.py` is the
thin adapter that builds the facts from a `Request` and turns the decision into a
response. Every rule in §3, §4 and §7 is therefore covered by tests that run on a
bare interpreter, and the HTTP layer that runs in CI is a few dozen lines of glue.

The modules:

- `permissions` — the exact five from §4, and the route policy that maps each
  endpoint to the one it needs.
- `sessions` — the `identity_sessions` record from §3 and its validation: expiry,
  revocation, idle timeout, existence.
- `csrf` — the three-part defence from §7, evaluated together rather than as three
  independent options.
- `gate` — the composition, in the order §2 of the remediation brief specifies:
  session extraction, lookup and validation, identity, route authorization.
"""

from oipulse.identity.csrf import (
    CSRF_HEADER,
    CsrfOutcome,
    evaluate_csrf,
)
from oipulse.identity.gate import (
    AccessDecision,
    AccessOutcome,
    RequestFacts,
    evaluate,
)
from oipulse.identity.permissions import (
    PUBLIC_ROUTES,
    Permission,
    RoutePolicy,
    policy_for,
)
from oipulse.identity.principal import Principal, PrincipalKind
from oipulse.identity.sessions import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_SESSION_TTL,
    SESSION_COOKIE,
    SessionRecord,
    SessionStatus,
    validate_session,
)

__all__ = [
    "CSRF_HEADER",
    "DEFAULT_IDLE_TIMEOUT",
    "DEFAULT_SESSION_TTL",
    "PUBLIC_ROUTES",
    "SESSION_COOKIE",
    "AccessDecision",
    "AccessOutcome",
    "CsrfOutcome",
    "Permission",
    "Principal",
    "PrincipalKind",
    "RequestFacts",
    "RoutePolicy",
    "SessionRecord",
    "SessionStatus",
    "evaluate",
    "evaluate_csrf",
    "policy_for",
    "validate_session",
]
