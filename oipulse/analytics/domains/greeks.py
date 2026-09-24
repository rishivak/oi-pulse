"""Greeks exposure — `07-ANALYTICS.md` §4.3.

    exposure = greek x OI x lot_size x contract_multiplier

**The aggregation convention is explicit and versioned.** Two things about it matter
more than the arithmetic:

* **Lot size is resolved from the instrument version valid at `observed_at`**, not the
  current one (`01-DOMAIN_MODEL.md` §3). A lot-size revision must not retroactively
  rewrite historical exposure — that would silently change every past number the moment
  an exchange republished a contract.
* Because this layer is pure and may not query the instrument store, the lot size is
  supplied as an explicit parameter resolved by the caller *for that `observed_at`*.
  Absent it, these features return `MISSING_INPUT` rather than assuming 1 or 50. A
  guessed lot size is a wrong exposure that looks right, and `18-ROADMAP.md` names
  lot-size correctness as gating all exposure work because the legacy seeds are stale.

Scope is CONTRACT per leg, with aggregates at STRIKE, EXPIRY and UNDERLYING selected by
the `aggregate` parameter. A property test asserts the aggregates sum consistently.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import ComputeFn, FeatureSpec, feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason
from oipulse.marketstate.state import OptionLeg

__all__ = ["delta_exposure", "gamma_exposure", "theta_exposure", "vega_exposure"]

_Q = ["quality!=UNRELIABLE", "greeks_coverage>=0.90", "oi_coverage>=0.95"]


def _lot_size(ctx: ComputeContext) -> Decimal | None:
    """Lot size for `observed_at`, supplied by the caller.

    Returns `None` when absent so the feature can refuse. There is deliberately no
    default: NIFTY's lot size has changed repeatedly, and a hardcoded value would
    silently misstate every exposure computed for a date on the other side of a
    revision.
    """
    raw = ctx.params.get("lot_size", None)
    return None if raw is None else Decimal(str(raw))


def _multiplier(ctx: ComputeContext) -> Decimal:
    """Contract multiplier. 1 for NSE index options; declared so it is not implicit."""
    return Decimal(str(ctx.params.get("contract_multiplier", 1)))


def _greek_of(leg: OptionLeg, name: str) -> Decimal | None:
    return {
        "delta": leg.delta,
        "gamma": leg.gamma,
        "theta": leg.theta,
        "vega": leg.vega,
    }[name]


def _exposure(ctx: ComputeContext, spec: FeatureSpec, greek: str) -> MetricValue | Unavailable:
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    expiry_id = int(expiry_raw)
    aggregate = str(ctx.params.get("aggregate", "expiry"))
    scope = {
        "expiry": ScopeRef.expiry(expiry_id),
        "underlying": ScopeRef.underlying(int(ctx.state.identity.underlying_id)),
    }.get(aggregate)
    if scope is None:
        return unavailable(
            spec,
            ScopeRef.expiry(expiry_id),
            UnavailableReason.INVALID_PARAMS,
            f"unknown aggregate {aggregate!r}; expected 'expiry' or 'underlying'",
        )

    lot = _lot_size(ctx)
    if lot is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            "lot_size for observed_at was not supplied; exposure is not "
            "computed from a guessed lot size",
        )
    if lot <= 0:
        return unavailable(
            spec, scope, UnavailableReason.INVALID_PARAMS, "lot_size must be positive"
        )
    multiplier = _multiplier(ctx)

    slices = (
        ctx.state.expiries
        if aggregate == "underlying"
        else tuple(s for s in ctx.state.expiries if int(s.expiry_id) == expiry_id)
    )
    if not slices:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "expiry not in state")

    total = Decimal(0)
    contributing = 0
    for slice_ in slices:
        for leg in slice_.legs:
            value = _greek_of(leg, greek)
            if value is None or leg.oi is None:
                continue
            total += value * Decimal(leg.oi) * lot * multiplier
            contributing += 1
    if contributing == 0:
        return unavailable(
            spec, scope, UnavailableReason.MISSING_INPUT, f"no leg carries both {greek} and OI"
        )
    return emit(
        spec,
        ctx,
        scope,
        total,
        extra_inputs={
            "lot_size": str(lot),
            "multiplier": str(multiplier),
            "legs": contributing,
            "aggregate": aggregate,
        },
        evidence=(f"{contributing} legs x {greek} x OI x {lot} x {multiplier}",),
    )


def _declare(
    identifier: str, greek: str, unit: str, definition: str
) -> Callable[[ComputeFn], ComputeFn]:
    return feature(
        identifier=identifier,
        version=1,
        definition=definition,
        inputs=[f"legs.{greek}", "legs.oi", "instrument_version.lot_size@observed_at"],
        formula=f"sum({greek} * oi * lot_size * contract_multiplier)",
        units=unit,
        sampling_frequency="1m",
        lookback="0s",
        availability_delay="2s",
        normalization="none",
        quality_requirements=_Q,
        scope=Scope.EXPIRY,
        parameters=("expiry_id", "lot_size", "contract_multiplier", "aggregate"),
    )


@_declare(
    "DELTA_EXPOSURE",
    "delta",
    "delta_shares",
    "Sum of delta x open interest x lot size x contract multiplier, over the legs of "
    "an expiry or the whole underlying. Lot size is the one valid at observed_at.",
)
def delta_exposure(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _exposure(ctx, delta_exposure.__feature_spec__, "delta")  # type: ignore[attr-defined]


@_declare(
    "GAMMA_EXPOSURE",
    "gamma",
    "gamma_shares_per_point",
    "Sum of gamma x open interest x lot size x contract multiplier. Carries no dealer "
    "sign convention; see the GEX family for the dealer-positioning variants.",
)
def gamma_exposure(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _exposure(ctx, gamma_exposure.__feature_spec__, "gamma")  # type: ignore[attr-defined]


@_declare(
    "THETA_EXPOSURE",
    "theta",
    "currency_per_day",
    "Sum of theta x open interest x lot size x contract multiplier.",
)
def theta_exposure(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _exposure(ctx, theta_exposure.__feature_spec__, "theta")  # type: ignore[attr-defined]


@_declare(
    "VEGA_EXPOSURE",
    "vega",
    "currency_per_vol_point",
    "Sum of vega x open interest x lot size x contract multiplier.",
)
def vega_exposure(ctx: ComputeContext) -> MetricValue | Unavailable:
    return _exposure(ctx, vega_exposure.__feature_spec__, "vega")  # type: ignore[attr-defined]
