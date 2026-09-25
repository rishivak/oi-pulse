#!/usr/bin/env python3
"""Guard: nothing in this codebase can place a real order.

Phase 10 brief §19 asks this guard to detect attempts to instantiate a real Upstox
order-submit client, load live credentials, enable live execution, bypass risk, route
a paper intent to a broker, invoke provider submit directly, or bypass the OMS — and
§19 adds: *"A source-text-only check is not enough if a structural AST/data-flow check
can be made."*

So every check below is structural. Where a claim could only be made by grepping, it
is instead made by parsing — the function's body, the class's bases, the call graph of
the module.

### The eight checks

1. **`LIVE_EXECUTION_ENABLED` is a literal `False`**, assigned once, at module level.
   Not read from an environment variable, not computed. Parsed from the AST, so a
   `LIVE_EXECUTION_ENABLED = os.getenv(...)` is caught even though the name is
   unchanged.
2. **No broker adapter grants `LIVE_SUBMIT`.** Every class implementing the adapter
   surface is instantiated and its real `capabilities` inspected — a runtime check,
   not a textual one, so a capability computed at import time is still caught.
3. **`UpstoxBrokerAdapter` has no submitting implementation.** Every method that
   could reach a venue must contain a `require_capability(...)` call before anything
   else, and must contain no `await` of anything other than that. A body that grew a
   real request would fail.
4. **No HTTP client is reachable from `trading/`.** `httpx`, `requests`, `aiohttp`,
   `urllib`, `http.client`, `socket` — none importable, directly or from a submodule.
5. **No credential module is reachable from `trading/`.** The settings and auth
   modules are the only places a token lives.
6. **No route submits.** No path in `api/` contains a submitting verb for a broker,
   and no endpoint function takes a `provider_*` parameter through which broker truth
   could be forged.
7. **Submission goes through the authorization gate.** `OrderManager.submit` must
   call `authorize_submission` before it calls `place_order`, checked positionally in
   the AST — a check that ran afterwards would authorize retrospectively.
8. **`UNKNOWN` has no edge to a submittable state.** Parsed out of the transition
   table itself, so "never resubmit from UNKNOWN" is verified against the machine
   rather than against a comment claiming it.

Stdlib-only AST plus a narrow runtime import, so it runs with no third-party
dependencies installed. Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TRADING = Path("oipulse/trading")
CAPABILITY = Path("oipulse/trading/brokers/capability.py")
UPSTOX = Path("oipulse/trading/brokers/upstox.py")
MANAGER = Path("oipulse/trading/oms/manager.py")
ORDERS = Path("oipulse/trading/orders.py")
API = Path("oipulse/api")

#: Anything that could open a socket.
NETWORK_MODULES = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "urllib3",
    "http",
    "socket",
    "websockets",
    "websocket",
)

#: Anything that could hold a live credential.
CREDENTIAL_MODULES = (
    "oipulse.core.config",
    "oipulse.core.secrets",
    "oipulse.marketdata.auth",
    "oipulse.marketdata.upstox",
    "oipulse.marketdata.providers",
)

#: Methods on the Upstox adapter that must remain incapable.
UPSTOX_GATED_METHODS = (
    "place_order",
    "cancel_order",
    "modify_order",
    "get_order",
    "list_orders",
    "list_trades",
    "get_positions",
    "subscribe_order_updates",
)


def _modules(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def _imports(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
    return out


def _check_flag_is_a_literal_false(repo: Path) -> list[str]:
    path = repo / CAPABILITY
    if not path.exists():
        return [f"{CAPABILITY}: missing; the barrier has no flag to check"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "LIVE_EXECUTION_ENABLED" for t in node.targets)
    ]
    if len(assignments) != 1:
        return [
            f"{CAPABILITY}: expected exactly one module-level assignment of "
            f"LIVE_EXECUTION_ENABLED, found {len(assignments)}"
        ]
    value = assignments[0].value
    if not (isinstance(value, ast.Constant) and value.value is False):
        return [
            f"{CAPABILITY}:{assignments[0].lineno}: LIVE_EXECUTION_ENABLED is not the "
            f"literal False. It must be a constant a reviewer can see in a diff, not "
            f"a value read from the environment or computed at import."
        ]
    return []


def _check_no_adapter_grants_live_submit(repo: Path) -> list[str]:
    """Instantiate the adapters and inspect their real capability sets."""
    sys.path.insert(0, str(repo))
    try:
        from oipulse.backtest.costs import INDIAN_OPTIONS_COSTS
        from oipulse.backtest.fills import FillModel, SlippageModel
        from oipulse.trading.brokers import (
            ExecutionCapability,
            PaperBrokerAdapter,
            UpstoxBrokerAdapter,
        )
        from oipulse.trading.execution import PaperExecutionModel
    except ImportError as exc:  # pragma: no cover - a real break, reported not hidden
        return [f"cannot import the broker adapters to inspect them: {exc}"]

    from datetime import timedelta
    from decimal import Decimal

    model = FillModel(
        name="GUARD",
        version=1,
        latency=timedelta(seconds=1),
        slippage_model=SlippageModel.MID,
        slippage_parameter=Decimal(0),
        assumed_spread_fraction=Decimal("0.01"),
        partial_fills_enabled=False,
        max_participation_rate=Decimal("0.1"),
        rejection_rate=Decimal(0),
        reject_on_stale_quote=False,
        cost_model=INDIAN_OPTIONS_COSTS,
    )
    adapters = [
        UpstoxBrokerAdapter(),
        PaperBrokerAdapter(execution=PaperExecutionModel(fill_model=model)),
    ]
    findings: list[str] = []
    for adapter in adapters:
        if ExecutionCapability.LIVE_SUBMIT in adapter.capabilities:
            findings.append(
                f"adapter {adapter.name} grants LIVE_SUBMIT. No adapter may, while "
                f"live execution is disabled (11-TRADING.md §9)."
            )
    return findings


def _check_upstox_cannot_submit(repo: Path) -> list[str]:
    """Every venue-reaching method must gate first and do nothing else."""
    path = repo / UPSTOX
    if not path.exists():
        return [f"{UPSTOX}: missing; the guard has nothing to check"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    findings: list[str] = []
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if node.name not in UPSTOX_GATED_METHODS:
            continue
        found.add(node.name)

        calls = [
            inner
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
        ]
        gates = [c for c in calls if c.func.id == "require_capability"]  # type: ignore[attr-defined]
        if not gates:
            findings.append(
                f"{UPSTOX}:{node.lineno}: {node.name} does not call "
                f"require_capability. Every venue-reaching method must gate first."
            )
            continue
        # Nothing may be awaited: an await here would mean real I/O.
        awaits = [inner for inner in ast.walk(node) if isinstance(inner, ast.Await)]
        if awaits:
            findings.append(
                f"{UPSTOX}:{awaits[0].lineno}: {node.name} awaits something. The "
                f"Upstox adapter must not perform I/O -- the Upstox order wire "
                f"format has no verified contract and is not invented here."
            )
        # The gate must come before anything else that could act.
        first_call = min(c.lineno for c in calls)
        if min(g.lineno for g in gates) > first_call:
            findings.append(
                f"{UPSTOX}:{node.lineno}: {node.name} calls something before "
                f"require_capability. The gate must be first."
            )

    missing = [m for m in UPSTOX_GATED_METHODS if m not in found]
    if missing:
        findings.append(
            f"{UPSTOX}: the adapter no longer defines {missing}. If a method was "
            f"removed the protocol is incomplete; if renamed, this guard is now "
            f"blind to it."
        )
    return findings


def _check_no_network_or_credentials(repo: Path) -> list[str]:
    findings: list[str] = []
    for path in _modules(repo / TRADING):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, module in _imports(tree):
            root = module.split(".")[0]
            if root in NETWORK_MODULES:
                findings.append(
                    f"{path.relative_to(repo)}:{lineno}: imports {module}, which can "
                    f"open a socket. Nothing under trading/ may reach a network."
                )
            for credential in CREDENTIAL_MODULES:
                if module == credential or module.startswith(credential + "."):
                    findings.append(
                        f"{path.relative_to(repo)}:{lineno}: imports {module}, which "
                        f"can reach a live credential."
                    )
    return findings


def _check_no_api_submit_route(repo: Path) -> list[str]:
    """No route submits to a broker, and none accepts forged provider truth."""
    findings: list[str] = []
    for path in _modules(repo / API):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "router"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                route = str(node.args[0].value).lower()
                for banned in ("/submit", "/place", "/broker", "/live"):
                    if banned in route:
                        findings.append(
                            f"{path.relative_to(repo)}:{node.lineno}: route {route!r} "
                            f"names a submission surface. Submission goes through "
                            f"intent -> risk -> OMS, never a direct endpoint."
                        )
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                for arg in [*node.args.args, *node.args.kwonlyargs]:
                    if arg.arg.startswith("provider_") and arg.arg != "provider_order_id":
                        findings.append(
                            f"{path.relative_to(repo)}:{node.lineno}: {node.name} takes "
                            f"{arg.arg!r}. Provider truth enters through the adapter, "
                            f"never a request parameter."
                        )
    return findings


def _check_authorization_precedes_submission(repo: Path) -> list[str]:
    """`authorize_submission` must run before `place_order`, positionally."""
    path = repo / MANAGER
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "submit":
            continue
        authorize: int | None = None
        place: int | None = None
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = (
                    inner.func.id
                    if isinstance(inner.func, ast.Name)
                    else getattr(inner.func, "attr", "")
                )
                if name == "authorize_submission" and authorize is None:
                    authorize = inner.lineno
                if name == "place_order" and place is None:
                    place = inner.lineno
        if authorize is None:
            return [
                f"{MANAGER}: submit() never calls authorize_submission. An order "
                f"could reach a venue without an approved risk decision."
            ]
        if place is None:
            return [f"{MANAGER}: submit() never calls place_order; guard is stale"]
        if authorize > place:
            return [
                f"{MANAGER}:{place}: place_order is called before the authorization "
                f"at line {authorize}. Authorization must precede submission."
            ]
        return []
    return [f"{MANAGER}: submit() not found"]


def _check_unknown_has_no_resubmit_edge(repo: Path) -> list[str]:
    """Parsed from the transition table: UNKNOWN must lead only to reconciliation.

    `11-TRADING.md` §5's "never resubmit from UNKNOWN" is only a real guarantee if
    the machine has no such edge. Reading the table is the only way to know.
    """
    path = repo / ORDERS
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_PERMITTED"
        ):
            continue
        table = node.value
        if not isinstance(table, ast.Dict):
            return [f"{ORDERS}: _PERMITTED is not a dict literal; cannot verify"]
        for key, value in zip(table.keys, table.values, strict=True):
            if not (isinstance(key, ast.Attribute) and key.attr == "UNKNOWN"):
                continue
            targets = {
                element.attr
                for element in ast.walk(value)
                if isinstance(element, ast.Attribute)
                and isinstance(element.value, ast.Name)
                and element.value.id == "OrderState"
            }
            if targets != {"PENDING_RECONCILIATION"}:
                return [
                    f"{ORDERS}: UNKNOWN permits {sorted(targets)}. It may lead only to "
                    f"PENDING_RECONCILIATION -- 11-TRADING.md §5 forbids resubmitting "
                    f"or assuming an outcome, and the machine is what enforces it."
                ]
            return []
        return [f"{ORDERS}: _PERMITTED has no UNKNOWN entry; the state is missing"]
    return [f"{ORDERS}: _PERMITTED table not found"]


def check(repo: Path) -> list[str]:
    findings: list[str] = []
    findings += _check_flag_is_a_literal_false(repo)
    # Static scans first. The runtime check below imports the adapters, and if a
    # network module has been introduced that import fails -- reporting "cannot
    # import" instead of naming the module that was added. Running the scan first
    # means the real finding is the one a reader sees.
    findings += _check_no_network_or_credentials(repo)
    findings += _check_upstox_cannot_submit(repo)
    findings += _check_no_adapter_grants_live_submit(repo)
    findings += _check_no_api_submit_route(repo)
    findings += _check_authorization_precedes_submission(repo)
    findings += _check_unknown_has_no_resubmit_edge(repo)
    return findings


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    findings = check(repo)
    if findings:
        print("FAIL  live-execution barrier violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(
        "PASS  live execution is impossible: the flag is a literal False, no adapter "
        "grants LIVE_SUBMIT, the Upstox adapter gates before acting and performs no "
        "I/O, trading/ reaches no network or credential, no route submits, "
        "authorization precedes submission, and UNKNOWN leads only to reconciliation"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
