"""Strength derivation — `08-SIGNALS.md` §2.

> Derived by a **declared, versioned function** of the weighted evidence set —
> typically a normalized weighted sum with any contradicting evidence subtracted. The
> function is registered like a feature, with its own version recorded on each signal.

> There is deliberately **no field** in which to record an unexplained confidence
> number. If strength cannot be derived from evidence, it cannot be set.

Registering the function and recording its version on every signal is what makes a
strength of 0.68 in March still mean the same thing in June. Changing the arithmetic
requires a new version, exactly as a feature formula does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from oipulse.signals.model import Evidence

__all__ = [
    "STRENGTH_FUNCTIONS",
    "StrengthFunction",
    "normalized_weighted_sum",
    "resolve_strength_function",
]


@dataclass(frozen=True, slots=True)
class StrengthFunction:
    """A named, versioned derivation from weighted evidence to a strength in [0, 1]."""

    name: str
    version: int
    description: str
    derive: Callable[[Sequence[Evidence], Sequence[Evidence]], Decimal]

    def __call__(
        self, supporting: Sequence[Evidence], contradicting: Sequence[Evidence]
    ) -> Decimal:
        return self.derive(supporting, contradicting)


def _normalized_weighted_sum(
    supporting: Sequence[Evidence], contradicting: Sequence[Evidence]
) -> Decimal:
    """Sum supporting weights, subtract contradicting magnitudes, normalise, clamp.

    Normalisation is by the **total absolute weight declared**, not by the supporting
    weight alone: otherwise adding a contradicting item could raise the strength, since
    it would shrink a denominator it also reduces the numerator of.

    Clamped to [0, 1]. A negative result means contradiction outweighs support, which
    is a real finding -- reported as strength 0, with the contradicting evidence still
    attached and visible, rather than as a negative number nobody knows how to read.
    """
    support = sum((e.weight for e in supporting), start=Decimal(0))
    against = sum((abs(e.weight) for e in contradicting), start=Decimal(0))
    total = support + against
    if total == 0:
        # No weighted evidence at all. Strength cannot be derived, so it is zero --
        # never a default midpoint, which would imply an inference nobody made.
        return Decimal(0)
    raw = (support - against) / total
    return min(max(raw, Decimal(0)), Decimal(1))


normalized_weighted_sum = StrengthFunction(
    name="NORMALIZED_WEIGHTED_SUM",
    version=1,
    description=(
        "Supporting weights minus contradicting magnitudes, divided by the total "
        "absolute weight, clamped to [0, 1]."
    ),
    derive=_normalized_weighted_sum,
)

#: Every registered derivation, by name. Versions coexist, as feature versions do.
STRENGTH_FUNCTIONS: dict[tuple[str, int], StrengthFunction] = {
    (normalized_weighted_sum.name, normalized_weighted_sum.version): normalized_weighted_sum,
}


def resolve_strength_function(name: str, version: int) -> StrengthFunction:
    try:
        return STRENGTH_FUNCTIONS[(name, version)]
    except KeyError as exc:
        raise KeyError(f"no strength function {name}@v{version} is registered") from exc
