"""APScheduler-based collector scheduler.

The scheduler runs as a separate process (`python run_worker.py`).
It shares the PostgreSQL database and Redis instance with the API server.

Scheduling strategy:
  - Every minute: query active CollectorJob rows from DB.
  - For each job, if `now` is at a new bucket boundary for that interval,
    dispatch run_snapshot_job.
  - The snapshot_service layer applies an additional Redis distributed lock
    so concurrent or duplicate triggers don't produce duplicate rows.

The scheduler also runs:
  - Outbox poller every 2s to flush pending events to Redis/SSE.
  - Recovery check on startup to catch missed buckets after a restart.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db.engine import get_session_factory
from app.db.models.operational import CollectorJob
from app.services.event_bus import flush_outbox
from app.services.market_session import get_bucket_timestamp, is_market_open, missed_buckets
from worker.jobs.snapshot_job import run_snapshot_job

logger = logging.getLogger(__name__)


async def _dispatch_active_jobs() -> None:
    """Called every minute — fires snapshot jobs that are due."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(CollectorJob).where(CollectorJob.is_running == True)
        )
        jobs: list[CollectorJob] = list(result.scalars())

    now = datetime.now(timezone.utc)
    for job in jobs:
        current_bucket = get_bucket_timestamp(now, job.interval_min)
        last_bucket = get_bucket_timestamp(job.last_run_at, job.interval_min) if job.last_run_at else None

        if last_bucket is not None and current_bucket == last_bucket:
            continue  # Already collected this bucket

        asyncio.create_task(
            _safe_run_job(job.user_id, job.underlying, job.interval_min)
        )


async def _safe_run_job(user_id: int, underlying: str, interval_min: int) -> None:
    """Wrapper that catches and logs exceptions so the scheduler keeps running."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            await run_snapshot_job(session, user_id, underlying, interval_min)
    except Exception as exc:
        logger.error(
            "Unhandled error in snapshot job user=%d underlying=%s interval=%dm: %s",
            user_id, underlying, interval_min, exc, exc_info=True,
        )


async def _flush_outbox_task() -> None:
    factory = get_session_factory()
    try:
        async with factory() as session:
            await flush_outbox(session)
    except Exception as exc:
        logger.warning("Outbox flush error: %s", exc)


async def _run_catch_up() -> None:
    """On worker start, back-fill any buckets missed while the process was down."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(CollectorJob).where(CollectorJob.is_running == True)
        )
        jobs: list[CollectorJob] = list(result.scalars())

    now = datetime.now(timezone.utc)
    for job in jobs:
        if not is_market_open(now):
            break
        missed = missed_buckets(job.last_run_at, job.interval_min, now)
        for bucket in missed[-3:]:  # cap catch-up to last 3 missed buckets
            logger.info(
                "Catch-up: user=%d %s %dm bucket=%s",
                job.user_id, job.underlying, job.interval_min, bucket,
            )
            await _safe_run_job(job.user_id, job.underlying, job.interval_min)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    # Main collection tick — every 60 seconds
    scheduler.add_job(_dispatch_active_jobs, "interval", seconds=60, id="dispatch_jobs")
    # Outbox flush — every 2 seconds
    scheduler.add_job(_flush_outbox_task, "interval", seconds=2, id="flush_outbox")
    return scheduler
