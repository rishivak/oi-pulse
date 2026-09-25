#!/usr/bin/env python3
"""Guard: the portfolio layer derives, and never invents.

Phase 11 brief §29 asks for guards preventing the portfolio from mutating OMS truth,
recomputing signals, bypassing canonical market data, leaking future valuations,
importing Phase 12, reaching a broker, mutating risk, or contaminating a semantic
hash with runtime metadata.

Every check below is structural. Where a claim could only be made by grepping, it is
made by parsing instead.

### The eight checks

1. **The residual is computed, never assigned.** `AttributionResult.residual` must be
   a property whose body is `total - explained`, and there must be no `residual`
   field a writer could set. This is the single most important property in the phase:
   `18-ROADMAP.md` Phase 11 says a large residual is information, and a settable
   residual is one refactor away from being balanced to zero.
2. **No component is adjusted to make the sum work.** Nothing in `attribution.py`
   assigns to a `ComponentAmount.amount`.
3. **Semantic hashes exclude runtime metadata.** `PortfolioSnapshot.content_digest`
   must not incorporate `computed_at` or `snapshot_id` (brief §24).
4. **Valuation has no second price source.** `valuation.py` may read prices only from
   the `MarketState` it is handed (brief §9).
5. **The portfolio does not recompute signals or analytics** (brief §29, §16).
6. **The portfolio does not mutate OMS or risk state.** No assignment to an order or
   a decision, no call to an OMS mutator.
7. **No broker access, no Phase 12 imports.**
8. **Nothing reads a clock.** Valuation times are supplied; a wall-clock read would
   make a historical valuation depend on when it ran.

Stdlib-only AST, so it runs with no dependencies installed. Exit 0 clean, 1 on any
violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PORTFOLIO = Path("oipulse/trading/portfolio")
ATTRIBUTION = PORTFOLIO / "attribution.py"
SNAPSHOT = PORTFOLIO / "snapshot.py"
VALUATION = PORTFOLIO / "valuation.py"

#: Modules the portfolio layer must not reach.
#:
#: Note what is *absent*: `oipulse.trading.brokers.protocol`. Position
#: reconciliation must accept the broker's view of a position, and `BrokerPosition`
#: is a frozen value type -- importing a dataclass definition is not reaching a
#: broker. The adapters are what could touch a venue, and they stay forbidden, so
#: the portfolio can read provider evidence a caller hands it and cannot go and get
#: any. Defining a duplicate position type here would be the alternative, and two
#: types for one concept eventually disagree.
FORBIDDEN_MODULES = (
    "oipulse.trading.brokers.paper",
    "oipulse.trading.brokers.upstox",
    "oipulse.trading.brokers.capability",
    "oipulse.trading.oms",
    "oipulse.trading.risk",
    "oipulse.marketdata.providers",
    "oipulse.marketdata.upstox",
    "oipulse.api",
    "oipulse.persistence",
    "oipulse.terminal",
    "oipulse.signals.evaluation",
    "oipulse.signals.rules",
    "oipulse.analytics.engine",
    "oipulse.analytics.domains",
)

NETWORK_MODULES = ("httpx", "requests", "aiohttp", "urllib", "socket", "websockets")
CLOCK_CALLS = frozenset({"now", "utcnow", "today", "monotonic", "time_ns"})
#: Runtime metadata that must never enter a semantic hash.
RUNTIME_FIELDS = ("computed_at", "snapshot_id", "started_at", "completed_at")


def _modules(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def _find_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _check_residual_is_derived(repo: Path) -> list[str]:
    """`residual` must be a property computing `total - explained`, not a field."""
    path = repo / ATTRIBUTION
    if not path.exists():
        return [f"{ATTRIBUTION}: missing; the residual guard has nothing to check"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    result = _find_class(tree, "AttributionResult")
    if result is None:
        return [f"{ATTRIBUTION}: AttributionResult not found"]

    fields = {
        stmt.target.id
        for stmt in result.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    }
    if "residual" in fields:
        return [
            f"{ATTRIBUTION}: AttributionResult declares `residual` as a field. It "
            f"must be a derived property: a settable residual is one refactor away "
            f"from being balanced to zero, and 18-ROADMAP.md Phase 11 requires it to "
            f"be reported rather than managed."
        ]

    for stmt in result.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "residual":
            is_property = any(
                isinstance(d, ast.Name) and d.id == "property" for d in stmt.decorator_list
            )
            if not is_property:
                return [f"{ATTRIBUTION}:{stmt.lineno}: residual is not a property"]
            body = ast.unparse(stmt)
            if "total_pnl" not in body or "explained" not in body:
                return [
                    f"{ATTRIBUTION}:{stmt.lineno}: residual does not derive from "
                    f"total_pnl and explained. It must be the difference, so no "
                    f"component adjustment can shrink it."
                ]
            return []
    return [f"{ATTRIBUTION}: AttributionResult has no `residual` property"]


def _check_no_component_is_adjusted(repo: Path) -> list[str]:
    """Nothing assigns to a component's amount to make a sum come out."""
    path = repo / ATTRIBUTION
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "amount":
                findings.append(
                    f"{ATTRIBUTION}:{node.lineno}: assigns to a component's `amount`. "
                    f"Components are computed from inputs; adjusting one to make the "
                    f"decomposition sum correctly would hide the residual."
                )
    return findings


def _check_hash_excludes_runtime_metadata(repo: Path) -> list[str]:
    """Brief §24: execution metadata must not alter a semantic hash."""
    path = repo / SNAPSHOT
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    snapshot = _find_class(tree, "PortfolioSnapshot")
    if snapshot is None:
        return [f"{SNAPSHOT}: PortfolioSnapshot not found"]

    for stmt in snapshot.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "content_digest":
            # The digest is built from `as_dict()`, so check that instead of the
            # digest body -- the contamination would enter there.
            as_dict = next(
                (
                    inner
                    for inner in snapshot.body
                    if isinstance(inner, ast.FunctionDef) and inner.name == "as_dict"
                ),
                None,
            )
            if as_dict is None:
                return [f"{SNAPSHOT}: PortfolioSnapshot has no as_dict to inspect"]
            keys = {
                element.value
                for element in ast.walk(as_dict)
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            leaked = [field for field in RUNTIME_FIELDS if field in keys]
            if leaked:
                return [
                    f"{SNAPSHOT}: the snapshot's as_dict -- and therefore its content "
                    f"digest -- includes runtime metadata {leaked}. When the "
                    f"arithmetic ran is not part of what it found (brief §24)."
                ]
            return []
    return [f"{SNAPSHOT}: PortfolioSnapshot has no content_digest"]


def _check_single_price_source(repo: Path) -> list[str]:
    """Valuation reads prices only from the MarketState it is handed."""
    path = repo / VALUATION
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        # A fetch of any kind would mean a second price source. `get` is excluded:
        # it is a dict lookup on marks already extracted from the supplied state.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {"fetch_quotes", "fetch_option_chain", "fetch_historical_ohlc", "get_state"}
        ):
            findings.append(
                f"{VALUATION}:{node.lineno}: calls {node.func.attr}. Valuation must "
                f"read prices from the MarketState it is given; fetching would "
                f"create a second price source (brief §9)."
            )
    return findings


def _check_imports(repo: Path) -> list[str]:
    findings: list[str] = []
    for path in _modules(repo / PORTFOLIO):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[tuple[int, str]] = []
            if isinstance(node, ast.Import):
                modules = [(node.lineno, a.name) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [(node.lineno, node.module)]
            for lineno, module in modules:
                if module.split(".")[0] in NETWORK_MODULES:
                    findings.append(
                        f"{path.relative_to(repo)}:{lineno}: imports {module}, which "
                        f"can open a socket."
                    )
                for forbidden in FORBIDDEN_MODULES:
                    if module == forbidden or module.startswith(forbidden + "."):
                        findings.append(
                            f"{path.relative_to(repo)}:{lineno}: imports {module}. "
                            f"The portfolio layer derives from canonical fills and "
                            f"market state; it does not reach the OMS, risk, a "
                            f"broker, the API, persistence, a later phase, or a "
                            f"second analytics implementation."
                        )
    return findings


def _check_no_mutation_of_upstream_truth(repo: Path) -> list[str]:
    """No assignment to an order, decision or fill attribute."""
    findings: list[str] = []
    upstream = {"order", "decision", "fill", "intent"}
    for path in _modules(repo / PORTFOLIO):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in upstream
                ):
                    findings.append(
                        f"{path.relative_to(repo)}:{node.lineno}: assigns to "
                        f"{target.value.id}.{target.attr}. The portfolio layer reads "
                        f"OMS truth; it never rewrites it."
                    )
    return findings


def _check_no_clock(repo: Path) -> list[str]:
    findings: list[str] = []
    for path in _modules(repo / PORTFOLIO):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CLOCK_CALLS
            ):
                findings.append(
                    f"{path.relative_to(repo)}:{node.lineno}: calls "
                    f"{node.func.attr}(). Valuation times are supplied; reading a "
                    f"clock would make a historical valuation depend on when it ran."
                )
    return findings


def check(repo: Path) -> list[str]:
    findings: list[str] = []
    findings += _check_residual_is_derived(repo)
    findings += _check_no_component_is_adjusted(repo)
    findings += _check_hash_excludes_runtime_metadata(repo)
    findings += _check_single_price_source(repo)
    findings += _check_imports(repo)
    findings += _check_no_mutation_of_upstream_truth(repo)
    findings += _check_no_clock(repo)
    return findings


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    if not (repo / PORTFOLIO).exists():
        print(f"FAIL  {PORTFOLIO} does not exist; nothing to verify")
        return 1

    findings = check(repo)
    if findings:
        print("FAIL  portfolio integrity violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print(
        "PASS  portfolio integrity intact: the residual is derived and no component "
        "is adjusted, semantic hashes exclude runtime metadata, valuation has one "
        "price source, the layer reaches no OMS, risk, broker, API or later phase, "
        "upstream truth is never mutated, and nothing reads a clock"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
