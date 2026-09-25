"""`/reconciliation` and the OMS read surface — `12-API_SPEC.md` §211.

> `GET /reconciliation/runs` · `/runs/{id}` (broker snapshot, discrepancies,
> resolutions) · `POST /reconciliation/trigger`.

Plus the OMS order lookups Phase 10 brief §24 lists: order state, provider state,
lifecycle events, and cancel.

**There is no submit endpoint.** Brief §24: "Do NOT create a generic endpoint that
allows callers to submit arbitrary live orders." Submission happens through the
authorization path — intent, risk decision, OMS — and is reachable from
`/paper-trading`, never from here. This router reads, reconciles and cancels.

**Provider truth enters only through the adapter.** Brief §25 requires the API to
reject forged provider state and fabricated provider ids. It does so structurally:
no endpoint accepts a provider order state, a provider fill or a provider id in a
request body. `POST /reconciliation/trigger` runs the reconciler, which queries the
adapter itself. There is nothing for a client to forge because there is no parameter
through which to supply it.

**Arbitrary state transitions are impossible.** No endpoint takes a target state. An
order moves when the transition table allows it, driven by provider evidence or by a
cancel request, and a client cannot name a destination.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.trading.brokers.capability import LIVE_EXECUTION_ENABLED, LiveExecutionDisabled
from oipulse.trading.oms.serialisation import (
    oms_order_to_dict,
    oms_orders_to_dict,
    reconciliation_run_to_dict,
    reconciliation_runs_to_dict,
)
from oipulse.trading.orders import InvalidTransition

router = APIRouter(prefix="/reconciliation", tags=["reconciliation", "oms"])

__all__ = ["router"]


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "oms", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no OMS service is configured on this process. An empty response is "
                "not returned: a client must never read the absence of an OMS as the "
                "absence of outstanding orders."
            ),
        )
    return service


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Readiness, per `18` Phase 10: trader is not ready until reconciliation is clean.

    Reports `trader_ready=False` when no reconciliation has run at all. The absence
    of evidence is not evidence of agreement, and a process that accepted intents
    before reconciling would be trading on an assumption about what the broker holds.
    """
    service = _service(request)
    ready, reason = service.readiness()
    return {
        "data": {"trader_ready": ready, "reason": reason},
        "meta": {"live_execution_enabled": LIVE_EXECUTION_ENABLED, "execution_mode": "PAPER"},
    }


@router.get("/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    return reconciliation_runs_to_dict(_service(request).runs())


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    """One run with its broker snapshot, discrepancies and resolutions (`12` §211)."""
    run = _service(request).run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no reconciliation run {run_id}"
        )
    return reconciliation_run_to_dict(run)


@router.post("/trigger")
async def trigger_reconciliation(
    request: Request, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Run reconciliation now (`12` §211, `11` §6's MANUAL trigger).

    The body may name a trigger and a scope. It may **not** supply provider state:
    the reconciler queries the adapter itself, so there is no parameter through
    which a client could inject a broker view (brief §25).
    """
    service = _service(request)
    for forbidden in ("provider_orders", "provider_fills", "provider_state", "orders"):
        if forbidden in body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{forbidden!r} may not be supplied. Provider truth enters only "
                    f"through the broker adapter; accepting it from a request body "
                    f"would let a caller forge the state reconciliation trusts."
                ),
            )
    try:
        run = await service.reconcile(body)
    except LiveExecutionDisabled as exc:
        # The configured adapter cannot even query the provider. Reporting an empty
        # reconciliation would be far worse: it would read as "the broker holds
        # nothing" and mark every local order MISSING_AT_PROVIDER.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return reconciliation_run_to_dict(run)


@router.get("/orders")
async def list_orders(
    request: Request,
    unresolved_only: bool = Query(False, description="restrict to unresolved orders"),
) -> dict[str, Any]:
    """OMS orders. All of them by default, including the unresolved ones."""
    service = _service(request)
    orders = service.unresolved_orders() if unresolved_only else service.orders()
    return oms_orders_to_dict(orders)


@router.get("/orders/{order_id}")
async def get_order(request: Request, order_id: str) -> dict[str, Any]:
    order = _service(request).order(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no OMS order {order_id}"
        )
    return oms_order_to_dict(order)


@router.get("/orders/{order_id}/events")
async def get_order_events(request: Request, order_id: str) -> dict[str, Any]:
    """The append-only transition log — the order's lifecycle evidence."""
    order = _service(request).order(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no OMS order {order_id}"
        )
    return {
        "data": [e.as_dict() for e in order.events],
        "meta": {
            "live_execution_enabled": LIVE_EXECUTION_ENABLED,
            "execution_mode": "PAPER",
            "order_id": order_id,
            "count": len(order.events),
        },
    }


@router.get("/orders/{order_id}/provider-state")
async def get_provider_state(request: Request, order_id: str) -> dict[str, Any]:
    """What the venue says about this order, queried live through the adapter.

    A `404` when we hold no provider id is the honest answer: without one there is
    nothing to ask about, and returning an empty state would read as the venue
    saying it has nothing.
    """
    service = _service(request)
    order = service.order(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no OMS order {order_id}"
        )
    if order.provider_order_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"order {order_id} has no provider order id, so the venue cannot be "
                f"asked about it. This is the normal state after a lost "
                f"acknowledgement; run reconciliation to establish it."
            ),
        )
    try:
        observed = await service.provider_state(order_id)
    except LiveExecutionDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "data": None if observed is None else observed.as_dict(),
        "meta": {
            "live_execution_enabled": LIVE_EXECUTION_ENABLED,
            "execution_mode": "PAPER",
            "order_id": order_id,
            # None means the venue does not have it -- a finding, not an error.
            "present_at_provider": observed is not None,
        },
    }


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    request: Request, order_id: str, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Request a cancel. **A request, not an outcome** (brief §15).

    The order moves to `CANCEL_PENDING` and reaches `CANCELLED` only if the venue
    confirms. An order that filled before the cancel landed is filled, and this
    endpoint will say so rather than reporting a cancellation that did not happen.
    """
    service = _service(request)
    if service.order(order_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no OMS order {order_id}"
        )
    try:
        order = await service.cancel(order_id, body)
    except InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LiveExecutionDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return oms_order_to_dict(order)
