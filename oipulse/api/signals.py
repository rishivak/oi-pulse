"""`/signals` — `12-API_SPEC.md` §168.

| GET | `/signals` | filter by underlying, type, status, period |
| GET | `/signals/{id}` | full evidence, both kinds |
| GET | `/signals/{id}/history` | lifecycle transitions |
| GET | `/signals/types` | catalogue with rule definitions |

`/signals/types` is served from the rule registry and needs no store, so it answers
even on a process with no database — the catalogue is code, not data.

The other three need a signal reader. Absent one they answer **503 naming the missing
dependency** rather than an empty list: an empty list is indistinguishable from "no
signals fired", which is a completely different and much more interesting fact.

Availability filtering applies here: results are filtered to
`available_at <= decision_time`, and `decision_time` is a request parameter defaulting
to `knowledge_time`, never a stored field (`05` §2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from oipulse.signals.rules import RULES, UnknownRule
from oipulse.signals.serialisation import signal_to_dict, signal_type_to_dict

router = APIRouter(prefix="/signals", tags=["signals"])

__all__ = ["router"]


def _reader(request: Request) -> Any:
    reader = getattr(request.app.state, "signal_reader", None)
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no signal reader is configured on this process. An empty list is not "
                "returned: it would be indistinguishable from no signals having fired."
            ),
        )
    return reader


@router.get("/types")
async def signal_types() -> dict[str, Any]:
    """The catalogue with rule definitions. Served from the registry, not a store."""
    specs = RULES.all()
    return {
        "data": [signal_type_to_dict(spec) for spec in specs],
        "meta": {"count": len(specs), "types": list(RULES.types())},
    }


@router.get("/types/{signal_type}/versions/{version}")
async def signal_type_definition(signal_type: str, version: int) -> dict[str, Any]:
    try:
        spec = RULES.get(signal_type, version)
    except UnknownRule as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"data": signal_type_to_dict(spec)}


@router.get("")
async def list_signals(
    request: Request,
    underlying_id: int | None = Query(None),
    signal_type: str | None = Query(None),
    signal_status: str | None = Query(None, alias="status"),
    market_time: datetime | None = Query(
        None,
        description="T — omit for the latest (live) reading",
    ),
    knowledge_time: datetime | None = Query(None, description="K, defaults to market_time"),
    decision_time: datetime | None = Query(
        None,
        description=(
            "results are filtered to available_at <= decision_time; defaults to "
            "knowledge_time. A request parameter, never a stored field."
        ),
    ),
) -> dict[str, Any]:
    from datetime import timezone

    if market_time is None:
        market_time = datetime.now(timezone.utc)
    if knowledge_time is not None and knowledge_time < market_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"knowledge_time {knowledge_time.isoformat()} precedes market_time "
                f"{market_time.isoformat()}; knowledge_time < market_time is rejected"
            ),
        )
    reader = _reader(request)
    effective_k = knowledge_time or market_time
    effective_decision = decision_time or effective_k
    signals = reader.list_signals(
        underlying_id=underlying_id,
        signal_type=signal_type,
        signal_status=signal_status,
        market_time=market_time,
        knowledge_horizon=effective_k,
        decision_time=effective_decision,
    )
    return {
        "data": [signal_to_dict(s, include_evidence=False) for s in signals],
        "meta": {
            "count": len(signals),
            "market_time": market_time.isoformat(),
            "knowledge_time": effective_k.isoformat(),
            "decision_time": effective_decision.isoformat(),
            "semantics": "tradable_information_at",
        },
    }


@router.get("/{signal_id}")
async def get_signal(request: Request, signal_id: str) -> dict[str, Any]:
    """Full signal with both kinds of evidence."""
    reader = _reader(request)
    signal = reader.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no signal {signal_id}")
    return {"data": signal_to_dict(signal), "meta": {"signal_id": signal_id}}


@router.get("/{signal_id}/history")
async def get_signal_history(request: Request, signal_id: str) -> dict[str, Any]:
    """Lifecycle transitions. Nothing is overwritten, so this is the full sequence."""
    reader = _reader(request)
    signal = reader.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no signal {signal_id}")
    return {
        "data": [
            {"transition": transition, "at": at.isoformat()} for transition, at in signal.history
        ],
        "meta": {"signal_id": signal_id, "current_status": signal.status.value},
    }
