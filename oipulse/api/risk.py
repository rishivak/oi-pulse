"""`/risk` — `12-API_SPEC.md` §204.

> `GET|PUT /risk/profiles` · `GET /risk/status` (current utilization against every
> limit) · `GET /risk/decisions` (audit) · `POST /risk/kill-switch` ·
> `DELETE /risk/kill-switch`.

**There is no endpoint that approves an intent.** Phase 9 brief §25 forbids one, and
this router has none: approval is only ever the output of `RiskEngine.evaluate`, run
server-side against a state the server assembled. `POST /risk/evaluate` triggers an
evaluation; it does not accept a verdict.

Six forgeries the brief §26 requires to be impossible, and why each is:

| Attack | Why it fails |
|---|---|
| bypass evaluation | no endpoint writes a decision; only the engine constructs one |
| forge an approval | `RiskDecisionRecord` is never deserialised from a request body |
| replace `inputs_digest` | computed by `inputs_digest_for` from the three real inputs |
| attach another intent | `intent_id` is taken from the evaluated intent, and the DB keys the order's FK on its **own** `intent_id` |
| extend `approved_until` | derived from the engine's `approval_validity`; no field reads it from input |
| select an arbitrary risk state | the server assembles the state; a client names an account, not a state |

The last column matters more than the endpoint list: these hold because of where
the objects are constructed, not because of validation in HTTP code. §26 requires
exactly that — validation at the domain boundary, not only in the router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.trading.risk.serialisation import (
    decision_to_dict,
    decisions_to_dict,
    limit_status_to_dict,
    policy_to_dict,
    state_to_dict,
)

router = APIRouter(prefix="/risk", tags=["risk"])

__all__ = ["router"]


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "risk", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no risk service is configured on this process. An empty response is "
                "not returned: a client must never read the absence of a risk service "
                "as the absence of risk."
            ),
        )
    return service


@router.get("/profiles")
async def list_profiles(request: Request) -> dict[str, Any]:
    profiles = _service(request).list_policies()
    return {
        "data": [p.as_dict() for p in profiles],
        "meta": {"count": len(profiles)},
    }


@router.get("/profiles/{policy_id}/versions/{version}")
async def get_profile(request: Request, policy_id: str, version: int) -> dict[str, Any]:
    policy = _service(request).get_policy(policy_id, version)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no risk policy {policy_id}@v{version}"
        )
    return policy_to_dict(policy)


@router.put("/profiles")
async def put_profile(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Register a policy version.

    A **new version**, never an edit: `11` §3 requires historical decision
    semantics to stay fixed, and the database enforces it with
    `uq_risk_profiles_identity` on `(policy_id, version)`. Re-submitting an
    existing version with different limits is a 409, not an update.
    """
    service = _service(request)
    try:
        policy = service.register_policy(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return policy_to_dict(policy)


@router.get("/state/{account_id}")
async def get_risk_state(request: Request, account_id: str) -> dict[str, Any]:
    """The snapshot risk would read now, with its content address.

    Exposed so an operator can see what an evaluation *would* see. A client cannot
    supply one: the server assembles it from the account's ledger and the canonical
    market state, which is what makes `risk_state_ref` evidence rather than a claim.
    """
    state = _service(request).risk_state(account_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no risk state for {account_id}"
        )
    return state_to_dict(state)


@router.get("/status/{account_id}")
async def get_limit_status(request: Request, account_id: str) -> dict[str, Any]:
    """Current utilization against every limit (`12` §205).

    Grouped by status, so `PASSED`, `NOT_CONFIGURED` and `NOT_EVALUABLE` stay
    distinguishable. A page showing only a green count would report an account with
    twenty unconfigured limits as comfortably within all of them.
    """
    service = _service(request)
    result = service.limit_status(account_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no risk status for {account_id}"
        )
    decision, policy = result
    return limit_status_to_dict(decision, policy)


@router.post("/evaluate")
async def evaluate_intent(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Run a real evaluation for an intent. **Does not accept a verdict.**

    The request names an account and an intent. Everything that determines the
    outcome — the policy, the state, the limits — is resolved server-side. There is
    no field in this body that a client could use to influence the verdict, which
    is why brief §25's "do not expose an endpoint allowing callers to simply mark
    an intent APPROVED" is satisfied structurally rather than by validation.
    """
    service = _service(request)
    try:
        decision, at = service.evaluate(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return decision_to_dict(decision, at=at)


@router.get("/decisions")
async def list_decisions(
    request: Request,
    intent_id: str = Query(..., description="the intent whose decision sequence to read"),
) -> dict[str, Any]:
    """The full appended sequence for one intent (`11` §3).

    Scoped to an intent rather than open-ended: a decision is only meaningful
    beside the intent it evaluated, and a global list invites reading one out of
    context.
    """
    service = _service(request)
    decisions = service.decisions_for(intent_id)
    if not decisions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no risk decisions recorded for intent {intent_id}",
        )
    return decisions_to_dict(decisions, at=service.market_time())


@router.get("/decisions/{intent_id}/{sequence_no}")
async def get_decision(request: Request, intent_id: str, sequence_no: int) -> dict[str, Any]:
    """One decision by its composite key — the audit lookup (`11` §10)."""
    service = _service(request)
    decision = service.decision(intent_id, sequence_no)
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no risk decision ({intent_id}, {sequence_no})",
        )
    return decision_to_dict(decision, at=service.market_time())


@router.post("/kill-switch")
async def engage_kill_switch(
    request: Request, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Halt trading, globally or per strategy. Immediate (`11` §3)."""
    service = _service(request)
    switch = service.engage_kill_switch(body)
    return {"data": switch.as_dict(), "meta": {"engaged": True}}


@router.delete("/kill-switch")
async def clear_kill_switch(request: Request) -> dict[str, Any]:
    """Release the halt. Recorded like any other operator action."""
    service = _service(request)
    switch = service.clear_kill_switch()
    return {"data": switch.as_dict(), "meta": {"engaged": switch.engaged}}
