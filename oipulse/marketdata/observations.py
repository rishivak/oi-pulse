"""Canonical market observations — the atom of the system.

`docs/design/01-DOMAIN_MODEL.md` §4. Immutable, append-only, **canonical and un-owned**:
one observation is not duplicated because two users are viewing NIFTY. There is no
`user_id` here or anywhere downstream (`02-DATA_MODEL.md` §12).

Two timestamps, always distinct and never interchangeable (`05-DATA_LIFECYCLE_PIT.md` §2):

* `observed_at` — when the fact was true, per the venue.
* `ingested_at` — when OI Pulse received and persisted it.

`observed_at` is **never** used as a substitute for ingestion or availability time. A fact
true at 11:40 that reaches us at 11:44 is invisible to `knowledge_at(11:42)` precisely
because the two are kept apart.

`HistoricalDailyOI` is deliberately a separate type with **date granularity** rather than
an instant: the Upstox OI endpoint is date-based, and presenting a daily figure as a
precise intraday observation is a misrepresentation someone reads as truth later
(`19-DECISIONS.md` AD-26).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from oipulse.core.clock import ensure_utc
from oipulse.core.ids import InstrumentId
from oipulse.marketdata.identity import ObservationIdentity

__all__ = [
    "DepthLevel",
    "DepthObservation",
    "GreeksObservation",
    "HistoricalDailyOI",
    "IndexObservation",
    "MarketObservation",
    "OHLCObservation",
    "ObservationKind",
    "QuoteObservation",
    "Source",
]


class Source(StrEnum):
    """Where an observation came from. Distinguishing these is not cosmetic.

    A REST chain response carries a cross-sectional consistency guarantee that
    independently-arriving WS ticks do not (`04-MARKETSTATE.md` §4), and backfilled rows
    must remain separable from live ones forever.
    """

    WS = "ws"
    REST_CHAIN = "rest_chain"
    REST_QUOTE = "rest_quote"
    REST_HIST_OI = "rest_hist_oi"
    REST_OHLC = "rest_ohlc"


class ObservationKind(StrEnum):
    QUOTE = "quote"
    GREEKS = "greeks"
    DEPTH = "depth"
    OHLC = "ohlc"
    INDEX = "index"
    HISTORICAL_DAILY_OI = "historical_daily_oi"


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """Common envelope. Every variant carries identity and both time axes."""

    instrument_id: InstrumentId
    observed_at: datetime
    ingested_at: datetime
    source: Source
    identity: ObservationIdentity
    #: Correction chain. A venue revision is a NEW row pointing at the prior one, never
    #: a mutation, so `knowledge_at` before the correction still returns what we believed
    #: then (`05` §6).
    supersedes_observation_id: int | None = None
    #: Vendor fields we do not model yet. Preserved rather than dropped so a provider
    #: schema addition is never silently lost before we notice it (`06` §8).
    raw_extra: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", ensure_utc(self.observed_at))
        object.__setattr__(self, "ingested_at", ensure_utc(self.ingested_at))

    @property
    def kind(self) -> ObservationKind:  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def ingestion_lag_seconds(self) -> float:
        """`ingested_at - observed_at`. The single most important health metric (`16` §3).

        May be negative when the venue clock runs ahead of ours; that is recorded as a
        `CLOCK_SKEW` quality issue rather than clamped, because clamping would hide it.
        """
        return (self.ingested_at - self.observed_at).total_seconds()


@dataclass(frozen=True, slots=True)
class QuoteObservation(MarketObservation):
    ltp: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    volume: int | None = None
    oi: int | None = None
    #: The provider's own assertion of previous OI. Stored because it is itself a raw
    #: observation of what the provider said. Distinct from *our* previous OI, which is
    #: never stored and is always reconstructed from history (`01` §4).
    provider_prev_oi: int | None = None
    prev_close: Decimal | None = None

    def __post_init__(self) -> None:
        MarketObservation.__post_init__(self)
        for name in ("oi", "volume", "bid_qty", "ask_qty", "provider_prev_oi"):
            v = getattr(self, name)
            if v is not None and v < 0:
                raise ValueError(f"{name} must be non-negative, got {v}")

    @property
    def kind(self) -> ObservationKind:
        return ObservationKind.QUOTE


@dataclass(frozen=True, slots=True)
class GreeksObservation(MarketObservation):
    """Greeks are persisted in full.

    The legacy system parsed `delta`, `gamma`, `theta` and `vega` from the very same
    payload and discarded all of them, keeping only `iv`. That data is unrecoverable once
    the moment passes, and GEX was blocked on four missing columns rather than on a data
    source.
    """

    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None

    @property
    def kind(self) -> ObservationKind:
        return ObservationKind.GREEKS


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: int
    orders: int | None = None


@dataclass(frozen=True, slots=True)
class DepthObservation(MarketObservation):
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()

    @property
    def kind(self) -> ObservationKind:
        return ObservationKind.DEPTH


@dataclass(frozen=True, slots=True)
class OHLCObservation(MarketObservation):
    interval: str = "1d"
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: int | None = None
    oi: int | None = None

    @property
    def kind(self) -> ObservationKind:
        return ObservationKind.OHLC


@dataclass(frozen=True, slots=True)
class IndexObservation(MarketObservation):
    ltp: Decimal | None = None
    prev_close: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None

    @property
    def kind(self) -> ObservationKind:
        return ObservationKind.INDEX


@dataclass(frozen=True, slots=True)
class HistoricalDailyOI(MarketObservation):
    """Date-granular OI from the Upstox historical OI endpoint.

    **Not an instant.** The endpoint returns OI across strikes for an underlying, expiry
    and *date*. An explicit validity interval is carried so that no consumer can serve
    this as live intraday state at 11:23:17 (`05` §6, `06` §7, AD-26).

    `observed_at` is set to `valid_to` for ordering purposes only; the granularity claim
    is made by `observation_date` and the interval, not by that timestamp.
    """

    observation_date: date | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    oi: int | None = None
    close_spot: Decimal | None = None

    def __post_init__(self) -> None:
        MarketObservation.__post_init__(self)
        if self.observation_date is None:
            raise ValueError("HistoricalDailyOI requires observation_date")
        if self.valid_from is None or self.valid_to is None:
            raise ValueError(
                "HistoricalDailyOI requires an explicit valid_from/valid_to session "
                "interval; a daily figure must never be stored as a bare instant"
            )
        object.__setattr__(self, "valid_from", ensure_utc(self.valid_from))
        object.__setattr__(self, "valid_to", ensure_utc(self.valid_to))
        if self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")

    @property
    def kind(self) -> ObservationKind:
        return ObservationKind.HISTORICAL_DAILY_OI

    def covers_instant(self, at: datetime) -> bool:
        """Whether this daily figure's validity interval contains *at*.

        Used to answer "is there daily OI for this moment?" without ever implying the
        value was observed *at* that moment.
        """
        at = ensure_utc(at)
        assert self.valid_from is not None and self.valid_to is not None
        return self.valid_from <= at < self.valid_to
