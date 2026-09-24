"""SYNTHETIC fixtures for Phase 4. Not recorded from Upstox.

States are constructed directly rather than assembled from observations where the test
only needs a shape, and assembled through the real Phase 3 builder where the test needs
genuine provenance (for availability). Both paths are used deliberately: the first
keeps formula tests readable, the second keeps availability tests honest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

from oipulse.analytics.context import ComputeContext, Params
from oipulse.marketstate.context import BuildContext
from oipulse.marketstate.staleness import QualityStatus
from oipulse.marketstate.state import (
    Coherence,
    CoherenceMode,
    ExpiryAggregates,
    ExpirySlice,
    FuturesLeg,
    MarketState,
    OptionLeg,
    Provenance,
    Quality,
    SessionPhase,
    SpotView,
    StateIdentity,
    Surfaces,
)

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

UNDERLYING = 100
EXPIRY_A = 10
EXPIRY_B = 11
BASE = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)

CONTEXT = BuildContext.create(staleness_policy_version="1.0.0", configuration={"fixture": "phase4"})


def at(minutes: float = 0) -> datetime:
    return BASE + timedelta(minutes=minutes)


def leg(
    strike: str,
    option_type: str,
    *,
    oi: int | None = 1000,
    ltp: str | None = "100",
    volume: int | None = 500,
    iv: str | None = "0.13",
    delta: str | None = None,
    gamma: str | None = "0.001",
    instrument_id: int = 0,
    provider_prev_oi: int | None = None,
) -> OptionLeg:
    return OptionLeg(
        instrument_id=instrument_id or (int(Decimal(strike)) + (0 if option_type == "call" else 1)),
        expiry_id=EXPIRY_A,
        strike=Decimal(strike),
        option_type=option_type,
        ltp=None if ltp is None else Decimal(ltp),
        bid=None if ltp is None else Decimal(ltp) - Decimal("0.5"),
        ask=None if ltp is None else Decimal(ltp) + Decimal("0.5"),
        volume=volume,
        oi=oi,
        provider_prev_oi=provider_prev_oi,
        iv=None if iv is None else Decimal(iv),
        delta=None if delta is None else Decimal(delta),
        gamma=None if gamma is None else Decimal(gamma),
        theta=Decimal("-8"),
        vega=Decimal("11"),
        quote_observed_at=BASE,
        greeks_observed_at=BASE,
    )


def expiry_slice(
    legs: tuple[OptionLeg, ...],
    *,
    expiry_id: int = EXPIRY_A,
    expiry_date: date = date(2026, 3, 5),
    missing: int = 0,
) -> ExpirySlice:
    return ExpirySlice(
        expiry_id=expiry_id,
        expiry_date=expiry_date,
        legs=tuple(legs),
        surfaces=Surfaces(
            oi_by_strike=MappingProxyType({x.strike: (x.oi, x.oi) for x in legs}),
            iv_by_strike=MappingProxyType({x.strike: (x.iv, x.iv) for x in legs}),
            gamma_by_strike=MappingProxyType({x.strike: (x.gamma, x.gamma) for x in legs}),
        ),
        aggregates=ExpiryAggregates(None, None, None),
        missing_leg_count=missing,
    )


def state(
    *,
    market_time: datetime | None = None,
    knowledge: datetime | None = None,
    spot: str | None = "25000",
    high: str | None = None,
    low: str | None = None,
    prev_close: str | None = "24900",
    expiries: tuple[ExpirySlice, ...] | None = None,
    futures: tuple[FuturesLeg, ...] = (),
    quality: QualityStatus = QualityStatus.OK,
    coverage: float = 1.0,
    ingested_at: datetime | None = None,
    context: BuildContext | None = None,
) -> MarketState:
    t = market_time or BASE
    k = knowledge or t
    return MarketState(
        identity=StateIdentity(UNDERLYING, t, k, (context or CONTEXT).id),
        session_phase=SessionPhase.OPEN,
        session_date=t.date(),
        spot=SpotView(
            ltp=None if spot is None else Decimal(spot),
            observed_at=t,
            age=timedelta(0),
            prev_close=None if prev_close is None else Decimal(prev_close),
            high=None if high is None else Decimal(high),
            low=None if low is None else Decimal(low),
        ),
        futures=futures,
        expiries=expiries if expiries is not None else (default_expiry(),),
        coherence=Coherence(mode=CoherenceMode.SNAPSHOT_ANCHORED, max_component_age=timedelta(0)),
        quality=Quality(status=quality, coverage_ratio=coverage, staleness_p95=timedelta(0)),
        provenance=Provenance(
            build_context=context or CONTEXT,
            observation_refs=("quote:1:" + t.isoformat(),),
            assembled_at=t,
            max_input_ingested_at=ingested_at or t,
        ),
    )


def default_expiry() -> ExpirySlice:
    """Three strikes, both sides, all values present. The baseline healthy chain."""
    legs = []
    for i, strike in enumerate(("24900", "25000", "25100")):
        legs.append(leg(strike, "call", oi=1000, instrument_id=200 + i * 2))
        legs.append(leg(strike, "put", oi=1000, instrument_id=201 + i * 2))
    return expiry_slice(tuple(legs))


def future(ltp: str = "25050", oi: int = 5000, instrument_id: int = 900) -> FuturesLeg:
    return FuturesLeg(
        instrument_id=instrument_id,
        expiry_id=EXPIRY_A,
        ltp=Decimal(ltp),
        oi=oi,
        volume=1000,
        observed_at=BASE,
        age=timedelta(0),
    )


def ctx(
    current: MarketState | None = None,
    *,
    history: tuple[MarketState, ...] = (),
    computed_at: datetime | None = None,
    **params: object,
) -> ComputeContext:
    s = current if current is not None else state()
    return ComputeContext(
        state=s,
        computed_at=computed_at or (s.identity.market_time + timedelta(seconds=1)),
        params=Params.of(**params),
        history=history,
    )
