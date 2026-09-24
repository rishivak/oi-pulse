"""Volatility — `07-ANALYTICS.md` §4.2.

Ten registered features. Two design points are load-bearing:

* **IV_SKEW has two registered variants**, not one with a hidden convention. The
  delta-based variant (25-delta put IV minus 25-delta call IV) and the strike-based
  variant are separate features with separate identifiers, because they are different
  measurements that happen to share a name in common usage.
* **`IV_RANK` and `IV_PERCENTILE` declare a minimum history requirement and return
  *insufficient history* rather than a number computed from a short window.**
  Fabricating a percentile is worse than withholding it: a percentile over four points
  looks exactly like a percentile over four hundred.
"""

from __future__ import annotations

from decimal import Decimal

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.domains._shared import (
    atm_strike,
    calls,
    expiry_by_id,
    mean_of,
    puts,
    safe_ratio,
)
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason
from oipulse.marketstate.state import ExpirySlice, MarketState, OptionLeg

__all__ = [
    "atm_iv",
    "implied_realized_spread",
    "iv_change",
    "iv_percentile",
    "iv_rank",
    "iv_skew_delta",
    "iv_skew_strike",
    "iv_term_structure",
    "realized_vol_close_to_close",
    "realized_vol_parkinson",
]

_Q_GREEKS = ["quality!=UNRELIABLE", "greeks_coverage>=0.90"]
_Q = ["quality!=UNRELIABLE"]
#: `IV_RANK`/`IV_PERCENTILE` refuse below this many observations (`07` §4.2).
MIN_HISTORY_POINTS = 20


def _expiry_scope(ctx: ComputeContext) -> ScopeRef | None:
    expiry_id = ctx.params.get("expiry_id", None)
    return None if expiry_id is None else ScopeRef.expiry(int(expiry_id))


def _atm_iv_of(state: MarketState, expiry_id: int) -> Decimal | None:
    """Mean of the call and put IV at the strike nearest spot.

    Averaging the two sides rather than picking one: at the money they should agree,
    and taking only the call would make the number depend on which side happened to
    have a greeks observation.
    """
    slice_ = expiry_by_id(state, expiry_id)
    if slice_ is None:
        return None
    strike = atm_strike(slice_, state.spot.ltp)
    if strike is None:
        return None
    ivs = [leg.iv for leg in slice_.legs if leg.strike == strike and leg.iv is not None]
    return mean_of(ivs)


@feature(
    identifier="ATM_IV",
    version=1,
    definition=(
        "Implied volatility at the money: the mean of call and put IV at the strike "
        "nearest spot, for one expiry."
    ),
    inputs=["iv_by_strike", "spot.ltp"],
    formula="mean(iv_call(atm), iv_put(atm))",
    units="iv_decimal",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_GREEKS,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def atm_iv(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = atm_iv.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    value = _atm_iv_of(ctx.state, int(ctx.params.get("expiry_id")))
    if value is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no ATM IV observed")
    return emit(spec, ctx, scope, value)


@feature(
    identifier="IV_CHANGE",
    version=1,
    definition="Change in ATM implied volatility over the window, in IV decimal points.",
    inputs=["iv_by_strike", "spot.ltp"],
    formula="atm_iv(T) - atm_iv(T - lookback)",
    units="iv_decimal",
    sampling_frequency="1m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_GREEKS,
    scope=Scope.EXPIRY,
    depends_on=(("ATM_IV", 1),),
    parameters=("expiry_id",),
)
def iv_change(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = iv_change.__feature_spec__  # type: ignore[attr-defined]
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
    now = _atm_iv_of(ctx.state, expiry_id)
    then = _atm_iv_of(reference, expiry_id) if reference else None
    if now is None or then is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "ATM IV absent")
    return emit(spec, ctx, scope, now - then, extra_inputs={"now": str(now), "then": str(then)})


@feature(
    identifier="IV_SKEW_DELTA",
    version=1,
    definition=(
        "Delta-based skew: the IV of the put whose delta is nearest -0.25 minus the IV "
        "of the call whose delta is nearest +0.25. Registered separately from the "
        "strike-based variant because they are different measurements."
    ),
    inputs=["iv_by_strike", "legs.delta"],
    formula="iv_put(delta ~ -0.25) - iv_call(delta ~ +0.25)",
    units="iv_decimal",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_GREEKS,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "target_delta"),
)
def iv_skew_delta(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = iv_skew_delta.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    target = Decimal(str(ctx.params.get("target_delta", "0.25")))
    call = _nearest_delta(calls(slice_.legs), target)
    put = _nearest_delta(puts(slice_.legs), -target)
    if call is None or put is None or call.iv is None or put.iv is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            "no leg with both delta and IV on one side",
        )
    return emit(
        spec,
        ctx,
        scope,
        put.iv - call.iv,
        extra_inputs={"call_strike": str(call.strike), "put_strike": str(put.strike)},
        evidence=(f"put {put.strike} iv {put.iv} - call {call.strike} iv {call.iv}",),
    )


def _nearest_delta(legs: tuple[OptionLeg, ...], target: Decimal) -> OptionLeg | None:
    candidates = [leg for leg in legs if leg.delta is not None and leg.iv is not None]
    if not candidates:
        return None
    return min(
        sorted(candidates, key=lambda leg: leg.strike),
        key=lambda leg: abs(leg.delta - target) if leg.delta is not None else Decimal("inf"),
    )


@feature(
    identifier="IV_SKEW_STRIKE",
    version=1,
    definition=(
        "Strike-based skew: put IV minus call IV at a strike offset the declared "
        "number of strike steps below and above the money. Registered separately from "
        "the delta-based variant."
    ),
    inputs=["iv_by_strike", "spot.ltp"],
    formula="iv_put(atm - offset) - iv_call(atm + offset)",
    units="iv_decimal",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_GREEKS,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "offset_steps"),
)
def iv_skew_strike(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = iv_skew_strike.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    steps = int(ctx.params.get("offset_steps", 2))
    strikes = sorted({leg.strike for leg in slice_.legs})
    atm = atm_strike(slice_, ctx.state.spot.ltp)
    if atm is None or atm not in strikes:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no ATM strike")
    index = strikes.index(atm)
    low, high = index - steps, index + steps
    if low < 0 or high >= len(strikes):
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            f"chain does not extend {steps} strikes either side of the money",
        )
    put_iv = _iv_at(slice_, strikes[low], "put")
    call_iv = _iv_at(slice_, strikes[high], "call")
    if put_iv is None or call_iv is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "IV absent at a wing")
    return emit(
        spec,
        ctx,
        scope,
        put_iv - call_iv,
        extra_inputs={"put_strike": str(strikes[low]), "call_strike": str(strikes[high])},
    )


def _iv_at(slice_: ExpirySlice, strike: Decimal, option_type: str) -> Decimal | None:
    for leg in slice_.legs:
        if leg.strike == strike and leg.option_type == option_type and leg.iv is not None:
            return leg.iv
    return None


@feature(
    identifier="IV_TERM_STRUCTURE",
    version=1,
    definition=(
        "Difference in ATM implied volatility between the two nearest expiries: the "
        "second expiry's ATM IV minus the front expiry's."
    ),
    inputs=["iv_by_strike", "spot.ltp"],
    formula="atm_iv(expiry_2) - atm_iv(expiry_1)",
    units="iv_decimal",
    sampling_frequency="5m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_GREEKS,
    scope=Scope.UNDERLYING,
)
def iv_term_structure(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = iv_term_structure.__feature_spec__  # type: ignore[attr-defined]
    scope = ScopeRef.underlying(int(ctx.state.identity.underlying_id))
    expiries = sorted(ctx.state.expiries, key=lambda e: e.expiry_date)
    if len(expiries) < 2:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "fewer than two expiries in the state"
        )
    front = _atm_iv_of(ctx.state, int(expiries[0].expiry_id))
    second = _atm_iv_of(ctx.state, int(expiries[1].expiry_id))
    if front is None or second is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "ATM IV absent")
    return emit(
        spec, ctx, scope, second - front, extra_inputs={"front": str(front), "second": str(second)}
    )


# ------------------------------------------------------------------- realized


def _spot_series(ctx: ComputeContext) -> list[Decimal]:
    return [s.spot.ltp for s in [*ctx.history, ctx.state] if s.spot.ltp is not None]


@feature(
    identifier="REALIZED_VOL_CLOSE_TO_CLOSE",
    version=1,
    definition=(
        "Close-to-close realized volatility: the population standard deviation of log "
        "returns across the window, annualised by the declared periods-per-year."
    ),
    inputs=["spot.ltp"],
    formula="stdev(ln(p_t / p_{t-1})) * sqrt(periods_per_year)",
    units="vol_annualised",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
    parameters=("periods_per_year",),
)
def realized_vol_close_to_close(ctx: ComputeContext) -> MetricValue | Unavailable:
    import math

    spec = realized_vol_close_to_close.__feature_spec__  # type: ignore[attr-defined]
    scope = ScopeRef.underlying(int(ctx.state.identity.underlying_id))
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    prices = _spot_series(ctx)
    if len(prices) < 3:
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "fewer than three prices"
        )
    returns = [
        math.log(float(prices[i]) / float(prices[i - 1]))
        for i in range(1, len(prices))
        if prices[i - 1] > 0
    ]
    if len(returns) < 2:
        return unavailable(spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "too few returns")
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    periods = float(ctx.params.get("periods_per_year", 98280))
    value = Decimal(str(math.sqrt(variance) * math.sqrt(periods)))
    return emit(spec, ctx, scope, value, extra_inputs={"returns": len(returns)})


@feature(
    identifier="REALIZED_VOL_PARKINSON",
    version=1,
    definition=(
        "Parkinson realized volatility from the session high-low range, annualised by "
        "the declared periods-per-year. Registered separately from close-to-close "
        "because it is a different estimator, not a variant of one."
    ),
    inputs=["spot.high", "spot.low"],
    formula="sqrt(ln(high/low)^2 / (4 * ln 2)) * sqrt(periods_per_year)",
    units="vol_annualised",
    sampling_frequency="5m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
    parameters=("periods_per_year",),
)
def realized_vol_parkinson(ctx: ComputeContext) -> MetricValue | Unavailable:
    import math

    spec = realized_vol_parkinson.__feature_spec__  # type: ignore[attr-defined]
    scope = ScopeRef.underlying(int(ctx.state.identity.underlying_id))
    high, low = ctx.state.spot.high, ctx.state.spot.low
    if high is None or low is None or low <= 0 or high <= 0:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "session high/low absent or non-positive"
        )
    if high < low:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "high is below low")
    periods = float(ctx.params.get("periods_per_year", 252))
    log_hl = math.log(float(high) / float(low))
    value = math.sqrt((log_hl**2) / (4 * math.log(2))) * math.sqrt(periods)
    return emit(
        spec, ctx, scope, Decimal(str(value)), extra_inputs={"high": str(high), "low": str(low)}
    )


@feature(
    identifier="IMPLIED_REALIZED_SPREAD",
    version=1,
    definition=(
        "ATM implied volatility minus close-to-close realized volatility for the front "
        "expiry. Positive means options are priced above recent realised movement."
    ),
    inputs=["ATM_IV", "REALIZED_VOL_CLOSE_TO_CLOSE"],
    formula="atm_iv - realized_vol_close_to_close",
    units="vol_annualised",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q_GREEKS,
    scope=Scope.UNDERLYING,
    depends_on=(("ATM_IV", 1), ("REALIZED_VOL_CLOSE_TO_CLOSE", 1)),
)
def implied_realized_spread(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = implied_realized_spread.__feature_spec__  # type: ignore[attr-defined]
    scope = ScopeRef.underlying(int(ctx.state.identity.underlying_id))
    iv = ctx.dependency("ATM_IV", 1)
    rv = ctx.dependency("REALIZED_VOL_CLOSE_TO_CLOSE", 1)
    if iv is None or rv is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.DEPENDENCY_UNAVAILABLE,
            "ATM_IV or REALIZED_VOL_CLOSE_TO_CLOSE not resolved",
        )
    if not isinstance(iv.value, Decimal) or not isinstance(rv.value, Decimal):
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "dependency has no value")
    return emit(
        spec,
        ctx,
        scope,
        iv.value - rv.value,
        extra_inputs={"iv": str(iv.value), "rv": str(rv.value)},
    )


# ------------------------------------------------------------------ rank/pctile


def _atm_iv_series(ctx: ComputeContext, expiry_id: int) -> list[Decimal]:
    out = []
    for state in [*ctx.history, ctx.state]:
        value = _atm_iv_of(state, expiry_id)
        if value is not None:
            out.append(value)
    return out


@feature(
    identifier="IV_RANK",
    version=1,
    definition=(
        "Position of current ATM IV within the window's observed range: "
        "(iv - min) / (max - min). Returns insufficient history below the declared "
        "minimum number of observations rather than a number from a short window."
    ),
    inputs=["ATM_IV"],
    formula="(iv_now - min(iv_window)) / (max(iv_window) - min(iv_window))",
    units="ratio",
    sampling_frequency="5m",
    lookback="1d",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_Q_GREEKS,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "min_points"),
)
def iv_rank(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = iv_rank.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    minimum = int(ctx.params.get("min_points", MIN_HISTORY_POINTS))
    series = _atm_iv_series(ctx, int(ctx.params.get("expiry_id")))
    if len(series) < minimum:
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"{len(series)} of {minimum} required observations",
        )
    low, high, now = min(series), max(series), series[-1]
    value = safe_ratio(now - low, high - low)
    if value is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "IV range is zero")
    return emit(spec, ctx, scope, value, extra_inputs={"min": str(low), "max": str(high)})


@feature(
    identifier="IV_PERCENTILE",
    version=1,
    definition=(
        "Fraction of observations in the window at or below current ATM IV. Returns "
        "insufficient history below the declared minimum: fabricating a percentile is "
        "worse than withholding it."
    ),
    inputs=["ATM_IV"],
    formula="count(iv_window <= iv_now) / count(iv_window)",
    units="ratio",
    sampling_frequency="5m",
    lookback="1d",
    availability_delay="2s",
    normalization="percentile",
    quality_requirements=_Q_GREEKS,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "min_points"),
)
def iv_percentile(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = iv_percentile.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    minimum = int(ctx.params.get("min_points", MIN_HISTORY_POINTS))
    series = _atm_iv_series(ctx, int(ctx.params.get("expiry_id")))
    if len(series) < minimum:
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"{len(series)} of {minimum} required observations",
        )
    now = series[-1]
    at_or_below = sum(1 for v in series if v <= now)
    return emit(
        spec,
        ctx,
        scope,
        Decimal(at_or_below) / Decimal(len(series)),
        extra_inputs={"points": len(series)},
    )
