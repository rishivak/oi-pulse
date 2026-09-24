"""Positioning — `07-ANALYTICS.md` §4.1.

Thirteen registered features. Two conventions recur and are stated once here because
both are places where a plausible wrong answer is easy to produce:

* **Reference resolution is point-in-time.** `OI_CHANGE` compares against a state
  resolved from supplied history at the window start, never against a stored `prev_*`
  column. A stored previous value cannot be reconstructed for an arbitrary past moment,
  so using one would leak the *current* previous value into a historical computation.
  `provider_prev_oi` is the provider's own assertion and is a separate observation, not
  this reference.
* **No directional labels.** `PCR` and `BUILDUP_CLASSIFICATION` are reported as a number
  and a category with their conventions documented. Neither carries bullish/bearish;
  such a claim needs context the metric alone does not have (`07` §4.1, brief §12).
"""

from __future__ import annotations

from decimal import Decimal

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.domains._shared import (
    calls,
    expiry_by_id,
    puts,
    safe_ratio,
    total_oi,
)
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import FeatureSpec, feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason
from oipulse.marketstate.state import MarketState

__all__ = [
    "buildup_classification",
    "call_oi_migration",
    "oi_change",
    "oi_change_pct",
    "oi_concentration",
    "oi_wall_call",
    "oi_wall_migration",
    "oi_wall_put",
    "pcr",
    "pcr_oi_change",
    "price_oi_relationship",
    "put_oi_migration",
    "volume_oi_ratio",
]

_QUALITY = ["quality!=UNRELIABLE"]
_QUALITY_OI = ["quality!=UNRELIABLE", "oi_coverage>=0.95"]


def _expiry_scope(ctx: ComputeContext) -> ScopeRef | None:
    """The expiry this computation is about, from the `expiry_id` parameter."""
    expiry_id = ctx.params.get("expiry_id", None)
    if expiry_id is None:
        return None
    return ScopeRef.expiry(int(expiry_id))


# --------------------------------------------------------------------- OI change


@feature(
    identifier="OI_CHANGE",
    version=1,
    definition=(
        "Change in total open interest for an expiry between the point-in-time "
        "reference state at the window start and the current state."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="total_oi(T) - total_oi(T - lookback)",
    units="contracts",
    sampling_frequency="1m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def oi_change(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = oi_change.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    expiry_id = int(ctx.params.get("expiry_id"))
    current = expiry_by_id(ctx.state, expiry_id)
    if current is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    prior = expiry_by_id(reference, expiry_id) if reference else None
    now_oi = total_oi(current.legs)
    then_oi = total_oi(prior.legs) if prior else None
    if now_oi is None or then_oi is None:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "OI absent in a window end"
        )
    return emit(
        spec,
        ctx,
        scope,
        now_oi - then_oi,
        extra_inputs={"now": now_oi, "then": then_oi},
        evidence=(f"total_oi {then_oi} -> {now_oi}",),
    )


@feature(
    identifier="OI_CHANGE_PCT",
    version=1,
    definition="OI_CHANGE expressed as a fraction of the reference total open interest.",
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="(total_oi(T) - total_oi(T - lookback)) / total_oi(T - lookback)",
    units="ratio",
    sampling_frequency="1m",
    lookback="15m",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def oi_change_pct(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = oi_change_pct.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    expiry_id = int(ctx.params.get("expiry_id"))
    current = expiry_by_id(ctx.state, expiry_id)
    if current is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    prior = expiry_by_id(reference, expiry_id) if reference else None
    now_oi = total_oi(current.legs)
    then_oi = total_oi(prior.legs) if prior else None
    if now_oi is None or then_oi is None:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "OI absent in a window end"
        )
    ratio = safe_ratio(now_oi - then_oi, then_oi)
    if ratio is None:
        # Zero reference OI: the percentage is undefined, not infinite and not zero.
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "reference OI is zero")
    return emit(spec, ctx, scope, ratio, extra_inputs={"now": now_oi, "then": then_oi})


# ------------------------------------------------------------------ concentration


@feature(
    identifier="OI_CONCENTRATION",
    version=1,
    definition=(
        "Herfindahl index over per-strike open interest for an expiry: the sum of "
        "squared OI shares. 1 means all OI sits at one strike; 1/n means it is spread "
        "evenly over n strikes."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="sum((oi_s / sum(oi)) ** 2)",
    units="index",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def oi_concentration(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = oi_concentration.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    per_strike: dict[Decimal, int] = {}
    for leg in slice_.legs:
        if leg.oi is not None:
            per_strike[leg.strike] = per_strike.get(leg.strike, 0) + leg.oi
    grand = sum(per_strike.values())
    if not per_strike or grand == 0:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no OI observed")
    index = sum((Decimal(v) / Decimal(grand)) ** 2 for v in per_strike.values())
    return emit(
        spec,
        ctx,
        scope,
        index,
        extra_inputs={"strikes": sorted(str(k) for k in per_strike), "total": grand},
    )


# ------------------------------------------------------------------------- walls


def _wall(ctx: ComputeContext, spec: FeatureSpec, option_type: str) -> MetricValue | Unavailable:
    """Strike carrying the largest OI, subject to a declared prominence threshold.

    "Locally extreme" is made precise: the top strike must exceed the mean of the rest
    by `prominence` (default 1.5x). Without a threshold, the largest of a flat
    distribution would be reported as a wall, which is a number with no meaning.
    """
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    prominence = Decimal(str(ctx.params.get("prominence", "1.5")))
    legs = calls(slice_.legs) if option_type == "call" else puts(slice_.legs)
    observed = {leg.strike: leg.oi for leg in legs if leg.oi is not None}
    if not observed:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no OI observed")
    top_strike = max(sorted(observed), key=lambda s: observed[s])
    top_oi = observed[top_strike]
    others = [v for k, v in observed.items() if k != top_strike]
    if others:
        mean_other = Decimal(sum(others)) / Decimal(len(others))
        if mean_other > 0 and Decimal(top_oi) < prominence * mean_other:
            return unavailable(
                spec,
                scope,
                UnavailableReason.UNDEFINED,
                f"no strike exceeds {prominence}x the mean of the rest",
            )
    return emit(
        spec,
        ctx,
        scope,
        top_strike,
        extra_inputs={"top_oi": top_oi, "prominence": str(prominence)},
        evidence=(f"{option_type} OI peak {top_oi} at {top_strike}",),
    )


@feature(
    identifier="OI_WALL_CALL",
    version=1,
    definition=(
        "Strike carrying the largest call open interest, provided it exceeds the mean "
        "of the remaining strikes by the declared prominence factor."
    ),
    inputs=["oi_by_strike(CE)"],
    formula="argmax_s oi_call(s) subject to oi_call(s) >= prominence * mean(oi_call(others))",
    units="strike",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "prominence"),
)
def oi_wall_call(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _wall(ctx, oi_wall_call.__feature_spec__, "call")  # type: ignore[attr-defined]


@feature(
    identifier="OI_WALL_PUT",
    version=1,
    definition=(
        "Strike carrying the largest put open interest, provided it exceeds the mean "
        "of the remaining strikes by the declared prominence factor."
    ),
    inputs=["oi_by_strike(PE)"],
    formula="argmax_s oi_put(s) subject to oi_put(s) >= prominence * mean(oi_put(others))",
    units="strike",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "prominence"),
)
def oi_wall_put(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _wall(ctx, oi_wall_put.__feature_spec__, "put")  # type: ignore[attr-defined]


@feature(
    identifier="OI_WALL_MIGRATION",
    version=1,
    definition=(
        "Displacement of the put OI wall strike between the point-in-time reference "
        "state at the window start and the current state, in strike points."
    ),
    inputs=["oi_by_strike(PE)"],
    formula="wall_strike(T) - wall_strike(T - lookback)",
    units="strike_points",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id", "option_type"),
)
def oi_wall_migration(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = oi_wall_migration.__feature_spec__  # type: ignore[attr-defined]
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
    option_type = str(ctx.params.get("option_type", "put"))
    expiry_id = int(ctx.params.get("expiry_id"))
    reference = ctx.reference_state(spec.lookback_delta)
    now = _peak_strike(ctx.state, expiry_id, option_type)
    then = _peak_strike(reference, expiry_id, option_type) if reference else None
    if now is None or then is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no wall at a window end")
    return emit(
        spec,
        ctx,
        scope,
        now - then,
        extra_inputs={"now": str(now), "then": str(then), "option_type": option_type},
        evidence=(f"{option_type} wall {then} -> {now}",),
    )


def _peak_strike(state: MarketState, expiry_id: int, option_type: str) -> Decimal | None:
    slice_ = expiry_by_id(state, expiry_id)
    if slice_ is None:
        return None
    legs = calls(slice_.legs) if option_type == "call" else puts(slice_.legs)
    observed = {leg.strike: leg.oi for leg in legs if leg.oi is not None}
    if not observed:
        return None
    return max(sorted(observed), key=lambda s: observed[s])


# --------------------------------------------------------------------- migration


def _oi_migration(
    ctx: ComputeContext, spec: FeatureSpec, option_type: str
) -> MetricValue | Unavailable:
    """OI-weighted displacement in strike points (`07` §2's worked declaration).

        sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)

    `s_ref` is the OI-weighted centroid of the reference state, so the result is a
    signed displacement of the book's centre of mass. Bounded by the strike range,
    which a property test asserts.
    """
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
    now = _oi_by_strike(ctx.state, expiry_id, option_type)
    then = _oi_by_strike(reference, expiry_id, option_type) if reference else None
    if not now or not then:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no OI at a window end")

    ref_total = sum(then.values())
    if ref_total == 0:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "reference OI is zero")
    s_ref = sum(s * Decimal(v) for s, v in then.items()) / Decimal(ref_total)

    numerator = Decimal(0)
    denominator = Decimal(0)
    for strike in sorted(set(now) | set(then)):
        delta = Decimal(now.get(strike, 0) - then.get(strike, 0))
        numerator += delta * (strike - s_ref)
        denominator += abs(delta)
    if denominator == 0:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "no OI change in the window")
    return emit(
        spec,
        ctx,
        scope,
        numerator / denominator,
        extra_inputs={"s_ref": str(s_ref), "denominator": str(denominator)},
        evidence=(f"centroid reference {s_ref}",),
    )


def _oi_by_strike(state: MarketState, expiry_id: int, option_type: str) -> dict[Decimal, int]:
    slice_ = expiry_by_id(state, expiry_id)
    if slice_ is None:
        return {}
    legs = calls(slice_.legs) if option_type == "call" else puts(slice_.legs)
    return {leg.strike: leg.oi for leg in legs if leg.oi is not None}


@feature(
    identifier="PUT_OI_MIGRATION",
    version=1,
    definition=(
        "Net displacement of put open interest between strikes over the window, "
        "weighted by OI magnitude and expressed in strike points."
    ),
    inputs=["oi_by_strike(PE)"],
    formula="sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)",
    units="strike_points",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=["oi_coverage>=0.95", "quality!=UNRELIABLE"],
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def put_oi_migration(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _oi_migration(ctx, put_oi_migration.__feature_spec__, "put")  # type: ignore[attr-defined]


@feature(
    identifier="CALL_OI_MIGRATION",
    version=1,
    definition=(
        "Net displacement of call open interest between strikes over the window, "
        "weighted by OI magnitude and expressed in strike points."
    ),
    inputs=["oi_by_strike(CE)"],
    formula="sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)",
    units="strike_points",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=["oi_coverage>=0.95", "quality!=UNRELIABLE"],
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def call_oi_migration(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _oi_migration(ctx, call_oi_migration.__feature_spec__, "call")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------- buildup


@feature(
    identifier="BUILDUP_CLASSIFICATION",
    version=1,
    definition=(
        "The OI/price matrix for an expiry: price up with OI up is LONG_BUILDUP, price "
        "down with OI up is SHORT_BUILDUP, price up with OI down is SHORT_COVERING, "
        "price down with OI down is LONG_UNWINDING. An interpretation of positioning, "
        "NOT a trading signal and carrying no directional recommendation."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)", "spot.ltp"],
    formula="matrix(sign(delta_price), sign(delta_oi))",
    units="category",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def buildup_classification(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = buildup_classification.__feature_spec__  # type: ignore[attr-defined]
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
    if reference is None:
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "no reference state"
        )
    now_slice = expiry_by_id(ctx.state, expiry_id)
    then_slice = expiry_by_id(reference, expiry_id)
    if now_slice is None or then_slice is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry absent")
    now_oi, then_oi = total_oi(now_slice.legs), total_oi(then_slice.legs)
    now_px, then_px = ctx.state.spot.ltp, reference.spot.ltp
    if None in (now_oi, then_oi, now_px, then_px):
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "price or OI absent")
    assert now_oi is not None and then_oi is not None
    assert now_px is not None and then_px is not None
    d_oi, d_px = now_oi - then_oi, now_px - then_px
    if d_oi == 0 or d_px == 0:
        # A flat leg makes the matrix undefined. Picking a quadrant anyway would be
        # inventing a classification the data does not support.
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "price or OI unchanged")
    label = {
        (True, True): "LONG_BUILDUP",
        (False, True): "SHORT_BUILDUP",
        (True, False): "SHORT_COVERING",
        (False, False): "LONG_UNWINDING",
    }[(d_px > 0, d_oi > 0)]
    return emit(
        spec,
        ctx,
        scope,
        label,
        extra_inputs={"d_oi": d_oi, "d_px": str(d_px)},
        evidence=(f"price {then_px}->{now_px}, OI {then_oi}->{now_oi}",),
    )


@feature(
    identifier="PRICE_OI_RELATIONSHIP",
    version=1,
    definition=(
        "Pearson correlation between the per-step change in spot price and the "
        "per-step change in total open interest across the window."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)", "spot.ltp"],
    formula="corr(delta_price_t, delta_oi_t) over the window",
    units="correlation",
    sampling_frequency="5m",
    lookback="30m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def price_oi_relationship(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = price_oi_relationship.__feature_spec__  # type: ignore[attr-defined]
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
    series = [*ctx.history, ctx.state]
    prices: list[Decimal] = []
    ois: list[int] = []
    for state in series:
        slice_ = expiry_by_id(state, expiry_id)
        oi = total_oi(slice_.legs) if slice_ else None
        if state.spot.ltp is None or oi is None:
            continue
        prices.append(state.spot.ltp)
        ois.append(oi)
    if len(prices) < 3:
        # Two points give a correlation of exactly +/-1 by construction, which is a
        # number with no information in it.
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "fewer than three usable points"
        )
    dp = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    do = [Decimal(ois[i] - ois[i - 1]) for i in range(1, len(ois))]
    corr = _pearson(dp, do)
    if corr is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "zero variance in a series")
    return emit(spec, ctx, scope, corr, extra_inputs={"points": len(dp)})


def _pearson(xs: list[Decimal], ys: list[Decimal]) -> Decimal | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs, start=Decimal(0)) / Decimal(n)
    my = sum(ys, start=Decimal(0)) / Decimal(n)
    cov = sum(((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)), start=Decimal(0))
    vx = sum(((x - mx) * (x - mx) for x in xs), start=Decimal(0))
    vy = sum(((y - my) * (y - my) for y in ys), start=Decimal(0))
    if vx == 0 or vy == 0:
        return None
    prod: Decimal = vx * vy
    return cov / prod.sqrt()


@feature(
    identifier="VOLUME_OI_RATIO",
    version=1,
    definition="Traded volume relative to open position for an expiry: turnover intensity.",
    inputs=["legs.volume", "legs.oi"],
    formula="sum(volume) / sum(oi)",
    units="ratio",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def volume_oi_ratio(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = volume_oi_ratio.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    volumes = [leg.volume for leg in slice_.legs if leg.volume is not None]
    oi = total_oi(slice_.legs)
    if not volumes or oi is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "volume or OI absent")
    ratio = safe_ratio(sum(volumes), oi)
    if ratio is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "open interest is zero")
    return emit(spec, ctx, scope, ratio, extra_inputs={"volume": sum(volumes), "oi": oi})


# -------------------------------------------------------------------------- PCR


@feature(
    identifier="PCR",
    version=1,
    definition=(
        "Put-call ratio by open interest for an expiry: total put OI divided by total "
        "call OI. Reported as a number with its convention documented. No bullish or "
        "bearish label is attached; such a claim requires context this metric does not "
        "carry."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="sum(oi_put) / sum(oi_call)",
    units="ratio",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def pcr(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = pcr.__feature_spec__  # type: ignore[attr-defined]
    scope = _expiry_scope(ctx)
    if scope is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    slice_ = expiry_by_id(ctx.state, int(ctx.params.get("expiry_id")))
    if slice_ is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")
    call_oi, put_oi = total_oi(calls(slice_.legs)), total_oi(puts(slice_.legs))
    if call_oi is None or put_oi is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "call or put OI absent")
    ratio = safe_ratio(put_oi, call_oi)
    if ratio is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "call OI is zero")
    return emit(spec, ctx, scope, ratio, extra_inputs={"call_oi": call_oi, "put_oi": put_oi})


@feature(
    identifier="PCR_OI_CHANGE",
    version=1,
    definition=(
        "Put-call ratio computed on the OI *change* over the window rather than on the "
        "OI level: the ratio of put OI added to call OI added. No directional label."
    ),
    inputs=["oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="delta_sum(oi_put) / delta_sum(oi_call)",
    units="ratio",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_QUALITY_OI,
    scope=Scope.EXPIRY,
    parameters=("expiry_id",),
)
def pcr_oi_change(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = pcr_oi_change.__feature_spec__  # type: ignore[attr-defined]
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
    now = expiry_by_id(ctx.state, expiry_id)
    then = expiry_by_id(reference, expiry_id) if reference else None
    if now is None or then is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry absent")
    nc, np_ = total_oi(calls(now.legs)), total_oi(puts(now.legs))
    tc, tp = total_oi(calls(then.legs)), total_oi(puts(then.legs))
    if None in (nc, np_, tc, tp):
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "OI absent at a window end"
        )
    assert nc is not None and np_ is not None and tc is not None and tp is not None
    ratio = safe_ratio(np_ - tp, nc - tc)
    if ratio is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "call OI change is zero")
    return emit(spec, ctx, scope, ratio, extra_inputs={"d_call": nc - tc, "d_put": np_ - tp})
