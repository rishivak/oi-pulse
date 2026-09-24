#!/usr/bin/env python3
"""Guard: the statically-checkable subset of `mypy --strict`, using only the stdlib.

**This is not mypy and does not replace it.** `mypy==1.13.0` is pinned in
`pyproject.toml` and `mypy --strict oipulse` remains the authoritative check; it is run
in CI and in any environment with package installation. This tool exists because the
development sandbox has no package index, so without it the entire strict-typing gate
would be unenforced locally -- which is how twelve strict errors reached external
verification in the first place.

What it checks (a subset, deliberately):

* ``disallow_untyped_defs`` -- every parameter and return is annotated.
* ``disallow_any_generics`` -- no bare ``list`` / ``dict`` / ``Callable`` / ... in an
  annotation position.
* ``no_implicit_optional`` -- ``x: T = None`` must be written ``T | None``.
* ``disallow_any_explicit``-adjacent -- every ``# type: ignore`` must carry an error
  code, and each one is reported so suppressions stay visible rather than accumulating.
* override compatibility -- an override must match its base in parameter names, arity
  and awaitability. This is the check that catches a synchronous caller wired to an
  async implementation, which no amount of presence checking would find.

What it does NOT check: type *inference*, assignment compatibility, narrowing,
unreachable code, or anything requiring a type lattice. The override check is
**nominal** -- it compares a class against base classes named in the same package, so
it does not verify conformance to a structural `Protocol`. Protocol conformance is
covered by unit tests instead (see
`tests/phase2/test_marketdata.py::test_the_durable_store_satisfies_the_sink_contract`).
A clean run here means the declarations are well-formed, not that the program
type-checks.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

#: Generic aliases that mypy --strict rejects when left unparameterized.
_NEEDS_PARAMS = frozenset(
    {
        "list",
        "dict",
        "set",
        "frozenset",
        "tuple",
        "type",
        "Callable",
        "Iterable",
        "Iterator",
        "AsyncIterator",
        "AsyncIterable",
        "Sequence",
        "Mapping",
        "MutableMapping",
        "Awaitable",
        "Coroutine",
        "Generator",
        "AsyncGenerator",
        "Collection",
        "Container",
        "Column",
    }
)

_IGNORE_RE = re.compile(r"#\s*type:\s*ignore(\[[^\]]*\])?")

__all__ = ["check_file"]


def _parameterized(node: ast.expr) -> set[int]:
    """Node ids of Names that appear as the base of a subscript, i.e. `list[int]`."""
    seen: set[int] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Subscript):
            base = sub.value
            if isinstance(base, ast.Name | ast.Attribute):
                seen.add(id(base))
    return seen


def _check_annotation(node: ast.expr | None, where: str, path: str) -> list[str]:
    if node is None:
        return []
    ok = _parameterized(node)
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in _NEEDS_PARAMS and id(sub) not in ok:
            out.append(
                f"{path}:{sub.lineno}: {where}: bare generic `{sub.id}` "
                f"(mypy --strict: Missing type parameters for generic type)"
            )
        if isinstance(sub, ast.Attribute) and sub.attr in _NEEDS_PARAMS and id(sub) not in ok:
            out.append(
                f"{path}:{sub.lineno}: {where}: bare generic `{sub.attr}` "
                f"(mypy --strict: Missing type parameters for generic type)"
            )
    return out


def _functions(tree: ast.Module) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    out: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((child, f"{prefix}{child.name}"))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _is_overload_or_abstract(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in fn.decorator_list:
        name = dec.attr if isinstance(dec, ast.Attribute) else getattr(dec, "id", None)
        if name in {"overload", "abstractmethod", "property", "setter"}:
            return True
    return False


def check_file(path: Path, root: Path) -> list[str]:
    rel = str(path.relative_to(root))
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:  # pragma: no cover - a syntax error fails earlier anyway
        return [f"{rel}: syntax error: {exc}"]

    aliases = _module_aliases(tree)
    problems: list[str] = []

    # ---- `# type: ignore` must carry an error code and is always reported.
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _IGNORE_RE.search(line)
        if match and match.group(1) is None:
            problems.append(
                f"{rel}:{lineno}: bare `# type: ignore` -- must name the error code "
                f"it suppresses, so a suppression cannot silently widen"
            )

    for fn, qualname in _functions(tree):
        args = fn.args
        positional = [*args.posonlyargs, *args.args]
        is_method = "." in qualname and positional and positional[0].arg in {"self", "cls"}
        checkable = positional[1:] if is_method else positional
        for arg in [*checkable, *args.kwonlyargs]:
            if arg.annotation is None:
                problems.append(
                    f"{rel}:{arg.lineno}: {qualname}(): parameter `{arg.arg}` is unannotated "
                    f"(mypy --strict: Function is missing a type annotation)"
                )
        for extra in (args.vararg, args.kwarg):
            if extra is not None and extra.annotation is None:
                problems.append(
                    f"{rel}:{extra.lineno}: {qualname}(): `{extra.arg}` is unannotated "
                    f"(mypy --strict: Function is missing a type annotation)"
                )
        if fn.returns is None and not _is_overload_or_abstract(fn):
            problems.append(
                f"{rel}:{fn.lineno}: {qualname}(): missing return annotation "
                f"(mypy --strict: Function is missing a return type annotation)"
            )

        for arg in [*positional, *args.kwonlyargs]:
            problems.extend(_check_annotation(arg.annotation, f"{qualname}({arg.arg})", rel))
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                problems.extend(
                    _check_annotation(extra.annotation, f"{qualname}({extra.arg})", rel)
                )
        problems.extend(_check_annotation(fn.returns, f"{qualname}() -> ", rel))

        # ---- implicit Optional: `x: T = None`
        defaults = list(args.defaults)
        tail = positional[len(positional) - len(defaults) :] if defaults else []
        for arg, default in zip(tail, defaults, strict=False):
            if (
                isinstance(default, ast.Constant)
                and default.value is None
                and arg.annotation is not None
                and not _allows_none(arg.annotation, aliases)
            ):
                problems.append(
                    f"{rel}:{arg.lineno}: {qualname}(): `{arg.arg}` defaults to None but its "
                    f"annotation does not allow None (mypy --strict: no_implicit_optional)"
                )

    # ---- module-level and class-level annotated assignments
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            problems.extend(_check_annotation(node.annotation, "annotated assignment", rel))

    return problems


def _module_aliases(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level type aliases, e.g. `Labels = dict[str, str] | None`.

    Needed so an alias that admits None is not reported as an implicit Optional. Only
    module level and only simple `Name = <expr>` / `Name: TypeAlias = <expr>` forms;
    anything cleverer is out of scope for a static approximation.
    """
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                out[node.target.id] = node.value
    return out


def _allows_none(annotation: ast.expr, aliases: dict[str, ast.expr] | None = None) -> bool:
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return True
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _allows_none(annotation.left, aliases) or _allows_none(annotation.right, aliases)
    if isinstance(annotation, ast.Name):
        if annotation.id in {"Any", "object"}:
            return True
        target = (aliases or {}).get(annotation.id)
        if target is not None:
            return _allows_none(target, None)  # one hop; aliases are not chained here
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return _allows_none(ast.parse(annotation.value, mode="eval").body, aliases)
        except SyntaxError:
            return False
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        return name == "Optional"
    return False


def check_overrides(root: Path, files: list[Path]) -> list[str]:
    """An override must match its base in parameter names, arity and awaitability.

    Awaitability is the one that matters most here: a synchronous caller wired to an
    async implementation type-checks nowhere and fails only at runtime, having written
    nothing. Bases are resolved by simple name across the scanned package, which is
    approximate -- it will not resolve a base imported from a third-party library.
    """
    methods: dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    bases: dict[str, list[str]] = {}
    where: dict[str, str] = {}

    for path in files:
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            where[node.name] = rel
            bases[node.name] = [
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                for b in node.bases
                if isinstance(b, (ast.Name, ast.Attribute))
            ] + [
                b.value.id
                for b in node.bases
                if isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name)
            ]
            methods[node.name] = {
                m.name: m
                for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

    problems: list[str] = []
    for cls, own in methods.items():
        for base in bases.get(cls, []):
            parent = methods.get(base)
            if parent is None:
                continue
            for name, fn in own.items():
                pfn = parent.get(name)
                if pfn is None or name.startswith("__"):
                    continue
                child_async = isinstance(fn, ast.AsyncFunctionDef)
                parent_async = isinstance(pfn, ast.AsyncFunctionDef)
                if child_async != parent_async:
                    problems.append(
                        f"{where[cls]}:{fn.lineno}: {cls}.{name}() is "
                        f"{'async' if child_async else 'sync'} but {base}.{name}() is "
                        f"{'async' if parent_async else 'sync'} -- callers cannot be "
                        f"correct for both (mypy --strict: override)"
                    )
                cnames = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
                pnames = [a.arg for a in [*pfn.args.posonlyargs, *pfn.args.args]]
                if cnames != pnames:
                    problems.append(
                        f"{where[cls]}:{fn.lineno}: {cls}.{name}{tuple(cnames)} does not match "
                        f"{base}.{name}{tuple(pnames)} (mypy --strict: override)"
                    )
                ckw = fn.args.kwarg
                pkw = pfn.args.kwarg
                if (ckw is None) != (pkw is None):
                    problems.append(
                        f"{where[cls]}:{fn.lineno}: {cls}.{name}() and {base}.{name}() "
                        f"disagree on **kwargs (mypy --strict: override)"
                    )
                elif ckw is not None and pkw is not None:
                    cann = ast.dump(ckw.annotation) if ckw.annotation else ""
                    pann = ast.dump(pkw.annotation) if pkw.annotation else ""
                    if cann != pann:
                        problems.append(
                            f"{where[cls]}:{ckw.lineno}: {cls}.{name}(**{ckw.arg}) annotation "
                            f"differs from {base}.{name}(**{pkw.arg}); a narrower override "
                            f"parameter is unsound (mypy --strict: override)"
                        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--package", default="oipulse")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    pkg = root / args.package
    if not pkg.exists():
        print(f"check_typing_strict: no such package {pkg}", file=sys.stderr)
        return 2

    files = [
        p
        for p in sorted(pkg.rglob("*.py"))
        # Excluded in [tool.mypy] too; migrations are op.* shapes checked by
        # check_migration_chain.py / check_schema_parity.py / check_migration_order.py.
        if "migrations" not in p.parts and "__pycache__" not in p.parts
    ]

    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path, root))
    problems.extend(check_overrides(root, files))

    if problems:
        print(f"FAIL  strict-typing subset: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\n  NOTE: this is a stdlib approximation of a subset of `mypy --strict`, "
            "not mypy.\n  `mypy --strict oipulse` remains authoritative and runs in CI."
        )
        return 1

    print(
        f"PASS  strict-typing subset across {len(files)} file(s) "
        f"(annotations, bare generics, implicit Optional, ignore codes, overrides).\n"
        f"      Subset only -- no inference or assignment checking. "
        f"`mypy --strict oipulse` remains authoritative."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
