"""Helpers shared across domains. Pure, no I/O, no clock.

Kept small deliberately. A helper that encodes a convention (a sign, a unit, a
lot-size assumption) belongs in the feature that declares it, not here, or the
convention becomes implicit again.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from statistics import fmean, pstdev

from oipulse.marketstate.state import ExpirySlice, MarketState, OptionLeg

__all__ = [
    "CALL",
    "PUT",
    "atm_strike",
    "calls",
    "expiry_by_id",
    "mean_of",
    "puts",
    "safe_ratio",
    "stdev_of",
    "strikes_of",
    "total_oi",
]

CALL = "call"
PUT = "put"


def calls(legs: tuple[OptionLeg, ...]) -> tuple[OptionLeg, ...]:
    return tuple(leg for leg in legs if leg.option_type == CALL)


def puts(legs: tuple[OptionLeg, ...]) -> tuple[OptionLeg, ...]:
    return tuple(leg for leg in legs if leg.option_type == PUT)


def total_oi(legs: tuple[OptionLeg, ...]) -> int | None:
    """Sum of observed OI, or `None` when nothing was observed.

    `None` rather than `0`: reporting zero total OI for legs we could not see would be
    a fabricated fact, and it is indistinguishable downstream from a genuinely empty
    book.
    """
    observed = [leg.oi for leg in legs if leg.oi is not None]
    return sum(observed) if observed else None


def strikes_of(legs: tuple[OptionLeg, ...]) -> tuple[Decimal, ...]:
    return tuple(sorted({leg.strike for leg in legs}))


def safe_ratio(
    numerator: Decimal | int | None, denominator: Decimal | int | None
) -> Decimal | None:
    """`numerator / denominator`, or `None` when undefined.

    An undefined ratio is not a value. Returning 0 or infinity here would be a number
    a consumer plots and averages.
    """
    if numerator is None or denominator is None:
        return None
    try:
        den = Decimal(denominator)
        if den == 0:
            return None
        return Decimal(numerator) / den
    except (InvalidOperation, ZeroDivisionError):  # pragma: no cover - defensive
        return None


def atm_strike(slice_: ExpirySlice, spot: Decimal | None) -> Decimal | None:
    """Strike closest to spot. `None` without spot -- never the middle of the list."""
    if spot is None:
        return None
    strikes = strikes_of(slice_.legs)
    return min(strikes, key=lambda s: abs(s - spot)) if strikes else None


def expiry_by_id(state: MarketState, expiry_id: int) -> ExpirySlice | None:
    for slice_ in state.expiries:
        if int(slice_.expiry_id) == expiry_id:
            return slice_
    return None


def mean_of(values: list[Decimal]) -> Decimal | None:
    return Decimal(str(fmean(float(v) for v in values))) if values else None


def stdev_of(values: list[Decimal]) -> Decimal | None:
    """Population standard deviation. `None` below two points -- a one-point spread
    is not a dispersion measurement."""
    if len(values) < 2:
        return None
    return Decimal(str(pstdev(float(v) for v in values)))
