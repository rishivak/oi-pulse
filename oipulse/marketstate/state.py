"""The `MarketState` value object.

`docs/design/04-MARKETSTATE.md` §1, §2 and §6.

Immutable after construction, everywhere and at every depth: every dataclass here is
`frozen=True, slots=True` and every collection is a tuple or a `MappingProxyType`. That
is not decoration. A consumer who can mutate a state can make two holders of "the same"
state disagree, which destroys the determinism property the whole architecture rests on
and does so silently. If a modified state is needed, build a new one.

Every leaf carries its own `observed_at` and a derived `age`, so a consumer can always
ask how fresh any individual number is. There is deliberately no flattening that hides
it (`04` §2).

**Missing stays missing.** Every observed value is `| None`. Absence is never rendered
as zero: a strike with no OI observation and a strike with genuinely zero OI are
different facts, and collapsing them corrupts every aggregate computed downstream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.dataquality.issues import QualityIssue
from oipulse.marketstate.context import BuildContext
from oipulse.marketstate.staleness import QualityStatus

__all__ = [
    "Coherence",
    "CoherenceMode",
    "ExpiryAggregates",
    "ExpirySlice",
    "FuturesLeg",
    "MarketState",
    "OptionLeg",
    "Provenance",
    "Quality",
    "SessionPhase",
    "SpotView",
    "StateIdentity",
    "Surfaces",
]


class SessionPhase(StrEnum):
    PRE_OPEN = "pre_open"
    OPEN = "open"
    CLOSED = "closed"
    POST_CLOSE = "post_close"


class CoherenceMode(StrEnum):
    """Which consistency regime produced this state (`04` §4).

    The distinction a consumer must be able to draw is between "these legs were
    mutually consistent" and "these legs are each individually fresh but were never
    observed together". An arbitrary collection of independent WebSocket messages is
    not a synchronized snapshot, and this enum is what stops it being presented as one.
    """

    #: Built on a recent REST chain snapshot, with WS ticks merged forward.
    SNAPSHOT_ANCHORED = "snapshot_anchored"
    #: Built purely from WS ticks. No cross-sectional guarantee whatsoever.
    STREAM_ONLY = "stream_only"
    #: Anchored on a snapshot now older than the anchor budget.
    SNAPSHOT_STALE = "snapshot_stale"
    #: A gap was detected and REST recovery is in flight.
    RECOVERING = "recovering"

    @property
    def is_cross_sectional(self) -> bool:
        """True only when the legs were observed together by the venue."""
        return self is CoherenceMode.SNAPSHOT_ANCHORED


@dataclass(frozen=True, slots=True)
class StateIdentity:
    """`(underlying_id, market_time, knowledge_horizon, build_context_id)` — `04` §1.

    All four are identity. `knowledge_horizon` in particular: a key omitting it would
    collapse `MarketState(NIFTY, 11:42, K=11:42)` and `MarketState(NIFTY, 11:42,
    K=11:50)`, which are different and equally valid states because late data may have
    arrived between them.

    `decision_time` is deliberately absent. It is a consumer/action parameter, not part
    of state identity and not a stored field (`05` §2).
    """

    underlying_id: InstrumentId
    market_time: datetime
    knowledge_horizon: datetime
    build_context_id: str

    def as_key(self) -> tuple[int, str, str, str]:
        """Hashable exact-match key. Used for checkpoint lookup, which is never fuzzy."""
        return (
            int(self.underlying_id),
            self.market_time.isoformat(),
            self.knowledge_horizon.isoformat(),
            self.build_context_id,
        )

    def describe(self) -> str:
        return (
            f"MarketState(underlying={int(self.underlying_id)}, "
            f"T={self.market_time.isoformat()}, K={self.knowledge_horizon.isoformat()}, "
            f"B={self.build_context_id})"
        )


@dataclass(frozen=True, slots=True)
class SpotView:
    """Underlying spot. `age` is `market_time - observed_at`, never wall-clock derived."""

    ltp: Decimal | None
    observed_at: datetime | None
    age: timedelta | None
    prev_close: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    stale: bool = False

    @property
    def change(self) -> Decimal | None:
        """`ltp - prev_close`, or None if either is missing. Not a formula in the
        analytics sense -- a difference of two observed values, kept here because §2
        lists `change` as part of the spot view."""
        if self.ltp is None or self.prev_close is None:
            return None
        return self.ltp - self.prev_close


@dataclass(frozen=True, slots=True)
class FuturesLeg:
    instrument_id: InstrumentId
    expiry_id: ExpiryId | None
    ltp: Decimal | None
    oi: int | None
    volume: int | None
    observed_at: datetime | None
    age: timedelta | None
    stale: bool = False

    def basis(self, spot: Decimal | None) -> Decimal | None:
        """`ltp - spot`. None when either side is absent, never zero."""
        if self.ltp is None or spot is None:
            return None
        return self.ltp - spot


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One option contract's observed values at `market_time`.

    `provider_prev_oi` is the provider's own assertion, stored because it is itself a
    raw observation of what the provider said. It is **not** OI Pulse's previous OI,
    which is always reconstructed by point-in-time lookup and never stored (`01` §4).
    """

    instrument_id: InstrumentId
    expiry_id: ExpiryId
    strike: Decimal
    option_type: str
    ltp: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: int | None = None
    oi: int | None = None
    provider_prev_oi: int | None = None
    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    quote_observed_at: datetime | None = None
    greeks_observed_at: datetime | None = None
    quote_age: timedelta | None = None
    greeks_age: timedelta | None = None
    quote_stale: bool = False
    oi_stale: bool = False
    greeks_stale: bool = False

    @property
    def has_quote(self) -> bool:
        return self.quote_observed_at is not None

    @property
    def has_greeks(self) -> bool:
        return self.greeks_observed_at is not None


@dataclass(frozen=True, slots=True)
class Surfaces:
    """Reorganisations of observed values. **No formula is applied** (`04` §6).

    Stored as read-only mappings keyed by strike so a consumer cannot mutate the state
    through a returned dict.
    """

    oi_by_strike: MappingProxyType[Decimal, tuple[int | None, int | None]]
    iv_by_strike: MappingProxyType[Decimal, tuple[Decimal | None, Decimal | None]]
    gamma_by_strike: MappingProxyType[Decimal, tuple[Decimal | None, Decimal | None]]

    @staticmethod
    def empty() -> Surfaces:
        return Surfaces(
            oi_by_strike=MappingProxyType({}),
            iv_by_strike=MappingProxyType({}),
            gamma_by_strike=MappingProxyType({}),
        )


@dataclass(frozen=True, slots=True)
class ExpiryAggregates:
    """Sums and a ratio over observed legs.

    `pcr` is a ratio of two observed sums, listed in `04` §2 as part of the state. It is
    None when call OI is absent or zero rather than being reported as 0 or infinity --
    an undefined ratio is not a value.
    """

    total_call_oi: int | None
    total_put_oi: int | None
    atm_strike: Decimal | None

    @property
    def pcr(self) -> Decimal | None:
        if not self.total_call_oi or self.total_put_oi is None:
            return None
        return Decimal(self.total_put_oi) / Decimal(self.total_call_oi)


@dataclass(frozen=True, slots=True)
class ExpirySlice:
    expiry_id: ExpiryId
    expiry_date: date
    legs: tuple[OptionLeg, ...]
    surfaces: Surfaces
    aggregates: ExpiryAggregates
    #: Legs the universe expected but for which no observation was visible at (T, K).
    missing_leg_count: int = 0

    @property
    def coverage_ratio(self) -> float:
        expected = len(self.legs) + self.missing_leg_count
        return 1.0 if expected == 0 else len(self.legs) / expected


@dataclass(frozen=True, slots=True)
class Coherence:
    mode: CoherenceMode
    max_component_age: timedelta | None
    chain_snapshot_ref: str | None = None
    ws_merge_count: int = 0
    anchor_observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Quality:
    status: QualityStatus
    coverage_ratio: float
    staleness_p95: timedelta | None
    issues: tuple[QualityIssue, ...] = ()

    @property
    def is_usable_for_signals(self) -> bool:
        """An `UNRELIABLE` state is still built and stored -- suppressing it would hide
        the outage -- but signal evaluation is skipped (`04` §3)."""
        return self.status is not QualityStatus.UNRELIABLE


@dataclass(frozen=True, slots=True)
class Provenance:
    """How this state can be explained (`04` §2).

    `assembled_at` is recorded but is deliberately **excluded from the content digest**:
    two builds of the same state at different wall-clock moments are the same state, and
    including it would make the determinism property untestable.
    """

    build_context: BuildContext
    observation_refs: tuple[str, ...]
    assembled_at: datetime
    source_kinds: tuple[str, ...] = ()

    @property
    def build_context_id(self) -> str:
        return self.build_context.id


@dataclass(frozen=True, slots=True)
class MarketState:
    """One underlying's market picture at `(T, K)` under a build context."""

    identity: StateIdentity
    session_phase: SessionPhase
    spot: SpotView
    futures: tuple[FuturesLeg, ...]
    expiries: tuple[ExpirySlice, ...]
    coherence: Coherence
    quality: Quality
    provenance: Provenance
    session_date: date | None = None
    _digest: str = field(default="", compare=False, repr=False)

    # -------------------------------------------------------------- convenience

    @property
    def market_time(self) -> datetime:
        return self.identity.market_time

    @property
    def knowledge_horizon(self) -> datetime:
        return self.identity.knowledge_horizon

    @property
    def build_context_id(self) -> str:
        return self.identity.build_context_id

    def expiry(self, expiry_id: ExpiryId) -> ExpirySlice | None:
        for slice_ in self.expiries:
            if slice_.expiry_id == expiry_id:
                return slice_
        return None

    # ------------------------------------------------------------ determinism

    def content_digest(self) -> str:
        """Deterministic digest of everything that makes this state what it is.

        Two states built from the same observations, at the same `T` and `K`, under the
        same build context, must produce the same digest regardless of the order the
        observations arrived in or the moment assembly ran. `assembled_at` is therefore
        excluded -- it is a record of when we did the work, not of what the market was.
        """
        return hashlib.sha256(
            json.dumps(self.as_comparable(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def as_comparable(self) -> dict[str, Any]:
        """The digest input, exposed so a failing determinism test can be diffed."""
        return {
            "identity": list(self.identity.as_key()),
            "session_phase": self.session_phase.value,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "spot": {
                "ltp": _num(self.spot.ltp),
                "observed_at": _ts(self.spot.observed_at),
                "prev_close": _num(self.spot.prev_close),
                "open": _num(self.spot.open),
                "high": _num(self.spot.high),
                "low": _num(self.spot.low),
                "stale": self.spot.stale,
            },
            "futures": [
                {
                    "instrument_id": int(f.instrument_id),
                    "ltp": _num(f.ltp),
                    "oi": f.oi,
                    "volume": f.volume,
                    "observed_at": _ts(f.observed_at),
                    "stale": f.stale,
                }
                for f in self.futures
            ],
            "expiries": [
                {
                    "expiry_id": int(e.expiry_id),
                    "expiry_date": e.expiry_date.isoformat(),
                    "missing_leg_count": e.missing_leg_count,
                    "aggregates": {
                        "total_call_oi": e.aggregates.total_call_oi,
                        "total_put_oi": e.aggregates.total_put_oi,
                        "atm_strike": _num(e.aggregates.atm_strike),
                    },
                    "legs": [
                        {
                            "instrument_id": int(leg.instrument_id),
                            "strike": _num(leg.strike),
                            "option_type": leg.option_type,
                            "ltp": _num(leg.ltp),
                            "bid": _num(leg.bid),
                            "ask": _num(leg.ask),
                            "volume": leg.volume,
                            "oi": leg.oi,
                            "provider_prev_oi": leg.provider_prev_oi,
                            "iv": _num(leg.iv),
                            "delta": _num(leg.delta),
                            "gamma": _num(leg.gamma),
                            "theta": _num(leg.theta),
                            "vega": _num(leg.vega),
                            "quote_observed_at": _ts(leg.quote_observed_at),
                            "greeks_observed_at": _ts(leg.greeks_observed_at),
                            "quote_stale": leg.quote_stale,
                            "oi_stale": leg.oi_stale,
                            "greeks_stale": leg.greeks_stale,
                        }
                        for leg in e.legs
                    ],
                }
                for e in self.expiries
            ],
            "coherence": {
                "mode": self.coherence.mode.value,
                "chain_snapshot_ref": self.coherence.chain_snapshot_ref,
                "ws_merge_count": self.coherence.ws_merge_count,
                "anchor_observed_at": _ts(self.coherence.anchor_observed_at),
            },
            "quality": {
                "status": self.quality.status.value,
                "coverage_ratio": round(self.quality.coverage_ratio, 9),
                "issues": sorted(
                    f"{i.type.value}:{i.severity.value}:{i.instrument_id}"
                    for i in self.quality.issues
                ),
            },
            "provenance": {
                "build_context_id": self.provenance.build_context_id,
                "observation_refs": sorted(self.provenance.observation_refs),
                "source_kinds": sorted(self.provenance.source_kinds),
            },
        }


def _num(value: Decimal | None) -> str | None:
    """Decimals as strings: `float` would make 0.1 + 0.2 a source of digest drift."""
    return None if value is None else str(value)


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
