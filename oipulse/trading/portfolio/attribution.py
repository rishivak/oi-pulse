"""P&L attribution — greek-based explain with an honest residual.

`11-TRADING.md` §8 defines the method and the component set exactly:

```
P&L
├── direction        delta-driven
├── volatility       vega-driven
├── time decay       theta-driven
├── convexity        gamma-driven
├── execution        fill price vs decision price
├── slippage         fill price vs expected
└── costs            brokerage, taxes, fees
```

> Decomposition uses a documented, versioned method (greek-based P&L explain with a
> residual term). The residual is **reported, not hidden** — a large residual signals
> that the decomposition is missing something, which is information.

`18-ROADMAP.md` Phase 11 names the same thing as the acceptance criterion and the
risk: *"Attribution residual large enough to be meaningless — mitigated by reporting
it prominently; a large residual is information, not something to hide."*

### The residual is computed, never balanced

This is the single most important property in the module, and the easiest to get
subtly wrong. `residual = total_pnl - sum(components)`. It is an **output**, not a
free parameter:

* No component is ever adjusted to make the sum come out right.
* The residual is not folded into "other", "execution" or "costs".
* A large residual is surfaced, not smoothed.

`AttributionResult.reconciles` asserts `total == sum(components) + residual`, which is
true by construction — and that is the point. It is an identity, not a test of the
model's quality. `residual_fraction` is the number that says how much the model
actually explained, and it is reported beside every total.

### The versioned method

`ATTRIBUTION_METHOD_VERSION` is part of the content hash. A change to the
decomposition formula produces a different identity, so an attribution computed in
March stays interpretable after the method changes in June — the same discipline as
feature versions in `07` §2.

The greek explain is the standard second-order expansion:

```
dP ~= delta*dS + 0.5*gamma*dS^2 + vega*dSigma + theta*dt
```

Each term is scaled by position quantity and contract economics. What is *not*
modelled — third-order terms, cross-greeks, rate sensitivity, dividend effects — is
precisely what lands in the residual, and that is the residual's job.

### Unattributed performance (brief §15)

> A trade whose strategy identity is unavailable must not silently become "Strategy
> A". Represent unattributed performance explicitly.

`UNATTRIBUTED` is a declared bucket id, not an empty string and not the first
strategy in the list. A slice that cannot name its owner says so.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

__all__ = [
    "ATTRIBUTION_METHOD",
    "ATTRIBUTION_METHOD_VERSION",
    "UNATTRIBUTED",
    "AttributionBucket",
    "AttributionComponent",
    "AttributionResult",
    "AttributionSlice",
    "ComponentAmount",
    "GreekInputs",
    "attribute_position",
    "roll_up",
]

#: The documented, versioned method (`11` §8). Part of every content hash, so a
#: formula change cannot silently redefine what a stored attribution meant.
ATTRIBUTION_METHOD = "GREEK_EXPLAIN_SECOND_ORDER"
ATTRIBUTION_METHOD_VERSION = 1

#: Brief §15: performance whose owner is unknown is labelled, never assigned.
UNATTRIBUTED = "UNATTRIBUTED"


class AttributionComponent(StrEnum):
    """Exactly the seven `11` §8 defines. None is invented, none is omitted."""

    DIRECTION = "DIRECTION"
    VOLATILITY = "VOLATILITY"
    TIME_DECAY = "TIME_DECAY"
    CONVEXITY = "CONVEXITY"
    EXECUTION = "EXECUTION"
    SLIPPAGE = "SLIPPAGE"
    COSTS = "COSTS"


class AttributionBucket(StrEnum):
    """The slices `11` §8 names. No additional dimension is invented (brief §14)."""

    PORTFOLIO = "PORTFOLIO"
    STRATEGY = "STRATEGY"
    SIGNAL = "SIGNAL"
    UNDERLYING = "UNDERLYING"
    EXPIRY = "EXPIRY"
    OPTION_TYPE = "OPTION_TYPE"
    TIME_OF_DAY = "TIME_OF_DAY"
    #: `11` §8: "the most useful cut, and only possible because regime is persisted
    #: per state rather than computed ad hoc".
    REGIME = "REGIME"
    INSTRUMENT = "INSTRUMENT"


@dataclass(frozen=True, slots=True)
class GreekInputs:
    """Per-position greeks and the market moves they act on.

    Every field is optional and separately absent. One missing greek must not
    poison the others: a position with delta but no vega can still have its
    direction term computed, and the vega term simply does not contribute — which
    the residual then absorbs and reports.
    """

    #: Position-level greeks, already scaled by quantity and economics by the caller
    #: from the canonical Phase 4 features. Not recomputed here (brief §16).
    delta: Decimal | None = None
    gamma: Decimal | None = None
    vega: Decimal | None = None
    theta: Decimal | None = None
    #: The moves over the attribution window.
    underlying_move: Decimal | None = None
    implied_vol_move: Decimal | None = None
    #: Elapsed time in days, matching theta's conventional per-day quotation.
    time_elapsed_days: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "delta": num(self.delta),
            "gamma": num(self.gamma),
            "vega": num(self.vega),
            "theta": num(self.theta),
            "underlying_move": num(self.underlying_move),
            "implied_vol_move": num(self.implied_vol_move),
            "time_elapsed_days": num(self.time_elapsed_days),
        }


@dataclass(frozen=True, slots=True)
class ComponentAmount:
    """One component's contribution, and whether it could be computed at all."""

    component: AttributionComponent
    amount: Decimal
    #: False when an input was missing. The component contributes zero *and says
    #: so*, rather than silently being zero because nothing was known.
    computed: bool = True
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component.value,
            "amount": str(self.amount),
            "computed": self.computed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class AttributionResult:
    """A decomposition of one total, with its residual.

    The invariant `total == sum(components) + residual` holds **by construction**,
    because the residual is defined as the difference. `reconciles` asserts it as a
    guard against an arithmetic mistake, not as evidence the model is good — that
    is what `residual_fraction` reports.
    """

    total_pnl: Decimal
    components: tuple[ComponentAmount, ...]
    bucket: AttributionBucket = AttributionBucket.PORTFOLIO
    bucket_id: str = UNATTRIBUTED
    method: str = ATTRIBUTION_METHOD
    method_version: int = ATTRIBUTION_METHOD_VERSION

    @property
    def explained(self) -> Decimal:
        return sum((c.amount for c in self.components), Decimal(0))

    @property
    def residual(self) -> Decimal:
        """What the decomposition did not explain.

        Computed, never chosen. Nothing in this class can adjust a component to
        shrink it, which is the whole reason it is a property rather than a field.
        """
        return self.total_pnl - self.explained

    @property
    def residual_fraction(self) -> Decimal | None:
        """Residual as a share of |total|. None when the total is zero.

        The honest headline number: a decomposition explaining 99% of a move and one
        explaining 20% both "reconcile", and only this distinguishes them.
        """
        if self.total_pnl == 0:
            return None
        return abs(self.residual) / abs(self.total_pnl)

    @property
    def reconciles(self) -> bool:
        return self.total_pnl == self.explained + self.residual

    @property
    def uncomputed_components(self) -> tuple[AttributionComponent, ...]:
        """Components whose inputs were missing. Usually the residual's explanation."""
        return tuple(c.component for c in self.components if not c.computed)

    def component(self, component: AttributionComponent) -> Decimal:
        for candidate in self.components:
            if candidate.component is component:
                return candidate.amount
        return Decimal(0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket.value,
            "bucket_id": self.bucket_id,
            "method": self.method,
            "method_version": self.method_version,
            "total_pnl": str(self.total_pnl),
            "components": [c.as_dict() for c in self.components],
            "explained": str(self.explained),
            # Always present, always beside the total. Never omitted when small.
            "residual": str(self.residual),
            "residual_fraction": (
                None if self.residual_fraction is None else str(self.residual_fraction)
            ),
            "reconciles": self.reconciles,
            "uncomputed_components": [c.value for c in self.uncomputed_components],
        }

    @property
    def content_digest(self) -> str:
        """Semantic identity. Includes the method version; excludes nothing else."""
        return (
            "attr_"
            + hashlib.sha256(
                json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:24]
        )


def attribute_position(
    *,
    total_pnl: Decimal,
    greeks: GreekInputs,
    execution_pnl: Decimal | None = None,
    slippage_pnl: Decimal | None = None,
    costs: Decimal = Decimal(0),
    bucket: AttributionBucket = AttributionBucket.INSTRUMENT,
    bucket_id: str = UNATTRIBUTED,
) -> AttributionResult:
    """Decompose one total into the seven components. Pure and deterministic.

    The greek terms use the standard second-order expansion; each is computed only
    when **all** its inputs are present, and reports `computed=False` otherwise. A
    component that could not be computed contributes zero and is named, so the
    residual it creates has a stated cause rather than being unexplained twice over.

    `costs` is subtracted, not added: fees reduce P&L. It is taken from the fill's
    own cost breakdown, which the Phase 7 model keeps separate from the fill price —
    so there is no double-count of fees already embedded in the price, because they
    never were (brief §17).
    """
    components: list[ComponentAmount] = []

    def term(
        component: AttributionComponent,
        inputs: Sequence[Decimal | None],
        compute: Any,
        missing: str,
    ) -> None:
        if any(value is None for value in inputs):
            components.append(
                ComponentAmount(
                    component=component,
                    amount=Decimal(0),
                    computed=False,
                    detail=f"{missing} unavailable; contributes nothing and the "
                    f"shortfall appears in the residual",
                )
            )
            return
        components.append(ComponentAmount(component=component, amount=compute()))

    # delta * dS
    term(
        AttributionComponent.DIRECTION,
        [greeks.delta, greeks.underlying_move],
        lambda: (greeks.delta or Decimal(0)) * (greeks.underlying_move or Decimal(0)),
        "delta or the underlying move",
    )
    # 0.5 * gamma * dS^2
    term(
        AttributionComponent.CONVEXITY,
        [greeks.gamma, greeks.underlying_move],
        lambda: (
            Decimal("0.5")
            * (greeks.gamma or Decimal(0))
            * (greeks.underlying_move or Decimal(0)) ** 2
        ),
        "gamma or the underlying move",
    )
    # vega * dSigma
    term(
        AttributionComponent.VOLATILITY,
        [greeks.vega, greeks.implied_vol_move],
        lambda: (greeks.vega or Decimal(0)) * (greeks.implied_vol_move or Decimal(0)),
        "vega or the implied-vol move",
    )
    # theta * dt
    term(
        AttributionComponent.TIME_DECAY,
        [greeks.theta, greeks.time_elapsed_days],
        lambda: (greeks.theta or Decimal(0)) * (greeks.time_elapsed_days or Decimal(0)),
        "theta or the elapsed time",
    )

    term(
        AttributionComponent.EXECUTION,
        [execution_pnl],
        lambda: execution_pnl or Decimal(0),
        "the decision price",
    )
    term(
        AttributionComponent.SLIPPAGE,
        [slippage_pnl],
        lambda: slippage_pnl or Decimal(0),
        "the expected price",
    )
    # Costs reduce P&L, so the contribution is negative.
    components.append(ComponentAmount(component=AttributionComponent.COSTS, amount=-costs))

    return AttributionResult(
        total_pnl=total_pnl,
        components=tuple(components),
        bucket=bucket,
        bucket_id=bucket_id,
    )


@dataclass(frozen=True, slots=True)
class AttributionSlice:
    """One bucket's decomposition, plus the children that rolled into it."""

    bucket: AttributionBucket
    bucket_id: str
    result: AttributionResult
    children: tuple[AttributionSlice, ...] = field(default_factory=tuple)

    @property
    def is_unattributed(self) -> bool:
        return self.bucket_id == UNATTRIBUTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket.value,
            "bucket_id": self.bucket_id,
            "is_unattributed": self.is_unattributed,
            "attribution": self.result.as_dict(),
            "children": [c.as_dict() for c in self.children],
        }


def roll_up(
    children: Sequence[AttributionSlice],
    *,
    bucket: AttributionBucket,
    bucket_id: str,
) -> AttributionSlice:
    """Aggregate children into a parent. Deterministic and exact.

    Brief §14 requires child attribution to roll up deterministically into parent
    attribution. Two properties make that true:

    * **Component-wise summation.** Each component is summed across children, so the
      parent's DIRECTION is the sum of its children's, and so on.
    * **The parent's residual is the sum of the children's**, because both the total
      and every component are sums, and subtraction distributes. It is *not*
      recomputed against some independently-measured parent total, which could
      differ and would silently absorb the discrepancy.

    Children are sorted by bucket id so the parent's content digest does not depend
    on input order.
    """
    ordered = tuple(sorted(children, key=lambda c: c.bucket_id))
    totals: dict[AttributionComponent, Decimal] = dict.fromkeys(AttributionComponent, Decimal(0))
    computed: dict[AttributionComponent, bool] = dict.fromkeys(AttributionComponent, True)

    for child in ordered:
        for amount in child.result.components:
            totals[amount.component] += amount.amount
            if not amount.computed:
                # One uncomputed child makes the parent's component partial too.
                # Reporting it as fully computed would hide the gap at every level
                # above the one where it happened.
                computed[amount.component] = False

    total_pnl = sum((c.result.total_pnl for c in ordered), Decimal(0))
    return AttributionSlice(
        bucket=bucket,
        bucket_id=bucket_id,
        result=AttributionResult(
            total_pnl=total_pnl,
            components=tuple(
                ComponentAmount(
                    component=component,
                    amount=totals[component],
                    computed=computed[component],
                    detail=""
                    if computed[component]
                    else "at least one child could not compute this component",
                )
                for component in AttributionComponent
            ),
            bucket=bucket,
            bucket_id=bucket_id,
        ),
        children=ordered,
    )


def group_by(
    slices: Sequence[AttributionSlice], *, bucket: AttributionBucket
) -> Mapping[str, AttributionSlice]:
    """Roll a flat set of slices into one parent per distinct bucket id.

    Slices with no identifiable owner collect under `UNATTRIBUTED` rather than being
    dropped or assigned to the first bucket — brief §15.
    """
    grouped: dict[str, list[AttributionSlice]] = {}
    for candidate in slices:
        grouped.setdefault(candidate.bucket_id or UNATTRIBUTED, []).append(candidate)
    return {
        bucket_id: roll_up(members, bucket=bucket, bucket_id=bucket_id)
        for bucket_id, members in sorted(grouped.items())
    }


__all__ += ["group_by"]
