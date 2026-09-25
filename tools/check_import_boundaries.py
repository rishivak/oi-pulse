#!/usr/bin/env python3
"""Guard: dependency direction and layer boundaries.

`docs/design/00-OVERVIEW.md` §5 and `15-TESTING.md` §5.

Two rules make the layering real rather than decorative: dependencies point downward
only, and the contract is enforced in CI rather than by convention. This is that
enforcement.

The contracts are declared for packages that do not exist yet (`analytics`, `trading`,
`signals`, …). That is deliberate — Phase 1 exists so that *later phases cannot erode the
design*. A contract armed before the package arrives is a contract the package is born
under. `--require` asserts that a package now exists, so a phase can turn its own
contract from latent to mandatory.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT_PACKAGE = "oipulse"


@dataclass(frozen=True)
class Contract:
    """A dependency rule over the root package's subpackages."""

    name: str
    #: Subpackage the rule constrains, e.g. "analytics".
    subject: str
    #: If set, the ONLY subpackages the subject may import (plus itself).
    allowed_only: frozenset[str] | None = None
    #: Subpackages the subject may never import.
    forbidden: frozenset[str] = field(default_factory=frozenset)
    #: Fully-qualified modules the subject may never import, e.g. "oipulse.core.clock".
    forbidden_modules: frozenset[str] = field(default_factory=frozenset)
    #: Third-party roots the subject may never import.
    forbidden_external: frozenset[str] = field(default_factory=frozenset)
    #: Module keys allowed to violate this contract, each with a stated reason.
    #: Kept deliberately small: an exemption list that grows is a contract dissolving.
    exempt: frozenset[str] = field(default_factory=frozenset)
    rationale: str = ""


_DB_AND_IO = frozenset(
    {
        "sqlalchemy",
        "asyncpg",
        "psycopg2",
        "psycopg",
        "redis",
        "httpx",
        "requests",
        "aiohttp",
        "fastapi",
        "starlette",
    }
)

CONTRACTS: tuple[Contract, ...] = (
    Contract(
        name="analytics-is-pure",
        subject="analytics",
        allowed_only=frozenset({"marketstate", "core"}),
        forbidden_modules=frozenset({f"{ROOT_PACKAGE}.core.clock"}),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "analytics must be pure: one implementation serves live, replay, research "
            "and backtest. No DB, no HTTP, and no clock — available_at derives from the "
            "state and the feature definition, never from 'now' (07-ANALYTICS.md §1)."
        ),
    ),
    Contract(
        name="signals-are-pure",
        subject="signals",
        allowed_only=frozenset({"analytics", "marketstate", "core"}),
        forbidden_modules=frozenset({f"{ROOT_PACKAGE}.core.clock"}),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "Signal rules are pure over RuleContext -- same purity contract as "
            "analytics, same testability, one implementation across live, replay and "
            "backtest (08-SIGNALS.md §4). No DB, no HTTP, no clock: a signal's "
            "available_at derives from its inputs, never from 'now'."
        ),
    ),
    Contract(
        name="alerts-never-import-signal-internals",
        subject="alerts",
        allowed_only=frozenset({"signals", "core"}),
        forbidden_modules=frozenset(
            {
                f"{ROOT_PACKAGE}.signals.evaluation",
                f"{ROOT_PACKAGE}.signals.lifecycle",
                f"{ROOT_PACKAGE}.signals.rules",
                f"{ROOT_PACKAGE}.core.clock",
            }
        ),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "Alerts are a delivery concern, not a truth concern. Importing the "
            "evaluator, the lifecycle table or the rule registry would let delivery "
            "re-evaluate or transition a signal; a signal must exist unchanged whether "
            "or not anyone is listening (18-ROADMAP.md Phase 5)."
        ),
    ),
    Contract(
        name="research-is-pure",
        subject="research",
        allowed_only=frozenset({"signals", "analytics", "marketstate", "core"}),
        forbidden_modules=frozenset({f"{ROOT_PACKAGE}.core.clock"}),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "A research result that cannot be reproduced live is worse than no result "
            "(09-RESEARCH.md). The engine reads supplied datasets and an injected "
            "clock-free instant, so a study runs identically in research, replay and "
            "backtest. It also must not import `alerts`: delivery has no bearing on "
            "what history shows."
        ),
    ),
    Contract(
        name="research-never-recomputes-analytics",
        subject="research",
        forbidden_modules=frozenset(
            {
                f"{ROOT_PACKAGE}.analytics.domains",
                f"{ROOT_PACKAGE}.analytics.engine",
                f"{ROOT_PACKAGE}.signals.evaluation",
            }
        ),
        rationale=(
            "Research consumes verified feature and signal *results*, pinned to exact "
            "versions. Importing the feature domains or either engine would create a "
            "second analytics implementation, and the two would drift silently -- the "
            "exact divergence the single-implementation rule exists to prevent."
        ),
    ),
    Contract(
        name="replay-is-pure",
        subject="replay",
        allowed_only=frozenset({"marketstate", "marketdata", "core"}),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "Replay drives the *same* builder as live processing (10-REPLAY.md §1), so "
            "it must be constructible from observations and a state service alone. A "
            "database or HTTP dependency here would mean a replay could only run where "
            "production runs, and the shared-mechanism guarantee would be untestable."
        ),
    ),
    Contract(
        name="replay-never-reaches-a-broker-or-a-provider",
        subject="replay",
        forbidden=frozenset({"trading", "brokers", "alerts", "api"}),
        forbidden_modules=frozenset(
            {
                f"{ROOT_PACKAGE}.marketdata.providers",
                f"{ROOT_PACKAGE}.marketdata.upstox",
            }
        ),
        rationale=(
            "A replay reconstructs history from stored observations. Reaching a live "
            "provider would make it non-deterministic; reaching a broker adapter or "
            "the alert layer would let replaying the past emit a real order or a real "
            "alert, which 10-REPLAY.md §8 forbids by namespacing replay events away "
            "from the live outbox. `core.clock` is deliberately NOT forbidden here, "
            "unlike in the pure layers: replay legitimately owns `ReplayClock`, whose "
            "'now' is the replayed instant. The rule that matters -- no *wall* clock "
            "-- is enforced for every module by tools/check_clock_access.py, which "
            "distinguishes the two; a blanket import ban here could not."
        ),
    ),
    Contract(
        name="backtest-is-pure-and-cannot-trade",
        subject="backtest",
        allowed_only=frozenset(
            {"replay", "research", "signals", "analytics", "marketstate", "core"}
        ),
        forbidden_modules=frozenset({f"{ROOT_PACKAGE}.core.clock"}),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "A backtest must not be able to submit an order, mutate live trading "
            "state, or read the wall clock. `trading`, `brokers`, `alerts`, `api` and "
            "`persistence` are all out of reach, so 'the backtest cannot touch "
            "production' is a property of the import graph rather than a policy "
            "someone has to remember. Persisting a result is the caller's job."
        ),
    ),
    Contract(
        name="no-phase-8-or-later-leakage",
        subject="backtest",
        forbidden=frozenset({"paper", "portfolio", "oms", "terminal"}),
        rationale=(
            "Phase 7 implements replay and backtesting only. Paper trading (Phase 8), "
            "the OMS (Phase 10), portfolio attribution (Phase 11) and the terminal "
            "(Phase 12) are later layers; importing one would build the next phase "
            "early and skip its gate."
        ),
    ),
    Contract(
        name="paper-trading-is-pure",
        subject="trading",
        allowed_only=frozenset(
            {
                "backtest",
                "replay",
                "research",
                "signals",
                "analytics",
                "marketstate",
                "events",
                # Phase 11: portfolio valuation resolves lot size and contract
                # multiplier from the instrument version valid at the valuation
                # time (`07-ANALYTICS.md` §4.3, `11-TRADING.md` §8). `instruments`
                # is a pure low layer -- stdlib plus `core` only, the same tier as
                # `events` -- so admitting it does not weaken the purity contract.
                # The alternative would be a second copy of contract economics for
                # the two to disagree about.
                "instruments",
                "core",
            }
        ),
        forbidden_modules=frozenset({f"{ROOT_PACKAGE}.core.clock"}),
        forbidden_external=_DB_AND_IO,
        rationale=(
            "The paper runtime must be constructible and testable with no database, "
            "no HTTP client and no clock. The clock matters most: `11-TRADING.md` "
            "and Phase 8 require market time to drive every transition, and a module "
            "that could read the wall clock could silently substitute it for "
            "market_time. Persisting a trade is the caller's job."
        ),
    ),
    Contract(
        name="paper-trading-cannot-reach-a-broker",
        subject="trading",
        forbidden=frozenset({"api", "alerts"}),
        forbidden_modules=frozenset(
            {
                f"{ROOT_PACKAGE}.marketdata.providers",
                f"{ROOT_PACKAGE}.marketdata.upstox",
                f"{ROOT_PACKAGE}.marketdata.auth",
                f"{ROOT_PACKAGE}.core.config",
                f"{ROOT_PACKAGE}.core.secrets",
            }
        ),
        rationale=(
            "Phase 8 is paper-only and that must be a property of the import graph, "
            "not a policy. The provider and Upstox packages are the only code that "
            "can speak to the broker; `auth` and the settings/secrets modules are "
            "the only code that can reach a credential. None is importable from "
            "`trading`, so there is no path -- direct or transitive -- by which a "
            "paper order could become a real one (11-TRADING.md §9)."
        ),
    ),
    Contract(
        name="paper-trading-imports-no-later-phase",
        subject="trading",
        forbidden=frozenset({"portfolio", "oms", "reconciliation", "terminal"}),
        rationale=(
            "Phase 8 implements paper trading only. The risk engine (Phase 9), the "
            "OMS and live reconciliation (Phase 10), portfolio attribution "
            "(Phase 11) and the terminal (Phase 12) are later layers; importing one "
            "would build the next phase early and skip its gate. `trading.risk` is "
            "the declared seam and is part of this package, not an import of Phase 9."
        ),
    ),
    Contract(
        name="nothing-imports-api",
        subject="*",
        forbidden=frozenset({"api"}),
        # `run` is the composition root: its whole job is to wire the outermost layer
        # to a validated configuration. Every layered design needs exactly one module
        # that may see the top, and naming it here keeps that privilege visible
        # instead of letting the contract quietly weaken.
        exempt=frozenset({"run"}),
        rationale=(
            "api/ is the outermost layer. Anything importing it has inverted the "
            "dependency direction (00-OVERVIEW.md §5). Only the composition root "
            "(oipulse/run.py) is exempt."
        ),
    ),
    Contract(
        name="risk-is-independent",
        subject="trading.risk",
        forbidden=frozenset(
            {
                "trading.oms",
                "trading.brokers",
                "strategies",
                # Phase 8's equivalents of `oms`: the order machine, the execution
                # model and the runtime. Named explicitly because the module layout
                # is `trading/orders.py` rather than `trading/oms/`, and a contract
                # that only knew the documented name would silently pass.
                "trading.orders",
                "trading.execution",
                "trading.runtime",
                "trading.ledger",
            }
        ),
        rationale=(
            "The component that says 'no' must not depend on the components it "
            "constrains. Risk must be testable with no trading infrastructure present "
            "(11-TRADING.md §3). It receives an intent plus context and returns a "
            "decision -- that is the entire surface."
        ),
    ),
    Contract(
        name="core-is-dependency-free",
        subject="core",
        allowed_only=frozenset(),
        forbidden_external=_DB_AND_IO | frozenset({"pydantic", "pydantic_settings", "structlog"}),
        rationale=(
            "core is the innermost layer, imported by every other including pure "
            "analytics. Third-party dependencies here would leak into layers the "
            "contract forbids from having them."
        ),
    ),
)


def _module_key(path: Path, root: Path) -> str:
    """'oipulse/analytics/positioning/oi.py' -> 'analytics.positioning.oi'."""
    rel = path.relative_to(root).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _imported_roots(tree: ast.AST) -> list[tuple[int, str]]:
    """Every imported module path, with line numbers."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — stays within the subpackage
                continue
            if node.module:
                out.append((node.lineno, node.module))
    return out


def _subpackage_of(dotted: str) -> str | None:
    """'oipulse.analytics.positioning' -> 'analytics.positioning'."""
    if dotted == ROOT_PACKAGE:
        return ""
    prefix = f"{ROOT_PACKAGE}."
    if not dotted.startswith(prefix):
        return None
    return dotted[len(prefix) :]


def _matches_subject(module_key: str, subject: str) -> bool:
    if subject == "*":
        return True
    return module_key == subject or module_key.startswith(subject + ".")


def _violates_target(target: str, pattern: str) -> bool:
    return target == pattern or target.startswith(pattern + ".")


def check(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module_key = _module_key(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(f"{path}:{exc.lineno}: syntax error, cannot verify")
            continue

        imports = _imported_roots(tree)
        for contract in CONTRACTS:
            if not _matches_subject(module_key, contract.subject):
                continue
            if module_key in contract.exempt or module_key.split(".")[0] in contract.exempt:
                continue

            # A module's own top-level package. Under the wildcard subject there is no
            # declared subject to compare against, so self-imports must be recognised
            # from the importer itself or `api.app` importing `api.health` is flagged.
            own_top = module_key.split(".")[0]

            for lineno, imported in imports:
                internal = _subpackage_of(imported)
                external_root = imported.split(".")[0]

                if internal is not None:
                    own = contract.subject if contract.subject != "*" else own_top
                    if own and _violates_target(internal, own):
                        continue  # a package may import itself
                    top = internal.split(".")[0]

                    if (
                        contract.allowed_only is not None
                        and internal
                        and top not in contract.allowed_only
                    ):
                        findings.append(
                            f"{path}:{lineno}: [{contract.name}] "
                            f"{module_key} imports {imported}; allowed: "
                            f"{sorted(contract.allowed_only) or '(none)'}"
                        )
                    for bad in contract.forbidden:
                        if _violates_target(internal, bad):
                            findings.append(
                                f"{path}:{lineno}: [{contract.name}] "
                                f"{module_key} must not import {imported}"
                            )
                    if imported in contract.forbidden_modules:
                        findings.append(
                            f"{path}:{lineno}: [{contract.name}] "
                            f"{module_key} must not import {imported}"
                        )
                else:
                    if external_root in contract.forbidden_external:
                        findings.append(
                            f"{path}:{lineno}: [{contract.name}] "
                            f"{module_key} must not import third-party {imported}"
                        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=ROOT_PACKAGE)
    parser.add_argument(
        "--require",
        nargs="*",
        default=[],
        help="subpackages that must exist (a phase arms its own contract)",
    )
    parser.add_argument("--list-contracts", action="store_true")
    args = parser.parse_args()

    if args.list_contracts:
        for c in CONTRACTS:
            state = "armed"
            print(f"  [{state}] {c.name}: subject={c.subject}")
        return 0

    root = Path(args.root)
    if not root.exists():
        print(f"check_import_boundaries: no such package {root}", file=sys.stderr)
        return 2

    missing = [r for r in args.require if not (root / Path(*r.split("."))).exists()]
    if missing:
        print(f"FAIL  required subpackages absent: {', '.join(missing)}")
        return 1

    findings = check(root)
    if findings:
        print("FAIL  layer boundary violations:")
        for f in findings:
            print(f"  {f}")
        print("\nContracts:")
        for c in CONTRACTS:
            print(f"  {c.name}: {c.rationale}")
        return 1

    armed = ", ".join(c.name for c in CONTRACTS)
    print(f"PASS  layer boundaries clean ({len(CONTRACTS)} contracts armed: {armed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
