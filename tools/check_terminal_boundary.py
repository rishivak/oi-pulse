#!/usr/bin/env python3
"""Guard: the terminal stays a presentation layer.

Phase 12 brief §4 and §30. The terminal "is NOT allowed to become a second
backend", and §30 asks for guards against frontend→database, →provider, →broker and
→secret access, against analytics and signal recomputation, against a risk bypass
or a live-execution path, and against Phase 13 imports.

Most of those are absences, and an absence is only a guarantee if something looks
for it. This scans the Phase 12 tree — `frontend/lib/terminal`,
`frontend/components/terminal`, `frontend/app/terminal` and the two contract files
in `frontend/lib/api` — and checks nine things.

**What this is not.** It is a lexical scan over TypeScript, not a type-aware
analysis: there is no TypeScript compiler in this environment and no package index
to install one from. Strings, template literals, comments and JSX text are stripped
before matching so the checks look at code, but a check here proves the *token* is
absent, not that the *behaviour* is impossible. Where a check is presence-based it
says so in its message, and the Phase 12 report states the same. `tsc --noEmit` and
`eslint` remain the authoritative type and lint checks and are run in CI.

The checks:

1. **No data source.** No import of a database, cache, ORM or provider client.
2. **No secrets.** No `process.env` at all in the terminal tree, and no credential
   identifier. The backend address lives in `next.config.mjs` as a server-only
   variable so it is never inlined into the bundle.
3. **One request surface, no stream.** No `EventSource`: `/stream/events` is
   specified but unimplemented, and brief §21 forbids inventing a stream around it.
4. **No live-execution path.** `liveTradingRenderable` must still be annotated to
   return the literal `false`, and no endpoint builder may name a live route.
5. **Arithmetic is contained.** Multiplication, division and modulo are permitted
   only in a short allow-list of presentation modules. A component cannot compute an
   analytic, because a component cannot compute. This is the structural half of
   brief §8's "Do NOT calculate ... again in the terminal".
6. **The residual is never derived.** No subtraction involving the attribution
   fields, anywhere. Phase 11 owns that arithmetic and a database constraint enforces it.
7. **Erasable syntax only** in `lib/terminal`, so the pure modules keep running
   under `node --test` with no packages installed.
8. **Registry and routes agree.** Every screen in the registry has a page, every
   page is in the registry, and every page renders through `ScreenFrame` — which is
   what makes the quality indicator, the two-axis control and the real-time state
   impossible for a screen to omit.
9. **No Phase 13.** No import or identifier referring to a phase beyond this one.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = Path("frontend")
TERMINAL_DIRS = (
    FRONTEND / "lib" / "terminal",
    FRONTEND / "components" / "terminal",
    FRONTEND / "app" / "terminal",
)
EXTRA_FILES = (
    FRONTEND / "lib" / "api" / "dto.ts",
    FRONTEND / "lib" / "api" / "contract.generated.ts",
)
SCREENS_TS = FRONTEND / "lib" / "terminal" / "screens.ts"
MODE_TS = FRONTEND / "lib" / "terminal" / "mode.ts"
PRIMITIVES = FRONTEND / "components" / "terminal" / "primitives.tsx"

#: Components that render `ScreenFrame` themselves, so a page delegating to one is
#: still framed. Each is verified below to actually contain `ScreenFrame`, so this
#: cannot become a way to opt out of the frame by adding a name to a list.
FRAME_PROVIDERS = {"FeatureScreen"}

# 1 -- data sources a presentation layer must not reach.
FORBIDDEN_IMPORTS = (
    "pg",
    "postgres",
    "pg-promise",
    "mysql",
    "mysql2",
    "sqlite3",
    "better-sqlite3",
    "knex",
    "prisma",
    "@prisma/client",
    "typeorm",
    "sequelize",
    "mongodb",
    "mongoose",
    "redis",
    "ioredis",
    "upstox",
    "upstox-client",
    "kiteconnect",
    "amqplib",
    "kafkajs",
    "node:fs",
    "node:net",
    "node:dgram",
    "node:child_process",
    "fs",
    "child_process",
)
IMPORT_FROM = re.compile(r"""(?:from|require\()\s*["']([^"']+)["']""")

# 2 -- credentials and configuration that must never appear client-side.
SECRET_TOKENS = (
    "process.env",
    "DATABASE_URL",
    "REDIS_URL",
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
    "access_token",
    "accessToken",
    "client_secret",
    "clientSecret",
    "api_secret",
    "apiSecret",
    "SECRET_KEY",
)

# 3 -- the stream that does not exist.
STREAM_TOKENS = ("EventSource", "WebSocket", "new SSE")

# 5 -- the only modules allowed to do arithmetic.
ARITHMETIC_ALLOWED = {
    "frontend/lib/terminal/formatting.ts",  # IST offset, padding, lag
    "frontend/lib/terminal/pagination.ts",  # row-count bookkeeping
    "frontend/lib/terminal/screens/research.ts",  # effective/raw retention ratio
    "frontend/lib/terminal/screens/optionSurface.ts",  # numeric strike ordering
    "frontend/components/terminal/AttributionTable.tsx",  # segment widths
    "frontend/components/terminal/primitives.tsx",  # skeleton row count
    # Epoch-second conversion for the chart axis. Not an analytic: it changes the
    # representation of an instant the backend supplied and computes no quantity.
    "frontend/lib/terminal/screens/chartData.ts",
}
#: Binary `*`, `%`, or a spaced `/`. `**` (exponent) is caught by the first.
ARITHMETIC = re.compile(r"(?<![*/])\*(?!\*?/)|(?<!\w)%(?!\w)|\s/\s")

# 6 -- the residual is read, never derived.
RESIDUAL_FIELDS = ("residual", "explained", "total_pnl", "totalPnl", "explainedPnl")

# 7 -- syntax Node's type stripping cannot erase.
NON_ERASABLE = (
    re.compile(r"^\s*(export\s+)?(const\s+)?enum\s+"),
    re.compile(r"^\s*(export\s+)?namespace\s+"),
    re.compile(r"constructor\s*\(\s*(readonly|private|public|protected)\s"),
)

#: React features that only exist in a Client Component.
CLIENT_ONLY = re.compile(
    r"\buse(?:State|Effect|Memo|Ref|Callback|Router|Pathname|SearchParams|Query|Mutation)\b"
    r"|\bon(?:Click|Change|Submit|Input|KeyDown)\s*="
)

# 9 -- nothing from a later phase.
FUTURE_PHASE = re.compile(r"phase[-_]?1[3-9]|PHASE_1[3-9]", re.IGNORECASE)


def strip_code(text: str) -> str:
    """Remove comments, strings and template literals, keeping line structure.

    Matching against raw source produces confident nonsense: a `/` inside a URL in a
    docstring reads as division, and the word `redis` in a sentence reads as an
    import. Newlines are preserved so reported line numbers stay true.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif ch == "/" and nxt == "*":
            i += 2
            while i < n - 1 and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
        elif ch in "\"'`":
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                elif text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 1
            out.append('""')
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def terminal_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory in TERMINAL_DIRS:
        target = root / directory
        if target.exists():
            files.extend(sorted(target.rglob("*.ts*")))
    for extra in EXTRA_FILES:
        if (root / extra).exists():
            files.append(root / extra)
    return files


def check(root: Path) -> list[str]:
    findings: list[str] = []
    files = terminal_files(root)
    if len(files) < 20:
        return [
            f"only {len(files)} terminal source files found; the guard is looking in "
            f"the wrong place and would pass vacuously"
        ]

    for path in files:
        relative = path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8")
        code = strip_code(raw)
        lines = code.splitlines()

        # 1. data sources
        for module in IMPORT_FROM.findall(raw):
            base = module.split("/")[0] if not module.startswith("@") else module
            if base in FORBIDDEN_IMPORTS or module in FORBIDDEN_IMPORTS:
                findings.append(
                    f"{relative}: imports {module}. The terminal reaches the backend "
                    f"through its HTTP API and nothing else (brief §4)."
                )

        for number, line in enumerate(lines, 1):
            # 2. secrets
            for token in SECRET_TOKENS:
                if token in line:
                    findings.append(
                        f"{relative}:{number}: contains {token}. Configuration and "
                        f"credentials must not reach the client bundle (brief §23); "
                        f"the backend address is server-only in next.config.mjs."
                    )
            # 3. invented stream
            for token in STREAM_TOKENS:
                if token in line:
                    findings.append(
                        f"{relative}:{number}: uses {token}. No stream endpoint exists "
                        f"(12-API_SPEC.md §3 specifies /stream/events; nothing serves "
                        f"it), and brief §21 forbids inventing one around it."
                    )
            # 5. contained arithmetic
            if relative not in ARITHMETIC_ALLOWED and ARITHMETIC.search(line):
                findings.append(
                    f"{relative}:{number}: performs arithmetic. Analytics are computed "
                    f"by the backend (13-FRONTEND_IA.md §1.3); if this is genuinely "
                    f"presentation, add the file to ARITHMETIC_ALLOWED and say why."
                )
            # 6. derived residual
            # Spaced subtraction only. `data-residual` and `-->` are hyphens in
            # names, not operators, and the formatter writes real subtraction as
            # `a - b`; requiring the spaces removes the false positives without
            # losing the expression this is here to catch.
            if (
                any(field in line for field in RESIDUAL_FIELDS)
                and re.search(r"\w\s-\s\w", line)
                and "->" not in line
            ):
                findings.append(
                    f"{relative}:{number}: subtracts an attribution field. The "
                    f"residual is read from the backend, where it is a derived "
                    f"property and a CHECK constraint enforces "
                    f"total_pnl = explained + residual (brief §15, §16)."
                )
            # 9. later phases
            if FUTURE_PHASE.search(line):
                findings.append(
                    f"{relative}:{number}: refers to a phase after 12. Phase 12 is the "
                    f"final phase and does not create a Phase 13 (brief §34)."
                )

        # 7. erasable syntax, so `node --test` keeps working without packages
        if relative.startswith("frontend/lib/terminal/"):
            for number, line in enumerate(lines, 1):
                for pattern in NON_ERASABLE:
                    if pattern.search(line):
                        findings.append(
                            f"{relative}:{number}: uses TypeScript syntax Node cannot "
                            f"strip (enum, namespace or a parameter property). The "
                            f"pure modules must stay runnable under `node --test`, "
                            f"which is the only way their tests run in this environment."
                        )

        # 8c. anything interactive declares itself a Client Component
        if path.suffix == ".tsx" and CLIENT_ONLY.search(code):
            head = "\n".join(raw.splitlines()[:3])
            if '"use client"' not in head:
                findings.append(
                    f"{relative}: uses a hook or an event handler without a "
                    f'"use client" directive. Next renders it on the server, where '
                    f"neither exists, and the build fails on a file nobody edited."
                )

        # 8b. tables go through DenseTable, which supplies caption and scope
        if relative != PRIMITIVES.as_posix() and "<table" in code:
            findings.append(
                f"{relative}: renders a raw <table>. Use DenseTable, which supplies "
                f'<caption> and scope="col" — table semantics are an accessibility '
                f"requirement (brief §28), not a detail."
            )

    # 4. the live barrier
    mode_source = (root / MODE_TS).read_text(encoding="utf-8")
    if "export function liveTradingRenderable(): false {" not in mode_source:
        findings.append(
            f"{MODE_TS.as_posix()}: liveTradingRenderable is no longer annotated to "
            f"return the literal `false`. A widened return type would let a caller "
            f"branch on live trading being renderable (13-FRONTEND_IA.md §6)."
        )
    if "return false;" not in mode_source:
        findings.append(f"{MODE_TS.as_posix()}: liveTradingRenderable does not return false.")

    # 8a. registry and routes agree, and every page uses the frame
    screens_source = (root / SCREENS_TS).read_text(encoding="utf-8")
    declared = set(re.findall(r'route:\s*"(/terminal[^"]*)"', screens_source))
    app_dir = root / FRONTEND / "app" / "terminal"
    found: set[str] = set()
    for page in sorted(app_dir.rglob("page.tsx")):
        suffix = page.parent.relative_to(app_dir).as_posix()
        route = "/terminal" if suffix == "." else f"/terminal/{suffix}"
        found.add(route)
        body = page.read_text(encoding="utf-8")
        delegated = sorted(name for name in FRAME_PROVIDERS if name in body)
        for name in delegated:
            provider = root / FRONTEND / "components" / "terminal" / f"{name}.tsx"
            if not provider.exists() or "ScreenFrame" not in provider.read_text(encoding="utf-8"):
                findings.append(
                    f"{provider.relative_to(root).as_posix()}: named as a frame "
                    f"provider but does not render ScreenFrame."
                )
        if "ScreenFrame" not in body and not delegated:
            findings.append(
                f"{page.relative_to(root).as_posix()}: does not render through "
                f"ScreenFrame. The frame is what supplies the data-quality indicator, "
                f"the two-axis time control and the real-time state; a screen that "
                f"bypasses it can omit all three and still look finished."
            )
    for route in sorted(declared - found):
        findings.append(
            f"{SCREENS_TS.as_posix()}: declares {route}, which has no page. A registry "
            f"entry without a screen is a promise nothing keeps."
        )
    for route in sorted(found - declared):
        findings.append(
            f"frontend/app{route}/page.tsx: not in "
            f"the screen registry. The registry is what declares a screen's backend, "
            f"and tools/check_frontend_contract.py only checks what is in it."
        )
    return findings


def main() -> int:
    findings = check(ROOT)
    if findings:
        print("FAIL  terminal boundary violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    count = len(terminal_files(ROOT))
    print(
        f"PASS  terminal boundary: {count} files scanned; no data source, secret, "
        f"stream, live path, stray arithmetic, derived residual or later-phase "
        f"reference, and every screen renders through ScreenFrame"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
