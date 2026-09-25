"""Give a `TestClient` a session, for apps built by `create_app`.

The Phase 12 remediation puts an authentication and authorization boundary in front
of every route (`17-SECURITY.md` §3, §4, §7). That is a real behaviour change, and
the HTTP tests written in Phases 3 to 7 exercised a system without one: they called
endpoints anonymously and expected 200, 422 or 503.

Those tests are **not weakened here.** Every assertion they make about status codes,
envelopes and error handling is unchanged. What changes is that their client now
carries a session, because a client without one is no longer a client that reaches
the route — it is a client testing the new 401, which is covered separately in
`tests/phase12/test_security_http.py`.

The alternative would have been to make the gate optional in tests, and that is the
thing not to do: a boundary that can be switched off for the convenience of a test
suite is a boundary whose production behaviour nothing exercises.

`ALL_PERMISSIONS` is granted by default because these suites are testing routing and
serialization, not authorization. The tests that care about permissions grant them
one at a time, in `tests/phase12/`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from oipulse.identity import Permission
from oipulse.identity.csrf import CSRF_COOKIE, CSRF_HEADER
from oipulse.identity.sessions import SESSION_COOKIE

__all__ = ["TEST_ORIGIN", "authenticate", "write_headers"]

#: Must match what `authenticate` puts on the security config.
TEST_ORIGIN = "https://tests.oipulse.local"

ALL = frozenset(Permission)


def authenticate(
    app: Any,
    client: Any,
    *,
    permissions: frozenset[Permission] = ALL,
    user_id: str = "test-user",
) -> Any:
    """Install a session store, issue a session, and give the client its cookies.

    Also sets an account-ownership resolver that answers `user_id` for every
    account, so account-scoped routes resolve. Tests that care about the
    cross-account refusal override it; the ones here are not about ownership.

    Returns the session record, for a test that wants to revoke or expire it.
    """
    from oipulse.api.security import InMemorySessionStore, issue_session

    store = InMemorySessionStore()
    record = issue_session(
        user_id=user_id,
        permissions=permissions,
        at=datetime.now(tz=UTC),
        ttl=timedelta(hours=1),
    )
    store.put(record)
    app.state.session_store = store
    app.state.account_owner_lookup = lambda _account_id: user_id

    config = getattr(app.state, "security_config", None)
    if config is not None:
        config.allowed_origins = frozenset({TEST_ORIGIN})

    client.cookies.set(SESSION_COOKIE, record.id)
    client.cookies.set(CSRF_COOKIE, record.csrf_token)
    client.headers.update(write_headers(record))
    return record


def write_headers(record: Any) -> dict[str, str]:
    """The `Origin` and CSRF header a state-changing request needs (`17` §7)."""
    return {"Origin": TEST_ORIGIN, CSRF_HEADER: record.csrf_token}
