"""Fill simulation — `10-REPLAY.md` §6.

> "A backtest's realism lives here, and every assumption is explicit and recorded on
> the run."

Three properties are load-bearing, and each one is a defence against a specific way
backtests lie.

**Nothing is assumed silently.** Brief §11 forbids hidden assumptions such as "fill at
next tick", "fill at close", "zero slippage", "zero fees". A `FillModel` therefore has
no default constructor: every parameter is named at the call site, and the whole
parameter set is content-addressed onto the run so a result can be read back against
the assumptions that produced it.

**A missing quote is not a zero spread.** Where the observation store has no bid/ask --
over a historical-OI-only backfill window, for instance (`06` §7) -- the model falls
back to a *declared* assumption and marks the fill `assumption_based`. §6 requires the
run to carry that flag onward "so its results are never read as equivalent to a run
over full-fidelity data". Quietly using the last traded price as if it were the mid is
how a backtest earns a spread it never paid.

**Nothing here is random.** Brief §13 requires an identical order and fill sequence
across runs, so rejection and partial sizing are deterministic functions of observable
state and of the intent's own content digest. Where a rate is expressed as a
probability, the draw comes from the intent id, not from `random` -- the same intent in
the same run always meets the same fate, and a rerun cannot shop for a better one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from oipulse.backtest.costs import CostBreakdown, CostModel
from oipulse.backtest.intents import OrderType, Side, TradeIntent
from oipulse.marketstate.state import MarketState, OptionLeg

__all__ = [
    "Fill",
    "FillModel",
    "FillOutcome",
    "PriceSource",
    "QuoteSnapshot",
    "RejectionReason",
    "SlippageModel",
    "simulate_fill",
]

_TICK = Decimal("0.05")


class SlippageModel(StrEnum):
    """The four models named in `10` §6. No "none" -- zero slippage is expressible as
    `FRACTION_OF_SPREAD` with a zero fraction, which at least says so out loud."""

    MID = "MID"
    TOUCH = "TOUCH"
    FRACTION_OF_SPREAD = "FRACTION_OF_SPREAD"
    SIZE_IMPACT = "SIZE_IMPACT"


class PriceSource(StrEnum):
    """Where the reference price came from. Recorded per fill, never inferred later."""

    OBSERVED_QUOTE = "OBSERVED_QUOTE"
    #: Last traded price with an *assumed* spread wrapped around it.
    ASSUMED_SPREAD_AROUND_LTP = "ASSUMED_SPREAD_AROUND_LTP"


class RejectionReason(StrEnum):
    NO_PRICE_AVAILABLE = "NO_PRICE_AVAILABLE"
    LIMIT_NOT_MARKETABLE = "LIMIT_NOT_MARKETABLE"
    NO_LIQUIDITY = "NO_LIQUIDITY"
    STALE_QUOTE = "STALE_QUOTE"
    MODELLED_REJECTION = "MODELLED_REJECTION"


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    """The price picture the fill is computed from, and where each part came from."""

    bid: Decimal | None
    ask: Decimal | None
    ltp: Decimal | None
    volume: int | None
    source: PriceSource
    stale: bool

    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class Fill:
    """A simulated fill. Carries its own assumptions, not just its price."""

    intent_id: str
    instrument_id: int
    side: Side
    quantity: int
    price: Decimal
    #: Market time the fill is attributed to: decision_time + modelled latency.
    filled_at: datetime
    price_source: PriceSource
    slippage_model: SlippageModel
    #: Reference price before slippage, so slippage paid is recoverable.
    reference_price: Decimal
    costs: CostBreakdown
    #: True when any input to this price was assumed rather than observed.
    assumption_based: bool
    #: Set when the intent could not be filled in full.
    requested_quantity: int = 0

    @property
    def slippage_per_unit(self) -> Decimal:
        """Signed against the trader: positive means the fill was worse than reference."""
        return (self.price - self.reference_price) * self.side.sign

    @property
    def turnover(self) -> Decimal:
        return self.price * Decimal(self.quantity)

    @property
    def is_partial(self) -> bool:
        return self.requested_quantity > self.quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "requested_quantity": self.requested_quantity,
            "price": str(self.price),
            "reference_price": str(self.reference_price),
            "slippage_per_unit": str(self.slippage_per_unit),
            "filled_at": self.filled_at.isoformat(),
            "price_source": self.price_source.value,
            "slippage_model": self.slippage_model.value,
            "assumption_based": self.assumption_based,
            "is_partial": self.is_partial,
            "costs": self.costs.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class FillOutcome:
    """A fill, a rejection, or a partial fill -- never an exception.

    A rejection is a legitimate simulation result that the ledger and the result must
    both see; raising would make it invisible in the statistics, which is exactly
    where a strategy's unfillable orders need to show up.
    """

    intent_id: str
    fill: Fill | None
    rejection: RejectionReason | None
    detail: str = ""

    @property
    def filled(self) -> bool:
        return self.fill is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "filled": self.filled,
            "fill": None if self.fill is None else self.fill.as_dict(),
            "rejection": None if self.rejection is None else self.rejection.value,
            "detail": self.detail,
        }


def _round_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        return price
    return (price / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick


def _unit_draw(intent_id: str, salt: str) -> Decimal:
    """A deterministic value in [0, 1) derived from the intent.

    Used where `10` §6 expresses a condition as a probability. This is not randomness:
    the same intent in the same run always draws the same number, so a rerun produces
    an identical fill sequence (brief §13) and cannot be repeated until it succeeds.
    """
    digest = hashlib.sha256(f"{salt}:{intent_id}".encode()).hexdigest()[:12]
    return Decimal(int(digest, 16)) / Decimal(16**12)


@dataclass(frozen=True, slots=True)
class FillModel:
    """The declared execution assumption set. Every field is required.

    `assumed_spread_fraction` is the width, as a fraction of the reference price, that
    the model invents when no bid/ask was observed. It is not a fallback detail: any
    fill that uses it is flagged, and the flag propagates to the run.
    """

    name: str
    version: int
    #: Intent -> venue. The fill is attributed to `decision_time + latency`.
    latency: timedelta
    slippage_model: SlippageModel
    #: Meaning depends on `slippage_model`: the fraction of the spread paid
    #: (FRACTION_OF_SPREAD), or the impact coefficient (SIZE_IMPACT).
    slippage_parameter: Decimal
    #: Assumed half-spread as a fraction of price when no quote was observed.
    assumed_spread_fraction: Decimal
    partial_fills_enabled: bool
    #: Cap on one fill as a fraction of the observed volume at the step.
    max_participation_rate: Decimal
    #: Deterministic rejection rate, drawn from the intent id. Zero disables it.
    rejection_rate: Decimal
    #: Reject rather than fill against a quote the state marked stale.
    reject_on_stale_quote: bool
    cost_model: CostModel
    tick_size: Decimal = _TICK
    #: Off by default, per `10` §6 and its stated limit.
    market_impact_enabled: bool = False
    _digest: str = field(default="", compare=False, repr=False)

    @property
    def label(self) -> str:
        return f"{self.name}@v{self.version}"

    def assumptions(self) -> dict[str, Any]:
        """The assumption set `BacktestResult` prints alongside every number (§6)."""
        return {
            "name": self.name,
            "version": self.version,
            "latency_seconds": self.latency.total_seconds(),
            "slippage_model": self.slippage_model.value,
            "slippage_parameter": str(self.slippage_parameter),
            "assumed_spread_fraction": str(self.assumed_spread_fraction),
            "partial_fills_enabled": self.partial_fills_enabled,
            "max_participation_rate": str(self.max_participation_rate),
            "rejection_rate": str(self.rejection_rate),
            "reject_on_stale_quote": self.reject_on_stale_quote,
            "tick_size": str(self.tick_size),
            "market_impact_enabled": self.market_impact_enabled,
            "cost_model": self.cost_model.as_dict(),
        }

    def content_digest(self) -> str:
        import json

        return hashlib.sha256(
            json.dumps(self.assumptions(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:32]

    # ------------------------------------------------------------------ pricing

    def quote_for(self, leg: OptionLeg) -> QuoteSnapshot:
        """Read a quote out of the state, declaring an assumption only when forced.

        Real bid/ask is used where the observation store has it (§6). Where it does
        not, a spread is assumed around the last traded price and the snapshot says
        so -- the caller cannot accidentally treat the two cases alike, because the
        distinction is in the returned value rather than in a comment.
        """
        if leg.bid is not None and leg.ask is not None and leg.ask >= leg.bid:
            return QuoteSnapshot(
                bid=leg.bid,
                ask=leg.ask,
                ltp=leg.ltp,
                volume=leg.volume,
                source=PriceSource.OBSERVED_QUOTE,
                stale=leg.quote_stale,
            )
        if leg.ltp is None:
            return QuoteSnapshot(
                bid=None,
                ask=None,
                ltp=None,
                volume=leg.volume,
                source=PriceSource.ASSUMED_SPREAD_AROUND_LTP,
                stale=leg.quote_stale,
            )
        half = leg.ltp * self.assumed_spread_fraction / Decimal(2)
        return QuoteSnapshot(
            bid=leg.ltp - half,
            ask=leg.ltp + half,
            ltp=leg.ltp,
            volume=leg.volume,
            source=PriceSource.ASSUMED_SPREAD_AROUND_LTP,
            stale=leg.quote_stale,
        )

    def reference_and_fill_price(
        self, quote: QuoteSnapshot, side: Side, quantity: int
    ) -> tuple[Decimal, Decimal] | None:
        """`(reference, fill)` under the declared slippage model, or None if unpriceable.

        The reference is always the mid, so "slippage paid" means the same thing across
        models and the four are comparable. Slippage is always applied *against* the
        trader -- a model that could improve a price would be a model of luck.
        """
        bid, ask = quote.bid, quote.ask
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / Decimal(2)
        spread = ask - bid

        match self.slippage_model:
            case SlippageModel.MID:
                raw = mid
            case SlippageModel.TOUCH:
                # Cross the spread: buy the ask, sell the bid.
                raw = ask if side is Side.BUY else bid
            case SlippageModel.FRACTION_OF_SPREAD:
                raw = mid + spread * self.slippage_parameter * side.sign
            case SlippageModel.SIZE_IMPACT:
                # Half-spread plus a term growing with size relative to observed
                # volume. With no volume observed there is no basis for the size
                # term, so it degrades to a touch fill rather than guessing.
                base = spread / Decimal(2)
                if quote.volume:
                    participation = Decimal(quantity) / Decimal(quote.volume)
                    impact = spread * self.slippage_parameter * participation
                else:
                    impact = Decimal(0)
                raw = mid + (base + impact) * side.sign

        return mid, _round_to_tick(raw, self.tick_size)

    def fillable_quantity(self, requested: int, quote: QuoteSnapshot) -> int:
        """Size vs liquidity (§6). Returns 0 when nothing can be filled.

        With partial fills disabled the intent is all-or-nothing, which is the
        stricter reading and the one that does not silently shrink a strategy's
        position. With no observed volume there is no liquidity evidence either way,
        so the request stands as made rather than being capped by a guess.
        """
        if not self.partial_fills_enabled or not quote.volume:
            return requested
        cap = int(Decimal(quote.volume) * self.max_participation_rate)
        return min(requested, max(cap, 0))


def simulate_fill(
    intent: TradeIntent,
    state: MarketState,
    model: FillModel,
) -> FillOutcome:
    """Simulate one intent against the state at its execution step. Pure.

    `state` must be the state at or after `intent.decision_time + model.latency` --
    selecting it is the runner's job, because only the runner knows the timeline. This
    function never reaches for a state of its own, which is what keeps it incapable of
    look-ahead: it can only see what it was handed.
    """
    leg = _find_leg(state, intent.instrument_id)
    if leg is None:
        return FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.NO_PRICE_AVAILABLE,
            detail=f"instrument {intent.instrument_id} is not in the state at {state.market_time}",
        )

    quote = model.quote_for(leg)
    if quote.bid is None or quote.ask is None:
        return FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.NO_PRICE_AVAILABLE,
            detail="no observed quote and no last traded price to assume a spread around",
        )

    if quote.stale and model.reject_on_stale_quote:
        return FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.STALE_QUOTE,
            detail="the state marked this quote stale and the model refuses stale fills",
        )

    if model.rejection_rate > 0 and _unit_draw(intent.intent_id, "reject") < model.rejection_rate:
        return FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.MODELLED_REJECTION,
            detail=f"deterministic draw below the declared rejection rate {model.rejection_rate}",
        )

    quantity = model.fillable_quantity(intent.quantity, quote)
    if quantity <= 0:
        return FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.NO_LIQUIDITY,
            detail="observed volume supports no fill at the declared participation rate",
        )

    priced = model.reference_and_fill_price(quote, intent.side, quantity)
    if priced is None:  # pragma: no cover - guarded by the bid/ask check above
        return FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.NO_PRICE_AVAILABLE,
            detail="the slippage model could not price this quote",
        )
    reference, price = priced

    # A limit order fills only at its price or better. Marketability is decided
    # against the *fill* price, after slippage, because that is the price that would
    # actually have been paid.
    if intent.order_type is OrderType.LIMIT and intent.limit_price is not None:
        marketable = (
            price <= intent.limit_price if intent.side is Side.BUY else price >= intent.limit_price
        )
        if not marketable:
            return FillOutcome(
                intent_id=intent.intent_id,
                fill=None,
                rejection=RejectionReason.LIMIT_NOT_MARKETABLE,
                detail=f"fill price {price} is beyond the limit {intent.limit_price}",
            )
        price = intent.limit_price

    costs = model.cost_model.charge(
        turnover=price * Decimal(quantity), is_buy=intent.side is Side.BUY
    )

    return FillOutcome(
        intent_id=intent.intent_id,
        fill=Fill(
            intent_id=intent.intent_id,
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=quantity,
            requested_quantity=intent.quantity,
            price=price,
            reference_price=reference,
            filled_at=intent.decision_time + model.latency,
            price_source=quote.source,
            slippage_model=model.slippage_model,
            costs=costs,
            assumption_based=quote.source is PriceSource.ASSUMED_SPREAD_AROUND_LTP,
        ),
        rejection=None,
    )


def _find_leg(state: MarketState, instrument_id: int) -> OptionLeg | None:
    for expiry in state.expiries:
        for leg in expiry.legs:
            if int(leg.instrument_id) == instrument_id:
                return leg
    return None
