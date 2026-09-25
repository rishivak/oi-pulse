"""`GET /market/state` — the primary market-data endpoint.

`docs/design/12-API_SPEC.md` §2 and `04-MARKETSTATE.md`.

Time semantics, exactly as specified — there is no second time model here:

| Request | Mode |
|---|---|
| `knowledge_time` omitted or equal to `market_time` | `knowledge_at(T)` |
| `knowledge_time` > `market_time` | `market_truth_at(valid_time, knowledge_as_of)` |
| `knowledge_time` < `market_time` | **rejected — incoherent** |

Because a later `knowledge_time` must be supplied explicitly, market-truth semantics can
never be entered by accident, and the response echoes both values back.

`available_at` filtering is deliberately **not** offered. Raw observations have no
availability, and a `MarketState` is a reorganisation of raw observations, so a
`decision_time` filter here would imply an availability that does not exist. It belongs
on `/features` and `/signals` in Phase 4.

The `api` layer performs no business logic: it parses, delegates to the one shared
`StateService`, and serialises. It does not assemble state itself, and it does not hold
a second construction path (`14-DEPLOYMENT.md` §1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from oipulse.core.ids import InstrumentId
from oipulse.marketstate.builder import IncoherentTimeRange
from oipulse.marketstate.checkpoints import StateService
from oipulse.marketstate.serialisation import state_to_envelope

router = APIRouter(prefix="/market", tags=["market"])

__all__ = ["router", "serialise_state"]

#: Re-exported so the endpoint and its tests name one function.
serialise_state = state_to_envelope


def _service(request: Request) -> StateService:
    service = getattr(request.app.state, "state_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "state assembly is not configured on this process; "
                "the api role requires an observation source and instrument universe"
            ),
        )
    if not isinstance(service, StateService):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="configured state service is not a StateService",
        )
    return service


@router.get("/state")
async def market_state(
    request: Request,
    underlying_id: int = Query(..., description="canonical underlying instrument id"),
    market_time: datetime | None = Query(
        None,
        description=(
            "T — when the fact was true. Omit for the latest (live) reading; "
            "the server resolves it to its own wall clock."
        ),
    ),
    knowledge_time: datetime | None = Query(
        None, description="K — what OI Pulse knew by. Defaults to market_time."
    ),
) -> dict[str, Any]:
    """Full `MarketState` at `(market_time, knowledge_time)`.

    When ``market_time`` is omitted the server resolves it to "now", which is the
    live reading.  This lets the frontend's live mode — where the URL carries no
    pinned timestamp — work without fabricating a client-side instant.
    """
    service = _service(request)
    if market_time is None:
        from datetime import timezone

        market_time = datetime.now(timezone.utc)
    if knowledge_time is not None and knowledge_time < market_time:
        # `12-API_SPEC.md` §2: rejected as incoherent. 422, not 400 -- both parameters
        # parsed fine; the combination is what is refused. Enforced here rather than in
        # the builder, because `K < T` is a legitimate bitemporal query for replay and
        # research, while at the HTTP edge it is almost always two timestamps entered
        # the wrong way round, and answering it would quietly return a far emptier
        # state than the caller expected.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"knowledge_time {knowledge_time.isoformat()} precedes market_time "
                f"{market_time.isoformat()}; knowledge_time < market_time is rejected"
            ),
        )
    try:
        state = service.get_state(InstrumentId(underlying_id), market_time, knowledge_time)
    except IncoherentTimeRange as exc:
        # 422, not 400: the parameters parsed fine, their combination is meaningless.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return state_to_envelope(state)
