"""Bridge the legacy OI snapshot data into the v2 ``StateService`` contract.

This module lives in ``backend/app/`` deliberately. The ``oipulse/`` package boundary
forbids importing any ``backend/`` module (``tools/check_import_boundaries.py``), so the
adapter — which reads legacy SQLAlchemy models — must sit in ``backend/`` and implement
the ``StateService`` interface that the v2 API endpoint consults. It is the thinnest
possible glue: read the latest snapshot, map it to ``MarketState`` value objects, done.

Once the v2 ingestion pipeline has its own observation store, this adapter is retired.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.marketstate.context import BuildContext
from oipulse.marketstate.staleness import QualityStatus
from oipulse.marketstate.state import (
    Coherence,
    CoherenceMode,
    ExpiryAggregates,
    ExpirySlice,
    MarketState,
    OptionLeg,
    Provenance,
    Quality,
    SessionPhase,
    SpotView,
    StateIdentity,
    Surfaces,
)


# A fixed build context for the snapshot adapter — its output is marked as coming
# from the legacy bridge so it is distinguishable from a real v2 build.
_BRIDGE_CONTEXT = BuildContext.create(
    staleness_policy_version="legacy-bridge-1.0",
    configuration={"source": "oi_snapshots", "adapter": "SnapshotBackedStateService"},
)


def _session_phase(now: datetime) -> SessionPhase:
    """Approximate the NSE session phase from the wall clock (IST)."""
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    ist_now = now.astimezone(ist_offset)
    t = ist_now.time()
    from datetime import time as time_

    if t < time_(9, 0):
        return SessionPhase.PRE_OPEN
    if t < time_(9, 15):
        return SessionPhase.PRE_OPEN
    if t <= time_(15, 30):
        return SessionPhase.OPEN
    if t <= time_(16, 0):
        return SessionPhase.POST_CLOSE
    return SessionPhase.CLOSED


class SnapshotBackedStateService:
    """A ``StateService``-compatible adapter that reads the legacy ``oi_snapshots`` table.

    It implements the same ``get_state(underlying_id, market_time, knowledge_horizon)``
    method that ``oipulse/api/market_state.py`` calls, but instead of running the full
    v2 observation → builder pipeline, it reads the nearest legacy snapshot and maps it
    to a ``MarketState``.

    This is deliberately synchronous from the ``StateService`` caller's perspective:
    the v2 endpoint calls ``service.get_state(...)`` synchronously and the builder
    protocol is synchronous. We use a synchronous SQLAlchemy engine here to match.
    """

    def __init__(self, database_url: str) -> None:
        # Convert async URL (postgresql+asyncpg://) to sync (postgresql://)
        sync_url = database_url.replace("+asyncpg", "").replace("+aiopg", "")
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        self._engine = create_engine(sync_url, pool_pre_ping=True, pool_size=2)
        self._Session = sessionmaker(bind=self._engine)

    def get_state(
        self,
        underlying_id: InstrumentId,
        market_time: datetime,
        knowledge_horizon: datetime | None = None,
    ) -> MarketState:
        k = knowledge_horizon if knowledge_horizon is not None else market_time
        now = datetime.now(timezone.utc)

        from sqlalchemy import select, desc
        from sqlalchemy.orm import selectinload

        # Import legacy models — allowed because this file lives in backend/app/
        from app.db.models.snapshot import OISnapshot, OIStrikeSnapshot
        from app.db.models.instrument import OptionExpiry

        session = self._Session()
        try:
            # Find the latest snapshot for any underlying (underlying_id=1 → NIFTY by
            # convention in the legacy schema)
            underlying_map = {1: "NIFTY", 2: "BANKNIFTY", 3: "SENSEX"}
            underlying_name = underlying_map.get(int(underlying_id), "NIFTY")

            stmt = (
                select(OISnapshot)
                .where(OISnapshot.underlying == underlying_name)
                .options(selectinload(OISnapshot.strikes))
                .order_by(desc(OISnapshot.bucket_ts))
                .limit(1)
            )
            snapshot = session.execute(stmt).scalars().first()

            if snapshot is None:
                # No data at all — return an empty state rather than raising
                return self._empty_state(underlying_id, market_time, k, now)

            # Look up the expiry date from the option_expiries table
            expiry_row = session.execute(
                select(OptionExpiry).where(OptionExpiry.id == snapshot.expiry_id)
            ).scalars().first()
            expiry_date = expiry_row.expiry_date if expiry_row else date.today()

            return self._snapshot_to_state(
                snapshot, expiry_date, underlying_id, market_time, k, now
            )
        finally:
            session.close()

    def _empty_state(
        self,
        underlying_id: InstrumentId,
        market_time: datetime,
        knowledge_horizon: datetime,
        now: datetime,
    ) -> MarketState:
        identity = StateIdentity(
            underlying_id=underlying_id,
            market_time=market_time,
            knowledge_horizon=knowledge_horizon,
            build_context_id=_BRIDGE_CONTEXT.id,
        )
        return MarketState(
            identity=identity,
            session_phase=_session_phase(now),
            spot=SpotView(ltp=None, observed_at=None, age=None),
            futures=(),
            expiries=(),
            coherence=Coherence(mode=CoherenceMode.STREAM_ONLY, max_component_age=None),
            quality=Quality(status=QualityStatus.DEGRADED, coverage_ratio=0.0, staleness_p95=None),
            provenance=Provenance(
                build_context=_BRIDGE_CONTEXT,
                observation_refs=(),
                assembled_at=now,
                source_kinds=("legacy_snapshot",),
            ),
            session_date=market_time.date() if market_time else date.today(),
        )

    def _snapshot_to_state(
        self,
        snapshot: Any,
        expiry_date: date,
        underlying_id: InstrumentId,
        market_time: datetime,
        knowledge_horizon: datetime,
        now: datetime,
    ) -> MarketState:
        observed_at = snapshot.bucket_ts
        age = market_time - observed_at if observed_at else None

        spot = SpotView(
            ltp=Decimal(str(snapshot.spot_price)) if snapshot.spot_price is not None else None,
            observed_at=observed_at,
            age=age,
            stale=age is not None and age > timedelta(minutes=5),
        )

        # Build legs from strike snapshots
        legs: list[OptionLeg] = []
        oi_by_strike: dict[Decimal, tuple[int | None, int | None]] = {}
        iv_by_strike: dict[Decimal, tuple[Decimal | None, Decimal | None]] = {}
        total_call_oi = 0
        total_put_oi = 0
        atm_strike: Decimal | None = None
        min_diff: Decimal | None = None

        for ss in snapshot.strikes:
            strike_dec = Decimal(str(ss.strike))

            # Find ATM strike (closest to spot)
            if spot.ltp is not None:
                diff = abs(strike_dec - spot.ltp)
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    atm_strike = strike_dec

            # CE leg
            call_oi_val = int(ss.call_oi) if ss.call_oi is not None else None
            put_oi_val = int(ss.put_oi) if ss.put_oi is not None else None
            call_iv_val = Decimal(str(ss.call_iv)) if ss.call_iv is not None else None
            put_iv_val = Decimal(str(ss.put_iv)) if ss.put_iv is not None else None

            legs.append(OptionLeg(
                instrument_id=InstrumentId(int(underlying_id) * 10000 + int(strike_dec)),
                expiry_id=ExpiryId(snapshot.expiry_id),
                strike=strike_dec,
                option_type="CE",
                ltp=Decimal(str(ss.call_ltp)) if ss.call_ltp is not None else None,
                oi=call_oi_val,
                iv=call_iv_val,
                volume=int(ss.call_volume) if ss.call_volume is not None else None,
                quote_observed_at=observed_at,
                quote_age=age,
            ))
            legs.append(OptionLeg(
                instrument_id=InstrumentId(int(underlying_id) * 10000 + int(strike_dec) + 1),
                expiry_id=ExpiryId(snapshot.expiry_id),
                strike=strike_dec,
                option_type="PE",
                ltp=Decimal(str(ss.put_ltp)) if ss.put_ltp is not None else None,
                oi=put_oi_val,
                iv=put_iv_val,
                volume=int(ss.put_volume) if ss.put_volume is not None else None,
                quote_observed_at=observed_at,
                quote_age=age,
            ))

            oi_by_strike[strike_dec] = (call_oi_val, put_oi_val)
            iv_by_strike[strike_dec] = (call_iv_val, put_iv_val)

            if call_oi_val is not None:
                total_call_oi += call_oi_val
            if put_oi_val is not None:
                total_put_oi += put_oi_val

        # Sort legs by strike
        legs.sort(key=lambda l: (l.strike, l.option_type))

        surfaces = Surfaces(
            oi_by_strike=MappingProxyType(oi_by_strike),
            iv_by_strike=MappingProxyType(iv_by_strike),
            gamma_by_strike=MappingProxyType({}),
        )

        expiry_slice = ExpirySlice(
            expiry_id=ExpiryId(snapshot.expiry_id),
            expiry_date=expiry_date,
            legs=tuple(legs),
            surfaces=surfaces,
            aggregates=ExpiryAggregates(
                total_call_oi=total_call_oi or None,
                total_put_oi=total_put_oi or None,
                atm_strike=atm_strike,
            ),
        )

        identity = StateIdentity(
            underlying_id=underlying_id,
            market_time=market_time,
            knowledge_horizon=knowledge_horizon,
            build_context_id=_BRIDGE_CONTEXT.id,
        )

        data_age = now - observed_at if observed_at else None
        coverage = len(snapshot.strikes) / max(len(snapshot.strikes), 1)

        return MarketState(
            identity=identity,
            session_phase=_session_phase(now),
            spot=spot,
            futures=(),
            expiries=(expiry_slice,),
            coherence=Coherence(
                mode=CoherenceMode.SNAPSHOT_ANCHORED,
                max_component_age=data_age,
                chain_snapshot_ref=f"oi_snapshots:{snapshot.id}",
                anchor_observed_at=observed_at,
            ),
            quality=Quality(
                status=(
                    QualityStatus.OK
                    if data_age and data_age < timedelta(minutes=10)
                    else QualityStatus.WARNING
                ),
                coverage_ratio=coverage,
                staleness_p95=data_age,
            ),
            provenance=Provenance(
                build_context=_BRIDGE_CONTEXT,
                observation_refs=(f"oi_snapshots:{snapshot.id}",),
                assembled_at=now,
                source_kinds=("legacy_snapshot",),
                max_input_ingested_at=snapshot.created_at,
            ),
            session_date=observed_at.date() if observed_at else date.today(),
        )
""", "Description": "Bridge adapter that reads legacy OI snapshots and converts them to v2 MarketState objects. Lives in backend/app/ because it imports legacy SQLAlchemy models, which the oipulse/ boundary forbids."
