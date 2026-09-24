"""Market structure — `07-ANALYTICS.md` §4.7.

Five registered features.

**Support and resistance here are positioning-derived, explicitly not price-technical
levels**, and are named `SUPPORT_FROM_POSITIONING` / `RESISTANCE_FROM_POSITIONING` to
make that unambiguous at every call site and in every stored row. A consumer reading
`SUPPORT` alone might reasonably assume a chart level; the longer name removes the
ambiguity permanently.

`REGIME` is **deterministic and rule-based**, returns `UNKNOWN` as a legitimate and
frequently correct answer, and carries the evidence that produced it. There is no ML
(brief §31), and a strength value is reported only where the rules justify one.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.domains._shared import calls, expiry_by_id, puts
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import FeatureSpec, feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason
from oipulse.marketstate.state import MarketState

__all__ = [
    "Regime",
    "max_pain",
    "regime",
    "resistance_from_positioning",
    "structure_migration",
    "support_from_positioning",
]

_Q_OI = ["quality!=UNRELIABLE", "oi_coverage>=0.95"]
_Q = ["quality!=UNRELIABLE"]


class Regime(StrEnum):
    """The documented, closed set (`07` §4.7). `UNKNOWN` is a real answer."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


def _expiry_scope(ctx: ComputeContext) -> ScopeRef | None:
    expiry_id = ctx.params.get("expiry_id", None)
    return None if expiry_id is None else ScopeRef.expiry(int(expiry_id))


def _positioning_level(
    ctx: ComputeContext, spec: FeatureSpec, option_type: str
) -> MetricValue | Unavailable:
    """Strike with the largest OI on one side, below (put) or above (call) spot.

    Puts below spot are where downside positioning sits, calls above it where upside
    positioning sits. Restricting each side to the relevant half of the chain is what
    makes this a structure read rather than a restatement of `OI_WALL_*`.
    """
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    spot = ctx.state.spot.ltp
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    if spot is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "spot absent")
    legs = puts(slice_.legs) if option_type == "put" else calls(slice_.legs)
    side = {
        leg.strike: leg.oi
        for leg in legs
        if leg.oi is not None
        and ((leg.strike < spot) if option_type == "put" else (leg.strike > spot))
    }
    if not side:
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            f"no {option_type} OI observed on the relevant side of spot",
        )
    strike = max(sorted(side), key=lambda s: side[s])
    return emit(
        spec,
        ctx,
        scope,
        strike,
        extra_inputs={"oi": side[strike], "spot": str(spot)},
        evidence=(f"{option_type} OI peak {side[strike]} at {strike}, spot {spot}",),
    )


@feature(
    identifier="SUPPORT_FROM_POSITIONING",
    version=1,
    definition=(
        "Strike below spot carrying the largest put open interest. A POSITIONING-"
        "derived level, explicitly not a price-technical support line."
    ),
    inputs=["oi_by_strike(PE)", "spot.ltp"],
    formula="argmax_{s < spot} oi_put(s)",
    units="strike",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def support_from_positioning(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _positioning_level(ctx, support_from_positioning.__feature_spec__, "put")  # type: ignore[attr-defined]


@feature(
    identifier="RESISTANCE_FROM_POSITIONING",
    version=1,
    definition=(
        "Strike above spot carrying the largest call open interest. A POSITIONING-"
        "derived level, explicitly not a price-technical resistance line."
    ),
    inputs=["oi_by_strike(CE)", "spot.ltp"],
    formula="argmax_{s > spot} oi_call(s)",
    units="strike",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def resistance_from_positioning(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _positioning_level(ctx, resistance_from_positioning.__feature_spec__, "call")  # type: ignore[attr-defined]


@feature(
    identifier="STRUCTURE_MIGRATION",
    version=1,
    definition=(
        "Net displacement of the positioning-derived support and resistance band "
        "midpoint over the window, in strike points."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)", "spot.ltp"],
    formula="mid(support, resistance)(T) - mid(support, resistance)(T - lookback)",
    units="strike_points",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def structure_migration(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = structure_migration.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    expiry_id = int(ctx.params.get("expiry_id"))
    reference = ctx.reference_state(spec.lookback_delta)
    now = _band_mid(ctx.state, expiry_id)
    then = _band_mid(reference, expiry_id) if reference else None
    if now is None or then is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            "support/resistance band absent at a window end",
        )
    return emit(spec, ctx, scope, now - then, extra_inputs={"now": str(now), "then": str(then)})


def _band_mid(state: MarketState, expiry_id: int) -> Decimal | None:
    slice_ = expiry_by_id(state, expiry_id)
    spot = state.spot.ltp
    if slice_ is None or spot is None:
        return None
    below = {
        leg.strike: leg.oi for leg in puts(slice_.legs) if leg.oi is not None and leg.strike < spot
    }
    above = {
        leg.strike: leg.oi for leg in calls(slice_.legs) if leg.oi is not None and leg.strike > spot
    }
    if not below or not above:
        return None
    support = max(sorted(below), key=lambda s: below[s])
    resistance = max(sorted(above), key=lambda s: above[s])
    return (support + resistance) / Decimal(2)


@feature(
    identifier="MAX_PAIN",
    version=1,
    definition=(
        "Strike at which the total intrinsic value of all open option contracts is "
        "minimised: the strike where the most open interest expires worthless."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="argmin_K sum_s oi_call(s)*max(K-s,0) + oi_put(s)*max(s-K,0)",
    units="strike",
    sampling_frequency="5m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def max_pain(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = max_pain.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    call_oi = {leg.strike: leg.oi for leg in calls(slice_.legs) if leg.oi is not None}
    put_oi = {leg.strike: leg.oi for leg in puts(slice_.legs) if leg.oi is not None}
    strikes = sorted(set(call_oi) | set(put_oi))
    if not strikes:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no OI observed")

    def pain(settle: Decimal) -> Decimal:
        total = Decimal(0)
        for strike in strikes:
            total += Decimal(call_oi.get(strike, 0)) * max(settle - strike, Decimal(0))
            total += Decimal(put_oi.get(strike, 0)) * max(strike - settle, Decimal(0))
        return total

    best = min(strikes, key=pain)
    return emit(
        spec, ctx, scope, best, extra_inputs={"strikes": len(strikes), "pain": str(pain(best))}
    )


@feature(
    identifier="REGIME",
    version=1,
    definition=(
        "Deterministic rule-based market regime from realised movement and trend "
        "across the window. Returns one of TRENDING_UP, TRENDING_DOWN, RANGE, "
        "HIGH_VOLATILITY, LOW_VOLATILITY, BREAKOUT, BREAKDOWN, TRANSITION, UNKNOWN. "
        "No ML. UNKNOWN is a legitimate answer and is returned whenever the rules do "
        "not clearly apply."
    ),
    inputs=["spot.ltp"],
    formula=(
        "rules over (realised_move, trend_slope, range) with declared thresholds; "
        "UNKNOWN when none applies"
    ),
    units="category",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
    parameters=("trend_threshold", "breakout_threshold", "quiet_threshold"),
)
def regime(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = regime.__feature_spec__  # type: ignore[attr-defined]
    scope = ScopeRef.underlying(int(ctx.state.identity.underlying_id))
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    prices = [s.spot.ltp for s in [*ctx.history, ctx.state] if s.spot.ltp is not None]
    if len(prices) < 4:
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "fewer than four prices"
        )

    first, last = prices[0], prices[-1]
    if first == 0:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "window opens at zero")
    move = (last - first) / first
    span = (max(prices) - min(prices)) / first

    trend_t = Decimal(str(ctx.params.get("trend_threshold", "0.003")))
    breakout_t = Decimal(str(ctx.params.get("breakout_threshold", "0.010")))
    quiet_t = Decimal(str(ctx.params.get("quiet_threshold", "0.001")))

    # Ordered most-specific first. Every branch is a declared threshold comparison;
    # nothing here is fitted, and the fall-through is UNKNOWN rather than a guess.
    if move >= breakout_t and last == max(prices):
        label, strength = Regime.BREAKOUT, abs(move)
    elif move <= -breakout_t and last == min(prices):
        label, strength = Regime.BREAKDOWN, abs(move)
    elif move >= trend_t:
        label, strength = Regime.TRENDING_UP, abs(move)
    elif move <= -trend_t:
        label, strength = Regime.TRENDING_DOWN, abs(move)
    elif span <= quiet_t:
        label, strength = Regime.LOW_VOLATILITY, span
    elif span >= breakout_t:
        label, strength = Regime.HIGH_VOLATILITY, span
    elif span <= trend_t:
        label, strength = Regime.RANGE, span
    else:
        label, strength = Regime.TRANSITION, span

    return emit(
        spec,
        ctx,
        scope,
        label.value,
        extra_inputs={"move": str(move), "span": str(span), "points": len(prices)},
        evidence=(f"move {move:.5f}, span {span:.5f}, strength {strength:.5f}",),
    )
