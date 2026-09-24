"""Futures — `07-ANALYTICS.md` §4.6.

Eight registered features. Futures are integrated into market state and signal
evidence, not a disconnected page: futures OI against futures price is the cleanest
long/short buildup read available, and it corroborates or contradicts options
positioning.

`FUTURES_OPTIONS_CONFIRMATION` reports **agreement or disagreement between two
positioning reads**, not a trade direction. It is deliberately a categorical
observation, and it is allowed to say the two disagree — which is often the most
informative answer.
"""

from __future__ import annotations

from decimal import Decimal

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.domains._shared import expiry_by_id, safe_ratio, total_oi
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason
from oipulse.marketstate.state import FuturesLeg, MarketState

__all__ = [
    "annualized_basis",
    "basis",
    "basis_change",
    "futures_oi",
    "futures_oi_change",
    "futures_options_confirmation",
    "futures_price",
    "spot_futures_divergence",
]

_Q = ["quality!=UNRELIABLE"]


def _scope(ctx: ComputeContext) -> ScopeRef:
    return ScopeRef.underlying(int(ctx.state.identity.underlying_id))


def _front(state: MarketState) -> FuturesLeg | None:
    """The front futures contract: lowest expiry id present, deterministically chosen.

    Chosen by expiry id rather than by list position so the answer cannot change with
    iteration order.
    """
    contracts = [f for f in state.futures if f.expiry_id is not None]
    if not contracts:
        return state.futures[0] if state.futures else None
    return min(contracts, key=lambda f: int(f.expiry_id or 0))


@feature(
    identifier="FUTURES_PRICE",
    version=1,
    definition="Last traded price of the front futures contract.",
    inputs=["futures.ltp"],
    formula="front_future.ltp",
    units="price",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def futures_price(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = futures_price.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    front = _front(ctx.state)
    if front is None or front.ltp is None:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "no futures price observed"
        )
    return emit(
        spec, ctx, scope, front.ltp, extra_inputs={"instrument_id": int(front.instrument_id)}
    )


@feature(
    identifier="FUTURES_OI",
    version=1,
    definition="Open interest of the front futures contract.",
    inputs=["futures.oi"],
    formula="front_future.oi",
    units="contracts",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def futures_oi(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = futures_oi.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    front = _front(ctx.state)
    if front is None or front.oi is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no futures OI observed")
    return emit(
        spec, ctx, scope, front.oi, extra_inputs={"instrument_id": int(front.instrument_id)}
    )


@feature(
    identifier="FUTURES_OI_CHANGE",
    version=1,
    definition=(
        "Change in front-futures open interest between the point-in-time reference "
        "state at the window start and the current state."
    ),
    inputs=["futures.oi"],
    formula="front_future.oi(T) - front_future.oi(T - lookback)",
    units="contracts",
    sampling_frequency="1m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def futures_oi_change(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = futures_oi_change.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    now = _front(ctx.state)
    then = _front(reference) if reference else None
    if now is None or then is None or now.oi is None or then.oi is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "futures OI absent")
    return emit(spec, ctx, scope, now.oi - then.oi, extra_inputs={"now": now.oi, "then": then.oi})


@feature(
    identifier="BASIS",
    version=1,
    definition="Front futures price minus spot, in price units. Positive is contango.",
    inputs=["futures.ltp", "spot.ltp"],
    formula="front_future.ltp - spot.ltp",
    units="price",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def basis(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = basis.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    front = _front(ctx.state)
    spot = ctx.state.spot.ltp
    if front is None or front.ltp is None or spot is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "futures or spot absent")
    return emit(
        spec,
        ctx,
        scope,
        front.ltp - spot,
        extra_inputs={"future": str(front.ltp), "spot": str(spot)},
    )


@feature(
    identifier="BASIS_CHANGE",
    version=1,
    definition="Change in basis over the window, in price units.",
    inputs=["futures.ltp", "spot.ltp"],
    formula="basis(T) - basis(T - lookback)",
    units="price",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
    depends_on=(("BASIS", 1),),
)
def basis_change(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = basis_change.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    now = _basis_of(ctx.state)
    then = _basis_of(reference) if reference else None
    if now is None or then is None:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, "basis absent at a window end"
        )
    return emit(spec, ctx, scope, now - then, extra_inputs={"now": str(now), "then": str(then)})


def _basis_of(state: MarketState) -> Decimal | None:
    front = _front(state)
    if front is None or front.ltp is None or state.spot.ltp is None:
        return None
    return front.ltp - state.spot.ltp


@feature(
    identifier="ANNUALIZED_BASIS",
    version=1,
    definition=(
        "Basis as an annualised rate: (future / spot - 1) scaled by the declared "
        "day-count over days to expiry. Requires days_to_expiry, which the caller "
        "resolves from the expiry calendar as of observed_at."
    ),
    inputs=["futures.ltp", "spot.ltp", "expiry.days_to_expiry@observed_at"],
    formula="(future / spot - 1) * (day_count / days_to_expiry)",
    units="rate_annualised",
    sampling_frequency="5m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
    parameters=("days_to_expiry", "day_count"),
)
def annualized_basis(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = annualized_basis.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    days_raw = ctx.params.get("days_to_expiry", None)
    if days_raw is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            "days_to_expiry for observed_at was not supplied",
        )
    days = Decimal(str(days_raw))
    if days <= 0:
        # At or past expiry the annualisation factor is undefined, not enormous.
        return unavailable(
            spec, scope, UnavailableReason.UNDEFINED, "days_to_expiry must be positive"
        )
    front = _front(ctx.state)
    spot = ctx.state.spot.ltp
    if front is None or front.ltp is None or spot is None or spot == 0:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "futures or spot absent")
    day_count = Decimal(str(ctx.params.get("day_count", 365)))
    value = (front.ltp / spot - Decimal(1)) * (day_count / days)
    return emit(
        spec, ctx, scope, value, extra_inputs={"days": str(days), "day_count": str(day_count)}
    )


@feature(
    identifier="SPOT_FUTURES_DIVERGENCE",
    version=1,
    definition=(
        "Difference between the futures return and the spot return over the window. "
        "Non-zero means the two legs moved apart; the sign says which outpaced."
    ),
    inputs=["futures.ltp", "spot.ltp"],
    formula="(f(T)/f(T-lb) - 1) - (s(T)/s(T-lb) - 1)",
    units="ratio",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="ratio",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
)
def spot_futures_divergence(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = spot_futures_divergence.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    if reference is None:
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "no reference state"
        )
    now_f, then_f = _front(ctx.state), _front(reference)
    now_s, then_s = ctx.state.spot.ltp, reference.spot.ltp
    if now_f is None or then_f is None or now_f.ltp is None or then_f.ltp is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "futures absent")
    if now_s is None or then_s is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "spot absent")
    fut_ret = safe_ratio(now_f.ltp - then_f.ltp, then_f.ltp)
    spot_ret = safe_ratio(now_s - then_s, then_s)
    if fut_ret is None or spot_ret is None:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "a reference price is zero")
    return emit(
        spec,
        ctx,
        scope,
        fut_ret - spot_ret,
        extra_inputs={"futures_return": str(fut_ret), "spot_return": str(spot_ret)},
    )


@feature(
    identifier="FUTURES_OPTIONS_CONFIRMATION",
    version=1,
    definition=(
        "Whether the futures OI/price read agrees with the options OI read over the "
        "window: CONFIRMING when both open interest series move the same way, "
        "DIVERGING when they move oppositely. A comparison of two positioning reads, "
        "not a trade direction and carrying no recommendation."
    ),
    inputs=["futures.oi", "oi_by_strike(CE)", "oi_by_strike(PE)"],
    formula="agree(sign(delta futures_oi), sign(delta options_oi))",
    units="category",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=["quality!=UNRELIABLE", "oi_coverage>=0.95"],
    scope=Scope.UNDERLYING,
    parameters=("expiry_id",),
)
def futures_options_confirmation(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = futures_options_confirmation.__feature_spec__  # type: ignore[attr-defined]
    scope = _scope(ctx)
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(spec, scope, UnavailableReason.INVALID_PARAMS, "expiry_id is required")
    if not ctx.covers(spec.lookback_delta):
        return unavailable(
            spec,
            scope,
            UnavailableReason.INSUFFICIENT_HISTORY,
            f"history does not cover {spec.lookback}",
        )
    reference = ctx.reference_state(spec.lookback_delta)
    if reference is None:
        return unavailable(
            spec, scope, UnavailableReason.INSUFFICIENT_HISTORY, "no reference state"
        )
    now_f, then_f = _front(ctx.state), _front(reference)
    if now_f is None or then_f is None or now_f.oi is None or then_f.oi is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "futures OI absent")
    now_slice = expiry_by_id(ctx.state, int(expiry_raw))
    then_slice = expiry_by_id(reference, int(expiry_raw))
    now_o = total_oi(now_slice.legs) if now_slice else None
    then_o = total_oi(then_slice.legs) if then_slice else None
    if now_o is None or then_o is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "options OI absent")
    d_fut, d_opt = now_f.oi - then_f.oi, now_o - then_o
    if d_fut == 0 or d_opt == 0:
        return unavailable(
            spec,
            scope,
            UnavailableReason.UNDEFINED,
            "an OI series is unchanged; agreement is undefined",
        )
    label = "CONFIRMING" if (d_fut > 0) == (d_opt > 0) else "DIVERGING"
    return emit(
        spec,
        ctx,
        scope,
        label,
        extra_inputs={"d_futures_oi": d_fut, "d_options_oi": d_opt},
        evidence=(f"futures OI {d_fut:+d}, options OI {d_opt:+d}",),
    )
