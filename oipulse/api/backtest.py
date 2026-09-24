"""`/backtest` — `12-API_SPEC.md` §193.

> `POST /backtest/runs` · `GET /backtest/runs/{id}` (status, progress) · `/results` ·
> `/trades` · `/equity-curve`.

**This surface cannot trade.** There is no order submission, modification or
cancellation verb, no broker reference and no account mutation. A backtest run is a
simulation over stored history; the nearest live-trading endpoints are `/trading/*`,
which `12` §199 gates behind the `LIVE_TRADE` permission and a feature flag, and which
Phase 7 does not implement.

**Every result response carries its assumption set and its two honesty flags.**
`10-REPLAY.md` §6 requires the assumptions printed alongside every number;
`serialisation.result_to_dict` puts them in `meta`, so a client receiving a P&L figure
has necessarily also received `assumption_based`, `risk_evaluated` and the caveats.

**An unknown run is a 404 and an unconfigured process is a 503.** Neither returns an
empty list, which would be indistinguishable from a strategy that placed no trades —
a completely different and far more interesting result.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.backtest.serialisation import (
    equity_curve_to_dict,
    result_to_dict,
    trades_to_dict,
)

router = APIRouter(prefix="/backtest", tags=["backtest"])

__all__ = ["router"]


def _runs(request: Request) -> Any:
    runs = getattr(request.app.state, "backtest_runs", None)
    if runs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no backtest run store is configured on this process. An empty list "
                "is not returned: it would be indistinguishable from a strategy "
                "having placed no trades."
            ),
        )
    return runs


def _run_or_404(request: Request, run_id: str) -> Any:
    run = _runs(request).get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no backtest run {run_id}"
        )
    return run


def _result_or_404(request: Request, run_id: str) -> Any:
    run = _run_or_404(request, run_id)
    result = run.result
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"backtest run {run_id} has status {run.status!r} and has produced no "
                f"result yet; a partial run has no interpretable statistics"
            ),
        )
    return result


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Start a run from a strategy, a replay context and an explicit fill model.

    The fill model is required, not defaulted: `10` §6 forbids hidden execution
    assumptions, and a server-side default would be exactly that — invisible in the
    request that produced the numbers.
    """
    runs = _runs(request)
    try:
        run = runs.create(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {
        "data": {"run_id": run.run_id, "status": run.status},
        "meta": {"assumptions": run.assumptions},
    }


@router.get("/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    runs = _runs(request).list()
    return {
        "data": [{"run_id": r.run_id, "status": r.status} for r in runs],
        "meta": {"count": len(runs)},
    }


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    """Status and progress, which exist before a result does."""
    run = _run_or_404(request, run_id)
    return {
        "data": {
            "run_id": run.run_id,
            "status": run.status,
            "steps_completed": run.steps_completed,
            "steps_total": run.steps_total,
        },
        "meta": {"assumptions": run.assumptions},
    }


@router.get("/runs/{run_id}/results")
async def get_run_results(request: Request, run_id: str) -> dict[str, Any]:
    return result_to_dict(_result_or_404(request, run_id))


@router.get("/results/{content_hash}")
async def get_result_by_hash(request: Request, content_hash: str) -> dict[str, Any]:
    """By content address. Two identical runs resolve to the same artifact."""
    result = _runs(request).get_result(content_hash)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no backtest result with content hash {content_hash}",
        )
    return result_to_dict(result)


@router.get("/runs/{run_id}/trades")
async def get_run_trades(request: Request, run_id: str) -> dict[str, Any]:
    """Fills **and** rejections. A trades list that hid rejections would flatter."""
    return trades_to_dict(_result_or_404(request, run_id))


@router.get("/runs/{run_id}/equity-curve")
async def get_equity_curve(
    request: Request,
    run_id: str,
    include_unmarked: bool = Query(
        True, description="keep points whose unrealized P&L excluded an unmarked position"
    ),
) -> dict[str, Any]:
    """The measured curve. Nothing between two points is invented.

    Dropping unmarked points is offered but not the default: excluding them makes a
    smoother curve that silently omits the periods where the data was worst.
    """
    run = _run_or_404(request, run_id)
    snapshots = tuple(run.equity_curve)
    if not include_unmarked:
        snapshots = tuple(s for s in snapshots if not s.unmarked_instruments)
    return equity_curve_to_dict(snapshots, run_id=run_id)
