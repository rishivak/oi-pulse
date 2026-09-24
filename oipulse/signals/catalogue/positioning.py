"""Positioning signals — `08-SIGNALS.md` §6.

`PUT_SUPPORT_MIGRATION` · `CALL_RESISTANCE_MIGRATION` · `OI_EXPANSION` ·
`OI_UNWINDING` · `POSITIONING_SHIFT` · `CONCENTRATION_BUILDING`.

`PUT_SUPPORT_MIGRATION` is the design's worked example (§5), implemented against the
same feature set it names. Every rule here assesses contradiction against real
opposing metrics rather than returning `NONE_OBSERVED` by default — a rule whose
assessment never finds anything is usually not assessing.
"""

from __future__ import annotations

from decimal import Decimal

from oipulse.signals.catalogue._shared import contradicting, supporting
from oipulse.signals.context import RuleContext
from oipulse.signals.model import NONE_OBSERVED, ContradictionAssessment, Evidence
from oipulse.signals.rules import RuleOutcome, signal_rule

__all__ = [
    "call_resistance_migration",
    "concentration_building",
    "oi_expansion",
    "oi_unwinding",
    "positioning_shift",
    "put_support_migration",
]

_QUALITY = ["quality != UNRELIABLE"]


def _assessment(items: list[Evidence]) -> ContradictionAssessment:
    """`NONE_OBSERVED` is a positive claim: assessed, nothing material found."""
    return tuple(items) if items else NONE_OBSERVED


@signal_rule(
    signal_type="PUT_SUPPORT_MIGRATION",
    version=1,
    definition=(
        "Put open interest is migrating upward and the put wall is rising, indicating "
        "strengthening downside positioning structure. An observation about market "
        "structure, not a trade recommendation."
    ),
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[
        ("PUT_OI_MIGRATION", 1),
        ("OI_WALL_PUT", 1),
        ("FUTURES_OI_CHANGE", 1),
        ("ATM_IV", 1),
        ("OI_WALL_CALL", 1),
        ("IV_CHANGE", 1),
    ],
    quality_requirements=_QUALITY,
    default_config=(("min_migration_points", "50"), ("full_migration_points", "100")),
)
def put_support_migration(ctx: RuleContext) -> RuleOutcome:
    migration = ctx.numeric("PUT_OI_MIGRATION", 1)
    if migration is None:
        return RuleOutcome.no_signal("PUT_OI_MIGRATION unavailable")

    partial = ctx.config.decimal("min_migration_points", "50")
    full = ctx.config.decimal("full_migration_points", "100")
    if migration < partial:
        return RuleOutcome.no_signal(f"migration {migration} below {partial}")

    support: list[Evidence] = [
        supporting(
            ctx.metric("PUT_OI_MIGRATION", 1),  # type: ignore[arg-type]
            f"Put OI migrated {migration} strike points over the window",
            "0.30",
        )
    ]
    wall = ctx.metric("OI_WALL_PUT", 1)
    if wall is not None:
        support.append(supporting(wall, f"Put wall at {wall.value}", "0.25"))
    futures = ctx.numeric("FUTURES_OI_CHANGE", 1)
    if futures is not None and futures > 0:
        support.append(
            supporting(
                ctx.metric("FUTURES_OI_CHANGE", 1),  # type: ignore[arg-type]
                f"Futures OI added {futures} contracts",
                "0.20",
            )
        )

    against: list[Evidence] = []
    call_wall = ctx.metric("OI_WALL_CALL", 1)
    if call_wall is not None:
        against.append(
            contradicting(call_wall, f"Call resistance still at {call_wall.value}", "0.12")
        )
    iv_change = ctx.numeric("IV_CHANGE", 1)
    if iv_change is not None and iv_change > 0:
        against.append(
            contradicting(
                ctx.metric("IV_CHANGE", 1),  # type: ignore[arg-type]
                f"ATM IV expanding by {iv_change}",
                "0.05",
            )
        )

    return RuleOutcome(
        fired=True,
        entry_conditions_met=migration >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=(
            f"put OI migration falls below {partial} strike points, "
            f"or the put wall returns below its prior strike"
        ),
    )


@signal_rule(
    signal_type="CALL_RESISTANCE_MIGRATION",
    version=1,
    definition=(
        "Call open interest is migrating and the call wall is moving, indicating a "
        "shift in upside positioning structure. A structural observation only."
    ),
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[
        ("CALL_OI_MIGRATION", 1),
        ("OI_WALL_CALL", 1),
        ("OI_WALL_PUT", 1),
        ("ATM_IV", 1),
    ],
    quality_requirements=_QUALITY,
    default_config=(("min_migration_points", "50"), ("full_migration_points", "100")),
)
def call_resistance_migration(ctx: RuleContext) -> RuleOutcome:
    migration = ctx.numeric("CALL_OI_MIGRATION", 1)
    if migration is None:
        return RuleOutcome.no_signal("CALL_OI_MIGRATION unavailable")
    partial = ctx.config.decimal("min_migration_points", "50")
    full = ctx.config.decimal("full_migration_points", "100")
    if abs(migration) < partial:
        return RuleOutcome.no_signal(f"|migration| {abs(migration)} below {partial}")

    support = [
        supporting(
            ctx.metric("CALL_OI_MIGRATION", 1),  # type: ignore[arg-type]
            f"Call OI migrated {migration} strike points",
            "0.35",
        )
    ]
    wall = ctx.metric("OI_WALL_CALL", 1)
    if wall is not None:
        support.append(supporting(wall, f"Call wall at {wall.value}", "0.25"))

    against: list[Evidence] = []
    put_wall = ctx.metric("OI_WALL_PUT", 1)
    if put_wall is not None:
        against.append(
            contradicting(put_wall, f"Put support unchanged at {put_wall.value}", "0.10")
        )

    return RuleOutcome(
        fired=True,
        entry_conditions_met=abs(migration) >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"call OI migration magnitude falls below {partial} strike points",
    )


@signal_rule(
    signal_type="OI_EXPANSION",
    version=1,
    definition=(
        "Total open interest is expanding materially over the window: new positions "
        "are being opened rather than closed. A structural observation."
    ),
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[("OI_CHANGE_PCT", 1), ("VOLUME_OI_RATIO", 1), ("PCR", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_expansion", "0.02"), ("full_expansion", "0.05")),
)
def oi_expansion(ctx: RuleContext) -> RuleOutcome:
    change = ctx.numeric("OI_CHANGE_PCT", 1)
    if change is None:
        return RuleOutcome.no_signal("OI_CHANGE_PCT unavailable")
    partial = ctx.config.decimal("min_expansion", "0.02")
    full = ctx.config.decimal("full_expansion", "0.05")
    if change < partial:
        return RuleOutcome.no_signal(f"expansion {change} below {partial}")

    support = [
        supporting(
            ctx.metric("OI_CHANGE_PCT", 1),  # type: ignore[arg-type]
            f"Open interest expanded {change}",
            "0.40",
        )
    ]
    turnover = ctx.metric("VOLUME_OI_RATIO", 1)
    if turnover is not None:
        support.append(supporting(turnover, f"Turnover ratio {turnover.value}", "0.15"))

    against: list[Evidence] = []
    ratio = ctx.numeric("VOLUME_OI_RATIO", 1)
    if ratio is not None and ratio < Decimal("0.1"):
        against.append(
            contradicting(
                ctx.metric("VOLUME_OI_RATIO", 1),  # type: ignore[arg-type]
                f"Low turnover ({ratio}) relative to the OI change",
                "0.10",
            )
        )

    return RuleOutcome(
        fired=True,
        entry_conditions_met=change >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"OI change falls back below {partial}",
    )


@signal_rule(
    signal_type="OI_UNWINDING",
    version=1,
    definition=(
        "Total open interest is contracting materially: positions are being closed. "
        "A structural observation, carrying no directional implication."
    ),
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[("OI_CHANGE_PCT", 1), ("VOLUME_OI_RATIO", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_contraction", "-0.02"), ("full_contraction", "-0.05")),
)
def oi_unwinding(ctx: RuleContext) -> RuleOutcome:
    change = ctx.numeric("OI_CHANGE_PCT", 1)
    if change is None:
        return RuleOutcome.no_signal("OI_CHANGE_PCT unavailable")
    partial = ctx.config.decimal("min_contraction", "-0.02")
    full = ctx.config.decimal("full_contraction", "-0.05")
    if change > partial:
        return RuleOutcome.no_signal(f"contraction {change} above {partial}")

    support = [
        supporting(
            ctx.metric("OI_CHANGE_PCT", 1),  # type: ignore[arg-type]
            f"Open interest contracted {change}",
            "0.40",
        )
    ]
    against: list[Evidence] = []
    turnover = ctx.numeric("VOLUME_OI_RATIO", 1)
    if turnover is not None and turnover > Decimal("0.5"):
        against.append(
            contradicting(
                ctx.metric("VOLUME_OI_RATIO", 1),  # type: ignore[arg-type]
                f"High turnover ({turnover}) suggests rotation rather than unwinding",
                "0.15",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=change <= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"OI change rises back above {partial}",
    )


@signal_rule(
    signal_type="POSITIONING_SHIFT",
    version=1,
    definition=(
        "The put-call ratio on newly added open interest diverges materially from the "
        "ratio on the existing book: the composition of positioning is changing."
    ),
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[("PCR", 1), ("PCR_OI_CHANGE", 1), ("OI_CHANGE_PCT", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_divergence", "0.25"), ("full_divergence", "0.50")),
)
def positioning_shift(ctx: RuleContext) -> RuleOutcome:
    level = ctx.numeric("PCR", 1)
    flow = ctx.numeric("PCR_OI_CHANGE", 1)
    if level is None or flow is None:
        return RuleOutcome.no_signal("PCR or PCR_OI_CHANGE unavailable")
    divergence = abs(flow - level)
    partial = ctx.config.decimal("min_divergence", "0.25")
    full = ctx.config.decimal("full_divergence", "0.50")
    if divergence < partial:
        return RuleOutcome.no_signal(f"divergence {divergence} below {partial}")

    support = [
        supporting(
            ctx.metric("PCR_OI_CHANGE", 1),  # type: ignore[arg-type]
            f"PCR on new OI is {flow} against a book ratio of {level}",
            "0.35",
        )
    ]
    against: list[Evidence] = []
    magnitude = ctx.numeric("OI_CHANGE_PCT", 1)
    if magnitude is not None and abs(magnitude) < Decimal("0.005"):
        against.append(
            contradicting(
                ctx.metric("OI_CHANGE_PCT", 1),  # type: ignore[arg-type]
                f"The shift rests on a very small OI change ({magnitude})",
                "0.20",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=divergence >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"flow-versus-level PCR divergence falls below {partial}",
    )


@signal_rule(
    signal_type="CONCENTRATION_BUILDING",
    version=1,
    definition=(
        "Open interest is concentrating into fewer strikes, measured by the Herfindahl "
        "index over per-strike OI. A structural observation about where positioning sits."
    ),
    horizon="1h",
    evaluation_interval="5m",
    requires_features=[("OI_CONCENTRATION", 1), ("OI_CHANGE_PCT", 1)],
    quality_requirements=_QUALITY,
    default_config=(("min_concentration", "0.20"), ("full_concentration", "0.30")),
)
def concentration_building(ctx: RuleContext) -> RuleOutcome:
    concentration = ctx.numeric("OI_CONCENTRATION", 1)
    if concentration is None:
        return RuleOutcome.no_signal("OI_CONCENTRATION unavailable")
    partial = ctx.config.decimal("min_concentration", "0.20")
    full = ctx.config.decimal("full_concentration", "0.30")
    if concentration < partial:
        return RuleOutcome.no_signal(f"concentration {concentration} below {partial}")

    support = [
        supporting(
            ctx.metric("OI_CONCENTRATION", 1),  # type: ignore[arg-type]
            f"OI concentration index {concentration}",
            "0.35",
        )
    ]
    against: list[Evidence] = []
    change = ctx.numeric("OI_CHANGE_PCT", 1)
    if change is not None and change < 0:
        against.append(
            contradicting(
                ctx.metric("OI_CHANGE_PCT", 1),  # type: ignore[arg-type]
                f"Concentration is rising while total OI shrinks ({change})",
                "0.15",
            )
        )
    return RuleOutcome(
        fired=True,
        entry_conditions_met=concentration >= full,
        supporting=tuple(support),
        contradiction=_assessment(against),
        invalidation_condition=f"concentration index falls below {partial}",
    )
