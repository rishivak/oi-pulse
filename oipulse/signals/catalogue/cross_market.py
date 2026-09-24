"""Cross-market signals — `08-SIGNALS.md` §6.

`FUTURES_OPTIONS_DIVERGENCE` · `BASIS_ANOMALY`.

Futures OI against futures price is the cleanest long/short buildup read available, and
it corroborates or contradicts options positioning (`07` §4.6). These two rules are
where that corroboration becomes a first-class observation.
"""

from __future__ import annotations

from oipulse.signals.catalogue._shared import contradicting, supporting
from oipulse.signals.context import RuleContext
from oipulse.signals.model import NONE_OBSERVED, ContradictionAssessment, Evidence
from oipulse.signals.rules import RuleOutcome, signal_rule

__all__ = ["basis_anomaly", "futures_options_divergence"]

_QUALITY = ["quality != UNRELIABLE"]


def _assessment(items: list[Evidence]) -> ContradictionAssessment:
    return tuple(items) if items else NONE_OBSERVED


@signal_rule(
    signal_type="FUTURES_OPTIONS_DIVERGENCE",
    version=1,
    definition=(
        "The futures open-interest read and the options open-interest read disagree "
        "over the window. Reports the disagreement itself; which side is right is not "
        "something this observation claims."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[
        ("FUTURES_OPTIONS_CONFIRMATION", 1),
        ("FUTURES_OI_CHANGE", 1),
        ("OI_CHANGE", 1),
        ("SPOT_FUTURES_DIVERGENCE", 1),
    ],
    quality_requirements=_QUALITY,
)
def futures_options_divergence(ctx: RuleContext) -> RuleOutcome:
    confirmation = ctx.categorical("FUTURES_OPTIONS_CONFIRMATION", 1)
    if confirmation is None:
        return RuleOutcome.no_signal("FUTURES_OPTIONS_CONFIRMATION unavailable")
    if confirmation != "DIVERGING":
        return RuleOutcome.no_signal(f"futures and options are {confirmation}")

    support = [
        supporting(
            ctx.metric("FUTURES_OPTIONS_CONFIRMATION", 1),  # type: ignore[arg-type]
            "Futures and options open interest moved in opposite directions",
            "0.40",
        )
    ]
    futures = ctx.metric("FUTURES_OI_CHANGE", 1)
    options = ctx.metric("OI_CHANGE", 1)
    if futures is not None:
        support.append(supporting(futures, f"Futures OI change {futures.value}", "0.15"))
    if options is not None:
        support.append(supporting(options, f"Options OI change {options.value}", "0.15"))

    against: list[Evidence] = []
    price_divergence = ctx.numeric("SPOT_FUTURES_DIVERGENCE", 1)
    if price_divergence is not None and abs(price_divergence) < ctx.config.decimal(
        "material_price_divergence", "0.0005"
    ):
        against.append(
            contradicting(
                ctx.metric("SPOT_FUTURES_DIVERGENCE", 1),  # type: ignore[arg-type]
                f"Spot and futures prices barely diverged ({price_divergence})",
                "0.15",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=True,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition="futures and options open interest move together again",
    )


@signal_rule(
    signal_type="BASIS_ANOMALY",
    version=1,
    definition=(
        "Annualised futures basis sits outside its declared normal band. A structural "
        "observation about carry, not an arbitrage recommendation."
    ),
    horizon="2h",
    evaluation_interval="5m",
    requires_features=[("ANNUALIZED_BASIS", 1), ("BASIS", 1), ("BASIS_CHANGE", 1)],
    quality_requirements=_QUALITY,
    default_config=(
        ("normal_low", "-0.02"),
        ("normal_high", "0.12"),
        ("extreme_low", "-0.05"),
        ("extreme_high", "0.20"),
    ),
)
def basis_anomaly(ctx: RuleContext) -> RuleOutcome:
    annualised = ctx.numeric("ANNUALIZED_BASIS", 1)
    if annualised is None:
        return RuleOutcome.no_signal("ANNUALIZED_BASIS unavailable")
    low = ctx.config.decimal("normal_low", "-0.02")
    high = ctx.config.decimal("normal_high", "0.12")
    if low <= annualised <= high:
        return RuleOutcome.no_signal(f"annualised basis {annualised} is within [{low}, {high}]")

    support = [
        supporting(
            ctx.metric("ANNUALIZED_BASIS", 1),  # type: ignore[arg-type]
            f"Annualised basis {annualised} sits outside [{low}, {high}]",
            "0.40",
        )
    ]
    raw = ctx.metric("BASIS", 1)
    if raw is not None:
        support.append(supporting(raw, f"Basis {raw.value} in price units", "0.15"))

    against: list[Evidence] = []
    change = ctx.numeric("BASIS_CHANGE", 1)
    if change is not None and abs(change) < ctx.config.decimal("material_change", "1"):
        against.append(
            contradicting(
                ctx.metric("BASIS_CHANGE", 1),  # type: ignore[arg-type]
                f"Basis is stable ({change} over the window); the level may be structural",
                "0.20",
            )
        )
    extreme_low = ctx.config.decimal("extreme_low", "-0.05")
    extreme_high = ctx.config.decimal("extreme_high", "0.20")
    return RuleOutcome(
        fired=True,
        entry_conditions_met=annualised <= extreme_low or annualised >= extreme_high,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"annualised basis returns inside [{low}, {high}]",
    )
