"""PostgreSQL observation repository.

`docs/design/02-DATA_MODEL.md` §3, §11. Implements the same contract as
`store/memory.py`, which is where the semantics are verified offline.

NOT EXECUTABLE IN THE DEVELOPMENT SANDBOX: requires `sqlalchemy`, `asyncpg` and a live
PostgreSQL. See the external verification checklist.

**Idempotency** is delegated to the database rather than to application logic. Each write
is `INSERT ... ON CONFLICT DO NOTHING` against the tiered partial unique indexes, so a
replay, a reconnect or a process restart produces no duplicate rows *even if two writers
race*. Checking-then-inserting in Python would be a time-of-check-to-time-of-use bug at
exactly the moment it matters — reconnect, when both the recovering REST fetch and the
resumed WS stream deliver the same instant.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from oipulse.core.timemode import TemporalBound
from oipulse.marketdata.observations import (
    GreeksObservation,
    HistoricalDailyOI,
    MarketObservation,
    QuoteObservation,
)
from oipulse.marketdata.store.memory import WriteResult
from oipulse.marketdata.store.schema import obs_greeks, obs_historical_oi, obs_quotes
from oipulse.persistence.repository import ResolvedBound, TemporalRepository, resolve_bound

__all__ = ["PostgresObservationRepository"]


def _json_safe(val: Any) -> Any:
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, dict):
        return {str(k): _json_safe(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_json_safe(v) for v in val]
    return val


def _identity_values(obs: MarketObservation) -> dict[str, Any]:
    i = obs.identity
    return {
        "instrument_id": int(obs.instrument_id),
        "observed_at": obs.observed_at,
        "ingested_at": obs.ingested_at,
        "source": obs.source.value,
        "identity_tier": i.tier.value,
        "identity_confidence": i.confidence.value,
        "provider_event_id": i.provider_event_id,
        "feed_session_id": i.feed_session_id,
        "channel": i.channel,
        "channel_sequence": i.channel_sequence,
        "content_digest": i.content_digest,
        "received_seq": i.received_seq,
        "supersedes_observation_id": obs.supersedes_observation_id,
        "raw_extra": _json_safe(obs.raw_extra) if obs.raw_extra else {},
    }


def _row_for(obs: MarketObservation) -> tuple[sa.Table, dict[str, Any]] | None:
    base = _identity_values(obs)
    if isinstance(obs, QuoteObservation):
        return obs_quotes, {
            **base,
            "ltp": obs.ltp,
            "bid": obs.bid,
            "ask": obs.ask,
            "bid_qty": obs.bid_qty,
            "ask_qty": obs.ask_qty,
            "volume": obs.volume,
            "oi": obs.oi,
            "provider_prev_oi": obs.provider_prev_oi,
            "prev_close": obs.prev_close,
        }
    if isinstance(obs, GreeksObservation):
        return obs_greeks, {
            **base,
            "iv": obs.iv,
            "delta": obs.delta,
            "gamma": obs.gamma,
            "theta": obs.theta,
            "vega": obs.vega,
            "rho": obs.rho,
        }
    if isinstance(obs, HistoricalDailyOI):
        return obs_historical_oi, {
            **base,
            "observation_kind": obs.kind.value,
            "observation_date": obs.observation_date,
            "valid_from": obs.valid_from,
            "valid_to": obs.valid_to,
            "oi": obs.oi,
            "close_spot": obs.close_spot,
        }
    return None


class PostgresObservationRepository(TemporalRepository[MarketObservation]):
    """Append-only, bitemporal, identity-keyed.

    Raw observations have no `available_at`, so availability semantics are refused.
    """

    supports_availability = False

    def __init__(self, connection: AsyncConnection) -> None:
        self._conn = connection

    async def append(self, observations: Iterable[MarketObservation]) -> WriteResult:
        """Insert a batch, suppressing duplicates in the database.

        Grouped per table so one round trip covers each kind; `RETURNING id` lets the
        inserted count be compared against the submitted count, which is what produces
        the `observation_duplicates_total` metric rather than an estimate of it.
        """
        batches: dict[sa.Table, list[dict[str, Any]]] = {}
        submitted = 0
        for obs in observations:
            resolved = _row_for(obs)
            if resolved is None:
                continue
            table, values = resolved
            batches.setdefault(table, []).append(values)
            submitted += 1

        inserted = 0
        for table, rows in batches.items():
            if not rows:
                continue
            # Two names, not one rebound: `.returning()` produces a `ReturningInsert`,
            # a different type from the `Insert` it was called on. Reusing the variable
            # made the statement's static type the pre-RETURNING one, which is how a
            # dropped RETURNING clause could have gone unnoticed -- and RETURNING is
            # what makes the inserted count exact rather than an estimate.
            insert_stmt = pg_insert(table).values(rows)
            # DO NOTHING across every identity tier at once: whichever partial unique
            # index the row falls under, a repeat is silently dropped.
            returning_stmt = insert_stmt.on_conflict_do_nothing().returning(table.c.id)
            result = await self._conn.execute(returning_stmt)
            inserted += len(result.fetchall())

        return WriteResult(inserted=inserted, duplicates=submitted - inserted)

    def _fetch(
        self, bound: ResolvedBound, **criteria: object
    ) -> Sequence[MarketObservation]:  # pragma: no cover - async variant is used
        raise NotImplementedError("use fetch_async; this repository is async")

    async def fetch_async(
        self, bound: TemporalBound, table: sa.Table, **criteria: object
    ) -> Sequence[Any]:
        """Read under a temporal bound.

        **Both** axes are always applied. Filtering on `observed_at` alone would be the
        market-truth reading and would leak late-arriving rows into knowledge queries.
        """
        resolved = resolve_bound(bound)
        if resolved.filters_availability:
            from oipulse.core.errors import TemporalBoundError

            raise TemporalBoundError(
                "raw observations have no available_at; use knowledge_at() or market_truth_at()"
            )

        query = sa.select(table).where(
            table.c.observed_at <= resolved.observed_at_max,
            table.c.ingested_at <= resolved.ingested_at_max,
        )
        if (instrument_id := criteria.get("instrument_id")) is not None:
            query = query.where(table.c.instrument_id == instrument_id)
        query = query.order_by(table.c.observed_at, table.c.id)

        result = await self._conn.execute(query)
        return result.fetchall()

    async def latest_async(
        self, bound: TemporalBound, table: sa.Table, instrument_id: int
    ) -> Any | None:
        rows = await self.fetch_async(bound, table, instrument_id=instrument_id)
        return rows[-1] if rows else None
