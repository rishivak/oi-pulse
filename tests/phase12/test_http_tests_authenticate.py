"""Every HTTP test that builds a real app must authenticate.

This exists because of a risk the development sandbox cannot see. `fastapi` is not
installed here, so the Phase 3-7 HTTP suites error on import and never run locally;
they run in CI. The Phase 12 gate now refuses an anonymous request, so a suite that
builds an app with `create_app` and calls it without a session would go from passing
to 401 — and nothing in this environment would report it.

So the relationship is checked statically instead: every `TestClient` constructed
over an app that came from `create_app` must be followed by `authenticate(...)`.
The check runs on a bare interpreter and fails here, now, rather than in CI later.

Suites that build a bare `FastAPI()` with a single router are excluded, and
correctly: they never mount the middleware, so there is nothing to authenticate
against. `tests/phase12/test_security_http.py` is excluded by name because
unauthenticated requests are its subject.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"

#: Its whole purpose is to call routes without a session.
EXEMPT = {"tests/phase12/test_security_http.py"}


def _modules_using_create_app() -> list[Path]:
    return [
        path
        for path in sorted(TESTS.rglob("test_*.py"))
        if "create_app" in path.read_text(encoding="utf-8")
        and path.relative_to(REPO).as_posix() not in EXEMPT
    ]


class HttpSuitesAuthenticate(unittest.TestCase):
    def test_the_scan_finds_the_suites_it_is_meant_to(self) -> None:
        """A check that matched nothing would pass silently."""
        modules = _modules_using_create_app()
        self.assertGreaterEqual(
            len(modules), 5, f"expected the Phase 3-7 HTTP suites, found {modules}"
        )

    def test_every_real_app_client_is_authenticated(self) -> None:
        for path in _modules_using_create_app():
            relative = path.relative_to(REPO).as_posix()
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertIn(
                    "from tests._http_auth import authenticate",
                    source,
                    f"{relative} builds an app with create_app but never authenticates; "
                    f"in CI every request it makes would be refused with 401.",
                )
                tree = ast.parse(source, filename=relative)
                clients = sum(
                    1
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "TestClient"
                )
                authenticated = sum(
                    1
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "authenticate"
                )
                self.assertEqual(
                    clients,
                    authenticated,
                    f"{relative} constructs {clients} TestClients and authenticates "
                    f"{authenticated} of them.",
                )

    def test_the_helper_grants_every_permission_by_default(self) -> None:
        """These suites test routing and serialization, not authorization.

        Authorization has its own suite, which grants permissions one at a time.
        """
        from oipulse.identity import Permission
        from tests._http_auth import ALL

        self.assertEqual(ALL, frozenset(Permission))

    def test_the_helper_does_not_disable_the_gate(self) -> None:
        """The thing not to do: a boundary switchable off for a test suite.

        The helper issues a real session and sets real cookies. It must not reach
        for the middleware, the config's installation, or a bypass flag.
        """
        source = (TESTS / "_http_auth.py").read_text(encoding="utf-8")
        for forbidden in ("install_security", "middleware", "user_middleware", "bypass"):
            self.assertNotIn(forbidden, source, f"the helper touches {forbidden}")
        self.assertIn("issue_session", source)
        self.assertIn("SESSION_COOKIE", source)


if __name__ == "__main__":
    unittest.main()
