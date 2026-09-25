#!/usr/bin/env python3
"""Guard: every import in the terminal resolves to something that exists.

This exists because of a gap, and the gap should be named. Phase 12 ships a
TypeScript application into an environment with no reachable package registry, so
`tsc --noEmit` and `next build` cannot run here — the two checks that would
ordinarily catch a mistyped path, a renamed export or a symbol that was never
exported at all. Those are the most common reasons a Next build fails, and they are
resolvable from the source alone.

So this resolves them. For every `.ts`/`.tsx` under the Phase 12 tree it checks:

1. **Local imports resolve to a file.** `@/lib/terminal/time` must exist as
   `frontend/lib/terminal/time.ts`, trying the extensions and `index` files a
   bundler would try.
2. **Every named import is actually exported** by the module it names, following
   `export * from` re-exports one level.
3. **A default import has a default export**, and every `page.tsx` has one — Next
   requires it and the error it produces is unhelpful.
4. **External packages are declared** in `package.json`. Their files cannot be
   resolved without `node_modules`, so this checks the dependency exists rather than
   pretending to verify the import.
5. **Braces, parens and brackets balance**, which catches a truncated file.

**What this is not.** It is not a type check. It resolves names, not types: a
`string` used where a `number` is required passes here and fails in `tsc`. Saying
so matters — this guard makes a build failure less likely, it does not make one
impossible, and the Phase 12 report states the same. `tsc --noEmit` and `next build`
run in CI and remain authoritative.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Relative to a root, so the guard can be pointed at a disposable copy of the tree.
SCAN = (
    Path("frontend/lib/terminal"),
    Path("frontend/components/terminal"),
    Path("frontend/app/terminal"),
    Path("frontend/tests"),
)
EXTRA = (Path("frontend/lib/api/dto.ts"), Path("frontend/lib/api/contract.generated.ts"))

#: `import ... from "x"` with the clause captured. Side-effect imports have no clause.
IMPORT = re.compile(
    r'^\s*import\s+(?:(?P<clause>[^"\']+?)\s+from\s+)?["\'](?P<module>[^"\']+)["\']',
    re.MULTILINE,
)
EXPORT_FROM = re.compile(
    r'^\s*export\s+(?:\*|\{[^}]*\})\s+from\s+["\']([^"\']+)["\']', re.MULTILINE
)

#: Declarations that create an export binding.
EXPORT_DECL = re.compile(
    r"^\s*export\s+(?:declare\s+)?"
    r"(?:async\s+)?(?:const|let|var|function|class|interface|type|enum)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
#: `export { a, b as c }` — the exported name is what follows `as`, or the bare name.
EXPORT_LIST = re.compile(r"^\s*export\s+(?:type\s+)?\{([^}]*)\}\s*(?:;|$)", re.MULTILINE)
DEFAULT_EXPORT = re.compile(r"^\s*export\s+default\b", re.MULTILINE)

#: Packages Next provides without a `package.json` entry.
BUILTIN_PACKAGES = {"react", "react-dom", "next"}
#: Node builtins the test harness may use.
NODE_BUILTINS = {
    "node:test",
    "node:assert/strict",
    "node:fs",
    "node:url",
    "node:path",
    "node:module",
}

CANDIDATES = (".ts", ".tsx", "/index.ts", "/index.tsx", "")


def strip_comments(text: str) -> str:
    """Drop comments so an example import in a docstring is not resolved."""
    without_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


#: Tokens after which a `/` begins a regular expression rather than a division.
#: `<` is deliberately absent: in TSX it precedes a JSX closing tag (`</div>`), and
#: including it made the scanner read the rest of the element as a regex. The first
#: run with `<` present reported seven files as truncated, none of which was; a
#: second run, before the self-closing lookahead below, reported two more.
_REGEX_PRECEDERS = "(,=:[!&|?{};+-*%>~^"


def strip_literals(text: str) -> str:
    r"""Drop string, template and regex contents, for the bracket-balance check.

    Balance cannot be counted over raw source. `endpoints.ts` contains
    `path.match(/\{[^}]+\}/)` and a `${key}` placeholder, so braces inside literals
    read as unclosed blocks -- the check's first run reported exactly that, on a
    file that is not truncated.

    Telling a regex literal from a division needs the preceding token: after a
    value `/` divides, after an operator or an opening bracket it opens a regex.
    That is the usual heuristic and it is enough here, where this only feeds a
    smoke check for a truncated file.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    previous = ""
    while index < length:
        char = text[index]
        if char in "\"'`":
            quote = char
            index += 1
            while index < length and text[index] != quote:
                if text[index] == "\\":
                    index += 1
                index += 1
            index += 1
            out.append('""')
            previous = '"'
            continue
        # `... />` closes a JSX element. The character before it is whatever the
        # last attribute ended with -- often `)` or `}`, both of which precede a
        # regex elsewhere -- so the lookahead, not the preceding token, is what
        # tells the two apart.
        self_closing = text[index + 1 : index + 2] == ">"
        if char == "/" and previous in _REGEX_PRECEDERS and not self_closing:
            index += 1
            while index < length and text[index] not in "/\n":
                if text[index] == "\\":
                    index += 1
                index += 1
            index += 1
            out.append("RE")
            previous = "E"
            continue
        out.append(char)
        if not char.isspace():
            previous = char
        index += 1
    return "".join(out)


def resolve(module: str, importer: Path, frontend: Path) -> Path | None:
    """The file a bundler would load, or None for a package."""
    if module.startswith("@/"):
        base = frontend / module[2:]
    elif module.startswith("."):
        base = (importer.parent / module).resolve()
    else:
        return None
    for suffix in CANDIDATES:
        candidate = Path(f"{base}{suffix}")
        if candidate.is_file():
            return candidate
    return Path(f"{base}.__missing__")


def exported_names(path: Path, frontend: Path, seen: set[Path] | None = None) -> set[str]:
    """Names this module exports, following `export * from` one level deep."""
    seen = seen if seen is not None else set()
    if path in seen or not path.is_file():
        return set()
    seen.add(path)
    text = strip_comments(path.read_text(encoding="utf-8"))
    names = set(EXPORT_DECL.findall(text))
    for clause in EXPORT_LIST.findall(text):
        for part in clause.split(","):
            part = part.strip()
            if not part:
                continue
            names.add(part.split(" as ")[-1].strip())
    for module in EXPORT_FROM.findall(text):
        target = resolve(module, path, frontend)
        if target is not None and target.is_file():
            names |= exported_names(target, frontend, seen)
    return names


def parse_clause(clause: str) -> tuple[list[str], bool]:
    """Named imports, and whether a default was imported."""
    clause = clause.strip()
    if clause.startswith("type "):
        clause = clause[5:].strip()
    named: list[str] = []
    has_default = False
    brace = clause.find("{")
    head = clause if brace == -1 else clause[:brace]
    head = head.rstrip().rstrip(",").strip()
    if head and not head.startswith("*"):
        has_default = True
    if brace != -1:
        close = clause.find("}", brace)
        for part in clause[brace + 1 : close].split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith("type "):
                part = part[5:].strip()
            named.append(part.split(" as ")[0].strip())
    return named, has_default


def targets(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for directory in SCAN:
        absolute = root / directory
        if absolute.exists():
            files.extend(sorted(p for p in absolute.rglob("*.ts*") if p.is_file()))
    files.extend(root / p for p in EXTRA if (root / p).is_file())
    return files


def check(root: Path = ROOT) -> list[str]:
    findings: list[str] = []
    frontend = root / "frontend"
    package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    declared = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))

    files = targets(root)
    if len(files) < 30:
        return [f"only {len(files)} files found; the scanner is looking in the wrong place"]

    resolved_imports = 0
    checked_names = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8")
        text = strip_comments(raw)

        balance_source = strip_literals(text)
        for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
            opened = balance_source.count(opener)
            closed = balance_source.count(closer)
            if opened != closed:
                findings.append(
                    f"{relative}: unbalanced {opener}{closer} "
                    f"({opened} vs {closed}); the file is probably truncated."
                )

        if path.name == "page.tsx" and not DEFAULT_EXPORT.search(text):
            findings.append(
                f"{relative}: no default export. Next requires one from a page and "
                f"the error it raises does not say which file."
            )

        for match in IMPORT.finditer(text):
            module = match.group("module")
            clause = match.group("clause")
            line = text.count("\n", 0, match.start()) + 1

            if module in NODE_BUILTINS or module.startswith("node:"):
                continue

            target = resolve(module, path, frontend)
            if target is None:
                base = module if module.startswith("@") else module.split("/")[0]
                if base.startswith("@"):
                    base = "/".join(module.split("/")[:2])
                if base not in declared and base not in BUILTIN_PACKAGES:
                    findings.append(
                        f"{relative}:{line}: imports {module}, whose package {base} is "
                        f"not in frontend/package.json. `npm ci` would not install it."
                    )
                continue

            if not target.is_file():
                findings.append(
                    f"{relative}:{line}: imports {module}, which resolves to no file. "
                    f"Tried {', '.join(f'{module}{s}' for s in CANDIDATES if s)}."
                )
                continue

            resolved_imports += 1
            if clause is None:
                continue
            named, has_default = parse_clause(clause)
            available = exported_names(target, frontend)
            checked_names += len(named)
            for name in named:
                if name not in available:
                    findings.append(
                        f"{relative}:{line}: imports {{{name}}} from {module}, which "
                        f"does not export it."
                    )
            if has_default and not DEFAULT_EXPORT.search(
                strip_comments(target.read_text(encoding="utf-8"))
            ):
                findings.append(
                    f"{relative}:{line}: imports a default from {module}, which has "
                    f"no default export."
                )

    # Two floors, because this guard's whole value is that it looked at something.
    # A parser that silently stops matching passes every file it fails to read.
    if resolved_imports < 150:
        findings.append(
            f"only {resolved_imports} local imports were resolved; the import parser "
            f"has stopped matching and this check would pass vacuously."
        )
    if checked_names < 300:
        findings.append(
            f"only {checked_names} named imports were compared against export lists; "
            f"the clause parser has stopped matching."
        )
    return findings


def main() -> int:
    findings = check(ROOT)
    if findings:
        print("FAIL  terminal import violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(
        f"PASS  terminal imports: {len(targets(ROOT))} files; every local import resolves, "
        f"every named import is exported, every page has a default export"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
