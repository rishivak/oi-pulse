"""Provider contracts.

`docs/design/06-UPSTOX_INTEGRATION.md` §1. Upstox sits behind these protocols so the
domain never depends on a vendor. Upstox remains the **sole** provider (CLAUDE.md rule
11); the abstraction exists to keep vendor shapes out of the domain, not to invite a
second one.

Only `MarketDataProvider` is defined in Phase 2. `BrokerAdapter` belongs to Phase 8-10
and is deliberately absent — declaring it now would be scope the phase has no way to
verify.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from oipulse.instruments.models import Expiry
from oipulse.marketdata.observations import (
    HistoricalDailyOI,
    MarketObservation,
    OHLCObservation,
)

__all__ = ["InstrumentDescriptor", "MarketDataProvider", "OptionChainSnapshot"]


class InstrumentDescriptor(Protocol):
    """A contract as the provider describes it, before canonical identity is assigned."""

    vendor_key: str
    symbol: str
    exchange: str
    lot_size: int


@runtime_checkable
class OptionChainSnapshot(Protocol):
    """A REST chain response: a **cross-sectional consistency set**.

    All legs as the venue reported them in one response, mutually consistent. Distinct
    from the tick stream, which carries no such guarantee (`04-MARKETSTATE.md` §4). The
    distinction is the whole reason this type exists separately from a list of
    observations.
    """

    underlying_id: int
    expiry_id: int
    observed_at: object
    ingested_at: object
    is_complete: bool
    legs: Sequence[MarketObservation]


@runtime_checkable
class MarketDataProvider(Protocol):
    """Everything the ingestion layer needs from a market-data vendor."""

    async def discover_instruments(
        self, underlying_vendor_key: str
    ) -> Sequence[InstrumentDescriptor]: ...

    async def fetch_expiries(self, underlying_vendor_key: str) -> Sequence[Expiry]: ...

    async def fetch_option_chain(
        self, underlying_vendor_key: str, expiry: date
    ) -> OptionChainSnapshot: ...

    async def fetch_quotes(self, vendor_keys: Sequence[str]) -> Sequence[MarketObservation]: ...

    async def fetch_historical_ohlc(
        self, vendor_key: str, interval: str, frm: date, to: date
    ) -> Sequence[OHLCObservation]: ...

    async def fetch_historical_oi(
        self, underlying_vendor_key: str, expiry: date, on: date
    ) -> Sequence[HistoricalDailyOI]:
        """Date-granular OI. Returns `HistoricalDailyOI`, never a quote.

        The return type enforces AD-26 at the contract level: a provider cannot hand
        back daily OI dressed as an intraday observation.
        """
        ...

    def subscribe(
        self, vendor_keys: Sequence[str], mode: str
    ) -> AsyncIterator[MarketObservation]: ...
