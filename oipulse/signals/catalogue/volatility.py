"""Volatility signals — `08-SIGNALS.md` §6.

`VOLATILITY_EXPANSION` · `VOLATILITY_CONTRACTION` · `SKEW_STEEPENING` ·
`TERM_STRUCTURE_INVERSION`.
"""

from __future__ import annotations

from oipulse.signals.catalogue._shared import contradicting, supporting
from oipulse.signals.context import RuleContext
from oipulse.signals.model import NONE_OBSERVED, ContradictionAssessment, Evidence
from oipulse.signals.rules import RuleOutcome, signal_rule

__all__ = [
    "skew_steepening",
    "term_structure_inversion",
    "volatility_contraction",
    "volatility_expansion",
]

_QUALITY = ["quality != UNRELIABLE"]


def _assessment(items: list[Evidence]) -> ContradictionAssessment:
    return tuple(items) if items else NONE_OBSERVED


@signal_rule(
    signal_type="VOLATILITY_EXPANSION",
    version=1,
    definition=(
        "At-the-money implied volatility is rising materially over the window. A "
        "structural observation about option pricing, not a directional view."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[
        ("IV_CHANGE", 1),
        ("ATM_IV", 1),
        ("REALIZED_VOL_CLOSE_TO_CLOSE", 1),
        ("IMPLIED_REALIZED_SPREAD", 1),
    ],
    quality_requirements=_QUALITY,
    default_config=(("min_change", "0.005"), ("full_change", "0.015")),
)
def volatility_expansion(ctx: RuleContext) -> RuleOutcome:
    change = ctx.numeric("IV_CHANGE", 1)
    if change is None:
        return RuleOutcome.no_signal("IV_CHANGE unavailable")
    partial = ctx.config.decimal("min_change", "0.005")
    full = ctx.config.decimal("full_change", "0.015")
    if change < partial:
        return RuleOutcome.no_signal(f"IV change {change} below {partial}")

    support = [
        supporting(
            ctx.metric("IV_CHANGE", 1),  # type: ignore[arg-type]
            f"ATM IV rose {change} over the window",
            "0.40",
        )
    ]
    atm = ctx.metric("ATM_IV", 1)
    if atm is not None:
        support.append(supporting(atm, f"ATM IV now {atm.value}", "0.15"))

    against: list[Evidence] = []
    spread = ctx.numeric("IMPLIED_REALIZED_SPREAD", 1)
    if spread is not None and spread > 0:
        against.append(
            contradicting(
                ctx.metric("IMPLIED_REALIZED_SPREAD", 1),  # type: ignore[arg-type]
                f"Implied already exceeds realised by {spread}; the expansion may be "
                f"premium rather than movement",
                "0.15",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=change >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"IV change falls back below {partial}",
    )


@signal_rule(
    signal_type="VOLATILITY_CONTRACTION",
    version=1,
    definition=(
        "At-the-money implied volatility is falling materially over the window. A "
        "structural observation about option pricing."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[("IV_CHANGE", 1), ("ATM_IV", 1), ("REALIZED_VOL_CLOSE_TO_CLOSE", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_change", "-0.005"), ("full_change", "-0.015")),
)
def volatility_contraction(ctx: RuleContext) -> RuleOutcome:
    change = ctx.numeric("IV_CHANGE", 1)
    if change is None:
        return RuleOutcome.no_signal("IV_CHANGE unavailable")
    partial = ctx.config.decimal("min_change", "-0.005")
    full = ctx.config.decimal("full_change", "-0.015")
    if change > partial:
        return RuleOutcome.no_signal(f"IV change {change} above {partial}")

    support = [
        supporting(
            ctx.metric("IV_CHANGE", 1),  # type: ignore[arg-type]
            f"ATM IV fell {change} over the window",
            "0.40",
        )
    ]
    against: list[Evidence] = []
    realised = ctx.numeric("REALIZED_VOL_CLOSE_TO_CLOSE", 1)
    atm = ctx.numeric("ATM_IV", 1)
    if realised is not None and atm is not None and realised > atm:
        against.append(
            contradicting(
                ctx.metric("REALIZED_VOL_CLOSE_TO_CLOSE", 1),  # type: ignore[arg-type]
                f"Realised vol ({realised}) already exceeds implied ({atm})",
                "0.20",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=change <= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"IV change rises back above {partial}",
    )


@signal_rule(
    signal_type="SKEW_STEEPENING",
    version=1,
    definition=(
        "Put-over-call implied volatility skew is steepening: downside protection is "
        "being bid relative to upside. A structural observation about relative pricing."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[("IV_SKEW_DELTA", 1), ("IV_SKEW_STRIKE", 1), ("ATM_IV", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_skew", "0.010"), ("full_skew", "0.025")),
)
def skew_steepening(ctx: RuleContext) -> RuleOutcome:
    skew = ctx.numeric("IV_SKEW_DELTA", 1)
    if skew is None:
        return RuleOutcome.no_signal("IV_SKEW_DELTA unavailable")
    partial = ctx.config.decimal("min_skew", "0.010")
    full = ctx.config.decimal("full_skew", "0.025")
    if skew < partial:
        return RuleOutcome.no_signal(f"skew {skew} below {partial}")

    support = [
        supporting(
            ctx.metric("IV_SKEW_DELTA", 1),  # type: ignore[arg-type]
            f"25-delta skew at {skew}",
            "0.35",
        )
    ]
    against: list[Evidence] = []
    # The two skew variants are registered separately and can disagree; when they do,
    # that disagreement is the contradiction, not something to average away.
    strike_skew = ctx.numeric("IV_SKEW_STRIKE", 1)
    if strike_skew is not None and strike_skew < 0:
        against.append(
            contradicting(
                ctx.metric("IV_SKEW_STRIKE", 1),  # type: ignore[arg-type]
                f"Strike-based skew disagrees at {strike_skew}",
                "0.20",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=skew >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"25-delta skew falls below {partial}",
    )


@signal_rule(
    signal_type="TERM_STRUCTURE_INVERSION",
    version=1,
    definition=(
        "Front-expiry implied volatility exceeds the next expiry's: the term structure "
        "is inverted. A structural observation about relative pricing across expiries."
    ),
    horizon="2h",
    evaluation_interval="5m",
    requires_features=[("IV_TERM_STRUCTURE", 1), ("ATM_IV", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_inversion", "-0.005"), ("full_inversion", "-0.015")),
)
def term_structure_inversion(ctx: RuleContext) -> RuleOutcome:
    term = ctx.numeric("IV_TERM_STRUCTURE", 1)
    if term is None:
        return RuleOutcome.no_signal("IV_TERM_STRUCTURE unavailable")
    partial = ctx.config.decimal("min_inversion", "-0.005")
    full = ctx.config.decimal("full_inversion", "-0.015")
    if term > partial:
        return RuleOutcome.no_signal(f"term structure {term} above {partial}; not inverted")

    support = [
        supporting(
            ctx.metric("IV_TERM_STRUCTURE", 1),  # type: ignore[arg-type]
            f"Second expiry IV minus front expiry IV is {term}",
            "0.45",
        )
    ]
    against: list[Evidence] = []
    atm = ctx.numeric("ATM_IV", 1)
    if atm is not None and atm < ctx.config.decimal("low_iv_floor", "0.08"):
        against.append(
            contradicting(
                ctx.metric("ATM_IV", 1),  # type: ignore[arg-type]
                f"Absolute IV is low ({atm}); the inversion is small in context",
                "0.15",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=term <= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"term structure rises back above {partial}",
    )
