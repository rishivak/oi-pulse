"""SSE streaming endpoint — one persistent connection per authenticated user."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import DB, CurrentUser
from app.services.event_bus import subscribe_sse

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/events")
async def stream_events(user: CurrentUser):
    """
    Server-Sent Events stream.  The browser connects once; the backend pushes
    events as they occur (snapshot_created, collector_status_changed, etc.).
    Nginx / reverse proxies must have proxy_buffering=off for this route.
    """
    return StreamingResponse(
        subscribe_sse(user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Nginx hint to disable buffering
            "Connection": "keep-alive",
        },
    )
