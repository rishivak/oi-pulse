"""`MetricValue`, `Unavailable`, and the input digest.

`docs/design/02-DATA_MODEL.md` §6 and `07-ANALYTICS.md` §2.

A feature computation yields exactly one of two things, and the second is a first-class
result rather than an error:

* `MetricValue` — a number (or structured value) with its unit, version, the four
  timestamps, and the digest of the inputs that produced it.
* `Unavailable` — the feature legitimately did not compute, **with a reason**.

Returning `Unavailable` rather than a default is the whole point of §2's quality gating:
a feature requiring greeks over a backfill window with no greeks must not return `0.0`.
Zero is a number a consumer will plot, average and trade on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.marketstate.staleness import QualityStatus

__all__ = [
    "MetricValue",
    "Scope",
    "ScopeRef",
    "Unavailable",
    "UnavailableReason",
    "inputs_digest",
]


class Scope(StrEnum):
    """What a value is *about* (`07` §2)."""

    UNDERLYING = "underlying"
    EXPIRY = "expiry"
    STRIKE = "strike"
    CONTRACT = "contract"


class UnavailableReason(StrEnum):
    """Why a feature did not produce a value. Always recorded, never swallowed."""

    #: `quality_requirements` were not met (`07` §2).
    QUALITY_NOT_MET = "quality_not_met"
    #: A required state surface was absent — e.g. no greeks in a backfill window.
    MISSING_INPUT = "missing_input"
    #: The lookback window is not covered by the supplied history.
    INSUFFICIENT_HISTORY = "insufficient_history"
    #: A declared dependency was itself unavailable (`07` §6).
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    #: The formula is undefined for these inputs — e.g. a ratio with a zero denominator.
    UNDEFINED = "undefined"
    #: Parameters were outside the feature's declared domain.
    INVALID_PARAMS = "invalid_params"


@dataclass(frozen=True, slots=True)
class ScopeRef:
    """Identifies the thing a value is about, within its scope kind."""

    kind: Scope
    ref: str

    @staticmethod
    def underlying(underlying_id: int) -> ScopeRef:
        return ScopeRef(Scope.UNDERLYING, str(underlying_id))

    @staticmethod
    def expiry(expiry_id: int) -> ScopeRef:
        return ScopeRef(Scope.EXPIRY, str(expiry_id))

    @staticmethod
    def strike(expiry_id: int, strike: Decimal) -> ScopeRef:
        return ScopeRef(Scope.STRIKE, f"{expiry_id}:{strike}")

    @staticmethod
    def contract(instrument_id: int) -> ScopeRef:
        return ScopeRef(Scope.CONTRACT, str(instrument_id))


def inputs_digest(payload: dict[str, Any]) -> str:
    """Stable hash of the input values a computation consumed.

    Sorted keys, compact separators, `Decimal` via `str`. Two computations with the same
    digest must produce the same value; a determinism test asserts exactly that. Floats
    are deliberately not normalised away -- if a feature's inputs are floats, its digest
    inherits their representation, which is a reason to keep money in `Decimal`.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One computed feature value (`02` §6).

    The four persisted timestamps are all present and all distinct:

    * `observed_at` — the market time the value describes.
    * `knowledge_horizon` — what was known when it was computed.
    * `computed_at` — when computation finished.
    * `available_at` — when it became legitimately consumable (`07` §3).

    `decision_time` is **not** here: it is a query/action parameter, never persisted.
    """

    feature_id: str
    feature_version: int
    scope: ScopeRef
    value: Decimal | int | str | tuple[tuple[str, Decimal | int | None], ...] | None
    unit: str
    observed_at: datetime
    knowledge_horizon: datetime
    computed_at: datetime
    available_at: datetime
    build_context_id: str
    inputs_digest: str
    quality_status: QualityStatus
    #: Human-readable trace of how the number arose. Not parsed by anything; it exists
    #: so a research result from March is still explainable in June.
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # The availability invariants are asserted where they are computed
        # (`availability.py`); this catches a MetricValue constructed by hand that
        # would otherwise carry an impossible timestamp into the store.
        if self.available_at < self.computed_at:
            raise ValueError(
                f"{self.feature_id} v{self.feature_version}: available_at "
                f"{self.available_at.isoformat()} precedes computed_at "
                f"{self.computed_at.isoformat()}"
            )

    @property
    def identity(self) -> tuple[str, int, str, str, str, str, str]:
        """`UNIQUE (feature_id, feature_version, scope_kind, scope_ref, observed_at,
        knowledge_horizon, build_context_id)` from `02` §6."""
        return (
            self.feature_id,
            self.feature_version,
            self.scope.kind.value,
            self.scope.ref,
            self.observed_at.isoformat(),
            self.knowledge_horizon.isoformat(),
            self.build_context_id,
        )


@dataclass(frozen=True, slots=True)
class Unavailable:
    """A feature legitimately produced no value.

    Carries the same identity coordinates as a `MetricValue` so a caller can record the
    absence against the same scope, and so `analytics_skipped_total` can be labelled by
    feature and reason without the caller guessing.
    """

    feature_id: str
    feature_version: int
    scope: ScopeRef
    reason: UnavailableReason
    detail: str = ""

    @property
    def is_value(self) -> bool:
        return False
