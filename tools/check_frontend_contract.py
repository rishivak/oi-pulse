#!/usr/bin/env python3
"""Guard: the terminal cannot get ahead of the backend.

`18-ROADMAP.md` Phase 12 acceptance: "No screen ships ahead of its backend — no
'Coming soon' pages." `13-FRONTEND_IA.md` §2 is blunter: "the legacy app shipped
three stubs over working endpoints, which is worse than not listing them."

A rule like that is only worth having if it is checked, and it cannot be checked by
reading the screens — a stub looks exactly like a working screen until the request
404s. What can be checked is the correspondence between what the terminal *asks for*
and what the backend *serves*, because both are statically readable: the backend's
routes are extracted by `tools/export_api_contract.py`, and the terminal builds every
request through `lib/terminal/endpoints.ts`.

Five checks, all stdlib:

1. **No drift.** The committed contract matches what the generator produces now, so
   a backend route renamed without regenerating fails here rather than at runtime.
2. **Every endpoint builder targets a real route.** Each `endpoint("GET", "/x")` call
   in `endpoints.ts` must appear in the generated `ROUTES`.
3. **Every screen's declared APIs exist.** The `"METHOD /path"` keys in
   `screens.ts` must appear in `ROUTES` too, so the registry cannot promise a
   capability the backend lacks.
4. **Every DTO field exists on the wire.** An interface tagged `@contract model X`
   may only declare fields in the generated key set for `X`. This catches the
   quiet, expensive class of bug: reading `order.status` when the backend sends
   `order.state`.
5. **One request surface.** `fetch`, `XMLHttpRequest` and `axios` appear only in the
   terminal's client module, so check 2 cannot be bypassed by a component that
   builds a URL itself.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path("frontend/lib/api/contract.generated.json")
ENDPOINTS = Path("frontend/lib/terminal/endpoints.ts")
SCREENS = Path("frontend/lib/terminal/screens.ts")
DTO = Path("frontend/lib/api/dto.ts")
CLIENT = Path("frontend/lib/terminal/client.ts")

#: Scanned for the single-request-surface rule. Deliberately the Phase 12 tree only:
#: `frontend/app/(dashboard)` and `frontend/lib/api/client.ts` are the **legacy v1**
#: UI, which talks to the separate `backend/` application over `/oi/*` and `/auth/*`.
#: That app is out of Phase 12's scope and is left untouched (`18-ROADMAP.md`'s
#: cutover is a separate operational step), so holding it to this rule would report
#: violations nobody in this phase is entitled to fix.
TERMINAL_SOURCES = (
    Path("frontend/lib/terminal"),
    Path("frontend/lib/api/dto.ts"),
    Path("frontend/lib/api/contract.generated.ts"),
    Path("frontend/app/terminal"),
    Path("frontend/components/terminal"),
)

#: `endpoint("GET", "/signals/{signal_id}"` -- method and template are always literal.
ENDPOINT_CALL = re.compile(r'endpoint\(\s*"(GET|POST|PUT|PATCH|DELETE)"\s*,\s*"([^"]+)"')
#: `"GET /signals"` as it appears in a screen's `apis` list.
ROUTE_KEY = re.compile(r'"(GET|POST|PUT|PATCH|DELETE) (/[^"]*)"')
#: `@contract model FeatureSpec` / `@contract serializer rule_to_dict` / `@contract none`
CONTRACT_TAG = re.compile(r"@contract\s+(model|serializer|none)\s*([A-Za-z_][A-Za-z0-9_]*)?")
INTERFACE = re.compile(r"^export interface ([A-Za-z0-9_]+)\s*\{", re.MULTILINE)
#: A field at the top level of an interface body. MULTILINE is essential: without
#: it `^` anchors to the start of the whole body and the check matches nothing,
#: which is how this silently verified zero fields until a mutation test found it.
FIELD = re.compile(r"^\s{2}(?:readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)\??\s*:", re.MULTILINE)
NETWORK_CALL = re.compile(r"\b(fetch\s*\(|XMLHttpRequest|from\s+\"axios\"|require\(\"axios\"\))")


def _interface_bodies(text: str) -> dict[str, tuple[int, str]]:
    """Map interface name -> (line number, body text), by brace matching.

    A regex cannot do this: interfaces nest object literals, and a naive `\\{.*?\\}`
    stops at the first inner closing brace and silently checks a fraction of the
    fields.
    """
    bodies: dict[str, tuple[int, str]] = {}
    for match in INTERFACE.finditer(text):
        name = match.group(1)
        start = match.end()
        depth = 1
        index = start
        while index < len(text) and depth > 0:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        bodies[name] = (text.count("\n", 0, match.start()) + 1, text[start : index - 1])
    return bodies


def _declared_fields(body: str) -> list[str]:
    """Top-level field names only.

    Nested object-literal members are indented further than two spaces, so the
    indentation anchor in `FIELD` excludes them — they belong to the nested type,
    not to the wire shape being checked.
    """
    return [m.group(1) for m in FIELD.finditer(body)]


def _tag_before(text: str, line: int) -> tuple[str, str | None] | None:
    """The `@contract` tag in the doc comment immediately above an interface."""
    lines = text.splitlines()
    for offset in range(2, 40):
        index = line - offset
        if index < 0:
            return None
        match = CONTRACT_TAG.search(lines[index])
        if match:
            return match.group(1), match.group(2)
        if lines[index].strip().startswith("export "):
            return None
    return None


def check(root: Path) -> list[str]:
    findings: list[str] = []

    contract_path = root / CONTRACT
    if not contract_path.exists():
        return [f"{CONTRACT}: missing -- run python3 tools/export_api_contract.py"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    # 1. Drift.
    sys.path.insert(0, str(root / "tools"))
    from export_api_contract import build

    if build(root) != contract:
        findings.append(
            f"{CONTRACT}: stale. The backend changed since this was generated; "
            f"run python3 tools/export_api_contract.py and review the diff."
        )

    known = {f"{r['method']} {r['path']}" for r in contract["routes"]}

    # 2. Endpoint builders.
    endpoints_text = (root / ENDPOINTS).read_text(encoding="utf-8")
    builders = ENDPOINT_CALL.findall(endpoints_text)
    if len(builders) < 50:
        findings.append(
            f"{ENDPOINTS}: only {len(builders)} endpoint() calls found; the parser "
            f"expects the terminal's full surface and a near-empty result means it "
            f"stopped matching rather than that the surface shrank."
        )
    for method, template in builders:
        key = f"{method} {template}"
        if key not in known:
            findings.append(
                f"{ENDPOINTS}: builds {key}, which the backend does not serve. "
                f"A screen wired to it would 404, and a 404 reads as 'no data'."
            )

    # 3. Screen registry.
    screens_text = (root / SCREENS).read_text(encoding="utf-8")
    for method, path in ROUTE_KEY.findall(screens_text):
        key = f"{method} {path}"
        if key not in known:
            findings.append(
                f"{SCREENS}: a screen declares {key}, which the backend does not serve."
            )

    # 4. DTO fields.
    dto_text = (root / DTO).read_text(encoding="utf-8")
    entries = {"model": contract["models"], "serializer": contract["serializers"]}
    tagged = 0
    checked_fields = 0
    for name, (line, body) in _interface_bodies(dto_text).items():
        tag = _tag_before(dto_text, line)
        if tag is None:
            findings.append(
                f"{DTO}:{line}: interface {name} has no @contract tag. Every payload "
                f"type must name the generated entry it describes, or say "
                f"`@contract none` with the reason."
            )
            continue
        kind, target = tag
        if kind == "none":
            continue
        tagged += 1
        table = entries[kind]
        if target is None or target not in table:
            findings.append(
                f"{DTO}:{line}: interface {name} cites {kind} {target}, which is not "
                f"in the generated contract."
            )
            continue
        entry = table[target]
        fields = _declared_fields(body)
        checked_fields += len(fields)
        unknown = [f for f in fields if f not in entry["keys"]]
        if unknown and not entry["partial"]:
            findings.append(
                f"{DTO}:{line}: interface {name} declares {unknown}, which "
                f"{kind} {target} does not send. Reading a field the backend does not "
                f"produce yields undefined, and undefined renders as an empty cell."
            )
    if tagged < 20:
        findings.append(
            f"{DTO}: only {tagged} interfaces were checked against the contract; "
            f"the tag parser has probably stopped matching."
        )
    if checked_fields < 100:
        # The field regex once lacked re.MULTILINE and matched nothing, so every
        # interface passed while verifying no fields at all. A count is the cheapest
        # way for the guard to notice it has stopped looking.
        findings.append(
            f"{DTO}: only {checked_fields} fields were compared against the contract; "
            f"the field parser has stopped matching."
        )

    # 5. One request surface.
    for base in TERMINAL_SOURCES:
        target = root / base
        if not target.exists():
            continue
        paths = sorted(target.rglob("*.ts*")) if target.is_dir() else [target]
        for path in paths:
            if path == root / CLIENT:
                continue
            relative = path.relative_to(root).as_posix()
            for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if text.lstrip().startswith(("*", "//", "/*")):
                    continue
                if NETWORK_CALL.search(text):
                    findings.append(
                        f"{relative}:{number}: builds its own request. Every call goes "
                        f"through {CLIENT.as_posix()}, or the contract check above "
                        f"covers only part of the terminal's traffic."
                    )
    return findings


def main() -> int:
    findings = check(ROOT)
    if findings:
        print("FAIL  frontend/backend contract violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    contract = json.loads((ROOT / CONTRACT).read_text(encoding="utf-8"))
    print(
        f"PASS  frontend contract: {len(contract['routes'])} backend routes, every "
        f"endpoint builder, screen declaration and DTO field checked against them"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
