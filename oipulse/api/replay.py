"""`/replay` — `12-API_SPEC.md` §187.

> `POST /replay/sessions` (create with period, universe, speed, knowledge mode) ·
> `GET /replay/sessions/{id}` · `POST .../control` (play, pause, step, seek, speed) ·
> `GET .../state` (state at the current replay position) ·
> `GET /replay/sessions/{id}/stream` (SSE).

Three behaviours are enforced here rather than left to a client.

**Hindsight is never entered by default.** `knowledge_mode=MARKET_TRUTH` requires an
explicit `pinned_knowledge_horizon`; omitting it is a 422. `ReplayContext` already
refuses to construct without one, so this endpoint surfaces that refusal as a client
error rather than a 500.

**Every state response carries both market time and knowledge time**, and a
market-truth run is badged `is_hindsight`. A client must not be able to render a
hindsight state as though it were live-reproducible.

**Nothing here can place an order.** A replay session reconstructs history; the
control verbs are play/pause/step/seek/speed and nothing else. Replay-derived events
are written to a run-scoped namespace and never to the live outbox (`10` §8).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from oipulse.replay.serialisation import (
    context_to_dict,
    progress_to_dict,
    step_to_dict,
    stepped_state_to_dict,
    timeline_to_dict,
)

router = APIRouter(prefix="/replay", tags=["replay"])

__all__ = ["router"]

#: The only control verbs `12` §187 defines. Anything resembling an order is absent
#: by construction, not filtered out later.
CONTROL_ACTIONS = frozenset({"play", "pause", "step", "seek", "speed"})


def _sessions(request: Request) -> Any:
    sessions = getattr(request.app.state, "replay_sessions", None)
    if sessions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no replay session manager is configured on this process. An empty "
                "list is not returned: it would be indistinguishable from there being "
                "no sessions."
            ),
        )
    return sessions


def _session_or_404(request: Request, session_id: str) -> Any:
    session = _sessions(request).get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no replay session {session_id}"
        )
    return session


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Create a run from a period, a universe, a step mode and a knowledge mode.

    A `MARKET_TRUTH` request without a pinned horizon is a 422: `ReplayContext`
    refuses to construct one, and hindsight must be asked for explicitly rather than
    fallen into.
    """
    sessions = _sessions(request)
    try:
        session = sessions.create(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"data": context_to_dict(session.context), "meta": {"status": session.status}}


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    sessions = _sessions(request).list()
    return {
        "data": [context_to_dict(s.context) for s in sessions],
        "meta": {"count": len(sessions)},
    }


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    session = _session_or_404(request, session_id)
    return {
        "data": timeline_to_dict(session.timeline),
        "meta": {
            "status": session.status,
            "progress": progress_to_dict(session.progress),
            "current_step": (
                None if session.current_step is None else step_to_dict(session.current_step)
            ),
        },
    }


@router.post("/sessions/{session_id}/control")
async def control_session(
    request: Request, session_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """play · pause · step · seek · speed. No other verb exists on this surface."""
    session = _session_or_404(request, session_id)
    action = str(body.get("action", "")).lower()
    if action not in CONTROL_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown control action {action!r}; replay supports "
                f"{sorted(CONTROL_ACTIONS)} and nothing that submits or modifies an order"
            ),
        )
    try:
        session.control(action, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {
        "data": {"action": action, "status": session.status},
        "meta": {
            "current_step": (
                None if session.current_step is None else step_to_dict(session.current_step)
            ),
            "progress": progress_to_dict(session.progress),
        },
    }


@router.get("/sessions/{session_id}/state")
async def get_session_state(
    request: Request,
    session_id: str,
    underlying_id: int = Query(..., description="which underlying's state to return"),
) -> dict[str, Any]:
    """The state at the current replay position, with both times on it."""
    session = _session_or_404(request, session_id)
    if session.current_step is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this session has not stepped yet, so it has no current position",
        )
    stepped = session.state_at(session.current_step, underlying_id)
    return {
        "data": stepped_state_to_dict(stepped),
        "meta": {
            "run_id": session.context.run_id,
            "is_hindsight": session.context.is_hindsight,
            "build_context_id": session.context.build_context_id,
        },
    }
