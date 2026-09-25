#!/usr/bin/env python3
"""Generate the frontend's API contract from the backend source.

Phase 12 brief §24:

> Generate or inspect typed client contracts from the existing API definitions where
> the project supports this. Do not duplicate DTO definitions manually if
> generated/shared contracts are available. Every frontend API call should correspond
> to an existing backend contract.

The obvious way to do that is to export FastAPI's OpenAPI document. It is not
available here: `fastapi` is not installed in this environment and the API cannot be
imported, so an OpenAPI export would either fail or be written by hand and called
generated. Instead this reads the same information out of the source with `ast`,
which needs nothing installed and cannot drift from the code it parses.

Three things are extracted.

**Routes.** Every `@router.<verb>("...")` in `oipulse/api/*.py`, combined with its
`APIRouter(prefix=...)`. This is the set of paths that exist. `tools/check_frontend_
contract.py` refuses any terminal request whose path is not in it, which is the
mechanical form of `18-ROADMAP.md` Phase 12's "no screen ships ahead of its backend".

**Models.** Every `as_dict` method in `oipulse/` whose return is a dict literal, as
`class name -> key set`. These are the `data` payloads. A model whose dict contains a
`**spread` this parser cannot resolve is recorded with `"partial": true` — the keys we
found are real, but the set is not known to be complete, and the guard only ever uses
it as a lower bound so a partial model can never produce a false accusation.

**Envelope meta keys.** Per route operation, the literal keys of the `meta` mapping in
the returned dict, when the return is a dict literal. Same partial-honesty rule.

The output is written to `frontend/lib/api/contract.generated.json` (consumed by the
Python guard) and `frontend/lib/api/contract.generated.ts` (consumed by the terminal
and type-checked by `tsc`). Both are committed, so a reviewer sees the contract change
in the same diff as the backend change that caused it, and `--check` fails when they
have drifted apart.

    python3 tools/export_api_contract.py            # write
    python3 tools/export_api_contract.py --check    # verify no drift, write nothing
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = Path("oipulse/api")
PACKAGE = Path("oipulse")
JSON_OUT = Path("frontend/lib/api/contract.generated.json")
TS_OUT = Path("frontend/lib/api/contract.generated.ts")

HTTP_VERBS = ("get", "post", "put", "patch", "delete")

#: Bumped when the shape of the generated artifact changes, so a stale checkout fails
#: loudly on the shape rather than silently on a missing field.
CONTRACT_VERSION = 1


def _literal(node: ast.expr | None) -> str | None:
    """The string value of a constant, or None for anything computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_keys(node: ast.Dict) -> tuple[list[str], bool]:
    """Literal string keys of a dict display, and whether anything was unresolvable.

    A `**other` entry appears in `node.keys` as `None`. We cannot know what it
    contributes without evaluating it, so the key list is returned as incomplete
    rather than as a closed set.
    """
    keys: list[str] = []
    partial = False
    for key in node.keys:
        if key is None:
            partial = True
            continue
        text = _literal(key)
        if text is None:
            partial = True
            continue
        keys.append(text)
    return keys, partial


def _returned_dict(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Dict | None:
    """The single dict literal this function returns, if that is unambiguous.

    Functions with several `return` statements returning different dicts describe no
    one shape, so nothing is recorded for them; a guard that guessed would be worse
    than a guard that abstains.
    """
    found: list[ast.Dict] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            found.append(node.value)
    return found[0] if len(found) == 1 else None


def _returned_dict_or_binding(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.Dict | None, bool]:
    """As `_returned_dict`, but also following `body = {...}; ...; return body`.

    Several serializers build the mapping, add a key conditionally and then return
    the name. The literal keys are still real, but the set is open, so the second
    element of the tuple reports that the result must be treated as a lower bound.
    """
    direct = _returned_dict(fn)
    if direct is not None:
        return direct, False

    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0].value, ast.Name):
        return None, False
    name = returns[0].value.id
    for node in ast.walk(fn):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id == name and isinstance(value, ast.Dict):
            # Open by construction: the name was mutated between here and the return.
            return value, True
    return None, False


# --------------------------------------------------------------------------- routes


def _router_prefix(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "APIRouter":
                continue
            for kw in node.keywords:
                if kw.arg == "prefix":
                    return _literal(kw.value) or ""
    return ""


def collect_routes(root: Path) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for path in sorted((root / API_DIR).glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        prefix = _router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr not in HTTP_VERBS or not dec.args:
                    continue
                suffix = _literal(dec.args[0])
                if suffix is None:
                    continue
                meta_keys: list[str] = []
                meta_partial = True
                returned = _returned_dict(node)
                if returned is not None:
                    for key, value in zip(returned.keys, returned.values, strict=True):
                        if _literal(key) == "meta" and isinstance(value, ast.Dict):
                            meta_keys, meta_partial = _dict_keys(value)
                            break
                routes.append(
                    {
                        "method": dec.func.attr.upper(),
                        "path": (prefix + suffix) or "/",
                        "operation": node.name,
                        "module": f"{API_DIR.as_posix()}/{path.name}",
                        "meta_keys": sorted(meta_keys),
                        "meta_partial": meta_partial,
                    }
                )
    routes.sort(key=lambda r: (r["path"], r["method"]))
    return routes


# --------------------------------------------------------------------------- models


def collect_models(root: Path) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for path in sorted((root / PACKAGE).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for fn in cls.body:
                if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if fn.name != "as_dict":
                    continue
                returned = _returned_dict(fn)
                if returned is None:
                    continue
                keys, partial = _dict_keys(returned)
                if not keys:
                    continue
                relative = path.relative_to(root).as_posix()
                existing = models.get(cls.name)
                if existing is not None:
                    # Two classes share a name (`TradeIntent` exists in both the
                    # backtest and the trading package). Merge, and mark partial:
                    # the union is a lower bound for either one, which is exactly
                    # what the guard treats it as.
                    existing["keys"] = sorted(set(existing["keys"]) | set(keys))
                    existing["partial"] = True
                    existing["sources"] = sorted({*existing["sources"], relative})
                    continue
                models[cls.name] = {
                    "keys": sorted(keys),
                    "partial": partial,
                    "sources": [relative],
                }
    return dict(sorted(models.items()))


# ---------------------------------------------------------------------------- write


SERIALISATION = "serialisation.py"


def collect_serializers(root: Path) -> dict[str, dict[str, Any]]:
    """Top-level keys of every `*_to_dict` in a `serialisation.py`.

    These are the response bodies that are not a domain `as_dict` — the ones the
    routers assemble. Recorded under the function name so `dto.ts` can cite them the
    same way it cites a model.
    """
    models: dict[str, dict[str, Any]] = {}
    for path in sorted((root / PACKAGE).rglob(SERIALISATION)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if fn.name.startswith("_") or not fn.name.endswith("_to_dict"):
                continue
            returned, open_set = _returned_dict_or_binding(fn)
            if returned is None:
                continue
            keys, partial = _dict_keys(returned)
            if not keys:
                continue
            relative = path.relative_to(root).as_posix()
            existing = models.get(fn.name)
            if existing is not None:
                existing["keys"] = sorted(set(existing["keys"]) | set(keys))
                existing["partial"] = True
                existing["sources"] = sorted({*existing["sources"], relative})
                continue
            models[fn.name] = {
                "keys": sorted(keys),
                "partial": partial or open_set,
                "sources": [relative],
            }
    return dict(sorted(models.items()))


def _source_digest(root: Path) -> str:
    """Content hash of every file the extraction reads.

    Lets `--check` distinguish "the backend changed and nobody regenerated" from
    "the generator changed", which are different problems with different fixes.

    **Line endings are normalised before hashing.** This repository has
    `core.autocrlf=true` and no `.gitattributes`, so a checkout rewrites every `.py`
    file to CRLF while git's stored blobs stay LF. Hashing raw bytes therefore made
    the digest a property of *how the tree was checked out* rather than of its
    content: the same commit produced two different digests on two machines, and
    `--check` failed on a tree where `git diff` was empty. That happened, on a
    rebase, and is what this line prevents.
    """
    digest = hashlib.sha256()
    for path in sorted((root / PACKAGE).rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        text = path.read_text(encoding="utf-8")
        digest.update(text.replace("\r\n", "\n").encode("utf-8"))
    return digest.hexdigest()


def build(root: Path) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "generator": "tools/export_api_contract.py",
        "source_digest": _source_digest(root),
        "routes": collect_routes(root),
        "models": collect_models(root),
        "serializers": collect_serializers(root),
    }


_TS_HEADER = """// GENERATED by tools/export_api_contract.py -- do not edit by hand.
//
// Phase 12 brief 24: every frontend API call corresponds to an existing backend
// contract. This file is that correspondence, extracted from `oipulse/api/*.py` and
// the domain `as_dict` methods rather than transcribed, so it cannot quietly drift
// from the backend it describes. `tools/check_frontend_contract.py` fails when it has.
//
// `ROUTES` is the closed set of paths that exist. `MODELS` records the keys each
// `data` payload actually carries; entries marked `partial` were assembled from a
// dict this parser could not fully resolve, so their key list is a lower bound and
// is only ever used as one.
"""


def to_typescript(contract: dict[str, Any]) -> str:
    lines = [_TS_HEADER]
    lines.append(f"export const CONTRACT_VERSION = {contract['contract_version']};")
    lines.append(f'export const CONTRACT_SOURCE_DIGEST = "{contract["source_digest"]}";')
    lines.append("")
    lines.append("export interface BackendRoute {")
    lines.append('  readonly method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";')
    lines.append("  readonly path: string;")
    lines.append("  readonly operation: string;")
    lines.append("  readonly module: string;")
    lines.append("  readonly metaKeys: readonly string[];")
    lines.append("  readonly metaPartial: boolean;")
    lines.append("}")
    lines.append("")
    lines.append("export const ROUTES: readonly BackendRoute[] = [")
    for route in contract["routes"]:
        meta = ", ".join(f'"{k}"' for k in route["meta_keys"])
        lines.append(
            f'  {{ method: "{route["method"]}", path: "{route["path"]}", '
            f'operation: "{route["operation"]}", module: "{route["module"]}", '
            f"metaKeys: [{meta}], metaPartial: {str(route['meta_partial']).lower()} }},"
        )
    lines.append("] as const;")
    lines.append("")
    lines.append("export interface BackendModel {")
    lines.append("  readonly keys: readonly string[];")
    lines.append("  readonly partial: boolean;")
    lines.append("}")
    lines.append("")
    lines.append("export const MODELS: Readonly<Record<string, BackendModel>> = {")
    for name, model in contract["models"].items():
        keys = ", ".join(f'"{k}"' for k in model["keys"])
        lines.append(f'  "{name}": {{ keys: [{keys}], partial: {str(model["partial"]).lower()} }},')
    lines.append("};")
    lines.append("")
    lines.append("/** Response bodies assembled by `*_to_dict` serializers, by function name. */")
    lines.append("export const SERIALIZERS: Readonly<Record<string, BackendModel>> = {")
    for name, model in contract["serializers"].items():
        keys = ", ".join(f'"{k}"' for k in model["keys"])
        lines.append(f'  "{name}": {{ keys: [{keys}], partial: {str(model["partial"]).lower()} }},')
    lines.append("};")
    lines.append("")
    lines.append("/** Every path the backend serves, for exhaustiveness checks in tests. */")
    lines.append("export const ROUTE_PATHS: ReadonlySet<string> = new Set(")
    lines.append("  ROUTES.map((r) => `${r.method} ${r.path}`),")
    lines.append(");")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify, do not write")
    args = parser.parse_args(argv)

    contract = build(ROOT)
    json_text = json.dumps(contract, indent=2, sort_keys=False) + "\n"
    ts_text = to_typescript(contract)

    json_path = ROOT / JSON_OUT
    ts_path = ROOT / TS_OUT

    if args.check:
        stale: list[str] = []
        for path, expected in ((json_path, json_text), (ts_path, ts_text)):
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            if actual != expected:
                stale.append(path.relative_to(ROOT).as_posix())
        if stale:
            print("FAIL  generated API contract is stale:")
            for name in stale:
                print(f"  {name}")
            print("  run: python3 tools/export_api_contract.py")
            return 1
        print(
            f"PASS  generated API contract current: {len(contract['routes'])} routes, "
            f"{len(contract['models'])} models, {len(contract['serializers'])} serializers"
        )
        return 0

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json_text, encoding="utf-8")
    ts_path.write_text(ts_text, encoding="utf-8")
    print(
        f"wrote {JSON_OUT} and {TS_OUT}: {len(contract['routes'])} routes, "
        f"{len(contract['models'])} models, {len(contract['serializers'])} serializers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
