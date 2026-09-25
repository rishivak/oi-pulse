"""The five permissions, and which one each route requires.

`17-SECURITY.md` §4 defines exactly five, and the table below is its wording:

| Permission | Grants |
|---|---|
| `MARKET_DATA_READ` | market state, analytics, signals |
| `RESEARCH` | studies, datasets, backtests, replay |
| `PAPER_TRADE` | paper accounts, intents, orders |
| `LIVE_TRADE` | live accounts — **separate, never implied by `PAPER_TRADE`** |
| `ADMIN` | universes, retention, operational endpoints |

### Why the policy is a table and not a decorator

A decorator on each route makes "which routes are unprotected?" a question you
answer by reading every router. A table makes it a set difference, which is what
`tools/check_auth_boundary.py` computes: every route in the generated API contract
must appear here or in `PUBLIC_ROUTES`, and a new route with no entry fails the
build rather than shipping open.

### Two places the specification does not reach, and what was done

**Alerts.** §4's table does not name them. They are delivery over signals, and
`05`/`08` keep alert state strictly separate from signal truth, so an alert rule is
a reader's own configuration rather than a system-administration object. Mapped to
`MARKET_DATA_READ`, the permission that grants signals. Recorded as an inference in
the Phase 12 report rather than presented as the specification's words.

**`/ops/health` and `/ops/ready`.** §4 puts "operational endpoints" under `ADMIN`,
but `14-DEPLOYMENT.md` §172 wires these two to container probes. A probe has no
session, so requiring `ADMIN` would fail every liveness check and take the service
out of rotation. They are the only public routes, they are listed explicitly rather
than matched by prefix, and the guard fails if that list grows.

### `LIVE_TRADE` grants nothing that exists

Holding it changes no behaviour in this build. `17` §4's live-trading gate needs
three conditions, and the other two — the feature flag and the confirmation
variable — are off; independently of both, no adapter can submit an order at all
(`oipulse/trading/brokers/capability.py`). No route below requires `LIVE_TRADE`,
because there is no live route to require it. It is defined so the permission model
is the specification's, and so granting it can never be the thing that turns
execution on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "PUBLIC_ROUTES",
    "ROUTE_POLICIES",
    "Permission",
    "RoutePolicy",
    "policy_for",
]


class Permission(StrEnum):
    """Exactly the five in `17-SECURITY.md` §4. No sixth, and no aliases."""

    MARKET_DATA_READ = "MARKET_DATA_READ"
    RESEARCH = "RESEARCH"
    PAPER_TRADE = "PAPER_TRADE"
    #: Never implied by PAPER_TRADE. Grants access to live accounts, of which there
    #: are none, and is not sufficient to place an order on its own.
    LIVE_TRADE = "LIVE_TRADE"
    ADMIN = "ADMIN"


#: The complete set, for validating a permissions snapshot read from the database.
ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)


def parse_permissions(values: object) -> frozenset[Permission]:
    """Read a stored snapshot, discarding anything not in the five.

    A permission string the code does not know is dropped rather than carried. It
    cannot grant anything — nothing checks for it — but keeping it would let a
    typo'd or injected value travel through logs and audit records looking like a
    capability.
    """
    if not isinstance(values, list | tuple | set | frozenset):
        return frozenset()
    known: set[Permission] = set()
    for value in values:
        if isinstance(value, Permission):
            known.add(value)
        elif isinstance(value, str):
            try:
                known.add(Permission(value))
            except ValueError:
                continue
    return frozenset(known)


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    """What one route requires."""

    #: The route template exactly as the backend declares it.
    template: str
    method: str
    required: Permission
    #: True when the path carries an `{account_id}` whose owner must match the
    #: principal. `17` §4's defence in depth: permission is not the same as access
    #: to somebody else's account.
    account_scoped: bool = False
    rationale: str = ""


def _p(
    method: str,
    template: str,
    required: Permission,
    *,
    account_scoped: bool = False,
    rationale: str = "",
) -> RoutePolicy:
    return RoutePolicy(
        template=template,
        method=method,
        required=required,
        account_scoped=account_scoped,
        rationale=rationale,
    )


#: Unauthenticated by design. Two entries, both container probes.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/ops/health"), ("GET", "/ops/ready")}
)

_MARKET = Permission.MARKET_DATA_READ
_RESEARCH = Permission.RESEARCH
_PAPER = Permission.PAPER_TRADE
_ADMIN = Permission.ADMIN

ROUTE_POLICIES: tuple[RoutePolicy, ...] = (
    # ---------------------------------------------------- market state, analytics
    _p("GET", "/market/state", _MARKET, rationale="§4: market state"),
    _p("GET", "/features", _MARKET, rationale="§4: analytics"),
    _p("GET", "/features/{identifier}/values", _MARKET, rationale="§4: analytics"),
    _p("GET", "/features/{identifier}/versions/{version}", _MARKET, rationale="§4: analytics"),
    # ------------------------------------------------------------------- signals
    _p("GET", "/signals", _MARKET, rationale="§4: signals"),
    _p("GET", "/signals/{signal_id}", _MARKET, rationale="§4: signals"),
    _p("GET", "/signals/{signal_id}/history", _MARKET, rationale="§4: signals"),
    _p("GET", "/signals/types", _MARKET, rationale="§4: signals"),
    _p("GET", "/signals/types/{signal_type}/versions/{version}", _MARKET, rationale="§4: signals"),
    # -------------------------------------------------------------------- alerts
    # Inferred: delivery over signals, not a system-administration object.
    _p("GET", "/alerts/rules", _MARKET, rationale="inferred: alerts ride on signals"),
    _p("GET", "/alerts/rules/{rule_id}", _MARKET, rationale="inferred"),
    _p("POST", "/alerts/rules", _MARKET, rationale="inferred"),
    _p("DELETE", "/alerts/rules/{rule_id}", _MARKET, rationale="inferred"),
    _p("POST", "/alerts/rules/{rule_id}/test", _MARKET, rationale="inferred; dry run only"),
    _p("GET", "/alerts/occurrences", _MARKET, rationale="inferred"),
    _p(
        "POST",
        "/alerts/occurrences/{occurrence_id}/acknowledge",
        _MARKET,
        rationale="inferred; delivery state only, cannot touch signal truth",
    ),
    # ------------------------------------------------------------------ research
    _p("GET", "/research/studies", _RESEARCH, rationale="§4: studies"),
    _p("GET", "/research/studies/{study_id}/versions/{version}", _RESEARCH, rationale="§4"),
    _p("POST", "/research/studies", _RESEARCH, rationale="§4"),
    _p("DELETE", "/research/studies/{study_id}/versions/{version}", _RESEARCH, rationale="§4"),
    _p("POST", "/research/studies/{study_id}/run", _RESEARCH, rationale="§4"),
    _p("GET", "/research/results", _RESEARCH, rationale="§4"),
    _p("GET", "/research/results/{content_hash}", _RESEARCH, rationale="§4"),
    _p("GET", "/research/datasets", _RESEARCH, rationale="§4: datasets"),
    _p("GET", "/research/datasets/{content_hash}", _RESEARCH, rationale="§4: datasets"),
    _p("GET", "/research/signal-evaluations", _RESEARCH, rationale="§4"),
    # -------------------------------------------------------------------- replay
    _p("GET", "/replay/sessions", _RESEARCH, rationale="§4: replay"),
    _p("POST", "/replay/sessions", _RESEARCH, rationale="§4: replay"),
    _p("GET", "/replay/sessions/{session_id}", _RESEARCH, rationale="§4: replay"),
    _p("POST", "/replay/sessions/{session_id}/control", _RESEARCH, rationale="§4: replay"),
    _p("GET", "/replay/sessions/{session_id}/state", _RESEARCH, rationale="§4: replay"),
    # ------------------------------------------------------------------ backtest
    _p("GET", "/backtest/runs", _RESEARCH, rationale="§4: backtests"),
    _p("POST", "/backtest/runs", _RESEARCH, rationale="§4: backtests"),
    _p("GET", "/backtest/runs/{run_id}", _RESEARCH, rationale="§4: backtests"),
    _p("GET", "/backtest/runs/{run_id}/results", _RESEARCH, rationale="§4: backtests"),
    _p("GET", "/backtest/runs/{run_id}/trades", _RESEARCH, rationale="§4: backtests"),
    _p("GET", "/backtest/runs/{run_id}/equity-curve", _RESEARCH, rationale="§4: backtests"),
    _p("GET", "/backtest/results/{content_hash}", _RESEARCH, rationale="§4: backtests"),
    # -------------------------------------------------------------- paper trading
    _p("GET", "/paper-trading/accounts", _PAPER, rationale="§4: paper accounts"),
    _p("POST", "/paper-trading/accounts", _PAPER, rationale="§4: paper accounts"),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}",
        _PAPER,
        account_scoped=True,
        rationale="§4: paper accounts",
    ),
    _p(
        "POST",
        "/paper-trading/accounts/{account_id}/intents",
        _PAPER,
        account_scoped=True,
        rationale="§4: intents",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/intents/{intent_id}",
        _PAPER,
        account_scoped=True,
        rationale="§4: intents",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/orders",
        _PAPER,
        account_scoped=True,
        rationale="§4: orders",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/orders/{order_id}",
        _PAPER,
        account_scoped=True,
        rationale="§4: orders",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/orders/{order_id}/events",
        _PAPER,
        account_scoped=True,
        rationale="§4: orders",
    ),
    _p(
        "POST",
        "/paper-trading/accounts/{account_id}/orders/{order_id}/cancel",
        _PAPER,
        account_scoped=True,
        rationale="§4: orders",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/fills",
        _PAPER,
        account_scoped=True,
        rationale="§4: orders",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/positions",
        _PAPER,
        account_scoped=True,
        rationale="§4: paper accounts",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/pnl",
        _PAPER,
        account_scoped=True,
        rationale="§4: paper accounts",
    ),
    _p(
        "GET",
        "/paper-trading/accounts/{account_id}/audit/{order_id}",
        _PAPER,
        account_scoped=True,
        rationale="§4: orders",
    ),
    # ---------------------------------------------------------------------- risk
    _p("GET", "/risk/profiles", _PAPER, rationale="reading the limits on one's accounts"),
    _p("GET", "/risk/profiles/{policy_id}/versions/{version}", _PAPER, rationale="read"),
    # §9 names "risk-limit modification" as a security-relevant action.
    _p("PUT", "/risk/profiles", _ADMIN, rationale="§9: risk-limit modification"),
    _p("GET", "/risk/state/{account_id}", _PAPER, account_scoped=True, rationale="read"),
    _p("GET", "/risk/status/{account_id}", _PAPER, account_scoped=True, rationale="read"),
    _p("POST", "/risk/evaluate", _PAPER, rationale="part of the intent path"),
    _p("GET", "/risk/decisions", _PAPER, rationale="decision audit"),
    _p("GET", "/risk/decisions/{intent_id}/{sequence_no}", _PAPER, rationale="decision audit"),
    # §9 names "kill-switch activation" as a security-relevant action; §4 puts
    # operational endpoints under ADMIN.
    _p("POST", "/risk/kill-switch", _ADMIN, rationale="§4: operational; §9: audited"),
    _p("DELETE", "/risk/kill-switch", _ADMIN, rationale="§4: operational; §9: audited"),
    # --------------------------------------------------------- OMS/reconciliation
    _p("GET", "/reconciliation/status", _PAPER, rationale="read"),
    _p("GET", "/reconciliation/runs", _PAPER, rationale="read"),
    _p("GET", "/reconciliation/runs/{run_id}", _PAPER, rationale="read"),
    _p("POST", "/reconciliation/trigger", _ADMIN, rationale="§4: operational endpoint"),
    _p("GET", "/reconciliation/orders", _PAPER, rationale="§4: orders"),
    _p("GET", "/reconciliation/orders/{order_id}", _PAPER, rationale="§4: orders"),
    _p("GET", "/reconciliation/orders/{order_id}/events", _PAPER, rationale="§4: orders"),
    _p("GET", "/reconciliation/orders/{order_id}/provider-state", _PAPER, rationale="§4: orders"),
    _p("POST", "/reconciliation/orders/{order_id}/cancel", _PAPER, rationale="§4: orders"),
    # ----------------------------------------------------------------- portfolio
    _p("GET", "/portfolio", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/positions", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/exposure", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/pnl", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/greeks", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/attribution", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/snapshots", _PAPER, rationale="§4: paper accounts"),
    _p("GET", "/portfolio/snapshots/{content_digest}", _PAPER, rationale="§4: paper accounts"),
    _p("POST", "/portfolio/position-reconciliation", _ADMIN, rationale="§4: operational endpoint"),
    _p("GET", "/portfolio/position-reconciliation/{run_id}", _PAPER, rationale="read"),
    # ------------------------------------------------------------------- journal
    _p("GET", "/journal/entries", _PAPER, rationale="§4: paper accounts; entries are trade notes"),
    _p("GET", "/journal/entries/{entry_id}", _PAPER, rationale="§4: paper accounts"),
)

_BY_KEY: dict[tuple[str, str], RoutePolicy] = {
    (policy.method, policy.template): policy for policy in ROUTE_POLICIES
}

#: `{name}` -> a single path segment. Anchored, so `/signals` cannot match
#: `/signals/x`, and a template cannot match a longer path by accident.
_PARAM = re.compile(r"\{[^/}]+\}")


def _matcher(template: str) -> re.Pattern[str]:
    escaped = re.escape(template)
    # `re.escape` leaves `{`/`}` alone on 3.12, so substitute on the escaped form.
    pattern = re.sub(r"\\?\{[^/}]+\\?\}", r"[^/]+", escaped)
    return re.compile(f"^{pattern}$")


_MATCHERS: tuple[tuple[re.Pattern[str], RoutePolicy], ...] = tuple(
    (_matcher(policy.template), policy) for policy in ROUTE_POLICIES
)


def policy_for(method: str, path: str) -> RoutePolicy | None:
    """The policy governing a concrete request path, or `None` if there is none.

    `None` means *no policy exists*, which the gate treats as a refusal rather than
    as permission. A route added without a policy is therefore closed by default,
    which is the only safe direction for this particular table to fail in.
    """
    key = (method.upper(), path)
    exact = _BY_KEY.get(key)
    if exact is not None:
        return exact
    upper = method.upper()
    for matcher, policy in _MATCHERS:
        if policy.method == upper and matcher.match(path):
            return policy
    return None


def is_public(method: str, path: str) -> bool:
    return (method.upper(), path) in PUBLIC_ROUTES
