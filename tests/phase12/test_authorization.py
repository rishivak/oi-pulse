"""Authorization. `17-SECURITY.md` §4.

The brief's §3 list: authenticated and permitted, authenticated and missing the
permission, a malformed permission, privilege escalation, cross-account access,
administrative operations, paper-trading operations, research operations.

> Hiding UI controls is NOT authorization. A forbidden action must fail even when
> invoked directly through HTTP.

Every case below invokes the decision directly, with no UI involved at all.
"""

from __future__ import annotations

import unittest

from oipulse.identity import AccessOutcome, Permission, evaluate
from oipulse.identity.permissions import (
    PUBLIC_ROUTES,
    ROUTE_POLICIES,
    parse_permissions,
    policy_for,
)
from tests.phase12._identity import ALLOWED, NOW, facts, lookup_of, session

MARKET = frozenset({Permission.MARKET_DATA_READ})
RESEARCH = frozenset({Permission.RESEARCH})
PAPER = frozenset({Permission.PAPER_TRADE})
LIVE = frozenset({Permission.LIVE_TRADE})
ADMIN = frozenset({Permission.ADMIN})


def decide(method: str, path: str, permissions: frozenset[Permission], **kw):
    owner = kw.pop("owner", "user-1")
    return evaluate(
        facts(method, path, **kw),
        at=NOW,
        lookup=lookup_of(session(permissions=permissions)),
        allowed_origins=ALLOWED,
        owner_lookup=lambda _account: owner,
    )


class TheFivePermissions(unittest.TestCase):
    def test_exactly_the_five_the_specification_names(self) -> None:
        self.assertEqual(
            [p.value for p in Permission],
            ["MARKET_DATA_READ", "RESEARCH", "PAPER_TRADE", "LIVE_TRADE", "ADMIN"],
        )

    def test_market_data_read_grants_state_analytics_and_signals(self) -> None:
        for path in ("/market/state", "/features", "/signals"):
            self.assertEqual(decide("GET", path, MARKET).outcome, AccessOutcome.ALLOWED, path)

    def test_research_grants_studies_datasets_backtests_and_replay(self) -> None:
        for path in (
            "/research/studies",
            "/research/datasets",
            "/backtest/runs",
            "/replay/sessions",
        ):
            self.assertEqual(decide("GET", path, RESEARCH).outcome, AccessOutcome.ALLOWED, path)

    def test_paper_trade_grants_accounts_intents_and_orders(self) -> None:
        self.assertEqual(
            decide("GET", "/paper-trading/accounts", PAPER).outcome, AccessOutcome.ALLOWED
        )
        self.assertEqual(
            decide(
                "POST",
                "/paper-trading/accounts/acct-1/intents",
                PAPER,
                account_id="acct-1",
            ).outcome,
            AccessOutcome.ALLOWED,
        )

    def test_admin_grants_the_operational_endpoints(self) -> None:
        for method, path in (
            ("POST", "/risk/kill-switch"),
            ("POST", "/reconciliation/trigger"),
            ("PUT", "/risk/profiles"),
            ("POST", "/portfolio/position-reconciliation"),
        ):
            self.assertEqual(decide(method, path, ADMIN).outcome, AccessOutcome.ALLOWED, path)

    def test_live_trade_grants_no_route_at_all(self) -> None:
        """Brief §4: the permission must not become a way to reach execution."""
        live_routes = [p for p in ROUTE_POLICIES if p.required is Permission.LIVE_TRADE]
        self.assertEqual(live_routes, [])
        # And holding only LIVE_TRADE reaches nothing.
        for policy in ROUTE_POLICIES:
            concrete = policy.template.replace("{account_id}", "acct-1")
            concrete = "/".join(
                "x" if part.startswith("{") else part for part in concrete.split("/")
            )
            self.assertNotEqual(
                decide(policy.method, concrete, LIVE).outcome,
                AccessOutcome.ALLOWED,
                f"LIVE_TRADE alone reached {policy.method} {concrete}",
            )


class MissingPermission(unittest.TestCase):
    def test_market_data_does_not_reach_research(self) -> None:
        decision = decide("GET", "/research/studies", MARKET)
        self.assertEqual(decision.outcome, AccessOutcome.FORBIDDEN)
        self.assertEqual(decision.http_status, 403)
        self.assertIn("RESEARCH", decision.detail)

    def test_research_does_not_reach_paper_trading(self) -> None:
        self.assertEqual(
            decide("GET", "/paper-trading/accounts", RESEARCH).outcome,
            AccessOutcome.FORBIDDEN,
        )

    def test_paper_trade_does_not_reach_the_kill_switch(self) -> None:
        """The escalation that matters most: a trader stopping the whole book."""
        self.assertEqual(
            decide("POST", "/risk/kill-switch", PAPER).outcome, AccessOutcome.FORBIDDEN
        )

    def test_paper_trade_does_not_modify_risk_limits(self) -> None:
        self.assertEqual(decide("PUT", "/risk/profiles", PAPER).outcome, AccessOutcome.FORBIDDEN)

    def test_paper_trade_does_not_trigger_reconciliation(self) -> None:
        self.assertEqual(
            decide("POST", "/reconciliation/trigger", PAPER).outcome,
            AccessOutcome.FORBIDDEN,
        )

    def test_an_empty_permission_set_reaches_nothing(self) -> None:
        self.assertEqual(decide("GET", "/signals", frozenset()).outcome, AccessOutcome.FORBIDDEN)


class PrivilegeEscalation(unittest.TestCase):
    def test_a_permission_string_the_code_does_not_know_grants_nothing(self) -> None:
        """A snapshot containing `SUPERUSER` or `ADMIN ` must not become ADMIN."""
        for bogus in (["SUPERUSER"], ["admin"], ["ADMIN "], ["*"], ["ADMIN;DROP"]):
            parsed = parse_permissions(bogus)
            self.assertEqual(parsed, frozenset(), bogus)

    def test_a_malformed_snapshot_is_empty_rather_than_permissive(self) -> None:
        for bogus in (None, "ADMIN", 42, {"ADMIN": True}):
            self.assertEqual(parse_permissions(bogus), frozenset(), repr(bogus))

    def test_a_known_permission_among_unknown_ones_still_parses(self) -> None:
        self.assertEqual(
            parse_permissions(["RESEARCH", "SUPERUSER"]), frozenset({Permission.RESEARCH})
        )

    def test_a_route_with_no_policy_is_refused_rather_than_allowed(self) -> None:
        """Closed by default: a new route ships unreachable, not open."""
        self.assertIsNone(policy_for("GET", "/admin/secrets"))
        self.assertEqual(decide("GET", "/admin/secrets", ADMIN).outcome, AccessOutcome.NO_POLICY)

    def test_an_unlisted_method_on_a_listed_path_is_refused(self) -> None:
        self.assertEqual(decide("DELETE", "/market/state", ADMIN).outcome, AccessOutcome.NO_POLICY)

    def test_a_path_prefix_does_not_match_a_longer_path(self) -> None:
        """`/signals` must not authorise `/signals/secret/extra`."""
        self.assertIsNone(policy_for("GET", "/signals/abc/extra/more"))


class CrossAccountAccess(unittest.TestCase):
    """`17` §4's defence in depth: permission is not access to another account."""

    def test_another_identity_s_account_is_refused(self) -> None:
        decision = decide(
            "GET",
            "/paper-trading/accounts/acct-9",
            PAPER,
            account_id="acct-9",
            owner="somebody-else",
        )
        self.assertEqual(decision.outcome, AccessOutcome.WRONG_OWNER)
        self.assertEqual(decision.http_status, 403)

    def test_an_account_with_no_known_owner_is_refused(self) -> None:
        self.assertEqual(
            decide(
                "GET", "/paper-trading/accounts/acct-9", PAPER, account_id="acct-9", owner=None
            ).outcome,
            AccessOutcome.WRONG_OWNER,
        )

    def test_the_owner_reaches_their_own_account(self) -> None:
        self.assertEqual(
            decide(
                "GET",
                "/paper-trading/accounts/acct-1",
                PAPER,
                account_id="acct-1",
                owner="user-1",
            ).outcome,
            AccessOutcome.ALLOWED,
        )

    def test_a_process_that_cannot_resolve_ownership_refuses_rather_than_serves(self) -> None:
        decision = evaluate(
            facts("GET", "/paper-trading/accounts/acct-1", account_id="acct-1"),
            at=NOW,
            lookup=lookup_of(session(permissions=PAPER)),
            allowed_origins=ALLOWED,
            owner_lookup=None,
        )
        self.assertEqual(decision.outcome, AccessOutcome.OWNERSHIP_UNAVAILABLE)
        self.assertEqual(decision.http_status, 503)

    def test_admin_reaches_any_account(self) -> None:
        """§4 grants ADMIN operational reach; it is the documented exception."""
        self.assertEqual(
            decide(
                "GET",
                "/paper-trading/accounts/acct-9",
                ADMIN,
                account_id="acct-9",
                owner="somebody-else",
            ).outcome,
            AccessOutcome.ALLOWED,
        )

    def test_risk_state_is_account_scoped_too(self) -> None:
        self.assertEqual(
            decide(
                "GET", "/risk/state/acct-9", PAPER, account_id="acct-9", owner="somebody-else"
            ).outcome,
            AccessOutcome.WRONG_OWNER,
        )


class PolicyCoverage(unittest.TestCase):
    def test_every_policy_names_one_of_the_five(self) -> None:
        for policy in ROUTE_POLICIES:
            self.assertIsInstance(policy.required, Permission)

    def test_the_public_list_is_two_container_probes(self) -> None:
        self.assertEqual(PUBLIC_ROUTES, frozenset({("GET", "/ops/health"), ("GET", "/ops/ready")}))

    def test_no_public_route_changes_state(self) -> None:
        for method, _path in PUBLIC_ROUTES:
            self.assertEqual(method, "GET")


if __name__ == "__main__":
    unittest.main()
