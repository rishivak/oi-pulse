"""Guard: no route is reachable without an authorization decision.

Phase 12's independent verification returned NOT VERIFIED because the backend had
no authentication, no permission enforcement and no CSRF. Adding them is necessary;
keeping them is what this guard is for. The specific way an authorization boundary
decays is well known — someone adds a route and nobody notices it was never given a
policy — and that failure is invisible to every test that exercises the routes
somebody did remember.

So this compares the policy table in `oipulse/identity/permissions.py` against the
generated API contract, which is itself extracted from `oipulse/api/*.py`. Both
directions matter: a route with no policy is an open door, and a policy for a route
that no longer exists is a comment pretending to be a control.

Eight checks:

1. **Every route has a policy**, or is one of the two public probes.
2. **Every policy names a real route.** No dead entries.
3. **The public list is exactly the container probes.** `14-DEPLOYMENT.md` §172
   wires `/ops/health` and `/ops/ready` to probes, which carry no session. Any
   third public route has to be argued for, and this fails until it is.
4. **The five permissions are exactly `17-SECURITY.md` §4's.** No sixth, no rename.
5. **No route requires `LIVE_TRADE`.** The remediation brief §4: holding the
   permission must not become a way to reach execution. There is no live route, so
   there is nothing for it to unlock.
6. **The safe and state-changing method sets are disjoint and complete**, so
   `17` §7's "no state change may ever occur on `GET`" is a property of the code.
7. **`install_security` is called** in the application factory, and CSRF is
   evaluated inside the gate rather than as an optional extra.
8. **Account-scoped routes are the ones with an account in the path**, so a route
   that takes an `{account_id}` cannot skip the ownership check by omitting a flag.

Stdlib only: `ast` plus the committed contract JSON. No web stack, no database.

Exit 0 clean, 1 on any violation.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path("frontend/lib/api/contract.generated.json")
PERMISSIONS = Path("oipulse/identity/permissions.py")
CSRF = Path("oipulse/identity/csrf.py")
GATE = Path("oipulse/identity/gate.py")
APP = Path("oipulse/api/app.py")

#: `17-SECURITY.md` §4, verbatim and in the document's order.
SPEC_PERMISSIONS = ("MARKET_DATA_READ", "RESEARCH", "PAPER_TRADE", "LIVE_TRADE", "ADMIN")
#: `14-DEPLOYMENT.md` §172 wires these to container probes.
SPEC_PUBLIC = {("GET", "/ops/health"), ("GET", "/ops/ready")}


def _load(root: Path, relative: Path) -> ast.Module:
    return ast.parse((root / relative).read_text(encoding="utf-8"), filename=str(relative))


def _enum_members(tree: ast.Module, class_name: str) -> list[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                stmt.targets[0].id
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ]
    return []


def _string_set(tree: ast.Module, name: str) -> set[str]:
    """The literal strings in a module-level `frozenset({...})` assignment."""
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if isinstance(first, ast.Name):
                target, value = first.id, node.value
        if target != name or value is None:
            continue
        return {
            element.value
            for element in ast.walk(value)
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
    return set()


def _policies(tree: ast.Module) -> list[tuple[str, str, str, bool]]:
    """`(method, template, permission, account_scoped)` for each `_p(...)` call."""
    found: list[tuple[str, str, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_p" or len(node.args) < 3:
            continue
        method, template, permission = node.args[0], node.args[1], node.args[2]
        if not isinstance(method, ast.Constant) or not isinstance(template, ast.Constant):
            continue
        name = ""
        if isinstance(permission, ast.Attribute):
            name = permission.attr
        elif isinstance(permission, ast.Name):
            # `_MARKET` and friends are module-level aliases; resolve below.
            name = permission.id
        scoped = False
        for keyword in node.keywords:
            if keyword.arg == "account_scoped" and isinstance(keyword.value, ast.Constant):
                scoped = bool(keyword.value.value)
        found.append((str(method.value), str(template.value), name, scoped))
    return found


def _aliases(tree: ast.Module) -> dict[str, str]:
    """`_MARKET = Permission.MARKET_DATA_READ` -> {'_MARKET': 'MARKET_DATA_READ'}."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Attribute):
            continue
        out[target.id] = node.value.attr
    return out


def check(root: Path = ROOT) -> list[str]:
    findings: list[str] = []

    contract_path = root / CONTRACT
    if not contract_path.exists():
        return [f"{CONTRACT}: missing; run python3 tools/export_api_contract.py"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes = {(r["method"], r["path"]) for r in contract["routes"]}
    if len(routes) < 50:
        return [f"only {len(routes)} routes in the contract; the guard would pass vacuously"]

    perms_tree = _load(root, PERMISSIONS)

    # 4. the five permissions
    members = _enum_members(perms_tree, "Permission")
    if tuple(members) != SPEC_PERMISSIONS:
        findings.append(
            f"{PERMISSIONS.as_posix()}: Permission is {members}, but 17-SECURITY.md §4 "
            f"defines exactly {list(SPEC_PERMISSIONS)}."
        )

    alias = _aliases(perms_tree)
    policies = _policies(perms_tree)
    if len(policies) < 50:
        findings.append(
            f"{PERMISSIONS.as_posix()}: only {len(policies)} policies parsed; the "
            f"parser has stopped matching and coverage would be reported vacuously."
        )

    declared: dict[tuple[str, str], tuple[str, bool]] = {}
    for method, template, permission, scoped in policies:
        resolved = alias.get(permission, permission)
        declared[(method, template)] = (resolved, scoped)

    # 3. the public list
    public_strings = _string_set(perms_tree, "PUBLIC_ROUTES")
    public = {("GET", p) for p in public_strings if p.startswith("/")}
    methods_in_public = {
        m for m in public_strings if m in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }
    if methods_in_public - {"GET"}:
        findings.append(
            f"{PERMISSIONS.as_posix()}: PUBLIC_ROUTES contains a non-GET method "
            f"{sorted(methods_in_public - {'GET'})}. A public state-changing route "
            f"is an unauthenticated write."
        )
    if public != SPEC_PUBLIC:
        findings.append(
            f"{PERMISSIONS.as_posix()}: PUBLIC_ROUTES is {sorted(public)}, expected "
            f"{sorted(SPEC_PUBLIC)}. Every unauthenticated route has to be argued for; "
            f"14-DEPLOYMENT.md §172 justifies the two container probes and nothing else."
        )

    # 1. every route has a policy
    for method, path in sorted(routes):
        if (method, path) in public:
            continue
        if (method, path) not in declared:
            findings.append(
                f"{method} {path} has no entry in ROUTE_POLICIES. A route without a "
                f"policy is refused at runtime, so this is not an open door -- but it "
                f"is an endpoint nobody can reach, which is equally a bug."
            )

    # 2. no dead policies
    for method, template in sorted(declared):
        if (method, template) not in routes:
            findings.append(
                f"{PERMISSIONS.as_posix()}: policy for {method} {template}, which the "
                f"backend does not serve. A policy for a route that does not exist "
                f"reads as protection and provides none."
            )

    # 5. LIVE_TRADE unlocks nothing
    live = [f"{m} {t}" for (m, t), (perm, _) in declared.items() if perm == "LIVE_TRADE"]
    if live:
        findings.append(
            f"{PERMISSIONS.as_posix()}: {live} require LIVE_TRADE. There is no live "
            f"execution path, and granting the permission must not become the thing "
            f"that creates one (remediation brief §4)."
        )

    # 8. account-scoped where the path carries an account
    for (method, template), (_, scoped) in sorted(declared.items()):
        has_account = "/accounts/{account_id}" in template or template.endswith("/{account_id}")
        if has_account and not scoped:
            findings.append(
                f"{PERMISSIONS.as_posix()}: {method} {template} carries an account in "
                f"the path but is not account_scoped, so the ownership check is "
                f"skipped and any PAPER_TRADE holder reaches any account."
            )
        if scoped and not has_account:
            findings.append(
                f"{PERMISSIONS.as_posix()}: {method} {template} is account_scoped but "
                f"has no account in the path, so ownership can never be resolved and "
                f"the route answers 503."
            )

    # 6. method sets
    csrf_tree = _load(root, CSRF)
    safe = _string_set(csrf_tree, "SAFE_METHODS")
    changing = _string_set(csrf_tree, "STATE_CHANGING_METHODS")
    if changing != {"POST", "PUT", "PATCH", "DELETE"}:
        findings.append(
            f"{CSRF.as_posix()}: STATE_CHANGING_METHODS is {sorted(changing)}; "
            f"17-SECURITY.md §7 names POST, PUT, PATCH and DELETE."
        )
    if safe & changing:
        findings.append(
            f"{CSRF.as_posix()}: {sorted(safe & changing)} is both safe and "
            f"state-changing, so CSRF would be skipped on a method that writes."
        )
    if "GET" not in safe:
        findings.append(f"{CSRF.as_posix()}: GET is not in SAFE_METHODS.")

    # 7. the gate is installed and evaluates CSRF
    app_source = (root / APP).read_text(encoding="utf-8")
    if "install_security(" not in app_source:
        findings.append(
            f"{APP.as_posix()}: install_security is not called. Without it every "
            f"route is served with no session, permission or CSRF check."
        )
    gate_source = (root / GATE).read_text(encoding="utf-8")
    for required in ("evaluate_csrf(", "validate_session(", "policy_for("):
        if required not in gate_source:
            findings.append(
                f"{GATE.as_posix()}: does not call {required.rstrip('(')}; the "
                f"pipeline in 17-SECURITY.md §3/§4/§7 is incomplete."
            )
    return findings


def main() -> int:
    findings = check(ROOT)
    if findings:
        print("FAIL  authorization boundary violations:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    contract = json.loads((ROOT / CONTRACT).read_text(encoding="utf-8"))
    print(
        f"PASS  authorization boundary: {len(contract['routes'])} routes, every one "
        f"policied or an explicit public probe; five permissions; no LIVE_TRADE route; "
        f"CSRF on every state-changing method"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
