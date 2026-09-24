#!/usr/bin/env python3
"""Guard: paper trading cannot become live trading.

`11-TRADING.md` §9 lists the safety posture. Phase 8 implements the paper half and
leaves the live half **absent** rather than disabled, because a disabled feature is
one configuration change away from being enabled while an absent implementation is
not. This guard is what makes "absent" checkable rather than asserted.

It is deliberately *not* an import check — `tools/check_import_boundaries.py` already
proves `trading` cannot reach the provider, Upstox or credential modules. This guard
checks the things an import graph cannot see:

1. **Exactly one `BrokerAdapter` implementation exists**, and it is the paper one. A
   second implementation appearing is the single clearest signal that Phase 10 work
   has landed without Phase 10's gates.
2. **No submission verb reaches a network.** No module under `oipulse/trading/`
   calls anything that looks like an outbound HTTP request.
3. **`resolve_execution_mode` still raises for non-paper.** The one function that
   turns a mode into an execution path must keep refusing; a future edit that made
   it return silently would open the path without touching any import.
4. **No API route offers a live mode.** No route path, query parameter or literal in
   the paper-trading router selects a live venue.
5. **Every paper API response states its mode.** §19 of the Phase 8 brief: every
   endpoint must clearly identify paper mode.

Stdlib-only AST, so it runs with no dependencies installed and cannot be disabled by
an environment failure. Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TRADING = Path("oipulse/trading")
ACCOUNTS = TRADING / "accounts.py"
BROKERS = TRADING / "brokers.py"
SERIALISATION = TRADING / "serialisation.py"
API_ROUTER = Path("oipulse/api/paper_trading.py")

#: The only permitted adapter implementation.
PERMITTED_ADAPTERS = frozenset({"PaperBrokerAdapter"})

#: Call names that would mean an outbound request. Matched on the attribute or
#: function name, so `client.post(...)`, `requests.post(...)` and a bare `post(...)`
#: are all caught.
NETWORK_CALLS = frozenset(
    {"post", "put", "patch", "request", "send", "urlopen", "connect", "websocket_connect"}
)

#: Tokens that would indicate a live execution escape hatch in the API surface.
LIVE_TOKENS = ("mode=live", "live_mode", "enable_live", "allow_live", "go_live")


def _module_files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def _check_single_adapter(repo: Path) -> list[str]:
    """Exactly one BrokerAdapter implementation, and it is the paper one."""
    findings: list[str] = []
    implementations: list[tuple[str, str]] = []

    for path in _module_files(repo / TRADING):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # A class is an adapter implementation if it declares the *whole*
            # adapter surface and is not the protocol itself. All three verbs are
            # required: the runtime also has `submit` and `cancel`, because it
            # delegates them, and flagging it would be a false positive that
            # trains a reader to ignore this guard.
            members = {
                n.name for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            is_protocol = any(
                isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
            )
            if {"submit", "cancel", "status"} <= members and not is_protocol:
                implementations.append((node.name, path.name))

    for name, filename in implementations:
        if name not in PERMITTED_ADAPTERS:
            findings.append(
                f"{TRADING}/{filename}: {name} implements the broker adapter surface "
                f"(submit + cancel) but is not the paper adapter. Phase 8 permits "
                f"exactly one implementation. If this is the Phase 10 live adapter, "
                f"it must not land without the three gates 11-TRADING.md §9 requires."
            )
    if not implementations:
        findings.append(
            f"{TRADING}: no broker adapter implementation found at all — the guard "
            f"has nothing to check, which means it would pass vacuously"
        )
    return findings


def _check_no_network_calls(repo: Path) -> list[str]:
    """No module under trading/ makes an outbound request."""
    findings: list[str] = []
    for path in _module_files(repo / TRADING):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            # `submit` is the adapter's own verb and is not a network call; it is
            # excluded by name because the paper adapter legitimately defines and
            # calls it. Everything else on the list has no honest use here.
            if name in NETWORK_CALLS and name != "submit":
                findings.append(
                    f"{path.relative_to(repo)}:{node.lineno}: calls {name}(), which "
                    f"looks like an outbound request. Paper trading reaches no "
                    f"network (11-TRADING.md §9)."
                )
    return findings


def _check_mode_resolution_refuses(repo: Path) -> list[str]:
    """`resolve_execution_mode` must still raise for a non-paper mode."""
    path = repo / ACCOUNTS
    if not path.exists():
        return [f"{ACCOUNTS}: missing — the mode-resolution guard has nothing to check"]

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_execution_mode":
            raises = [n for n in ast.walk(node) if isinstance(n, ast.Raise)]
            if not raises:
                return [
                    f"{ACCOUNTS}:{node.lineno}: resolve_execution_mode no longer "
                    f"raises. It is the single place a mode becomes an execution "
                    f"path; if it returns silently for a non-paper account, the live "
                    f"path is open without any import having changed."
                ]
            return []
    return [f"{ACCOUNTS}: resolve_execution_mode not found"]


def _check_api_offers_no_live_mode(repo: Path) -> list[str]:
    """No route, parameter or literal in the paper API selects a live venue."""
    path = repo / API_ROUTER
    if not path.exists():
        return [f"{API_ROUTER}: missing — the API guard has nothing to check"]

    source = path.read_text(encoding="utf-8")
    findings: list[str] = []
    lowered = source.lower()
    for token in LIVE_TOKENS:
        if token in lowered:
            findings.append(
                f"{API_ROUTER}: contains {token!r}, which reads as a live-execution "
                f"escape hatch. Phase 8 brief §19 forbids one."
            )

    tree = ast.parse(source, filename=str(path))

    # Route paths must not name a live surface.
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
            for banned in ("/live", "/broker", "/real"):
                if banned in route:
                    findings.append(
                        f"{API_ROUTER}:{node.lineno}: route {route!r} names a live "
                        f"surface; Phase 8 exposes paper trading only."
                    )

    # A function parameter named `mode` would let a client choose one.
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for arg in [*node.args.args, *node.args.kwonlyargs]:
                if arg.arg == "mode":
                    findings.append(
                        f"{API_ROUTER}:{node.lineno}: {node.name} takes a `mode` "
                        f"parameter. Mode is not a client's choice in Phase 8; the "
                        f"account carries it and only PAPER is servable."
                    )
    return findings


def _check_every_envelope_states_paper(repo: Path) -> list[str]:
    """Every paper-trading envelope must declare its mode.

    Checked against the serialisation module, which is where the claim is made, and
    asserts the single `_MODE` constant is `PAPER` — so a future envelope cannot
    quietly emit something else by constructing its own string.
    """
    path = repo / SERIALISATION
    if not path.exists():
        return [f"{SERIALISATION}: missing — the envelope guard has nothing to check"]

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mode_values: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_MODE" for t in node.targets)
            and isinstance(node.value, ast.Constant)
        ):
            mode_values.append(str(node.value.value))

    if mode_values != ["PAPER"]:
        return [
            f'{SERIALISATION}: expected exactly one `_MODE = "PAPER"`, found '
            f"{mode_values or 'none'}. Every paper response must state its mode from "
            f"one place, so a new envelope cannot forget or contradict it."
        ]
    return []


def check(repo: Path) -> list[str]:
    findings: list[str] = []
    findings += _check_single_adapter(repo)
    findings += _check_no_network_calls(repo)
    findings += _check_mode_resolution_refuses(repo)
    findings += _check_api_offers_no_live_mode(repo)
    findings += _check_every_envelope_states_paper(repo)
    return findings


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    if not (repo / TRADING).exists():
        print(f"FAIL  {TRADING} does not exist; nothing to verify")
        return 1

    findings = check(repo)
    if findings:
        print("FAIL  paper-trading safety violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print(
        "PASS  paper trading cannot become live: one adapter (PaperBrokerAdapter), no "
        "outbound call, mode resolution still refuses non-paper, no live API surface, "
        "every envelope states PAPER"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
