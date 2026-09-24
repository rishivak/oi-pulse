"""SYNTHETIC fixtures for Phase 3. Not recorded from Upstox.

Everything here is constructed, and the module name and this docstring say so. Recorded
provider captures live in `tests/fixtures/recorded/` and nothing in this file may be
described as one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from oipulse.core.clock import FrozenClock
from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.instruments.models import (
    Expiry,
    ExpiryType,
    Instrument,
    InstrumentType,
    OptionType,
)
from oipulse.marketdata.identity import resolve_identity
from oipulse.marketdata.observations import (
    GreeksObservation,
    IndexObservation,
    QuoteObservation,
    Source,
)
from oipulse.marketdata.store.memory import InMemoryObservationStore
from oipulse.marketstate.builder import StateBuilder
from oipulse.marketstate.universe import ExpiryUniverse, ResolvedUniverse, StaticUniverseResolver

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

UNDERLYING = InstrumentId(100)
SPOT_ID = InstrumentId(101)
EXPIRY_A = ExpiryId(10)
EXPIRY_B = ExpiryId(11)
STRIKES = (Decimal("24900"), Decimal("25000"), Decimal("25100"))

BASE = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)  # 11:30 IST, mid-session Tuesday


def at(minute: int = 0, second: int = 0) -> datetime:
    return BASE + timedelta(minutes=minute, seconds=second)


def clock(minute: int = 30) -> FrozenClock:
    return FrozenClock(at(minute))


def _identity(instrument_id: int, observed_at: datetime, source: str, payload: dict[str, object]):
    return resolve_identity(
        instrument_id=instrument_id,
        observed_at=observed_at,
        source=source,
        payload=payload,
        feed_session_id="synthetic-session-1",
    )


def index_obs(
    observed_at: datetime,
    ingested_at: datetime | None = None,
    ltp: str = "25000",
    instrument_id: InstrumentId = SPOT_ID,
) -> IndexObservation:
    ingested = ingested_at or observed_at
    return IndexObservation(
        instrument_id=instrument_id,
        observed_at=observed_at,
        ingested_at=ingested,
        source=Source.WS,
        identity=_identity(int(instrument_id), observed_at, "ws", {"ltp": ltp}),
        ltp=Decimal(ltp),
        prev_close=Decimal("24850"),
        open=Decimal("24900"),
    )


def quote_obs(
    instrument_id: int,
    observed_at: datetime,
    ingested_at: datetime | None = None,
    *,
    ltp: str = "120",
    oi: int = 450000,
    volume: int = 12000,
    provider_prev_oi: int | None = 440000,
    source: Source = Source.WS,
) -> QuoteObservation:
    ingested = ingested_at or observed_at
    return QuoteObservation(
        instrument_id=InstrumentId(instrument_id),
        observed_at=observed_at,
        ingested_at=ingested,
        source=source,
        identity=_identity(instrument_id, observed_at, source.value, {"ltp": ltp, "oi": oi}),
        ltp=Decimal(ltp),
        bid=Decimal(ltp) - Decimal("0.4"),
        ask=Decimal(ltp) + Decimal("0.4"),
        volume=volume,
        oi=oi,
        provider_prev_oi=provider_prev_oi,
    )


def greeks_obs(
    instrument_id: int,
    observed_at: datetime,
    ingested_at: datetime | None = None,
    *,
    iv: str = "0.1345",
    source: Source = Source.WS,
) -> GreeksObservation:
    ingested = ingested_at or observed_at
    return GreeksObservation(
        instrument_id=InstrumentId(instrument_id),
        observed_at=observed_at,
        ingested_at=ingested,
        source=source,
        identity=_identity(instrument_id, observed_at, source.value, {"iv": iv}),
        iv=Decimal(iv),
        delta=Decimal("0.52"),
        gamma=Decimal("0.0031"),
        theta=Decimal("-8.4"),
        vega=Decimal("11.2"),
    )


def leg_id(expiry_index: int, strike_index: int, is_call: bool) -> int:
    """Stable synthetic instrument id, so tests can name a leg without a lookup."""
    return 1000 + expiry_index * 100 + strike_index * 2 + (0 if is_call else 1)


def _legs(expiry_id: ExpiryId, expiry_index: int) -> tuple[Instrument, ...]:
    out: list[Instrument] = []
    for strike_index, strike in enumerate(STRIKES):
        for is_call in (True, False):
            out.append(
                Instrument(
                    id=InstrumentId(leg_id(expiry_index, strike_index, is_call)),
                    instrument_type=InstrumentType.OPTION,
                    underlying_id=UNDERLYING,
                    expiry_id=expiry_id,
                    strike=strike,
                    option_type=OptionType.CALL if is_call else OptionType.PUT,
                )
            )
    return tuple(out)


def universe(*, expiries: int = 1, subscribed: bool = False) -> ResolvedUniverse:
    entries = []
    for index, (expiry_id, day) in enumerate(
        ((EXPIRY_A, date(2026, 3, 5)), (EXPIRY_B, date(2026, 3, 12)))[:expiries]
    ):
        entries.append(
            ExpiryUniverse(
                expiry=Expiry(
                    id=expiry_id,
                    underlying_id=UNDERLYING,
                    expiry_date=day,
                    expiry_type=ExpiryType.WEEKLY,
                ),
                legs=_legs(expiry_id, index),
            )
        )
    return ResolvedUniverse(
        underlying_id=UNDERLYING,
        spot_instrument_id=SPOT_ID,
        expiries=tuple(entries),
        subscribed_expiry_ids=(
            frozenset(int(e.expiry_id) for e in entries) if subscribed else frozenset()
        ),
    )


def resolver(**kwargs: object) -> StaticUniverseResolver:
    r = StaticUniverseResolver()
    r.register(universe(**kwargs))  # type: ignore[arg-type]
    return r


def populated_store(
    observed: datetime,
    ingested: datetime | None = None,
    *,
    expiries: int = 1,
) -> InMemoryObservationStore:
    """A store with spot plus a full chain, all at one instant."""
    store = InMemoryObservationStore()
    rows: list[object] = [index_obs(observed, ingested)]
    for index in range(expiries):
        for strike_index in range(len(STRIKES)):
            for is_call in (True, False):
                iid = leg_id(index, strike_index, is_call)
                rows.append(quote_obs(iid, observed, ingested))
                rows.append(greeks_obs(iid, observed, ingested))
    store.append(rows)  # type: ignore[arg-type]
    return store


def builder(
    store: InMemoryObservationStore,
    *,
    expiries: int = 1,
    subscribed: bool = False,
    now_minute: int = 30,
    **kwargs: object,
) -> StateBuilder:
    return StateBuilder(
        store,
        resolver(expiries=expiries, subscribed=subscribed),
        clock(now_minute),
        **kwargs,  # type: ignore[arg-type]
    )
