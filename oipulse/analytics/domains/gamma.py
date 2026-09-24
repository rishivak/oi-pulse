"""Gamma exposure (GEX) — `07-ANALYTICS.md` §4.4.

Six registered features.

**The dealer-positioning sign convention is a registered parameter, not a hidden
assumption, and the two variants are registered separately** rather than one being
silently assumed:

| Convention | Meaning |
|---|---|
| `DEALER_SHORT_CALLS_LONG_PUTS` | dealers are short calls and long puts: `+gamma` on calls, `-gamma` on puts |
| `DEALER_LONG_ALL` | dealers hold both sides long: `+gamma` on everything |

Both are legitimate readings of who is on the other side of retail flow, they produce
different signs, and neither is a fact about the market. Declaring it as a parameter is
what stops a number's meaning depending on an assumption nobody wrote down.

> **Presented as market-structure information only.** The system does not assert
> "positive GEX = bullish" or any equivalent, and no feature here carries a directional
> label. Such a claim requires context the metric alone does not carry (`07` §4.4,
> brief §12 and §31).
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from enum import StrEnum

from oipulse.analytics.context import ComputeContext
from oipulse.analytics.domains._shared import expiry_by_id
from oipulse.analytics.emit import emit
from oipulse.analytics.registry import FeatureSpec, feature, unavailable
from oipulse.analytics.values import MetricValue, Scope, ScopeRef, Unavailable, UnavailableReason

__all__ = [
    "DealerConvention",
    "gamma_flip_level",
    "gex_by_expiry",
    "gex_by_strike",
    "gex_concentration",
    "gex_profile",
    "gex_total",
]

_Q = ["quality!=UNRELIABLE", "greeks_coverage>=0.90", "oi_coverage>=0.95"]


class DealerConvention(StrEnum):
    """Who is assumed to be on the other side. A declared parameter, never implicit."""

    DEALER_SHORT_CALLS_LONG_PUTS = "dealer_short_calls_long_puts"
    DEALER_LONG_ALL = "dealer_long_all"

    def sign_for(self, option_type: str) -> Decimal:
        if self is DealerConvention.DEALER_LONG_ALL:
            return Decimal(1)
        return Decimal(1) if option_type == "call" else Decimal(-1)


def _convention(ctx: ComputeContext) -> DealerConvention | None:
    raw = ctx.params.get("convention", DealerConvention.DEALER_SHORT_CALLS_LONG_PUTS.value)
    try:
        return DealerConvention(str(raw))
    except ValueError:
        return None


def _lot(ctx: ComputeContext) -> Decimal | None:
    raw = ctx.params.get("lot_size", None)
    return None if raw is None else Decimal(str(raw))


def _gex_by_strike(ctx: ComputeContext, expiry_id: int) -> dict[Decimal, Decimal] | None:
    """Signed gamma exposure per strike under the declared convention.

    Returns `None` when the inputs are absent, rather than an empty dict -- an empty
    profile and an unobservable one are different facts.
    """
    convention = _convention(ctx)
    lot = _lot(ctx)
    if convention is None or lot is None or lot <= 0:
        return None
    slice_ = expiry_by_id(ctx.state, expiry_id)
    if slice_ is None:
        return None
    multiplier = Decimal(str(ctx.params.get("contract_multiplier", 1)))
    out: dict[Decimal, Decimal] = {}
    seen = False
    for leg in slice_.legs:
        if leg.gamma is None or leg.oi is None:
            continue
        seen = True
        contribution = (
            convention.sign_for(leg.option_type) * leg.gamma * Decimal(leg.oi) * lot * multiplier
        )
        out[leg.strike] = out.get(leg.strike, Decimal(0)) + contribution
    return dict(sorted(out.items())) if seen else None


def _guard(ctx: ComputeContext, spec: FeatureSpec, scope: ScopeRef) -> Unavailable | None:
    if _convention(ctx) is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.INVALID_PARAMS,
            f"unknown dealer convention {ctx.params.get('convention', None)!r}",
        )
    lot = _lot(ctx)
    if lot is None:
        return unavailable(
            spec,
            scope,
            UnavailableReason.MISSING_INPUT,
            "lot_size for observed_at was not supplied",
        )
    if lot <= 0:
        return unavailable(
            spec, scope, UnavailableReason.INVALID_PARAMS, "lot_size must be positive"
        )
    return None


_PARAMS = ("expiry_id", "convention", "lot_size", "contract_multiplier")
_INPUTS = ["gamma_by_strike", "legs.oi", "instrument_version.lot_size@observed_at"]


@feature(
    identifier="GEX_BY_STRIKE",
    version=1,
    definition=(
        "Signed gamma exposure per strike for one expiry, under the declared dealer "
        "convention. Market-structure information; no directional label is attached."
    ),
    inputs=_INPUTS,
    formula="per strike: sum(sign(convention, type) * gamma * oi * lot_size * multiplier)",
    units="gamma_shares_per_point",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=_PARAMS,
)
def gex_by_strike(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = gex_by_strike.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    refused = _guard(ctx, spec, scope)
    if refused is not None:
        return refused
    profile = _gex_by_strike(ctx, int(expiry_raw))
    if profile is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no gamma and OI observed")
    value = tuple((str(strike), amount) for strike, amount in profile.items())
    return emit(
        spec,
        ctx,
        scope,
        value,
        extra_inputs={"convention": str(_convention(ctx)), "strikes": len(profile)},
    )


@feature(
    identifier="GEX_TOTAL",
    version=1,
    definition=(
        "Total signed gamma exposure across every expiry in the state, under the "
        "declared dealer convention. Market-structure information only."
    ),
    inputs=_INPUTS,
    formula="sum over expiries and strikes of GEX_BY_STRIKE",
    units="gamma_shares_per_point",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.UNDERLYING,
    parameters=("convention", "lot_size", "contract_multiplier"),
)
def gex_total(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = gex_total.__feature_spec__  # type: ignore[attr-defined]
    scope = ScopeRef.underlying(int(ctx.state.identity.underlying_id))
    refused = _guard(ctx, spec, scope)
    if refused is not None:
        return refused
    total = Decimal(0)
    seen = False
    for slice_ in ctx.state.expiries:
        profile = _gex_by_strike(ctx, int(slice_.expiry_id))
        if profile is None:
            continue
        seen = True
        total += sum(profile.values())
    if not seen:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no gamma and OI observed")
    return emit(spec, ctx, scope, total, extra_inputs={"convention": str(_convention(ctx))})


@feature(
    identifier="GEX_BY_EXPIRY",
    version=1,
    definition=(
        "Total signed gamma exposure for one expiry under the declared dealer "
        "convention. Market-structure information only."
    ),
    inputs=_INPUTS,
    formula="sum over strikes of GEX_BY_STRIKE for the expiry",
    units="gamma_shares_per_point",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=_PARAMS,
)
def gex_by_expiry(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = gex_by_expiry.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    refused = _guard(ctx, spec, scope)
    if refused is not None:
        return refused
    profile = _gex_by_strike(ctx, int(expiry_raw))
    if profile is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no gamma and OI observed")
    return emit(
        spec, ctx, scope, sum(profile.values()), extra_inputs={"convention": str(_convention(ctx))}
    )


@feature(
    identifier="GEX_CONCENTRATION",
    version=1,
    definition=(
        "Herfindahl index over absolute per-strike gamma exposure for one expiry: how "
        "concentrated gamma is at a few strikes. 1 means all of it sits at one strike."
    ),
    inputs=_INPUTS,
    formula="sum((|gex_s| / sum(|gex|)) ** 2)",
    units="index",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=_PARAMS,
)
def gex_concentration(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = gex_concentration.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    refused = _guard(ctx, spec, scope)
    if refused is not None:
        return refused
    profile = _gex_by_strike(ctx, int(expiry_raw))
    if profile is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no gamma and OI observed")
    magnitudes = [abs(v) for v in profile.values()]
    grand = sum(magnitudes)
    if grand == 0:
        return unavailable(spec, scope, UnavailableReason.UNDEFINED, "total absolute GEX is zero")
    return emit(
        spec,
        ctx,
        scope,
        sum((m / grand) ** 2 for m in magnitudes),
        extra_inputs={"strikes": len(profile)},
    )


@feature(
    identifier="GEX_PROFILE",
    version=1,
    definition=(
        "Cumulative signed gamma exposure by ascending strike for one expiry: the "
        "running total that GAMMA_FLIP_LEVEL locates the zero crossing of."
    ),
    inputs=_INPUTS,
    formula="cumulative sum over ascending strikes of GEX_BY_STRIKE",
    units="gamma_shares_per_point",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=_PARAMS,
)
def gex_profile(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = gex_profile.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    refused = _guard(ctx, spec, scope)
    if refused is not None:
        return refused
    profile = _gex_by_strike(ctx, int(expiry_raw))
    if profile is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no gamma and OI observed")
    running = Decimal(0)
    cumulative: list[tuple[str, Decimal]] = []
    for strike in sorted(profile):
        running += profile[strike]
        cumulative.append((str(strike), running))
    return emit(
        spec, ctx, scope, tuple(cumulative), extra_inputs={"convention": str(_convention(ctx))}
    )


@feature(
    identifier="GAMMA_FLIP_LEVEL",
    version=1,
    definition=(
        "Strike at which cumulative signed gamma exposure crosses zero, linearly "
        "interpolated between the bracketing strikes. Unavailable when the cumulative "
        "profile never changes sign. Market-structure information; no directional "
        "label is attached."
    ),
    inputs=_INPUTS,
    formula="strike where cumulative_gex(s) crosses 0, linear between brackets",
    units="strike",
    sampling_frequency="1m",
    lookback="0s",
    availability_delay="2s",
    normalization="none",
    quality_requirements=_Q,
    scope=Scope.EXPIRY,
    parameters=_PARAMS,
)
def gamma_flip_level(ctx: ComputeContext) -> MetricValue | Unavailable:
    spec = gamma_flip_level.__feature_spec__  # type: ignore[attr-defined]
    expiry_raw = ctx.params.get("expiry_id", None)
    if expiry_raw is None:
        return unavailable(
            spec, ScopeRef.underlying(0), UnavailableReason.INVALID_PARAMS, "expiry_id is required"
        )
    scope = ScopeRef.expiry(int(expiry_raw))
    refused = _guard(ctx, spec, scope)
    if refused is not None:
        return refused
    profile = _gex_by_strike(ctx, int(expiry_raw))
    if profile is None:
        return unavailable(spec, scope, UnavailableReason.MISSING_INPUT, "no gamma and OI observed")

    running = Decimal(0)
    points: list[tuple[Decimal, Decimal]] = []
    for strike in sorted(profile):
        running += profile[strike]
        points.append((strike, running))

    for (s0, c0), (s1, c1) in itertools.pairwise(points):
        if c0 == 0:
            return emit(spec, ctx, scope, s0, extra_inputs={"exact": True})
        if (c0 < 0) != (c1 < 0):
            # Linear interpolation between the bracketing strikes. Reporting the
            # nearer strike instead would quantise the level to the strike grid and
            # imply a precision the profile does not have.
            crossing = s0 + (s1 - s0) * (-c0) / (c1 - c0)
            return emit(
                spec,
                ctx,
                scope,
                crossing,
                extra_inputs={"bracket": [str(s0), str(s1)]},
                evidence=(f"cumulative GEX {c0} -> {c1} between {s0} and {s1}",),
            )
    return unavailable(
        spec,
        scope,
        UnavailableReason.UNDEFINED,
        "cumulative gamma exposure does not cross zero in this chain",
    )
