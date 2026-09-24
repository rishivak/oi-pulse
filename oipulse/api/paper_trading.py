"""`/paper-trading` — `12-API_SPEC.md` §199.

> `/accounts` · `POST /intents` · `GET /intents/{id}` (with its full risk decision
> sequence) · `/orders` · `/orders/{id}/events` · `/fills` · `/positions` ·
> `POST /orders/{id}/cancel`.

**This surface cannot place a real order.** There is no broker reference, no
credential, and no parameter that selects an execution venue. `12` §199 gates
`/trading/*` behind the `LIVE_TRADE` permission and a feature flag; Phase 8 does not
implement `/trading/*` at all, because there is nothing behind it — no live adapter
exists in the codebase.

Three behaviours are enforced here rather than left to a client.

**Paper mode is stated, never inferred.** Every response carries `mode: "PAPER"` and
`live_execution_available: false`, supplied by
`oipulse.trading.serialisation._paper_meta` so a new route cannot forget.

**A live-mode request is refused, not ignored.** `POST /accounts` with anything other
than `mode=PAPER` is a 422 naming the reason. Silently coercing it to paper would be
worse: a caller who believed they had opened a live account would keep believing it.

**An invalid transition is a 409, not a silent success.** Cancelling a filled order
fails explicitly, because the caller believes something is outstanding that is not.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.trading.accounts import AccountMode, LiveExecutionUnavailable
from oipulse.trading.orders import InvalidTransition
from oipulse.trading.serialisation import (
    account_to_dict,
    audit_to_dict,
    fills_to_dict,
    intent_to_dict,
    order_to_dict,
    orders_to_dict,
    positions_to_dict,
    snapshot_to_dict,
)

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])

__all__ = ["router"]


def _runtimes(request: Request) -> Any:
    runtimes = getattr(request.app.state, "paper_trading", None)
    if runtimes is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no paper-trading runtime is configured on this process. An empty "
                "list is not returned: it would be indistinguishable from an account "
                "that has placed no trades."
            ),
        )
    return runtimes


def _runtime_or_404(request: Request, account_id: str) -> Any:
    runtime = _runtimes(request).get(account_id)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no paper account {account_id}"
        )
    return runtime


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Open a paper account.

    A request naming any mode other than `PAPER` is refused with 422. It is not
    coerced: a caller who believed they had opened a live account would keep
    believing it, and the whole point of the safety posture is that the refusal is
    visible.
    """
    requested_mode = str(body.get("mode", AccountMode.PAPER.value)).upper()
    if requested_mode != AccountMode.PAPER.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"mode {requested_mode!r} is not available. This deployment implements "
                f"paper trading only: no live broker adapter exists in the codebase, "
                f"so there is no live execution path to enable. The request is refused "
                f"rather than silently downgraded to paper."
            ),
        )
    try:
        runtime = _runtimes(request).create(body)
    except LiveExecutionUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return account_to_dict(runtime.account, risk_evaluated=runtime.risk_evaluated)


@router.get("/accounts")
async def list_accounts(request: Request) -> dict[str, Any]:
    runtimes = _runtimes(request).list()
    return {
        "data": [r.account.as_dict() for r in runtimes],
        "meta": {"mode": "PAPER", "live_execution_available": False, "count": len(runtimes)},
    }


@router.get("/accounts/{account_id}")
async def get_account(request: Request, account_id: str) -> dict[str, Any]:
    runtime = _runtime_or_404(request, account_id)
    return account_to_dict(runtime.account, risk_evaluated=runtime.risk_evaluated)


@router.post("/accounts/{account_id}/intents", status_code=status.HTTP_201_CREATED)
async def create_intent(
    request: Request, account_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Submit an intent. Idempotent on the intent's content-addressed identity.

    A repeated submission returns `200` with the existing result rather than `201`,
    and nothing is re-applied — the status code itself tells a client which happened.
    """
    runtime = _runtime_or_404(request, account_id)
    try:
        result = _runtimes(request).submit(account_id, body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    body_out = {
        "data": result.as_dict(),
        "meta": {
            "mode": "PAPER",
            "live_execution_available": False,
            "duplicate": result.duplicate,
            "risk_evaluated": runtime.risk_evaluated,
        },
    }
    return body_out


@router.get("/accounts/{account_id}/intents/{intent_id}")
async def get_intent(request: Request, account_id: str, intent_id: str) -> dict[str, Any]:
    """The intent **with its full risk decision sequence** (`12` §199)."""
    runtime = _runtime_or_404(request, account_id)
    intent = runtime.intent(intent_id)
    if intent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no intent {intent_id}")
    return intent_to_dict(intent, decisions=runtime.decisions_for(intent_id))


@router.get("/accounts/{account_id}/orders")
async def list_orders(
    request: Request,
    account_id: str,
    open_only: bool = Query(False, description="restrict to non-terminal orders"),
) -> dict[str, Any]:
    """Every order by default, rejections included.

    `open_only` is offered but not the default: a list that hid rejections would
    make a strategy whose orders mostly fail look healthy.
    """
    runtime = _runtime_or_404(request, account_id)
    orders = runtime.open_orders() if open_only else runtime.orders()
    return orders_to_dict(orders)


@router.get("/accounts/{account_id}/orders/{order_id}")
async def get_order(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
    runtime = _runtime_or_404(request, account_id)
    order = runtime.order(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no order {order_id}")
    return order_to_dict(order)


@router.get("/accounts/{account_id}/orders/{order_id}/events")
async def get_order_events(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
    """The append-only transition log. The order's history *is* this sequence."""
    runtime = _runtime_or_404(request, account_id)
    order = runtime.order(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no order {order_id}")
    return {
        "data": [e.as_dict() for e in order.events],
        "meta": {
            "mode": "PAPER",
            "live_execution_available": False,
            "order_id": order_id,
            "count": len(order.events),
        },
    }


@router.post("/accounts/{account_id}/orders/{order_id}/cancel")
async def cancel_order(
    request: Request, account_id: str, order_id: str, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Cancel a resting order.

    Cancelling a terminal order is a `409`, not a no-op. The caller believes
    something is outstanding that is not, and a silent success would let that
    belief survive into their next decision.
    """
    runtime = _runtime_or_404(request, account_id)
    if runtime.order(order_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no order {order_id}")
    try:
        cancelled = _runtimes(request).cancel(account_id, order_id, body)
    except InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return order_to_dict(cancelled)


@router.get("/accounts/{account_id}/fills")
async def list_fills(request: Request, account_id: str) -> dict[str, Any]:
    runtime = _runtime_or_404(request, account_id)
    return fills_to_dict(runtime.fills())


@router.get("/accounts/{account_id}/positions")
async def list_positions(request: Request, account_id: str) -> dict[str, Any]:
    _runtime_or_404(request, account_id)
    return positions_to_dict(_runtimes(request).snapshot(account_id))


@router.get("/accounts/{account_id}/pnl")
async def get_pnl(request: Request, account_id: str) -> dict[str, Any]:
    """Cash, equity and P&L, with gross, fees and net kept separate."""
    _runtime_or_404(request, account_id)
    return snapshot_to_dict(_runtimes(request).snapshot(account_id))


@router.get("/accounts/{account_id}/audit/{order_id}")
async def get_audit_chain(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
    """Signal → evidence → decision → intent → order → fill → position (`11` §10)."""
    _runtime_or_404(request, account_id)
    chain = _runtimes(request).audit(account_id, order_id)
    if chain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no audit chain for order {order_id}",
        )
    return audit_to_dict(chain)
