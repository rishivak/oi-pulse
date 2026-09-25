"""The HTTP adapter for the access gate. `17-SECURITY.md` §3, §4 and §7.

Deliberately thin. Every rule lives in `oipulse/identity/`, which imports no web
stack and is therefore testable on a bare interpreter; this module builds
`RequestFacts` from a Starlette `Request`, calls `evaluate`, and turns the
`AccessDecision` into a response. Phase 12's first attempt put the security story in
places that could not be executed here, and an unexecuted security control is a
claim rather than a control.

### Middleware, not a per-route dependency

A dependency has to be added to each route, and a route added without it is open.
Middleware is the other way round: every request passes through, and
`oipulse/identity/permissions.py` closes by default — a path with no policy entry is
refused. `tools/check_auth_boundary.py` then checks that the policy table covers
every route the generated contract knows about, so "which routes are unprotected?"
has a mechanical answer rather than a reviewed one.

### The clock

Injected, like every other clock in this repository. `00-OVERVIEW.md` §5 requires
that no module read wall time directly, and `oipulse/core/clock.py` is the single
authorised call site; the gate takes a `Clock` and defaults to `SystemClock`.

That is not ceremony here. Session expiry and the idle timeout are time comparisons,
and a test that has to sleep to reach them is a test nobody writes. With the clock
injected, "this session expired one second ago" is an argument.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from oipulse.core.clock import Clock, SystemClock
from oipulse.identity.csrf import CSRF_COOKIE
from oipulse.identity.gate import AccessDecision, AccessOutcome, RequestFacts, evaluate
from oipulse.identity.permissions import Permission
from oipulse.identity.sessions import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_SESSION_TTL,
    SESSION_COOKIE,
    SessionRecord,
)
from oipulse.observability.logging import get_logger

__all__ = [
    "InMemorySessionStore",
    "SecurityConfig",
    "install_security",
    "issue_session",
    "set_session_cookies",
]

log = get_logger(__name__)

#: `{account_id}` is the only owned scope in the API surface today.
_ACCOUNT_IN_PATH = re.compile(
    r"/accounts/(?P<account_id>[^/]+)|/(?:state|status)/(?P<scoped>[^/]+)"
)

#: Bytes of entropy for a session id and a CSRF token. 32 bytes is 256 bits, which
#: puts guessing an opaque id out of reach; `17` §3 requires the id be random and
#: says nothing weaker would do.
_TOKEN_BYTES = 32


class SecurityConfig:
    """What the middleware needs, resolved once at startup."""

    def __init__(
        self,
        *,
        allowed_origins: frozenset[str],
        session_ttl: timedelta = DEFAULT_SESSION_TTL,
        idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
        secure_cookies: bool = True,
        clock: Clock | None = None,
    ) -> None:
        self.allowed_origins = allowed_origins
        self.session_ttl = session_ttl
        self.idle_timeout = idle_timeout
        self.secure_cookies = secure_cookies
        self.clock: Clock = clock or SystemClock()


class InMemorySessionStore:
    """A store for tests and for a single-process development run.

    The durable implementation reads `identity_sessions` (migration 0012). This one
    exists so the enforcement path can be exercised without PostgreSQL, and so the
    HTTP tests in CI have somewhere to put a session. It is **not** a fallback: a
    process that reaches production without a real store has no sessions at all and
    therefore serves nothing but the two public probes, which is the safe failure.
    """

    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}

    def put(self, record: SessionRecord) -> None:
        self._records[record.id] = record

    def get(self, session_id: str) -> SessionRecord | None:
        return self._records.get(session_id)

    def touch(self, session_id: str, *, at: datetime) -> None:
        record = self._records.get(session_id)
        if record is None:
            return
        self._records[session_id] = SessionRecord(
            id=record.id,
            user_id=record.user_id,
            created_at=record.created_at,
            last_seen_at=at,
            expires_at=record.expires_at,
            revoked_at=record.revoked_at,
            user_agent=record.user_agent,
            ip=record.ip,
            permissions_snapshot=record.permissions_snapshot,
            csrf_token=record.csrf_token,
        )

    def revoke(self, session_id: str, *, at: datetime) -> None:
        record = self._records.get(session_id)
        if record is None or record.revoked_at is not None:
            return
        self._records[session_id] = SessionRecord(
            id=record.id,
            user_id=record.user_id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            expires_at=record.expires_at,
            revoked_at=at,
            user_agent=record.user_agent,
            ip=record.ip,
            permissions_snapshot=record.permissions_snapshot,
            csrf_token=record.csrf_token,
        )


def issue_session(
    *,
    user_id: str,
    permissions: frozenset[Permission],
    at: datetime,
    ttl: timedelta = DEFAULT_SESSION_TTL,
    user_agent: str | None = None,
    ip: str | None = None,
) -> SessionRecord:
    """Mint a session record.

    **There is no HTTP endpoint that calls this.** `17-SECURITY.md` §11 says the
    legacy `/auth/offline-session` — unauthenticated, issuing a session for whichever
    user owned the newest snapshot — is "not carried forward in any form", and no
    document in `docs/design/` specifies a user login flow to replace it. §2's OAuth
    is for broker credentials, not for signing a person in.

    So issuance is a server-side operation an operator or a future login route calls,
    and the gate is closed to everyone until one exists. That is the direction this
    should fail in: a system nobody can sign into is recoverable, and an invented
    login endpoint is an authentication bypass with good intentions.
    """
    return SessionRecord(
        id=secrets.token_urlsafe(_TOKEN_BYTES),
        user_id=user_id,
        created_at=at,
        last_seen_at=at,
        expires_at=at + ttl,
        revoked_at=None,
        user_agent=user_agent,
        ip=ip,
        permissions_snapshot=permissions,
        csrf_token=secrets.token_urlsafe(_TOKEN_BYTES),
    )


def set_session_cookies(
    response: Response, record: SessionRecord, *, secure: bool, ttl: timedelta
) -> None:
    """Apply `17` §3's cookie flags, and `17` §7.1's `SameSite=Lax`.

    The session cookie is `HttpOnly` — script must not be able to read a bearer
    credential. The CSRF cookie deliberately is not: the client has to echo it in a
    header, which is the whole mechanism of a double submit. It is not a credential
    on its own, because it is only ever accepted alongside the session cookie and is
    compared against the server's record of that session.
    """
    max_age = int(ttl.total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        record.id,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        record.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _account_id_from(path: str) -> str | None:
    match = _ACCOUNT_IN_PATH.search(path)
    if match is None:
        return None
    return match.group("account_id") or match.group("scoped")


def _owner_lookup(request: Request) -> Callable[[str], str | None] | None:
    resolver = getattr(request.app.state, "account_owner_lookup", None)
    if resolver is None:
        return None
    return resolver  # type: ignore[no-any-return]


def _refusal(decision: AccessDecision, path: str) -> JSONResponse:
    """The `12-API_SPEC.md` §4 error envelope for a refusal."""
    body: dict[str, Any] = {
        "error": {
            "code": decision.error_code,
            "message": decision.client_detail,
            "details": {"path": path, "outcome": decision.outcome.value},
        }
    }
    response = JSONResponse(status_code=decision.http_status, content=body)
    if decision.outcome is AccessOutcome.UNAUTHENTICATED:
        # Tells a browser client to re-authenticate rather than retry.
        response.headers["WWW-Authenticate"] = "Cookie"
    return response


def install_security(app: FastAPI, config: SecurityConfig) -> None:
    """Attach the gate to every request.

    Registered as an HTTP middleware so no route can be added outside it. The order
    inside is `identity/gate.py`'s, which is the remediation brief §2's: extract,
    look up, validate, identify, authorize.
    """
    app.state.security_config = config

    @app.middleware("http")
    async def _gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        store = getattr(request.app.state, "session_store", None)
        lookup = (
            store.get
            if store is not None
            # No store means no session can ever validate. Every protected route
            # answers 401, which is correct: the process cannot authenticate anyone.
            else (lambda _session_id: None)
        )
        at = config.clock.now()
        facts = RequestFacts(
            method=request.method,
            path=request.url.path,
            headers={k.lower(): v for k, v in request.headers.items()},
            cookies=dict(request.cookies),
            account_id=_account_id_from(request.url.path),
        )
        decision = evaluate(
            facts,
            at=at,
            lookup=lookup,
            allowed_origins=config.allowed_origins,
            idle_timeout=config.idle_timeout,
            owner_lookup=_owner_lookup(request),
        )

        if not decision.is_allowed:
            # `17` §9: security-relevant refusals are recorded. The session id is
            # never logged -- it is a bearer credential.
            log.warning(
                "access_refused",
                extra={
                    "path": request.url.path,
                    "method": request.method,
                    "outcome": decision.outcome.value,
                    "identity": (
                        None if decision.principal is None else decision.principal.identity_id
                    ),
                    "required": (
                        None if decision.policy is None else decision.policy.required.value
                    ),
                },
            )
            return _refusal(decision, request.url.path)

        request.state.principal = decision.principal
        if decision.principal is not None and store is not None:
            store.touch(decision.principal.session_id or "", at=at)
        return await call_next(request)
