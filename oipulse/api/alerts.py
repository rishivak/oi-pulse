"""`/alerts` — `12-API_SPEC.md` §176.

| CRUD | `/alerts/rules` | full CRUD on rules |
| POST | `/alerts/rules/{id}/test` | dry-run against current or historical state |
| GET  | `/alerts/occurrences` | |
| POST | `/alerts/occurrences/{id}/acknowledge` | |

**The dry-run never delivers and never writes.** It routes a candidate signal through
the rule and reports what *would* happen, which is the whole point of a test endpoint:
an operator tuning a cooldown must be able to see the effect without waking anyone.

No endpoint here mutates a signal. Acknowledgement marks the *occurrence*, which is a
delivery record; the signal it refers to is untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.alerts.routing import AlertRouter
from oipulse.alerts.serialisation import occurrence_to_dict, rule_to_dict

router = APIRouter(prefix="/alerts", tags=["alerts"])

__all__ = ["router"]


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "alert_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no alert store is configured on this process. An empty list is not "
                "returned: it would be indistinguishable from no alerts having fired."
            ),
        )
    return store


@router.get("/rules")
async def list_rules(request: Request) -> dict[str, Any]:
    store = _store(request)
    rules = store.list_rules()
    return {"data": [rule_to_dict(r) for r in rules], "meta": {"count": len(rules)}}


@router.get("/rules/{rule_id}")
async def get_rule(request: Request, rule_id: str) -> dict[str, Any]:
    store = _store(request)
    rule = store.get_rule(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no alert rule {rule_id}"
        )
    return {"data": rule_to_dict(rule)}


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    store = _store(request)
    try:
        rule = store.create_rule(body)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"data": rule_to_dict(rule)}


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(request: Request, rule_id: str) -> None:
    store = _store(request)
    if not store.delete_rule(rule_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no alert rule {rule_id}"
        )


@router.post("/rules/{rule_id}/test")
async def test_rule(request: Request, rule_id: str, signal_id: str = Query(...)) -> dict[str, Any]:
    """Dry-run. Reports what would happen; delivers nothing and writes nothing."""
    store = _store(request)
    rule = store.get_rule(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no alert rule {rule_id}"
        )
    reader = getattr(request.app.state, "signal_reader", None)
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="no signal reader is configured; a dry-run needs a signal to test against",
        )
    signal = reader.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no signal {signal_id}")

    # A throwaway router: the dry-run must not affect real dedup or cooldown state.
    decision = AlertRouter().route(rule, signal, signal.available_at)
    return {
        "data": {
            "would_alert": decision.occurrence is not None and not decision.suppressed,
            "suppressed": decision.suppressed,
            "reason": decision.reason,
            "occurrence": (
                occurrence_to_dict(decision.occurrence) if decision.occurrence else None
            ),
        },
        "meta": {"dry_run": True, "delivered": False, "persisted": False},
    }


@router.get("/occurrences")
async def list_occurrences(
    request: Request,
    rule_id: str | None = Query(None),
    signal_id: str | None = Query(None),
    since: datetime | None = Query(None),
) -> dict[str, Any]:
    store = _store(request)
    occurrences = store.list_occurrences(rule_id=rule_id, signal_id=signal_id, since=since)
    return {
        "data": [occurrence_to_dict(o) for o in occurrences],
        "meta": {"count": len(occurrences)},
    }


@router.post("/occurrences/{occurrence_id}/acknowledge")
async def acknowledge(
    request: Request,
    occurrence_id: str,
    by: str = Query(..., description="who acknowledged"),
) -> dict[str, Any]:
    """Acknowledge an occurrence. The signal it refers to is not touched."""
    store = _store(request)
    occurrence = store.acknowledge(occurrence_id, by)
    if occurrence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no occurrence {occurrence_id}"
        )
    return {"data": occurrence_to_dict(occurrence), "meta": {"signal_unchanged": True}}
