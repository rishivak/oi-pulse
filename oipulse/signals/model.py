"""The signal record and its evidence — `08-SIGNALS.md` §2.

Two shapes carry the architecture:

* **Evidence references rows, it does not embed rendered numbers.** `metric_value_ref`
  and `observation_refs` make `Signal -> Evidence -> MetricValue -> MarketState ->
  Observation` a foreign-key path rather than a log search, which is why
  explainability survives a UI rewrite.
* **`contradiction_assessment` is mandatory and `NONE_OBSERVED` is a positive claim** —
  *we looked and found nothing material* — not an absent field. Requiring a non-empty
  list would invite authors to manufacture a token objection to satisfy the schema,
  which is worse than honestly reporting none.

There is deliberately **no field for an unexplained confidence number**. `strength` is
derived by a declared, versioned function of the weighted evidence set; if it cannot be
derived from evidence, it cannot be set.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.marketstate.staleness import QualityStatus

__all__ = [
    "NONE_OBSERVED",
    "ContradictionAssessment",
    "Evidence",
    "EvidenceKind",
    "MetricRef",
    "Signal",
    "SignalIdentity",
    "SignalProvenance",
    "SignalStatus",
    "signal_digest",
]


class SignalStatus(StrEnum):
    """`08` §3. Terminal states are INVALIDATED, EXPIRED and FADED."""

    FORMING = "FORMING"
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    FADED = "FADED"

    @property
    def is_terminal(self) -> bool:
        return self in (SignalStatus.INVALIDATED, SignalStatus.EXPIRED, SignalStatus.FADED)

    @property
    def is_actionable(self) -> bool:
        """ACTIVE and CONFIRMED are the states a consumer may act on.

        FORMING deliberately is not: entry conditions are only partially met, and
        treating it as actionable would make every near-miss a signal.
        """
        return self in (SignalStatus.ACTIVE, SignalStatus.CONFIRMED)


class EvidenceKind(StrEnum):
    SUPPORTING = "SUPPORTING"
    CONTRADICTING = "CONTRADICTING"


#: Sentinel for "assessed; no material contradiction found" (`08` §2).
NONE_OBSERVED = "NONE_OBSERVED"


@dataclass(frozen=True, slots=True)
class MetricRef:
    """A reference to the exact computed value behind a piece of evidence.

    Carries the feature version, so a reader knows which formula produced it even after
    a v3 lands, and the `inputs_digest`, so the value can be matched to the row that
    produced it without a timestamp join.
    """

    feature_id: str
    feature_version: int
    scope_kind: str
    scope_ref: str
    observed_at: datetime
    knowledge_horizon: datetime
    build_context_id: str
    inputs_digest: str
    available_at: datetime

    @property
    def label(self) -> str:
        return f"{self.feature_id}@v{self.feature_version}"

    def as_key(self) -> tuple[str, int, str, str, str, str, str]:
        """The `metric_values` identity tuple (`02` §6), so the FK path resolves."""
        return (
            self.feature_id,
            self.feature_version,
            self.scope_kind,
            self.scope_ref,
            self.observed_at.isoformat(),
            self.knowledge_horizon.isoformat(),
            self.build_context_id,
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    """One referenced argument for or against a signal (`08` §2)."""

    kind: EvidenceKind
    metric_value_ref: MetricRef
    statement: str
    weight: Decimal
    observed_at: datetime
    observation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A supporting item with negative weight, or a contradicting item with
        # positive weight, would silently invert its contribution to strength.
        if self.kind is EvidenceKind.SUPPORTING and self.weight < 0:
            raise ValueError(
                f"supporting evidence {self.statement!r} has negative weight {self.weight}"
            )
        if self.kind is EvidenceKind.CONTRADICTING and self.weight > 0:
            raise ValueError(
                f"contradicting evidence {self.statement!r} has positive weight {self.weight}; "
                f"contradicting weights are negative or zero"
            )

    @property
    def label(self) -> str:
        return self.metric_value_ref.label


#: Either the positive finding `NONE_OBSERVED`, or the evidence that argues against.
ContradictionAssessment = str | tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class SignalProvenance:
    """Everything needed to reproduce why the signal existed (`08` §2, §4).

    Deliberately references and digests rather than embedding mutable state: a research
    result from March must stay interpretable after the rule, the features and the
    thresholds have all moved on.
    """

    rule_type: str
    rule_version: int
    #: Content digest of the rule's thresholds and configuration. A threshold change
    #: produces a new digest, so it cannot silently rewrite historical meaning.
    config_digest: str
    #: `(identifier, version)` of every feature the rule pinned.
    feature_versions: tuple[tuple[str, int], ...]
    #: Digest over the resolved input values actually consumed.
    inputs_digest: str
    strength_function: str
    strength_function_version: int
    build_context_id: str
    state_checkpoint_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "rule_version": self.rule_version,
            "config_digest": self.config_digest,
            "feature_versions": [f"{i}@v{v}" for i, v in self.feature_versions],
            "inputs_digest": self.inputs_digest,
            "strength_function": self.strength_function,
            "strength_function_version": self.strength_function_version,
            "build_context_id": self.build_context_id,
            "state_checkpoint_ref": self.state_checkpoint_ref,
        }


@dataclass(frozen=True, slots=True)
class SignalIdentity:
    """Deterministic identity and deduplication key.

    A signal is identified by what produced it, not by when it was written. Two
    evaluations of the same rule version, over the same underlying and expiry, at the
    same market time and knowledge horizon, under the same build context and
    configuration, are **the same signal** -- which is what makes repeated delivery of
    a source event idempotent rather than duplicating state.

    Recurrence after a terminal state is a **new** signal with a new id (`08` §3), and
    that is expressed by `occurrence`: it increments per recurrence, so research counts
    two occurrences rather than one long-lived entity.
    """

    signal_type: str
    rule_version: int
    underlying_id: int
    expiry_id: int | None
    market_time: datetime
    knowledge_horizon: datetime
    build_context_id: str
    config_digest: str
    occurrence: int = 1

    @property
    def signal_id(self) -> str:
        """Content-addressable id. Stable across processes without coordination."""
        return (
            "sig_"
            + signal_digest(
                {
                    "signal_type": self.signal_type,
                    "rule_version": self.rule_version,
                    "underlying_id": self.underlying_id,
                    "expiry_id": self.expiry_id,
                    "market_time": self.market_time.isoformat(),
                    "knowledge_horizon": self.knowledge_horizon.isoformat(),
                    "build_context_id": self.build_context_id,
                    "config_digest": self.config_digest,
                    "occurrence": self.occurrence,
                }
            )[:32]
        )

    @property
    def stream_key(self) -> tuple[str, int, int, int | None, int]:
        """Identifies the *lifecycle stream* a signal belongs to.

        Market time is excluded: successive evaluations of one developing signal share
        a stream and update one entity, which is what makes "how do these resolve?"
        answerable. A new occurrence starts a new stream.
        """
        return (
            self.signal_type,
            self.rule_version,
            self.underlying_id,
            self.expiry_id,
            self.occurrence,
        )


def signal_digest(payload: dict[str, Any]) -> str:
    """Stable digest. Sorted keys, compact separators, `Decimal` via `str`."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class Signal:
    """One evaluated signal (`08` §2). Immutable; a transition produces a new instance."""

    identity: SignalIdentity
    horizon: timedelta
    status: SignalStatus
    strength: Decimal
    evidence: tuple[Evidence, ...]
    contradiction_assessment: ContradictionAssessment
    invalidation_condition: str
    provenance: SignalProvenance
    #: The four-time model. `created_at`/`updated_at` are evaluation metadata;
    #: `available_at` is when the signal became legitimately consumable and derives
    #: from its inputs' availability, never from market time (`07` §3).
    created_at: datetime
    updated_at: datetime
    available_at: datetime
    expires_at: datetime
    quality_status: QualityStatus
    history: tuple[tuple[str, datetime], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.invalidation_condition.strip():
            raise ValueError(
                f"{self.identity.signal_type}: invalidation_condition is mandatory; "
                f"an unfalsifiable signal cannot be evaluated against reality (`08` §7)"
            )
        if self.available_at < self.identity.market_time:
            raise ValueError(f"{self.identity.signal_type}: available_at precedes market_time")

    @property
    def signal_id(self) -> str:
        return self.identity.signal_id

    @property
    def signal_type(self) -> str:
        return self.identity.signal_type

    @property
    def supporting(self) -> tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if e.kind is EvidenceKind.SUPPORTING)

    @property
    def contradicting(self) -> tuple[Evidence, ...]:
        if isinstance(self.contradiction_assessment, str):
            return ()
        return self.contradiction_assessment

    @property
    def contradiction_was_assessed(self) -> bool:
        """Always True: the field is mandatory and `NONE_OBSERVED` is a finding."""
        return self.contradiction_assessment == NONE_OBSERVED or bool(self.contradiction_assessment)

    @property
    def found_no_contradiction(self) -> bool:
        return self.contradiction_assessment == NONE_OBSERVED

    def metric_refs(self) -> tuple[MetricRef, ...]:
        """Every referenced metric, supporting and contradicting, in a stable order."""
        refs = [e.metric_value_ref for e in (*self.supporting, *self.contradicting)]
        return tuple(sorted(refs, key=lambda r: (r.feature_id, r.feature_version, r.scope_ref)))

    def content_digest(self) -> str:
        """Deterministic digest of the signal's decision content.

        Excludes `created_at` and `updated_at`: those record when the work ran, not
        what was decided, and including them would make the determinism property
        untestable. `available_at` IS included -- it is a decision output, derived
        from input availability.
        """
        return signal_digest(
            {
                "identity": self.identity.signal_id,
                "status": self.status.value,
                "strength": str(self.strength),
                "available_at": self.available_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "invalidation_condition": self.invalidation_condition,
                "quality_status": self.quality_status.value,
                "evidence": [
                    {
                        "kind": e.kind.value,
                        "ref": list(e.metric_value_ref.as_key()),
                        "weight": str(e.weight),
                        "statement": e.statement,
                    }
                    for e in sorted(
                        (*self.supporting, *self.contradicting),
                        key=lambda e: (e.kind.value, e.metric_value_ref.label, e.statement),
                    )
                ],
                "contradiction": (
                    NONE_OBSERVED if self.found_no_contradiction else "ASSESSED_WITH_EVIDENCE"
                ),
                "provenance": self.provenance.as_dict(),
            }
        )
