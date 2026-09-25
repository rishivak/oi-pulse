"""Portfolio valuation — point-in-time correct, from the canonical pipeline only.

Phase 11 brief §9 and §10:

> Price inputs must come through the canonical market-data/MarketState pipeline. **Do
> not create a second price-data source.** ... Never silently substitute a future
> price.
>
> For a portfolio state at `market_time = T`, `knowledge_time = K`, only information
> available by K may be used. Do not treat valuation-at-T as synonymous with
> latest-known valuation.

So `value_positions` takes a `MarketState` — the Phase 3 object, already scoped to
`(T, K, BuildContext)` — and reads marks out of it. It fetches nothing. That is what
makes the point-in-time guarantee inherited rather than reimplemented: a state built
at K cannot contain a price ingested after K, because the Phase 3 builder would not
have included it.

### Five ways a valuation input can fail, and what each produces

Brief §9 lists them and requires the specified behaviour. There is no specified
*substitution* anywhere in the design, and `04-MARKETSTATE.md` §3 establishes the
pattern: an unreliable state is still built and stored, but consumers skip it rather
than compute from it.

| Condition | Result |
|---|---|
| no price for the instrument | that position is **unvalued** and named |
| the state is `UNRELIABLE` | the whole valuation is **refused** unless explicitly allowed |
| the leg's quote is marked stale | unvalued, and the reason recorded |
| contract economics unresolvable | unvalued (see `economics.py`) |
| a price ingested after K | **cannot occur** — the state never contains one |

An unvalued position is never treated as zero. A portfolio with unvalued positions
reports a partial valuation and says which positions are missing, because a total
that silently omitted a position would read as a smaller, safer book than the one
that exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.marketstate.staleness import QualityStatus
from oipulse.marketstate.state import MarketState
from oipulse.trading.portfolio.positions import PortfolioPosition

__all__ = [
    "MarkSource",
    "PositionValuation",
    "UnvaluedReason",
    "ValuationRefused",
    "ValuationResult",
    "marks_from_state",
    "value_positions",
]


class MarkSource(StrEnum):
    """Where a mark came from. Recorded per position, never inferred later."""

    #: Last traded price observed in the canonical state.
    OBSERVED_LTP = "OBSERVED_LTP"
    #: Mid of an observed two-sided quote.
    OBSERVED_MID = "OBSERVED_MID"


class UnvaluedReason(StrEnum):
    """Why a position could not be valued. Always one of these, never silence."""

    NO_PRICE_IN_STATE = "NO_PRICE_IN_STATE"
    STALE_QUOTE = "STALE_QUOTE"
    NO_CONTRACT_ECONOMICS = "NO_CONTRACT_ECONOMICS"


class ValuationRefused(Exception):
    """The whole valuation was refused, not merely incomplete.

    Distinct from a partial valuation: a partial one is a number with named gaps,
    this is the absence of a number. Raised when the state itself is unreliable, so
    a caller cannot mistake a refusal for a portfolio worth nothing.
    """


@dataclass(frozen=True, slots=True)
class PositionValuation:
    """One position, valued or explicitly not."""

    position: PortfolioPosition
    mark: Decimal | None = None
    mark_source: MarkSource | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    unvalued_reason: UnvaluedReason | None = None
    #: The leg's own observation time, kept apart from the valuation time.
    mark_observed_at: datetime | None = None

    @property
    def is_valued(self) -> bool:
        return self.market_value is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position.as_dict(),
            "mark": None if self.mark is None else str(self.mark),
            "mark_source": None if self.mark_source is None else self.mark_source.value,
            "market_value": None if self.market_value is None else str(self.market_value),
            "unrealized_pnl": (None if self.unrealized_pnl is None else str(self.unrealized_pnl)),
            "unvalued_reason": (
                None if self.unvalued_reason is None else self.unvalued_reason.value
            ),
            "mark_observed_at": (
                None if self.mark_observed_at is None else self.mark_observed_at.isoformat()
            ),
            "is_valued": self.is_valued,
        }


@dataclass(frozen=True, slots=True)
class ValuationResult:
    """The valued book at one `(market_time, knowledge_time)`.

    `is_complete` is the honesty flag. A caller reading `total_market_value` without
    it would treat a book with three unvalued positions as though it were fully
    priced, which understates exposure by exactly the amount nobody could see.
    """

    market_time: datetime
    knowledge_time: datetime
    build_context_id: str
    positions: tuple[PositionValuation, ...] = ()
    #: Quality of the state the marks came from. Carried, never recomputed.
    state_quality: str = "UNKNOWN"
    market_state_ref: str = ""

    @property
    def valued(self) -> tuple[PositionValuation, ...]:
        return tuple(p for p in self.positions if p.is_valued)

    @property
    def unvalued(self) -> tuple[PositionValuation, ...]:
        return tuple(p for p in self.positions if not p.is_valued)

    @property
    def is_complete(self) -> bool:
        return not self.unvalued

    @property
    def total_market_value(self) -> Decimal:
        """Sum over **valued** positions only.

        Partial by construction when `is_complete` is False, which is why that flag
        travels with every response that carries this number.
        """
        return sum((p.market_value or Decimal(0) for p in self.valued), Decimal(0))

    @property
    def total_unrealized_pnl(self) -> Decimal:
        return sum((p.unrealized_pnl or Decimal(0) for p in self.valued), Decimal(0))

    @property
    def gross_exposure(self) -> Decimal:
        return sum((abs(p.market_value or Decimal(0)) for p in self.valued), Decimal(0))

    @property
    def net_exposure(self) -> Decimal:
        return self.total_market_value

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_time": self.market_time.isoformat(),
            "knowledge_time": self.knowledge_time.isoformat(),
            "build_context_id": self.build_context_id,
            "market_state_ref": self.market_state_ref,
            "state_quality": self.state_quality,
            "positions": [p.as_dict() for p in self.positions],
            "total_market_value": str(self.total_market_value),
            "total_unrealized_pnl": str(self.total_unrealized_pnl),
            "gross_exposure": str(self.gross_exposure),
            "net_exposure": str(self.net_exposure),
            "is_complete": self.is_complete,
            "unvalued_instruments": [p.position.key.instrument_id for p in self.unvalued],
        }


@dataclass(frozen=True, slots=True)
class _Mark:
    price: Decimal
    source: MarkSource
    observed_at: datetime | None
    stale: bool


def marks_from_state(state: MarketState) -> Mapping[int, _Mark]:
    """Extract marks from the canonical state. The **only** price source here.

    Prefers the last traded price and falls back to the mid of an observed
    two-sided quote. It does not fall back to a bid, an ask, a previous close or a
    theoretical value — each of those is a different number with different
    meaning, and picking one silently would make a portfolio's value depend on
    which fallback happened to fire.
    """
    marks: dict[int, _Mark] = {}
    for expiry in state.expiries:
        for leg in expiry.legs:
            if leg.ltp is not None:
                marks[int(leg.instrument_id)] = _Mark(
                    price=leg.ltp,
                    source=MarkSource.OBSERVED_LTP,
                    observed_at=leg.quote_observed_at,
                    stale=leg.quote_stale,
                )
            elif leg.bid is not None and leg.ask is not None:
                marks[int(leg.instrument_id)] = _Mark(
                    price=(leg.bid + leg.ask) / Decimal(2),
                    source=MarkSource.OBSERVED_MID,
                    observed_at=leg.quote_observed_at,
                    stale=leg.quote_stale,
                )
    return marks


def value_positions(
    positions: Sequence[PortfolioPosition],
    state: MarketState,
    *,
    allow_unreliable_state: bool = False,
    allow_stale_quotes: bool = False,
) -> ValuationResult:
    """Value a book against one canonical state. Pure; fetches nothing.

    `state` carries its own `(T, K, BuildContext)`, so the point-in-time guarantee
    is inherited from Phase 3 rather than re-enforced here. There is no parameter
    through which a later price could be supplied, which is what makes brief §10's
    "never substitute a future price" structural rather than a rule to remember.

    Refuses outright on an `UNRELIABLE` state unless told otherwise. `04` §3 keeps
    such a state — suppressing it would hide the outage — but consumers skip it, and
    a portfolio valuation computed from data the system distrusts is a number that
    looks authoritative and is not.
    """
    if state.quality.status is QualityStatus.UNRELIABLE and not allow_unreliable_state:
        raise ValuationRefused(
            f"the market state at {state.market_time.isoformat()} is marked "
            f"UNRELIABLE; refusing to value the portfolio from it. Pass "
            f"allow_unreliable_state=True to value anyway, which records the "
            f"decision rather than hiding it."
        )

    marks = marks_from_state(state)
    valued: list[PositionValuation] = []

    for position in sorted(positions, key=lambda p: p.key.sort_key):
        if position.economics is None:
            valued.append(
                PositionValuation(
                    position=position,
                    unvalued_reason=UnvaluedReason.NO_CONTRACT_ECONOMICS,
                )
            )
            continue

        mark = marks.get(position.key.instrument_id)
        if mark is None:
            valued.append(
                PositionValuation(
                    position=position, unvalued_reason=UnvaluedReason.NO_PRICE_IN_STATE
                )
            )
            continue
        if mark.stale and not allow_stale_quotes:
            valued.append(
                PositionValuation(
                    position=position,
                    unvalued_reason=UnvaluedReason.STALE_QUOTE,
                    mark_observed_at=mark.observed_at,
                )
            )
            continue

        valued.append(
            PositionValuation(
                position=position,
                mark=mark.price,
                mark_source=mark.source,
                market_value=position.market_value(mark.price),
                unrealized_pnl=position.unrealized_pnl(mark.price),
                mark_observed_at=mark.observed_at,
            )
        )

    return ValuationResult(
        market_time=state.market_time,
        knowledge_time=state.knowledge_horizon,
        build_context_id=state.build_context_id,
        positions=tuple(valued),
        state_quality=state.quality.status.value,
        market_state_ref=state.content_digest(),
    )
