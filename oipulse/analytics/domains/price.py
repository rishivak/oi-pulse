"""Price — `07-ANALYTICS.md` §4.5.

Eight registered features. **Deliberately not a general technical-analysis library.**
Each exists because market state, signals, research or risk consume it; indicators
without a downstream consumer are not added (brief §31).

`VWAP` is computed *where volume data permits, otherwise unavailable rather than
approximated* — the design says so explicitly, and an unweighted mean masquerading as a
volume-weighted one is exactly the kind of quiet substitution that makes a number
untrustworthy.
"""

from __future__ import annotations

from decimal import Decimal

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.domains._shared import safe_ratio, stdev_of
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason

__all__ = [
    "atr",
    "momentum",
    "price_range",
    "price_return",
    "realized_move",
    "trend",
    "volume_zscore",
    "vwap",
]

_Q = ["quality!=UNRELIABLE"]


def _scope(ctx: ComputeContext) -> ScopeRef:
    return ScopeRef.underlying(int(ctx.state.identity.underlying_id))


def _prices(ctx: ComputeContext) -> list[Decimal]:
    return [s.spot.ltp for s in [*ctx.history, ctx.state] if s.spot.ltp is not None]


@feature(
    identifier="RETURN",
    version=1,
    definition=(
        "Simple return of spot over the window: (price now / price at window start) - 1. "
        "The horizon is the declared lookback, so multiple horizons are multiple "
        "parameterised computations of this one feature."
    ),
    inputs=["spot.ltp"],
    formula="p(T) / p(T - lookback) - 1",
    units="ratio",
    sampling_frequency="1m",
    lookback="15m",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def price_return(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = price_return.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    now, then = ctx.state.spot.ltp, reference.spot.ltp if reference else None
    if now is None or then is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "spot absent")
    ratio = safe_ratio(now - then, then)
    if ratio is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "reference price is zero")
    return emit(spec, ctx, scope, ratio, extra_inputs={"now": str(now), "then": str(then)})


@feature(
    identifier="TREND",
    version=1,
    definition=(
        "Ordinary-least-squares slope of spot against observation index across the "
        "window, in price units per step. Sign and magnitude only; no directional "
        "label or recommendation is attached."
    ),
    inputs=["spot.ltp"],
    formula="ols_slope(price_t ~ t)",
    units="price_per_step",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def trend(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = trend.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    prices = _prices(ctx)
    if len(prices) < 3:
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            "fewer than three prices; a two-point slope is just a difference",
        )
    n = Decimal(len(prices))
    xs = [Decimal(i) for i in range(len(prices))]
    mx = sum(xs, start=Decimal(0)) / n
    my = sum(prices, start=Decimal(0)) / n
    denominator = sum(((x - mx) * (x - mx) for x in xs), start=Decimal(0))
    if denominator == 0:  # pragma: no cover - impossible for n >= 2 distinct indices
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "zero variance in index")
    slope = (
        sum(((x - mx) * (p - my) for x, p in zip(xs, prices, strict=True)), start=Decimal(0))
        / denominator
    )
    return emit(spec, ctx, scope, slope, extra_inputs={"points": len(prices)})


@feature(
    identifier="MOMENTUM",
    version=1,
    definition="Absolute change in spot over the window, in price units.",
    inputs=["spot.ltp"],
    formula="p(T) - p(T - lookback)",
    units="price",
    sampling_frequency="1m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def momentum(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = momentum.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    now, then = ctx.state.spot.ltp, reference.spot.ltp if reference else None
    if now is None or then is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "spot absent")
    return emit(spec, ctx, scope, now - then, extra_inputs={"now": str(now), "then": str(then)})


@feature(
    identifier="RANGE",
    version=1,
    definition="Highest minus lowest observed spot across the window, in price units.",
    inputs=["spot.ltp"],
    formula="max(p_window) - min(p_window)",
    units="price",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def price_range(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = price_range.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    prices = _prices(ctx)
    if not prices:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no spot observed")
    return emit(spec, ctx, scope, max(prices) - min(prices), extra_inputs={"points": len(prices)})


@feature(
    identifier="ATR",
    version=1,
    definition=(
        "Average true range over the window. True range per step is the absolute "
        "change in spot between consecutive states; the session high/low are not used "
        "because a state carries a point observation, not a bar."
    ),
    inputs=["spot.ltp"],
    formula="mean(|p_t - p_{t-1}|)",
    units="price",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def atr(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = atr.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    prices = _prices(ctx)
    if len(prices) < 2:
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "fewer than two prices"
        )
    ranges = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    return emit(
        spec, ctx, scope, sum(ranges) / Decimal(len(ranges)), extra_inputs={"steps": len(ranges)}
    )


@feature(
    identifier="VWAP",
    version=1,
    definition=(
        "Volume-weighted average option price across the legs of an expiry. "
        "Unavailable where volume data is absent rather than approximated by an "
        "unweighted mean."
    ),
    inputs=["legs.ltp", "legs.volume"],
    formula="sum(ltp_i * volume_i) / sum(volume_i)",
    units="price",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def vwap(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = vwap.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    slice_ = next((s for s in ctx.state.expiries if int(s.expiry_id) == int(expiry_raw)), None)
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    pairs = [
        (leg.ltp, leg.volume)
        for leg in slice_.legs
        if leg.ltp is not None and leg.volume is not None
    ]
    if not pairs:
        # The documented behaviour: unavailable, never an unweighted mean.
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            "no leg carries both price and volume; VWAP is not approximated",
        )
    total_volume = sum(v for _, v in pairs)
    value = safe_ratio(sum(p * Decimal(v) for p, v in pairs), total_volume)
    if value is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "total volume is zero")
    return emit(spec, ctx, scope, value, extra_inputs={"legs": len(pairs), "volume": total_volume})


@feature(
    identifier="VOLUME_ZSCORE",
    version=1,
    definition=(
        "Standard scores of current total expiry volume against the window's "
        "distribution of the same quantity."
    ),
    inputs=["legs.volume"],
    formula="(v_now - mean(v_window)) / stdev(v_window)",
    units="zscore",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="zscore",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def volume_zscore(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = volume_zscore.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    series: list[Decimal] = []
    for state in [*ctx.history, ctx.state]:
        slice_ = next((s for s in state.expiries if int(s.expiry_id) == int(expiry_raw)), None)
        if slice_ is None:
            continue
        volumes = [leg.volume for leg in slice_.legs if leg.volume is not None]
        if volumes:
            series.append(Decimal(sum(volumes)))
    if len(series) < 3:
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            "fewer than three volume observations",
        )
    mean = sum(series) / Decimal(len(series))
    sd = stdev_of(series)
    if sd is None or sd == 0:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "zero volume dispersion")
    return emit(spec, ctx, scope, (series[-1] - mean) / sd, extra_inputs={"points": len(series)})


@feature(
    identifier="REALIZED_MOVE",
    version=1,
    definition=(
        "Absolute spot move over the window as a fraction of the window-start price: "
        "how far price actually travelled, unsigned."
    ),
    inputs=["spot.ltp"],
    formula="|p(T) - p(T - lookback)| / p(T - lookback)",
    units="ratio",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def realized_move(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = realized_move.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    now, then = ctx.state.spot.ltp, reference.spot.ltp if reference else None
    if now is None or then is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "spot absent")
    value = safe_ratio(abs(now - then), then)
    if value is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "reference price is zero")
    return emit(spec, ctx, scope, value)
