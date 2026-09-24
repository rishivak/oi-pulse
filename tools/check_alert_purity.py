#!/usr/bin/env python3
"""Guard: alert delivery cannot mutate signal truth.

`18-ROADMAP.md` Phase 5 separates signals from alerts, and the separation is only real
if delivery physically cannot write back. A signal must exist unchanged whether or not
anyone is listening: with no destination configured, with delivery delayed, failed or
retried.

The failure this prevents is quiet and expensive. If delivery could mark a signal
"sent", every later research query over signals would silently become a query over
*delivered* signals, and the undelivered ones -- often the interesting ones -- would
vanish from the analysis without anyone noticing.

Three checks, all AST, no imports required:

1. `oipulse/alerts/**` never constructs or calls a mutator on a `Signal`.
2. `oipulse/alerts/**` holds `signal_id`, never a `Signal` instance, in its dataclass
   field annotations -- you cannot mutate what you do not hold.
3. No module under `oipulse/signals/**` imports `oipulse.alerts`, so signal evaluation
   cannot become dependent on whether an alert was raised.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

__all__ = ["check_alerts", "check_signals"]

#: Names that would indicate alerts reaching into signal state.
_FORBIDDEN_CALLS = frozenset({"apply_transition", "SignalEvaluator", "evaluate"})
#: Types an alert module must not hold as a field -- holding one invites mutating it.
_FORBIDDEN_FIELD_TYPES = frozenset({"Signal"})


def _annotation_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def check_alerts(root: Path) -> list[str]:
    problems: list[str] = []
    alerts = root / "oipulse" / "alerts"
    if not alerts.exists():
        return problems

    for path in sorted(alerts.rglob("*.py")):
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)

        for node in ast.walk(tree):
            # 1. No calls that would transition or re-evaluate a signal.
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None)
                )
                if name in _FORBIDDEN_CALLS:
                    problems.append(
                        f"{rel}:{node.lineno}: alerts call {name}(), which would let "
                        f"delivery change signal truth"
                    )
            # 2. No dataclass field typed as a Signal.
            if (
                isinstance(node, ast.AnnAssign)
                and _annotation_names(node.annotation) & _FORBIDDEN_FIELD_TYPES
            ):
                target = getattr(node.target, "id", "?")
                problems.append(
                    f"{rel}:{node.lineno}: field {target!r} holds a Signal. Alerts must "
                    f"hold a signal_id: you cannot mutate what you do not hold."
                )
    return problems


def check_signals(root: Path) -> list[str]:
    problems: list[str] = []
    signals = root / "oipulse" / "signals"
    if not signals.exists():
        return problems

    for path in sorted(signals.rglob("*.py")):
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            for module in modules:
                if module.startswith("oipulse.alerts"):
                    problems.append(
                        f"{rel}:{node.lineno}: signals import {module}. Evaluation must "
                        f"not depend on whether an alert was raised."
                    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    problems = [*check_alerts(root), *check_signals(root)]
    if problems:
        print("FAIL  alert/signal separation:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("PASS  alerts cannot mutate signal truth; signals do not depend on alerts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
