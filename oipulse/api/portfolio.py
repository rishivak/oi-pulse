"""`/portfolio` — `12-API_SPEC.md` §208.

> `/portfolio` · `/pnl` · `/greeks` · `/exposure` · `/attribution` (sliceable) ·
> `/snapshots`.

Plus `/positions` and `/position-reconciliation`, which Phase 11 brief §26 lists and
which close the Phase 10 deferral.

**A historical request must name its knowledge time.** Brief §26: *"Do not expose
unsupported latest-knowledge shortcuts for historical requests."* Any endpoint that
reads a past `market_time` therefore **requires** `knowledge_time`; omitting it is a
422, not a default to "now". Defaulting would turn a point-in-time question into a
hindsight answer with nothing in the response to say it had happened — the same rule
`/research` enforces (`12` §176).

**The residual is never optional.** Every attribution response carries it in `meta`,
and `18-ROADMAP.md` Phase 11 is explicit that a large one is information rather than
something to hide.

**This router computes nothing.** It resolves a service and renders. The portfolio
layer is pure and holds no database, so the service seam is where persistence and
market data are wired, exactly as in every other router here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.trading.portfolio.serialisation import (
    greeks_to_dict,
    pnl_to_dict,
    position_reconciliation_to_dict,
    positions_to_dict,
    slices_to_dict,
    snapshot_to_dict,
    valuation_to_dict,
)
from oipulse.trading.portfolio.valuation import ValuationRefused

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

__all__ = ["router"]


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "portfolio", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no portfolio service is configured on this process. An empty "
                "portfolio is not returned: it would be indistinguishable from an "
                "account that holds nothing."
            ),
        )
    return service


def _require_knowledge_time(market_time: datetime | None, knowledge_time: datetime | None) -> None:
    """A historical read must state what it was allowed to know.

    Silently answering with latest knowledge would turn a point-in-time question
    into a hindsight answer, and nothing in the response would reveal it.
    """
    if market_time is not None and knowledge_time is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "knowledge_time is required whenever market_time is supplied. A "
                "historical valuation must state what it was permitted to know; "
                "defaulting to latest knowledge would silently answer a different "
                "question."
            ),
        )


@router.get("")
async def get_portfolio(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
    market_time: datetime | None = Query(None),
    knowledge_time: datetime | None = Query(None),
) -> dict[str, Any]:
    """The portfolio snapshot at `(market_time, knowledge_time)`, or current."""
    _require_knowledge_time(market_time, knowledge_time)
    service = _service(request)
    try:
        snapshot = await service.snapshot(
            account_id=account_id,
            portfolio_id=portfolio_id,
            market_time=market_time,
            knowledge_time=knowledge_time,
        )
    except ValuationRefused as exc:
        # An unreliable state is a 409, not an empty portfolio: a caller must not
        # read a refusal as a book worth nothing.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no portfolio {portfolio_id} for account {account_id}",
        )
    return snapshot_to_dict(snapshot)


@router.get("/positions")
async def get_positions(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
    market_time: datetime | None = Query(None),
    knowledge_time: datetime | None = Query(None),
    include_closed: bool = Query(False),
) -> dict[str, Any]:
    _require_knowledge_time(market_time, knowledge_time)
    service = _service(request)
    positions, times = await service.positions(
        account_id=account_id,
        portfolio_id=portfolio_id,
        market_time=market_time,
        knowledge_time=knowledge_time,
        include_closed=include_closed,
    )
    return positions_to_dict(positions, market_time=times[0], knowledge_time=times[1])


@router.get("/exposure")
async def get_exposure(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
    market_time: datetime | None = Query(None),
    knowledge_time: datetime | None = Query(None),
) -> dict[str, Any]:
    """Gross and net exposure, with the positions that could not be valued named."""
    _require_knowledge_time(market_time, knowledge_time)
    service = _service(request)
    try:
        valuation = await service.valuation(
            account_id=account_id,
            portfolio_id=portfolio_id,
            market_time=market_time,
            knowledge_time=knowledge_time,
        )
    except ValuationRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return valuation_to_dict(valuation)


@router.get("/pnl")
async def get_pnl(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
    market_time: datetime | None = Query(None),
    knowledge_time: datetime | None = Query(None),
) -> dict[str, Any]:
    """Gross, fees and net kept separate; the return is labelled `SIMPLE_PERIOD`."""
    _require_knowledge_time(market_time, knowledge_time)
    service = _service(request)
    snapshot = await service.snapshot(
        account_id=account_id,
        portfolio_id=portfolio_id,
        market_time=market_time,
        knowledge_time=knowledge_time,
    )
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no portfolio")
    return pnl_to_dict(snapshot)


@router.get("/greeks")
async def get_greeks(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
    market_time: datetime | None = Query(None),
    knowledge_time: datetime | None = Query(None),
) -> dict[str, Any]:
    _require_knowledge_time(market_time, knowledge_time)
    service = _service(request)
    snapshot = await service.snapshot(
        account_id=account_id,
        portfolio_id=portfolio_id,
        market_time=market_time,
        knowledge_time=knowledge_time,
    )
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no portfolio")
    return greeks_to_dict(snapshot)


@router.get("/attribution")
async def get_attribution(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
    bucket: str = Query("PORTFOLIO", description="slice: STRATEGY, SIGNAL, UNDERLYING, ..."),
    market_time: datetime | None = Query(None),
    knowledge_time: datetime | None = Query(None),
) -> dict[str, Any]:
    """Sliceable attribution. The residual is in `meta` on every response.

    An unrecognised bucket is a 422 rather than a silent fallback to PORTFOLIO: a
    client asking for a slice that does not exist should learn that, not receive a
    different slice that looks like an answer.
    """
    _require_knowledge_time(market_time, knowledge_time)
    service = _service(request)
    try:
        slices, times = await service.attribution(
            account_id=account_id,
            portfolio_id=portfolio_id,
            bucket=bucket,
            market_time=market_time,
            knowledge_time=knowledge_time,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return slices_to_dict(slices, market_time=times[0], knowledge_time=times[1])


@router.get("/snapshots")
async def list_snapshots(
    request: Request,
    account_id: str = Query(...),
    portfolio_id: str = Query("default"),
) -> dict[str, Any]:
    snapshots = await _service(request).snapshots(account_id=account_id, portfolio_id=portfolio_id)
    return {
        "data": [s.as_dict() for s in snapshots],
        "meta": {
            "count": len(snapshots),
            "complete": sum(1 for s in snapshots if s.is_complete),
        },
    }


@router.get("/snapshots/{content_digest}")
async def get_snapshot(request: Request, content_digest: str) -> dict[str, Any]:
    """By content address. Two identical snapshots resolve to one artifact."""
    snapshot = await _service(request).snapshot_by_digest(content_digest)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no portfolio snapshot with digest {content_digest}",
        )
    return snapshot_to_dict(snapshot)


@router.post("/position-reconciliation")
async def reconcile_positions(
    request: Request, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Compare our positions with the broker's (brief §4, §18).

    The body may name an account, a portfolio and whether to apply corrections. It
    may **not** supply provider positions: they are read through the broker adapter,
    so there is no parameter through which a client could inject the truth
    reconciliation trusts.
    """
    service = _service(request)
    for forbidden in ("provider_positions", "positions", "provider_snapshot"):
        if forbidden in body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{forbidden!r} may not be supplied. Provider positions are read "
                    f"through the broker adapter; accepting them from a request body "
                    f"would let a caller forge the evidence reconciliation trusts."
                ),
            )
    try:
        run = await service.reconcile_positions(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return position_reconciliation_to_dict(run)


@router.get("/position-reconciliation/{run_id}")
async def get_position_reconciliation(request: Request, run_id: str) -> dict[str, Any]:
    run = await _service(request).position_reconciliation(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no position reconciliation run {run_id}",
        )
    return position_reconciliation_to_dict(run)
