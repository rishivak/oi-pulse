"""Redis-backed event bus with transactional outbox.

Flow:
  1. Within a DB transaction a service writes an OutboxEvent row (status=pending).
  2. The outbox poller (called from the worker every few seconds) picks up pending
     rows, publishes them to a Redis channel, and marks them as published.
  3. The FastAPI SSE endpoint reads from a per-user Redis subscription.

This two-phase approach prevents lost events if the process crashes between the
DB commit and the Redis publish.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Redis pub/sub channel prefix: events:{user_id}
_CHANNEL_PREFIX = "events"
_OUTBOX_POLL_INTERVAL = 2  # seconds


def _channel(user_id: int) -> str:
    return f"{_CHANNEL_PREFIX}:{user_id}"


def _build_redis() -> aioredis.Redis:
    settings = get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=True)


# ── Publish ───────────────────────────────────────────────────────────────────

async def publish_event(
    user_id: int,
    event_type: str,
    payload: dict,
    schema_version: int = 1,
) -> None:
    """Publish a single event to the user's Redis channel."""
    message = json.dumps({
        "event_type": event_type,
        "schema_version": schema_version,
        "user_id": user_id,
        "payload": payload,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    redis = _build_redis()
    try:
        await redis.publish(_channel(user_id), message)
    finally:
        await redis.aclose()


# ── Outbox poller ─────────────────────────────────────────────────────────────

async def flush_outbox(db_session) -> None:
    """
    Drain pending OutboxEvent rows and publish them to Redis.
    Called by the worker on a short loop.
    """
    from sqlalchemy import select, update
    from app.db.models.operational import OutboxEvent

    result = await db_session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending")
        .order_by(OutboxEvent.created_at)
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    rows: list[OutboxEvent] = list(result.scalars())
    if not rows:
        return

    redis = _build_redis()
    try:
        for row in rows:
            try:
                message = json.dumps({
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "schema_version": row.schema_version,
                    "user_id": row.user_id,
                    "payload": row.payload,
                    "ts": row.created_at.isoformat(),
                })
                await redis.publish(_channel(row.user_id), message)
                row.status = "published"
                row.processed_at = datetime.now(timezone.utc)
            except Exception as exc:
                logger.warning("Failed to publish outbox event %s: %s", row.event_id, exc)
                row.retry_count += 1
                row.last_error = str(exc)
                if row.retry_count >= 5:
                    row.status = "failed"
        await db_session.commit()
    finally:
        await redis.aclose()


# ── SSE subscription ──────────────────────────────────────────────────────────

async def subscribe_sse(user_id: int) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted strings for the given user.
    Used by the FastAPI streaming endpoint.
    """
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(_channel(user_id))

    try:
        # Send an initial "connected" event so the client knows the stream is live
        yield f"event: connected\ndata: {json.dumps({'user_id': user_id})}\n\n"

        async for message in pubsub.listen():
            if message["type"] == "message":
                data = message["data"]
                try:
                    parsed = json.loads(data)
                    event_type = parsed.get("event_type", "message")
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except json.JSONDecodeError:
                    yield f"data: {data}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(_channel(user_id))
        await redis.aclose()
