"""
Snapshot collection job — the core of the background worker.

For each active CollectorJob row the scheduler calls `run_snapshot_job`.
The job:
  1. Checks market session (skip if closed).
  2. Resolves the active expiry (first/nearest expiry for the underlying).
  3. Fetches the Upstox option chain.
  4. Delegates to snapshot_service for idempotent upsert + delta computation.
  5. Updates the CollectorJob row with status/error.
  6. Publishes a collector_status_changed event via the outbox.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import UNDERLYING_INSTRUMENT_KEYS
from app.core.security import decrypt_token
from app.db.models.instrument import OptionExpiry
from app.db.models.operational import AuditLog, CollectorJob, OutboxEvent
from app.db.models.user import UpstoxAccount
from app.integrations.upstox.client import (
    UpstoxAuthError,
    UpstoxError,
    get_option_chain,
    get_option_expiries,
)
from app.services.market_session import get_bucket_timestamp, is_market_open
from app.services.snapshot_service import save_snapshot

logger = logging.getLogger(__name__)


async def _resolve_active_expiry(
    session: AsyncSession,
    access_token: str,
    underlying: str,
) -> date | None:
    """Return the nearest active expiry date for *underlying*."""
    instrument_key = UNDERLYING_INSTRUMENT_KEYS.get(underlying)
    if not instrument_key:
        return None

    try:
        expiry_strings = await get_option_expiries(access_token, instrument_key)
    except UpstoxError as exc:
        logger.warning("Could not fetch expiries for %s: %s", underlying, exc)
        return None

    today = datetime.now(timezone.utc).date()
    for es in sorted(expiry_strings):
        d = date.fromisoformat(es)
        if d >= today:
            return d
    return None


async def run_snapshot_job(
    session: AsyncSession,
    user_id: int,
    underlying: str,
    interval_min: int,
) -> None:
    """Execute a single snapshot collection cycle."""
    now = datetime.now(timezone.utc)

    if not is_market_open(now):
        logger.debug("Market closed — skipping %s %dm", underlying, interval_min)
        return

    bucket_ts = get_bucket_timestamp(now, interval_min)

    # Load active Upstox account
    acc_result = await session.execute(
        select(UpstoxAccount).where(
            UpstoxAccount.user_id == user_id,
            UpstoxAccount.is_active == True,
        )
    )
    account = acc_result.scalar_one_or_none()
    if account is None:
        logger.warning("No active Upstox account for user %d — skipping", user_id)
        await _update_job_status(session, user_id, underlying, interval_min, "failed", "No active Upstox account")
        return

    access_token = decrypt_token(account.access_token_enc)
    instrument_key = UNDERLYING_INSTRUMENT_KEYS[underlying]

    # Resolve current expiry
    expiry_date = await _resolve_active_expiry(session, access_token, underlying)
    if expiry_date is None:
        logger.warning("Could not resolve expiry for %s", underlying)
        await _update_job_status(session, user_id, underlying, interval_min, "skipped", "No expiry found")
        return

    # Fetch option chain
    try:
        chain_resp = await get_option_chain(access_token, instrument_key, expiry_date)
    except UpstoxAuthError:
        logger.error("Upstox auth expired for user %d — suspending collector", user_id)
        await _update_job_status(session, user_id, underlying, interval_min, "failed", "Auth expired")
        # Publish re-auth required event
        session.add(OutboxEvent(
            user_id=user_id,
            event_type="auth_required",
            payload={"reason": "token_expired", "underlying": underlying},
        ))
        await session.commit()
        return
    except UpstoxError as exc:
        logger.error("Option chain fetch failed for %s: %s", underlying, exc)
        await _update_job_status(session, user_id, underlying, interval_min, "failed", str(exc))
        return

    if not chain_resp.data:
        logger.warning("Empty option chain returned for %s on %s", underlying, expiry_date)
        await _update_job_status(session, user_id, underlying, interval_min, "skipped", "Empty chain")
        return

    # Derive spot price from first row that has it
    spot_price: float | None = None
    for row in chain_resp.data:
        if row.underlying_spot_price:
            spot_price = row.underlying_spot_price
            break

    # Persist snapshot (idempotent)
    try:
        snapshot = await save_snapshot(
            session=session,
            user_id=user_id,
            underlying=underlying,
            expiry_date=expiry_date,
            interval_min=interval_min,
            bucket_ts=bucket_ts,
            chain_rows=chain_resp.data,
            spot_price=spot_price,
        )
        await _update_job_status(session, user_id, underlying, interval_min, "success", None)
        logger.info("Snapshot %d saved for %s interval=%dm bucket=%s", snapshot.id, underlying, interval_min, bucket_ts)
    except Exception as exc:
        logger.error("Snapshot save failed for %s: %s", underlying, exc, exc_info=True)
        await _update_job_status(session, user_id, underlying, interval_min, "failed", str(exc))


async def _update_job_status(
    session: AsyncSession,
    user_id: int,
    underlying: str,
    interval_min: int,
    status: str,
    error: str | None,
) -> None:
    result = await session.execute(
        select(CollectorJob).where(
            CollectorJob.user_id == user_id,
            CollectorJob.underlying == underlying,
            CollectorJob.interval_min == interval_min,
        )
    )
    job = result.scalar_one_or_none()
    if job:
        job.last_run_at = datetime.now(timezone.utc)
        job.last_status = status
        job.last_error = error
        await session.commit()
