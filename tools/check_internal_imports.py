"""Guard: every intra-package import names a symbol that exists.

Written because of a real defect, caught late. `oipulse/api/security.py` imported
`utcnow` from `oipulse.core.clock`, which exports `utc`, `ensure_utc` and a `Clock`
protocol and has never had a `utcnow`. Nothing here noticed: `compileall` compiles
without resolving imports, the guards are lexical, and the only test that would have
executed the module needs `fastapi`, which cannot be installed in this environment.
It would have failed in CI, at import, on a module nobody could run locally.

So this resolves them statically. For every `from oipulse.x.y import a, b` in the
package it opens `oipulse/x/y.py` and checks that each name is defined there — as a
function, class, assignment, import, or `__all__` entry.

**What this is not.** It is not a type check and not `mypy`. It resolves names, not
types or signatures, and it deliberately abstains where it cannot be certain: a
module that re-exports through a `*` import, or a package `__init__` that pulls from
submodules, is followed one level and then skipped rather than guessed at. A guard
that produced false accusations here would be turned off within a week.

Exit 0 clean, 1 on any unresolved name.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "oipulse"


def _module_path(root: Path, dotted: str) -> Path | None:
    """`oipulse.core.clock` -> `oipulse/core/clock.py`, or the package `__init__`."""
    parts = dotted.split(".")
    direct = root.joinpath(*parts).with_suffix(".py")
    if direct.is_file():
        return direct
    package = root.joinpath(*parts, "__init__.py")
    return package if package.is_file() else None


def _defined_names(path: Path, root: Path, depth: int = 0) -> set[str] | None:
    """Names a module provides, or `None` when it cannot be determined.

    `None` is the abstention: a `from x import *` means the set is open, and
    reporting a name as missing from an open set would be a false accusation.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    if depth > 0 or node.module is None:
                        return None
                    target = _module_path(root, node.module)
                    if target is None:
                        return None
                    inner = _defined_names(target, root, depth + 1)
                    if inner is None:
                        return None
                    names |= inner
                    continue
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.If | ast.Try):
            # Conditional imports (`try: import x except ImportError:`) bind names
            # this walk does not see. Recurse one level rather than abstain.
            for inner_node in ast.walk(node):
                if isinstance(inner_node, ast.ImportFrom | ast.Import):
                    for alias in inner_node.names:
                        if alias.name != "*":
                            names.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(inner_node, ast.Assign):
                    for target in inner_node.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
    return names


def check(root: Path = ROOT) -> list[str]:
    findings: list[str] = []
    package_root = root / PACKAGE
    if not package_root.is_dir():
        return [f"{PACKAGE}/ not found under {root}"]

    checked = 0
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            module = node.module
            if module is None or not module.startswith(f"{PACKAGE}."):
                continue
            target = _module_path(root, module)
            if target is None:
                findings.append(
                    f"{relative}:{node.lineno}: imports from {module}, which is not a "
                    f"module or package in this repository."
                )
                continue
            available = _defined_names(target, root)
            if available is None:
                continue  # open set; abstain rather than accuse
            if target.name == "__init__.py":
                # `from oipulse.analytics import domains` imports a *submodule*, not
                # a name the `__init__` defines. Python resolves it against the
                # directory, so the directory is part of the available set.
                available = available | {
                    entry.stem if entry.suffix == ".py" else entry.name
                    for entry in target.parent.iterdir()
                    if (entry.suffix == ".py" and entry.name != "__init__.py")
                    or (entry.is_dir() and (entry / "__init__.py").is_file())
                }
            for alias in node.names:
                if alias.name == "*":
                    continue
                checked += 1
                if alias.name not in available:
                    findings.append(
                        f"{relative}:{node.lineno}: imports {alias.name!r} from "
                        f"{module}, which does not define it. This fails at import "
                        f"time, and compileall does not catch it."
                    )
    if checked < 200:
        findings.append(
            f"only {checked} imported names were resolved; the scanner has stopped "
            f"matching and this check would pass vacuously."
        )
    return findings


def main() -> int:
    findings = check(ROOT)
    if findings:
        print("FAIL  unresolved internal imports:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS  internal imports: every `from oipulse.…` name resolves to a definition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
