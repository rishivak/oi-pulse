"""Collector control endpoints — start/stop/status per user."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import DB, CurrentUser, has_live_market_access
from app.core.config import BucketInterval, SUPPORTED_UNDERLYINGS
from app.db.models.operational import CollectorJob

router = APIRouter(prefix="/api/collector", tags=["collector"])


class CollectorStartRequest(BaseModel):
    underlying: str
    interval_min: int = Field(default=5)

    @property
    def underlying_upper(self) -> str:
        return self.underlying.upper()


@router.post("/start")
async def start_collector(body: CollectorStartRequest, user: CurrentUser, db: DB):
    if body.underlying_upper not in SUPPORTED_UNDERLYINGS:
        raise HTTPException(400, f"Unsupported underlying: {body.underlying}")
    if body.interval_min not in list(BucketInterval):
        raise HTTPException(400, f"Unsupported interval: {body.interval_min}")

    if not await has_live_market_access(user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Live collection needs an active Upstox session.",
        )

    stmt = (
        pg_insert(CollectorJob)
        .values(
            user_id=user.id,
            underlying=body.underlying_upper,
            interval_min=body.interval_min,
            is_running=True,
        )
        .on_conflict_do_update(
            constraint="uq_collector_job",
            set_={"is_running": True},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "started", "underlying": body.underlying_upper, "interval_min": body.interval_min}


@router.post("/stop")
async def stop_collector(body: CollectorStartRequest, user: CurrentUser, db: DB):
    result = await db.execute(
        select(CollectorJob).where(
            CollectorJob.user_id == user.id,
            CollectorJob.underlying == body.underlying_upper,
            CollectorJob.interval_min == body.interval_min,
        )
    )
    job = result.scalar_one_or_none()
    if job:
        job.is_running = False
        await db.commit()
    return {"status": "stopped"}


@router.get("/status")
async def collector_status(user: CurrentUser, db: DB):
    result = await db.execute(
        select(CollectorJob).where(CollectorJob.user_id == user.id)
    )
    jobs = list(result.scalars())
    return {
        "jobs": [
            {
                "underlying": j.underlying,
                "interval_min": j.interval_min,
                "is_running": j.is_running,
                "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
                "last_status": j.last_status,
            }
            for j in jobs
        ]
    }
