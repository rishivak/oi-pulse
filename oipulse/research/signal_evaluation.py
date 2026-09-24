"""Signal evaluation and evidence attribution — `09-RESEARCH.md` §5.

> For every signal type, measure forward behaviour after each lifecycle transition:
>
> | `FORMING` | does early entry pay, or is it noise? |
> | `ACTIVE` | the base case |
> | `CONFIRMED` | does waiting for confirmation beat the missed move? |
> | `INVALIDATED` | how fast does invalidation arrive, and how costly is it? |

### Evidence attribution

> Because evidence is stored individually with weights, research can ask which items
> carry the predictive load. Signals are grouped by evidence presence/absence and
> forward returns compared. An evidence item that does not separate outcomes is
> decorative and is a candidate for removal or reweighting.
>
> This is the mechanism by which the system improves rather than accumulating rules.

Attribution compares the forward-return distribution of signals **with** an evidence
item against those **without** it. The separation is reported as a difference in means
alongside both sample counts — never as a significance verdict. Phase 6 does not do
inference testing, and a bare p-value over overlapping samples would be exactly the
false precision `09` §3 warns about.

Pure: signals and their outcomes are supplied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from oipulse.research.statistics import DistributionStats, describe
from oipulse.signals.model import Signal, SignalStatus

__all__ = [
    "EvidenceAttribution",
    "SignalEvaluation",
    "attribute_evidence",
    "evaluate_by_transition",
]


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    """Forward behaviour of one signal type at one lifecycle state."""

    signal_type: str
    rule_version: int
    status: SignalStatus
    distribution: DistributionStats
    sample: int
    #: Below this the evaluation reports insufficiency rather than a mean.
    minimum_sample: int = 30

    @property
    def is_reportable(self) -> bool:
        return self.sample >= self.minimum_sample

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "rule_version": self.rule_version,
            "status": self.status.value,
            "sample": self.sample,
            "minimum_sample": self.minimum_sample,
            "reportable": self.is_reportable,
            # Withheld below the minimum: a mean of nine is not a finding.
            "distribution": self.distribution.as_dict() if self.is_reportable else None,
        }


@dataclass(frozen=True, slots=True)
class EvidenceAttribution:
    """Whether one evidence item separates outcomes.

    Both counts are always present. A large-looking separation over four signals with
    the item and two without is not evidence of anything, and hiding the counts would
    let it read as though it were.
    """

    signal_type: str
    evidence_label: str
    with_item: DistributionStats
    without_item: DistributionStats

    @property
    def separation(self) -> Decimal | None:
        """Difference in mean forward return. `None` when either side is empty."""
        if self.with_item.mean is None or self.without_item.mean is None:
            return None
        return self.with_item.mean - self.without_item.mean

    @property
    def is_decorative(self) -> bool:
        """A candidate for removal: present in signals but separating nothing.

        Deliberately a weak claim. It flags an item for human review; it does not
        assert the item is useless, which would need inference this phase does not do.
        """
        gap = self.separation
        return gap is not None and abs(gap) < Decimal("0.0001")

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "evidence_label": self.evidence_label,
            "with_item": self.with_item.as_dict(),
            "without_item": self.without_item.as_dict(),
            "separation": None if self.separation is None else str(self.separation),
            "flagged_decorative": self.is_decorative,
        }


def evaluate_by_transition(
    outcomes: Sequence[tuple[Signal, Decimal]],
    *,
    minimum_sample: int = 30,
    histogram_bucket: Decimal = Decimal("0.005"),
) -> tuple[SignalEvaluation, ...]:
    """Group `(signal, forward_return)` pairs by type, version and lifecycle state.

    All four documented states are evaluated, including `INVALIDATED`: how fast
    invalidation arrives and how costly it is, is a question `09` §5 asks explicitly
    and one an emission-only model cannot answer.
    """
    grouped: dict[tuple[str, int, SignalStatus], list[Decimal]] = {}
    for signal, forward_return in outcomes:
        key = (signal.signal_type, signal.identity.rule_version, signal.status)
        grouped.setdefault(key, []).append(forward_return)

    return tuple(
        SignalEvaluation(
            signal_type=signal_type,
            rule_version=version,
            status=status,
            distribution=describe(returns, bucket=histogram_bucket),
            sample=len(returns),
            minimum_sample=minimum_sample,
        )
        # Sorted so evaluation order never depends on dict iteration order.
        for (signal_type, version, status), returns in sorted(
            grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].value)
        )
    )


def attribute_evidence(
    outcomes: Sequence[tuple[Signal, Decimal]],
    *,
    histogram_bucket: Decimal = Decimal("0.005"),
) -> tuple[EvidenceAttribution, ...]:
    """Compare forward returns of signals with each evidence item against without it.

    Only supporting evidence is attributed. A contradicting item is already an
    argument against the signal, so "does it separate outcomes?" is a differently
    shaped question and is not answered by this comparison.
    """
    by_type: dict[str, list[tuple[Signal, Decimal]]] = {}
    for signal, forward_return in outcomes:
        by_type.setdefault(signal.signal_type, []).append((signal, forward_return))

    results: list[EvidenceAttribution] = []
    for signal_type, pairs in sorted(by_type.items()):
        labels = sorted({item.label for signal, _ in pairs for item in signal.supporting})
        for label in labels:
            present = [
                value
                for signal, value in pairs
                if any(item.label == label for item in signal.supporting)
            ]
            absent = [
                value
                for signal, value in pairs
                if not any(item.label == label for item in signal.supporting)
            ]
            results.append(
                EvidenceAttribution(
                    signal_type=signal_type,
                    evidence_label=label,
                    with_item=describe(present, bucket=histogram_bucket),
                    without_item=describe(absent, bucket=histogram_bucket),
                )
            )
    return tuple(results)
