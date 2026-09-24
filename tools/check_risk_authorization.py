#!/usr/bin/env python3
"""Guard: nothing executes without an approved risk decision from its own intent.

`18-ROADMAP.md` Phase 9 names the acceptance criterion:

> **no code path reaches a broker without an approved decision from its own intent**
> (static + DB constraint + runtime)

This is the *static* third. The DB constraint lives in migration `0009` (a composite
FK plus a trigger) and the runtime third is in `PaperTradingRuntime.submit`. Three
independent mechanisms, because any one of them can be edited.

What this checks, and why an import graph cannot:

1. **`PaperOrder` carries the authorization.** Both halves of the composite key
   `(intent_id, sequence_no)` must exist as fields. Losing them would silently
   detach every order from its approval.
2. **Only the runtime constructs a `PaperOrder`.** If any other module could build
   one, it could build one with no authorization, and the runtime's check would
   guard nothing.
3. **The runtime checks approval before constructing.** Verified positionally in
   the AST: the `is_actionable_at` guard must appear before the `PaperOrder(...)`
   call in `submit`. A check that ran afterwards would authorize retrospectively.
4. **The risk package cannot reach execution.** `11` §3: the component that says
   "no" must not depend on the components it constrains.
5. **Risk does not mutate the intent.** No assignment to an intent attribute, and
   no `dataclasses.replace` of one, anywhere under `trading/risk/`. Brief §11: the
   original intent remains immutable; a reduction is recorded on the decision.
6. **A decision binds exactly one intent.** `RiskDecisionRecord.authorizes` must
   compare `intent_id`, or an approval for A could be presented for B.

Stdlib-only AST, so it runs with no dependencies installed and cannot be disabled by
an environment failure. Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

RISK_PKG = Path("oipulse/trading/risk")
ORDERS = Path("oipulse/trading/orders.py")
RUNTIME = Path("oipulse/trading/runtime.py")
DECISION = Path("oipulse/trading/risk/decision.py")
TRADING = Path("oipulse/trading")

REQUIRED_ORDER_FIELDS = ("authorizing_risk_decision_id", "authorizing_decision_sequence")

#: Modules the risk package must not reach. `11` §3's list, plus the Phase 8
#: equivalents of `oms` (the module layout differs from the name in the design).
RISK_FORBIDDEN = (
    "oipulse.trading.orders",
    "oipulse.trading.brokers",
    "oipulse.trading.execution",
    "oipulse.trading.runtime",
    "oipulse.trading.ledger",
    "oipulse.marketdata.providers",
    "oipulse.marketdata.upstox",
    "oipulse.api",
)


def _modules(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def _check_order_fields(repo: Path) -> list[str]:
    path = repo / ORDERS
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PaperOrder":
            fields = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
            missing = [f for f in REQUIRED_ORDER_FIELDS if f not in fields]
            if missing:
                return [
                    f"{ORDERS}: PaperOrder is missing {missing}. `02-DATA_MODEL.md` §11 "
                    f"keys an order's authorization on (intent_id, sequence_no); "
                    f"without both halves an order is detached from its approval."
                ]
            return []
    return [f"{ORDERS}: PaperOrder not found"]


def _check_single_constructor(repo: Path) -> list[str]:
    """Only the runtime may construct a PaperOrder."""
    findings: list[str] = []
    for path in _modules(repo / TRADING):
        if path.name in {"orders.py", "runtime.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "PaperOrder"
            ):
                findings.append(
                    f"{path.relative_to(repo)}:{node.lineno}: constructs a PaperOrder. "
                    f"Only the runtime may, because only the runtime checks for an "
                    f"approved decision first; another construction site is a path "
                    f"around the gate."
                )
    return findings


def _check_approval_precedes_construction(repo: Path) -> list[str]:
    """The approval check must appear before the order is built, in `submit`."""
    path = repo / RUNTIME
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "submit":
            continue

        check_line: int | None = None
        construct_line: int | None = None
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "is_actionable_at"
                and check_line is None
            ):
                check_line = inner.lineno
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "PaperOrder"
                and construct_line is None
            ):
                construct_line = inner.lineno

        if check_line is None:
            return [
                f"{RUNTIME}: submit() never calls is_actionable_at. An order would be "
                f"created without confirming its decision approves and has not expired."
            ]
        if construct_line is None:
            return [f"{RUNTIME}: submit() never constructs a PaperOrder; guard is stale"]
        if check_line > construct_line:
            return [
                f"{RUNTIME}:{construct_line}: a PaperOrder is constructed before the "
                f"approval check at line {check_line}. Authorization must precede "
                f"execution, not follow it."
            ]
        return []
    return [f"{RUNTIME}: submit() not found"]


def _check_risk_independence(repo: Path) -> list[str]:
    findings: list[str] = []
    for path in _modules(repo / RISK_PKG):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [(node.lineno, a.name) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append((node.lineno, node.module))
        for lineno, module in imported:
            for forbidden in RISK_FORBIDDEN:
                if module == forbidden or module.startswith(forbidden + "."):
                    findings.append(
                        f"{path.relative_to(repo)}:{lineno}: imports {module}. "
                        f"The component that says 'no' must not depend on the "
                        f"components it constrains (11-TRADING.md §3)."
                    )
    return findings


def _check_risk_does_not_mutate_intents(repo: Path) -> list[str]:
    """No assignment to an intent attribute, and no `replace()` of one.

    Brief §11 and §16: risk decides whether and how much of an intent may proceed;
    it does not rewrite strategy truth. A reduction is recorded as
    `approved_quantity` on the decision.
    """
    findings: list[str] = []
    for path in _modules(repo / RISK_PKG):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "intent"
                    ):
                        findings.append(
                            f"{path.relative_to(repo)}:{node.lineno}: assigns to "
                            f"intent.{target.attr}. The intent is immutable; a risk "
                            f"decision records what was allowed, it does not rewrite "
                            f"what was asked (brief §11)."
                        )
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "replace" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Name) and first.id == "intent":
                        findings.append(
                            f"{path.relative_to(repo)}:{node.lineno}: replaces the "
                            f"intent. Risk must not produce a modified intent; it "
                            f"records an approved quantity instead (brief §11)."
                        )
    return findings


def _check_decision_binds_one_intent(repo: Path) -> list[str]:
    """`authorizes` must compare the intent id."""
    path = repo / DECISION
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "authorizes":
            # Look for an actual comparison between the `intent_id` parameter and
            # `self.intent_id`. Searching the unparsed source for the string
            # "intent_id" would always succeed -- it is the parameter's own name --
            # so the check has to be structural to mean anything.
            compares = False
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Compare):
                    continue
                operands = [inner.left, *inner.comparators]
                names = {o.id for o in operands if isinstance(o, ast.Name)}
                attrs = {
                    o.attr
                    for o in operands
                    if isinstance(o, ast.Attribute)
                    and isinstance(o.value, ast.Name)
                    and o.value.id == "self"
                }
                if "intent_id" in names and "intent_id" in attrs:
                    compares = True
                    break
            if not compares:
                return [
                    f"{DECISION}:{node.lineno}: authorizes() does not compare the "
                    f"intent_id argument against self.intent_id. An approval for one "
                    f"intent could then authorize another (brief §6)."
                ]
            return []
    return [f"{DECISION}: RiskDecisionRecord.authorizes not found"]


def check(repo: Path) -> list[str]:
    findings: list[str] = []
    findings += _check_order_fields(repo)
    findings += _check_single_constructor(repo)
    findings += _check_approval_precedes_construction(repo)
    findings += _check_risk_independence(repo)
    findings += _check_risk_does_not_mutate_intents(repo)
    findings += _check_decision_binds_one_intent(repo)
    return findings


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    if not (repo / RISK_PKG).exists():
        print(f"FAIL  {RISK_PKG} does not exist; nothing to verify")
        return 1

    findings = check(repo)
    if findings:
        print("FAIL  risk authorization violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print(
        "PASS  risk authorization intact: orders carry their approval's composite key, "
        "only the runtime builds one, the approval check precedes construction, risk "
        "imports nothing it constrains, risk never mutates an intent, and a decision "
        "binds exactly one intent"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
