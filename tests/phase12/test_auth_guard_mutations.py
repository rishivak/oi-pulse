"""Mutation tests for `tools/check_auth_boundary.py`.

The remediation brief §15: "Mutation-test critical security guards." A guard over
an authorization boundary is exactly the kind that must be shown to bite, because
the thing it protects against — a route added without a policy — leaves no other
trace.

Each test breaks one thing in a disposable copy and asserts the guard names it.
The mutation helper raises when its anchor is missing, so a test cannot silently
stop mutating and start passing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from check_auth_boundary import check as auth_check

from tests.phase12._workspace import append, edit, workspace

PERMISSIONS = Path("oipulse") / "identity" / "permissions.py"
CSRF = Path("oipulse") / "identity" / "csrf.py"
GATE = Path("oipulse") / "identity" / "gate.py"
APP = Path("oipulse") / "api" / "app.py"


class AuthBoundaryGuard(unittest.TestCase):
    def assertCaught(self, findings: list[str], fragment: str) -> None:
        self.assertTrue(findings, "the guard reported nothing")
        joined = "\n".join(findings)
        self.assertIn(fragment, joined, f"expected {fragment!r} in:\n{joined}")

    def test_passes_on_the_committed_tree(self) -> None:
        self.assertEqual(auth_check(REPO), [])

    def test_passes_on_an_untouched_copy(self) -> None:
        with workspace() as root:
            self.assertEqual(auth_check(root), [])

    def test_a_route_with_no_policy(self) -> None:
        """The failure this guard exists for: an endpoint nobody gave a permission."""
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                '_p("POST", "/risk/kill-switch", _ADMIN',
                '_p("POST", "/risk/kill-switch-renamed", _ADMIN',
            )
            findings = auth_check(root)
            self.assertCaught(findings, "POST /risk/kill-switch has no entry")
            self.assertCaught(findings, "kill-switch-renamed, which the backend does not serve")

    def test_a_sixth_permission(self) -> None:
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                '    ADMIN = "ADMIN"',
                '    ADMIN = "ADMIN"\n    SUPERUSER = "SUPERUSER"',
            )
            self.assertCaught(auth_check(root), "17-SECURITY.md §4 defines exactly")

    def test_a_renamed_permission(self) -> None:
        with workspace() as root:
            edit(root / PERMISSIONS, '    RESEARCH = "RESEARCH"', '    STUDY = "STUDY"')
            self.assertCaught(auth_check(root), "defines exactly")

    def test_a_third_public_route(self) -> None:
        """Every unauthenticated route has to be argued for."""
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                '{("GET", "/ops/health"), ("GET", "/ops/ready")}',
                '{("GET", "/ops/health"), ("GET", "/ops/ready"), ("GET", "/signals")}',
            )
            self.assertCaught(auth_check(root), "PUBLIC_ROUTES is")

    def test_a_public_state_changing_route(self) -> None:
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                '{("GET", "/ops/health"), ("GET", "/ops/ready")}',
                '{("GET", "/ops/health"), ("POST", "/risk/kill-switch")}',
            )
            self.assertCaught(auth_check(root), "unauthenticated write")

    def test_a_route_requiring_live_trade(self) -> None:
        """Brief §4: the permission must not become a path to execution."""
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                '_p("POST", "/paper-trading/accounts", _PAPER',
                '_p("POST", "/paper-trading/accounts", Permission.LIVE_TRADE',
            )
            self.assertCaught(auth_check(root), "require LIVE_TRADE")

    def test_an_account_route_that_skips_the_ownership_check(self) -> None:
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                """    _p(
        "GET",
        "/paper-trading/accounts/{account_id}",
        _PAPER,
        account_scoped=True,""",
                """    _p(
        "GET",
        "/paper-trading/accounts/{account_id}",
        _PAPER,
        account_scoped=False,""",
            )
            self.assertCaught(auth_check(root), "is not account_scoped")

    def test_a_scoped_route_with_no_account_in_the_path(self) -> None:
        with workspace() as root:
            edit(
                root / PERMISSIONS,
                '_p("GET", "/portfolio", _PAPER, rationale="§4: paper accounts")',
                '_p("GET", "/portfolio", _PAPER, account_scoped=True, rationale="x")',
            )
            self.assertCaught(auth_check(root), "is account_scoped but")

    def test_a_state_changing_method_removed_from_the_csrf_set(self) -> None:
        with workspace() as root:
            edit(
                root / CSRF,
                'frozenset({"POST", "PUT", "PATCH", "DELETE"})',
                'frozenset({"POST", "PUT", "PATCH"})',
            )
            self.assertCaught(auth_check(root), "STATE_CHANGING_METHODS is")

    def test_a_method_declared_both_safe_and_state_changing(self) -> None:
        with workspace() as root:
            edit(
                root / CSRF,
                'SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})',
                'SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "POST"})',
            )
            self.assertCaught(auth_check(root), "both safe and")

    def test_the_gate_not_installed(self) -> None:
        with workspace() as root:
            # A rename that does not contain the original token: a mutation whose
            # replacement still matches would leave the guard passing and the test
            # would be testing nothing.
            edit(root / APP, "    install_security(", "    disabled_gate(")
            self.assertCaught(auth_check(root), "install_security is not called")

    def test_the_gate_no_longer_evaluating_csrf(self) -> None:
        with workspace() as root:
            edit(root / GATE, "    csrf = evaluate_csrf(", "    csrf = _skipped_csrf(")
            self.assertCaught(auth_check(root), "does not call evaluate_csrf")

    def test_the_gate_no_longer_validating_the_session(self) -> None:
        with workspace() as root:
            edit(
                root / GATE,
                "    status = validate_session(record",
                "    status = _assume_active(record",
            )
            self.assertCaught(auth_check(root), "does not call validate_session")

    def test_the_policy_parser_noticing_it_has_stopped_matching(self) -> None:
        with workspace() as root:
            path = root / PERMISSIONS
            text = path.read_text(encoding="utf-8")
            head, _, _ = text.partition("ROUTE_POLICIES: tuple[RoutePolicy, ...] = (")
            path.write_text(
                head + "ROUTE_POLICIES: tuple[RoutePolicy, ...] = ()\n", encoding="utf-8"
            )
            self.assertCaught(auth_check(root), "policies parsed")


if __name__ == "__main__":
    unittest.main()


class InternalImportGuard(unittest.TestCase):
    """`tools/check_internal_imports.py`.

    Added after it caught a real defect: `oipulse/api/security.py` imported `utcnow`
    from `oipulse.core.clock`, which has never had one. Nothing else here would have
    noticed — the module needs `fastapi` to import, and `compileall` compiles without
    resolving names — so it would have failed in CI, at import.
    """

    def assertCaught(self, findings: list[str], fragment: str) -> None:
        self.assertTrue(findings, "the guard reported nothing")
        joined = "\n".join(findings)
        self.assertIn(fragment, joined, f"expected {fragment!r} in:\n{joined}")

    def test_passes_on_the_committed_tree(self) -> None:
        from check_internal_imports import check as import_check

        self.assertEqual(import_check(REPO), [])

    def test_a_name_the_target_module_does_not_define(self) -> None:
        from check_internal_imports import check as import_check

        with workspace() as root:
            edit(
                root / "oipulse" / "api" / "security.py",
                "from oipulse.core.clock import Clock, SystemClock",
                "from oipulse.core.clock import Clock, utcnow",
            )
            self.assertCaught(import_check(root), "'utcnow'")

    def test_a_module_that_does_not_exist(self) -> None:
        from check_internal_imports import check as import_check

        with workspace() as root:
            append(
                root / "oipulse" / "identity" / "permissions.py",
                "\nfrom oipulse.nowhere.at_all import thing\n",
            )
            self.assertCaught(import_check(root), "which is not a module or package")

    def test_a_submodule_import_is_not_a_false_positive(self) -> None:
        """`from oipulse.analytics import domains` imports a package, not a name."""
        from check_internal_imports import check as import_check

        with workspace() as root:
            append(
                root / "oipulse" / "identity" / "permissions.py",
                "\nfrom oipulse.analytics import domains\n",
            )
            self.assertEqual(import_check(root), [])

    def test_the_scanner_noticing_it_has_stopped_resolving(self) -> None:
        from check_internal_imports import check as import_check

        with workspace() as root:
            import shutil

            shutil.rmtree(root / "oipulse" / "trading")
            shutil.rmtree(root / "oipulse" / "analytics")
            shutil.rmtree(root / "oipulse" / "research")
            findings = import_check(root)
            self.assertTrue(findings)
