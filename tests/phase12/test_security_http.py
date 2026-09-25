"""The security boundary over real HTTP.

The remediation brief §7: "Test direct HTTP behavior, not just internal functions."

`tests/phase12/test_authentication.py`, `test_authorization.py` and `test_csrf.py`
cover the rules; this covers the wiring — that the middleware is actually installed,
that a cookie becomes a session, that the refusal reaches the client as a status and
an envelope, and that the cookie flags `17-SECURITY.md` §3 requires are set.

**These skip in the development sandbox** and run in CI. `fastapi` cannot be
installed here: the package registry is unreachable. A skip is reported as a skip
and never as a pass, and the rules the skipped tests wire together are exercised by
the three pure suites above, which do run here.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

try:  # pragma: no cover - import guard
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - the development sandbox
    HAVE_FASTAPI = False

from oipulse.identity import Permission
from oipulse.identity.csrf import CSRF_COOKIE, CSRF_HEADER
from oipulse.identity.sessions import SESSION_COOKIE

ORIGIN = "https://terminal.oipulse.test"


def _client(*, permissions: frozenset[Permission], owner: str | None = "user-1"):
    """An app with the gate installed and one session in the store."""
    from oipulse.api.security import (
        InMemorySessionStore,
        SecurityConfig,
        install_security,
        issue_session,
    )

    app = FastAPI()

    @app.get("/ops/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/signals")
    async def signals() -> dict[str, object]:
        return {"data": [], "meta": {}}

    @app.get("/paper-trading/accounts/{account_id}")
    async def account(account_id: str) -> dict[str, object]:
        return {"data": {"account_id": account_id}, "meta": {}}

    @app.post("/paper-trading/accounts/{account_id}/intents")
    async def intents(account_id: str) -> dict[str, object]:
        return {"data": {"account_id": account_id}, "meta": {}}

    @app.post("/risk/kill-switch")
    async def kill_switch() -> dict[str, object]:
        return {"data": {"engaged": True}, "meta": {}}

    @app.post("/reconciliation/trigger")
    async def trigger() -> dict[str, object]:
        return {"data": {}, "meta": {}}

    store = InMemorySessionStore()
    record = issue_session(
        user_id="user-1", permissions=permissions, at=datetime.now(tz=UTC), ttl=timedelta(hours=1)
    )
    store.put(record)
    app.state.session_store = store
    app.state.account_owner_lookup = lambda _account_id: owner

    install_security(
        app,
        SecurityConfig(allowed_origins=frozenset({ORIGIN}), secure_cookies=False),
    )
    return TestClient(app), record


@unittest.skipUnless(HAVE_FASTAPI, "fastapi is not installed in this environment")
class AnonymousOverHttp(unittest.TestCase):
    """Brief §2: the three named operational routes must reject anonymous callers."""

    def setUp(self) -> None:
        self.client, _ = _client(permissions=frozenset({Permission.ADMIN}))

    def test_kill_switch_rejects_an_anonymous_post(self) -> None:
        response = self.client.post("/risk/kill-switch", headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 401)

    def test_paper_intents_rejects_an_anonymous_post(self) -> None:
        response = self.client.post(
            "/paper-trading/accounts/acct-1/intents", headers={"Origin": ORIGIN}
        )
        self.assertEqual(response.status_code, 401)

    def test_reconciliation_trigger_rejects_an_anonymous_post(self) -> None:
        response = self.client.post("/reconciliation/trigger", headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 401)

    def test_a_read_route_rejects_an_anonymous_get(self) -> None:
        self.assertEqual(self.client.get("/signals").status_code, 401)

    def test_the_health_probe_is_public(self) -> None:
        self.assertEqual(self.client.get("/ops/health").status_code, 200)

    def test_a_refusal_carries_the_api_spec_error_envelope(self) -> None:
        body = self.client.get("/signals").json()
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], "PERMISSION_DENIED")
        self.assertEqual(body["error"]["message"], "authentication required")

    def test_a_401_asks_the_client_to_re_authenticate(self) -> None:
        self.assertIn("WWW-Authenticate", self.client.get("/signals").headers)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi is not installed in this environment")
class AuthenticatedOverHttp(unittest.TestCase):
    def test_a_valid_session_cookie_reaches_a_permitted_route(self) -> None:
        client, record = _client(permissions=frozenset({Permission.MARKET_DATA_READ}))
        client.cookies.set(SESSION_COOKIE, record.id)
        self.assertEqual(client.get("/signals").status_code, 200)

    def test_a_forged_session_cookie_is_refused(self) -> None:
        client, _ = _client(permissions=frozenset({Permission.MARKET_DATA_READ}))
        client.cookies.set(SESSION_COOKIE, "forged")
        self.assertEqual(client.get("/signals").status_code, 401)

    def test_a_revoked_session_stops_working_immediately(self) -> None:
        client, record = _client(permissions=frozenset({Permission.MARKET_DATA_READ}))
        client.cookies.set(SESSION_COOKIE, record.id)
        self.assertEqual(client.get("/signals").status_code, 200)
        client.app.state.session_store.revoke(record.id, at=datetime.now(tz=UTC))
        self.assertEqual(client.get("/signals").status_code, 401)

    def test_a_missing_permission_is_403_not_404(self) -> None:
        client, record = _client(permissions=frozenset({Permission.MARKET_DATA_READ}))
        client.cookies.set(SESSION_COOKIE, record.id)
        response = client.post(
            "/risk/kill-switch",
            headers={"Origin": ORIGIN, CSRF_HEADER: record.csrf_token},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("ADMIN", response.json()["error"]["message"])

    def test_another_identity_s_account_is_403(self) -> None:
        client, record = _client(
            permissions=frozenset({Permission.PAPER_TRADE}), owner="someone-else"
        )
        client.cookies.set(SESSION_COOKIE, record.id)
        self.assertEqual(client.get("/paper-trading/accounts/acct-9").status_code, 403)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi is not installed in this environment")
class CsrfOverHttp(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.record = _client(permissions=frozenset({Permission.ADMIN}))
        self.client.cookies.set(SESSION_COOKIE, self.record.id)
        self.client.cookies.set(CSRF_COOKIE, self.record.csrf_token)

    def _post(self, **headers: str):
        return self.client.post("/risk/kill-switch", headers=headers)

    def test_a_valid_same_site_mutation_succeeds(self) -> None:
        response = self._post(Origin=ORIGIN, **{CSRF_HEADER: self.record.csrf_token})
        self.assertEqual(response.status_code, 200)

    def test_a_missing_origin_is_refused(self) -> None:
        self.assertEqual(self._post(**{CSRF_HEADER: self.record.csrf_token}).status_code, 403)

    def test_an_invalid_origin_is_refused(self) -> None:
        response = self._post(Origin="https://evil.test", **{CSRF_HEADER: self.record.csrf_token})
        self.assertEqual(response.status_code, 403)

    def test_a_missing_csrf_token_is_refused(self) -> None:
        self.assertEqual(self._post(Origin=ORIGIN).status_code, 403)

    def test_a_mismatched_csrf_token_is_refused(self) -> None:
        self.assertEqual(self._post(Origin=ORIGIN, **{CSRF_HEADER: "wrong"}).status_code, 403)

    def test_a_get_needs_neither_origin_nor_token(self) -> None:
        client, record = _client(permissions=frozenset({Permission.MARKET_DATA_READ}))
        client.cookies.set(SESSION_COOKIE, record.id)
        self.assertEqual(client.get("/signals").status_code, 200)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi is not installed in this environment")
class CookieFlags(unittest.TestCase):
    """`17` §3: HttpOnly, Secure in production, SameSite=Lax, host-scoped."""

    def _set(self, *, secure: bool):
        from fastapi import Response

        from oipulse.api.security import issue_session, set_session_cookies

        record = issue_session(
            user_id="user-1",
            permissions=frozenset({Permission.MARKET_DATA_READ}),
            at=datetime.now(tz=UTC),
        )
        response = Response()
        set_session_cookies(response, record, secure=secure, ttl=timedelta(hours=1))
        return [v for k, v in response.raw_headers if k == b"set-cookie"]

    def test_the_session_cookie_is_httponly_and_lax(self) -> None:
        header = next(h.decode() for h in self._set(secure=True) if SESSION_COOKIE in h.decode())
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header.replace("SameSite=Lax", "SameSite=lax"))
        self.assertIn("Path=/", header)

    def test_the_session_cookie_is_secure_in_production(self) -> None:
        header = next(h.decode() for h in self._set(secure=True) if SESSION_COOKIE in h.decode())
        self.assertIn("Secure", header)

    def test_the_csrf_cookie_is_readable_by_the_client(self) -> None:
        """A double submit needs the client to echo it; HttpOnly would prevent that."""
        header = next(h.decode() for h in self._set(secure=True) if CSRF_COOKIE in h.decode())
        self.assertNotIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header.replace("SameSite=Lax", "SameSite=lax"))

    def test_no_cookie_carries_a_domain_so_it_stays_host_scoped(self) -> None:
        for raw in self._set(secure=True):
            self.assertNotIn("Domain=", raw.decode())


if __name__ == "__main__":
    unittest.main()
