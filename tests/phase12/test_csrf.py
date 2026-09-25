"""CSRF. `17-SECURITY.md` §7.

The specification requires three controls together — `SameSite=Lax`, Origin
validation, and a double-submit token — and says the first is "necessary, not
sufficient". The brief's §5 list is covered below, plus the two places §7 is
specific about behaviour that is easy to get backwards: an absent `Origin` is
rejected rather than allowed through, and no state change may occur on `GET`.
"""

from __future__ import annotations

import unittest

from oipulse.identity import AccessOutcome, CsrfOutcome, Permission, evaluate, evaluate_csrf
from oipulse.identity.csrf import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SAFE_METHODS,
    STATE_CHANGING_METHODS,
)
from oipulse.identity.principal import PrincipalKind
from tests.phase12._identity import ALLOWED, NOW, ORIGIN, TOKEN, facts, lookup_of, session

PAPER = frozenset({Permission.PAPER_TRADE})


def csrf(**kw) -> CsrfOutcome:
    defaults = {
        "method": "POST",
        "principal_kind": PrincipalKind.SESSION,
        "origin": ORIGIN,
        "referer": None,
        "header_token": TOKEN,
        "session_token": TOKEN,
        "allowed_origins": ALLOWED,
    }
    defaults.update(kw)
    return evaluate_csrf(**defaults).outcome  # type: ignore[arg-type]


def through_gate(**kw):
    return evaluate(
        facts("POST", "/paper-trading/accounts", **kw),
        at=NOW,
        lookup=lookup_of(session(permissions=PAPER)),
        allowed_origins=ALLOWED,
    )


class MethodScope(unittest.TestCase):
    def test_the_four_state_changing_methods_are_the_specification_s(self) -> None:
        self.assertEqual(STATE_CHANGING_METHODS, frozenset({"POST", "PUT", "PATCH", "DELETE"}))

    def test_the_safe_methods_are_exempt(self) -> None:
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertEqual(
                csrf(method=method, origin=None, header_token=None), CsrfOutcome.NOT_REQUIRED
            )

    def test_safe_and_state_changing_do_not_overlap(self) -> None:
        """`17` §7.1: no state change may ever occur on a safe method."""
        self.assertEqual(SAFE_METHODS & STATE_CHANGING_METHODS, frozenset())

    def test_every_state_changing_method_is_checked(self) -> None:
        for method in STATE_CHANGING_METHODS:
            self.assertEqual(csrf(method=method, header_token=None), CsrfOutcome.TOKEN_MISSING)


class OriginValidation(unittest.TestCase):
    """`17` §7.2."""

    def test_a_valid_same_site_request_passes(self) -> None:
        self.assertEqual(csrf(), CsrfOutcome.PASSED)

    def test_a_missing_origin_is_rejected_not_skipped(self) -> None:
        """The clause that is easiest to implement backwards."""
        self.assertEqual(csrf(origin=None), CsrfOutcome.ORIGIN_MISSING)
        self.assertEqual(csrf(origin=""), CsrfOutcome.ORIGIN_MISSING)

    def test_a_cross_origin_request_is_rejected(self) -> None:
        self.assertEqual(csrf(origin="https://evil.test"), CsrfOutcome.ORIGIN_NOT_ALLOWED)

    def test_a_referer_substitutes_when_origin_is_absent(self) -> None:
        """§7.2 says "Origin / Referer validation"."""
        self.assertEqual(
            csrf(origin=None, referer=f"{ORIGIN}/terminal/portfolio"), CsrfOutcome.PASSED
        )

    def test_a_cross_origin_referer_is_rejected(self) -> None:
        self.assertEqual(
            csrf(origin=None, referer="https://evil.test/x"), CsrfOutcome.ORIGIN_NOT_ALLOWED
        )

    def test_a_referer_with_no_scheme_does_not_pass_as_an_origin(self) -> None:
        self.assertEqual(
            csrf(origin=None, referer="terminal.oipulse.test"), CsrfOutcome.ORIGIN_MISSING
        )

    def test_origin_comparison_ignores_case_and_a_trailing_slash(self) -> None:
        self.assertEqual(csrf(origin=f"{ORIGIN.upper()}/"), CsrfOutcome.PASSED)

    def test_an_origin_with_a_path_does_not_match(self) -> None:
        """A serialised origin has no path; tolerating one would widen the match."""
        self.assertEqual(csrf(origin=f"https://evil.test/{ORIGIN}"), CsrfOutcome.ORIGIN_NOT_ALLOWED)

    def test_an_empty_allow_list_accepts_nothing(self) -> None:
        self.assertEqual(csrf(allowed_origins=frozenset()), CsrfOutcome.ORIGIN_NOT_ALLOWED)

    def test_origin_is_checked_before_the_token(self) -> None:
        """So a cross-origin refusal does not depend on the token being right."""
        self.assertEqual(
            csrf(origin="https://evil.test", header_token="wrong"),
            CsrfOutcome.ORIGIN_NOT_ALLOWED,
        )


class DoubleSubmitToken(unittest.TestCase):
    """`17` §7.3."""

    def test_a_missing_token_is_rejected(self) -> None:
        self.assertEqual(csrf(header_token=None), CsrfOutcome.TOKEN_MISSING)
        self.assertEqual(csrf(header_token=""), CsrfOutcome.TOKEN_MISSING)

    def test_a_mismatched_token_is_rejected(self) -> None:
        self.assertEqual(csrf(header_token="not-the-token"), CsrfOutcome.TOKEN_MISMATCH)

    def test_a_session_with_no_token_refuses_rather_than_comparing_nothing(self) -> None:
        self.assertEqual(csrf(session_token="", header_token=""), CsrfOutcome.TOKEN_UNAVAILABLE)
        # And not by accident of both being empty.
        self.assertEqual(
            csrf(session_token="", header_token="anything"), CsrfOutcome.TOKEN_UNAVAILABLE
        )

    def test_the_header_is_one_the_browser_cannot_set_cross_origin(self) -> None:
        self.assertEqual(CSRF_HEADER, "x-oipulse-csrf")
        self.assertTrue(CSRF_HEADER.startswith("x-"))

    def test_the_cookie_and_the_header_are_different_names(self) -> None:
        """A double submit compares two carriers; one name would be one carrier."""
        self.assertNotEqual(CSRF_COOKIE, CSRF_HEADER)


class ApiKeyExemption(unittest.TestCase):
    """`17` §7: "API-key principals are exempt from CSRF"."""

    def test_an_api_key_principal_is_exempt(self) -> None:
        self.assertEqual(
            csrf(principal_kind=PrincipalKind.API_KEY, origin=None, header_token=None),
            CsrfOutcome.EXEMPT_API_KEY,
        )

    def test_a_session_principal_is_not(self) -> None:
        self.assertEqual(
            csrf(principal_kind=PrincipalKind.SESSION, origin=None), CsrfOutcome.ORIGIN_MISSING
        )


class ThroughTheGate(unittest.TestCase):
    """CSRF runs after authentication and authorization, on a real decision."""

    def test_a_valid_same_site_mutation_is_allowed(self) -> None:
        self.assertEqual(through_gate().outcome, AccessOutcome.ALLOWED)

    def test_an_invalid_cross_site_mutation_is_refused(self) -> None:
        decision = through_gate(origin="https://evil.test")
        self.assertEqual(decision.outcome, AccessOutcome.CSRF_FAILED)
        self.assertEqual(decision.http_status, 403)

    def test_a_missing_token_is_refused_through_the_gate(self) -> None:
        self.assertEqual(through_gate(csrf=None).outcome, AccessOutcome.CSRF_FAILED)

    def test_a_safe_method_needs_no_origin_or_token(self) -> None:
        decision = evaluate(
            facts("GET", "/paper-trading/accounts", origin=None, csrf=None),
            at=NOW,
            lookup=lookup_of(session(permissions=PAPER)),
            allowed_origins=ALLOWED,
        )
        self.assertEqual(decision.outcome, AccessOutcome.ALLOWED)
        assert decision.csrf is not None
        self.assertEqual(decision.csrf.outcome, CsrfOutcome.NOT_REQUIRED)

    def test_an_anonymous_cross_site_write_fails_on_authentication_first(self) -> None:
        """Authentication precedes CSRF, so the refusal does not leak route detail."""
        decision = evaluate(
            facts("POST", "/risk/kill-switch", session_id=None, origin="https://evil.test"),
            at=NOW,
            lookup=lookup_of(),
            allowed_origins=ALLOWED,
        )
        self.assertEqual(decision.outcome, AccessOutcome.UNAUTHENTICATED)


if __name__ == "__main__":
    unittest.main()
