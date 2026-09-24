"""Structure signals — `08-SIGNALS.md` §6.

`BREAKOUT_CONTEXT` · `BREAKDOWN_CONTEXT` · `GAMMA_CONCENTRATION_SHIFT` ·
`REGIME_TRANSITION`.

Note the naming: `BREAKOUT_CONTEXT`, not `BREAKOUT`. These describe the *structural
context* in which a move is occurring. None is named for a trade direction, and none
asserts one.
"""

from __future__ import annotations

from oipulse.signals.catalogue._shared import contradicting, supporting
from oipulse.signals.context import RuleContext
from oipulse.signals.model import NONE_OBSERVED, ContradictionAssessment, Evidence
from oipulse.signals.rules import RuleOutcome, signal_rule

__all__ = [
    "breakdown_context",
    "breakout_context",
    "gamma_concentration_shift",
    "regime_transition",
]

_QUALITY = ["quality != UNRELIABLE"]


def _assessment(items: list[Evidence]) -> ContradictionAssessment:
    return tuple(items) if items else NONE_OBSERVED


@signal_rule(
    signal_type="BREAKOUT_CONTEXT",
    version=1,
    definition=(
        "The deterministic regime classifier reports BREAKOUT or TRENDING_UP while "
        "positioning-derived resistance sits nearby. Describes the structural context "
        "of a move; it is not a recommendation to buy."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[
        ("REGIME", 1),
        ("RESISTANCE_FROM_POSITIONING", 1),
        ("GEX_TOTAL", 1),
        ("REALIZED_MOVE", 1),
    ],
    quality_requirements=_QUALITY,
)
def breakout_context(ctx: RuleContext) -> RuleOutcome:
    regime = ctx.categorical("REGIME", 1)
    if regime is None:
        return RuleOutcome.no_signal("REGIME unavailable")
    if regime not in {"BREAKOUT", "TRENDING_UP"}:
        return RuleOutcome.no_signal(f"regime is {regime}")

    support = [
        supporting(
            ctx.metric("REGIME", 1),  # type: ignore[arg-type]
            f"Regime classifier reports {regime}",
            "0.35",
        )
    ]
    move = ctx.metric("REALIZED_MOVE", 1)
    if move is not None:
        support.append(supporting(move, f"Realised move {move.value}", "0.20"))

    against: list[Evidence] = []
    resistance = ctx.metric("RESISTANCE_FROM_POSITIONING", 1)
    if resistance is not None:
        against.append(
            contradicting(
                resistance,
                f"Positioning-derived resistance sits at {resistance.value}",
                "0.15",
            )
        )
    gex = ctx.numeric("GEX_TOTAL", 1)
    if gex is not None and gex > 0:
        against.append(
            contradicting(
                ctx.metric("GEX_TOTAL", 1),  # type: ignore[arg-type]
                f"Net dealer gamma is positive ({gex}) under the declared convention",
                "0.10",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=regime == "BREAKOUT",
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition="regime leaves BREAKOUT and TRENDING_UP",
    )


@signal_rule(
    signal_type="BREAKDOWN_CONTEXT",
    version=1,
    definition=(
        "The deterministic regime classifier reports BREAKDOWN or TRENDING_DOWN while "
        "positioning-derived support sits nearby. Structural context only."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[
        ("REGIME", 1),
        ("SUPPORT_FROM_POSITIONING", 1),
        ("GEX_TOTAL", 1),
        ("REALIZED_MOVE", 1),
    ],
    quality_requirements=_QUALITY,
)
def breakdown_context(ctx: RuleContext) -> RuleOutcome:
    regime = ctx.categorical("REGIME", 1)
    if regime is None:
        return RuleOutcome.no_signal("REGIME unavailable")
    if regime not in {"BREAKDOWN", "TRENDING_DOWN"}:
        return RuleOutcome.no_signal(f"regime is {regime}")

    support = [
        supporting(
            ctx.metric("REGIME", 1),  # type: ignore[arg-type]
            f"Regime classifier reports {regime}",
            "0.35",
        )
    ]
    move = ctx.metric("REALIZED_MOVE", 1)
    if move is not None:
        support.append(supporting(move, f"Realised move {move.value}", "0.20"))

    against: list[Evidence] = []
    support_level = ctx.metric("SUPPORT_FROM_POSITIONING", 1)
    if support_level is not None:
        against.append(
            contradicting(
                support_level,
                f"Positioning-derived support sits at {support_level.value}",
                "0.15",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=regime == "BREAKDOWN",
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition="regime leaves BREAKDOWN and TRENDING_DOWN",
    )


@signal_rule(
    signal_type="GAMMA_CONCENTRATION_SHIFT",
    version=1,
    definition=(
        "Dealer gamma exposure is concentrating into fewer strikes under the declared "
        "sign convention. Market-structure information; no directional label is "
        "attached and none is implied."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[("GEX_CONCENTRATION", 1), ("GEX_TOTAL", 1), ("GAMMA_FLIP_LEVEL", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_concentration", "0.25"), ("full_concentration", "0.40")),
)
def gamma_concentration_shift(ctx: RuleContext) -> RuleOutcome:
    concentration = ctx.numeric("GEX_CONCENTRATION", 1)
    if concentration is None:
        return RuleOutcome.no_signal("GEX_CONCENTRATION unavailable")
    partial = ctx.config.decimal("min_concentration", "0.25")
    full = ctx.config.decimal("full_concentration", "0.40")
    if concentration < partial:
        return RuleOutcome.no_signal(f"concentration {concentration} below {partial}")

    support = [
        supporting(
            ctx.metric("GEX_CONCENTRATION", 1),  # type: ignore[arg-type]
            f"Gamma concentration index {concentration}",
            "0.35",
        )
    ]
    flip = ctx.metric("GAMMA_FLIP_LEVEL", 1)
    if flip is not None:
        support.append(supporting(flip, f"Gamma flip level at {flip.value}", "0.15"))

    against: list[Evidence] = []
    total = ctx.numeric("GEX_TOTAL", 1)
    if total is not None and abs(total) < ctx.config.decimal("material_gex", "1000"):
        against.append(
            contradicting(
                ctx.metric("GEX_TOTAL", 1),  # type: ignore[arg-type]
                f"Total gamma exposure is small ({total}); the concentration is of little",
                "0.20",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=concentration >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"gamma concentration falls below {partial}",
    )


@signal_rule(
    signal_type="REGIME_TRANSITION",
    version=1,
    definition=(
        "The deterministic regime classifier reports TRANSITION: the rules that "
        "identify a clear regime no longer apply cleanly. An honest statement of "
        "structural ambiguity, not a prediction."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[("REGIME", 1), ("RANGE", 1), ("ATR", 1)],
    quality_requirements=_QUALITY,
)
def regime_transition(ctx: RuleContext) -> RuleOutcome:
    regime = ctx.categorical("REGIME", 1)
    if regime is None:
        return RuleOutcome.no_signal("REGIME unavailable")
    if regime != "TRANSITION":
        return RuleOutcome.no_signal(f"regime is {regime}")

    support = [
        supporting(
            ctx.metric("REGIME", 1),  # type: ignore[arg-type]
            "Regime classifier reports TRANSITION",
            "0.40",
        )
    ]
    span = ctx.metric("RANGE", 1)
    if span is not None:
        support.append(supporting(span, f"Window range {span.value}", "0.15"))

    against: list[Evidence] = []
    atr = ctx.numeric("ATR", 1)
    if atr is not None and atr == 0:
        against.append(
            contradicting(
                ctx.metric("ATR", 1),  # type: ignore[arg-type]
                "Average true range is zero; the market is not moving at all",
                "0.25",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=True,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition="regime resolves to any classification other than TRANSITION",
    )
