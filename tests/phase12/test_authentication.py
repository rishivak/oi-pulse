"""Authentication. `17-SECURITY.md` §3.

Phase 12's independent verification returned NOT VERIFIED because "the backend
currently has no session validation". These tests cover the boundary that
remediates it, at the level where the decision is made: `evaluate` is a pure
function, so every case below runs on a bare interpreter rather than waiting for a
web stack that cannot be installed here. `test_security_http.py` exercises the same
paths over real HTTP in CI.

The brief's §7 list, items 1 to 5.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from oipulse.identity import (
    AccessOutcome,
    Permission,
    SessionStatus,
    evaluate,
    validate_session,
)
from oipulse.identity.sessions import DEFAULT_IDLE_TIMEOUT, DEFAULT_SESSION_TTL
from tests.phase12._identity import ALLOWED, NOW, facts, lookup_of, session

ADMIN = frozenset({Permission.ADMIN})

#: The three operational routes the remediation brief §2 names explicitly.
OPERATIONAL = (
    ("POST", "/risk/kill-switch"),
    ("POST", "/paper-trading/accounts/acct-1/intents"),
    ("POST", "/reconciliation/trigger"),
)


class AnonymousRequests(unittest.TestCase):
    """Brief §7.1 and §2's explicit list."""

    def test_the_three_named_operational_routes_reject_anonymous_callers(self) -> None:
        for method, path in OPERATIONAL:
            with self.subTest(route=f"{method} {path}"):
                decision = evaluate(
                    facts(method, path, session_id=None),
                    at=NOW,
                    lookup=lookup_of(),
                    allowed_origins=ALLOWED,
                )
                self.assertEqual(decision.outcome, AccessOutcome.UNAUTHENTICATED)
                self.assertEqual(decision.http_status, 401)

    def test_every_non_public_route_rejects_an_anonymous_caller(self) -> None:
        """Not a sample: the whole policy table.

        A boundary that protects the routes someone remembered is the boundary that
        was already missing.
        """
        from oipulse.identity.permissions import ROUTE_POLICIES

        checked = 0
        for policy in ROUTE_POLICIES:
            concrete = policy.template.replace("{account_id}", "acct-1")
            concrete = "/".join(
                "x" if part.startswith("{") else part for part in concrete.split("/")
            )
            decision = evaluate(
                facts(policy.method, concrete, session_id=None),
                at=NOW,
                lookup=lookup_of(),
                allowed_origins=ALLOWED,
            )
            self.assertIn(
                decision.outcome,
                (AccessOutcome.UNAUTHENTICATED, AccessOutcome.NO_POLICY),
                f"{policy.method} {concrete} was not refused",
            )
            checked += 1
        self.assertGreater(checked, 50, "the policy table was not traversed")

    def test_a_refusal_does_not_say_why(self) -> None:
        """A probe must not learn that a guessed session id was real."""
        absent = evaluate(
            facts("GET", "/signals", session_id="does-not-exist"),
            at=NOW,
            lookup=lookup_of(),
            allowed_origins=ALLOWED,
        )
        revoked = evaluate(
            facts("GET", "/signals"),
            at=NOW,
            lookup=lookup_of(session(revoked_at=NOW - timedelta(minutes=1))),
            allowed_origins=ALLOWED,
        )
        self.assertEqual(absent.client_detail, revoked.client_detail)
        self.assertEqual(absent.client_detail, "authentication required")
        # The server still knows, for the audit trail.
        self.assertEqual(absent.session_status, SessionStatus.NOT_FOUND)
        self.assertEqual(revoked.session_status, SessionStatus.REVOKED)

    def test_the_two_container_probes_are_public(self) -> None:
        for path in ("/ops/health", "/ops/ready"):
            decision = evaluate(
                facts("GET", path, session_id=None),
                at=NOW,
                lookup=lookup_of(),
                allowed_origins=ALLOWED,
            )
            self.assertEqual(decision.outcome, AccessOutcome.PUBLIC)


class SessionValidation(unittest.TestCase):
    """`17` §3. Brief §7 items 2 to 5."""

    def test_a_valid_session_authenticates(self) -> None:
        decision = evaluate(
            facts("GET", "/paper-trading/accounts"),
            at=NOW,
            lookup=lookup_of(session()),
            allowed_origins=ALLOWED,
        )
        self.assertEqual(decision.outcome, AccessOutcome.ALLOWED)
        assert decision.principal is not None
        self.assertEqual(decision.principal.identity_id, "user-1")

    def test_a_revoked_session_is_refused_immediately(self) -> None:
        """The property the legacy design lacked: revocation without waiting for TTL."""
        record = session(revoked_at=NOW - timedelta(seconds=1))
        self.assertEqual(validate_session(record, at=NOW), SessionStatus.REVOKED)
        decision = evaluate(
            facts("GET", "/paper-trading/accounts"),
            at=NOW,
            lookup=lookup_of(record),
            allowed_origins=ALLOWED,
        )
        self.assertEqual(decision.outcome, AccessOutcome.UNAUTHENTICATED)

    def test_an_expired_session_is_refused(self) -> None:
        record = session(expires_at=NOW - timedelta(seconds=1))
        self.assertEqual(validate_session(record, at=NOW), SessionStatus.EXPIRED)

    def test_expiry_is_inclusive_at_the_instant_it_expires(self) -> None:
        record = session(expires_at=NOW)
        self.assertEqual(validate_session(record, at=NOW), SessionStatus.EXPIRED)

    def test_an_idle_session_times_out(self) -> None:
        record = session(
            last_seen_at=NOW - DEFAULT_IDLE_TIMEOUT - timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=5),
        )
        self.assertEqual(validate_session(record, at=NOW), SessionStatus.IDLE_TIMEOUT)

    def test_a_session_inside_the_idle_window_survives(self) -> None:
        record = session(
            last_seen_at=NOW - DEFAULT_IDLE_TIMEOUT + timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=5),
        )
        self.assertEqual(validate_session(record, at=NOW), SessionStatus.ACTIVE)

    def test_a_nonexistent_session_is_not_found(self) -> None:
        self.assertEqual(validate_session(None, at=NOW), SessionStatus.NOT_FOUND)

    def test_revocation_is_reported_ahead_of_expiry(self) -> None:
        """Both are true; the security-relevant one is what §9 audits."""
        record = session(revoked_at=NOW - timedelta(hours=2), expires_at=NOW - timedelta(hours=1))
        self.assertEqual(validate_session(record, at=NOW), SessionStatus.REVOKED)

    def test_no_cookie_means_no_lookup_is_even_attempted(self) -> None:
        calls: list[str] = []

        def spy(session_id: str):
            calls.append(session_id)
            return None

        evaluate(
            facts("GET", "/signals", session_id=None),
            at=NOW,
            lookup=spy,
            allowed_origins=ALLOWED,
        )
        self.assertEqual(calls, [])

    def test_the_documented_ttl_and_idle_timeout_exist(self) -> None:
        """`17` §3 requires both to be documented; these are the values."""
        self.assertEqual(DEFAULT_SESSION_TTL, timedelta(hours=12))
        self.assertEqual(DEFAULT_IDLE_TIMEOUT, timedelta(hours=2))
        self.assertLess(DEFAULT_IDLE_TIMEOUT, DEFAULT_SESSION_TTL)


class CredentialRedaction(unittest.TestCase):
    """`17` §2: redacted structurally, not by discipline."""

    def test_a_session_never_prints_its_id_or_token(self) -> None:
        text = repr(session(session_id="secret-id", csrf_token="secret-token"))
        self.assertNotIn("secret-id", text)
        self.assertNotIn("secret-token", text)
        self.assertIn("<redacted>", text)

    def test_a_principal_never_prints_its_session_id(self) -> None:
        decision = evaluate(
            facts("GET", "/paper-trading/accounts"),
            at=NOW,
            lookup=lookup_of(session(session_id="secret-id")),
            allowed_origins=ALLOWED,
        )
        self.assertNotIn("secret-id", repr(decision.principal))


if __name__ == "__main__":
    unittest.main()
