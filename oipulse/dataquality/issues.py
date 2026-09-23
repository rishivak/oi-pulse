"""Data-quality issues — recorded facts, not transient warnings.

`docs/design/01-DOMAIN_MODEL.md` §5 and `16-OBSERVABILITY.md` §4. Issues are
**persisted** so research can exclude affected windows and an operator can review
history rather than only the present. This is what makes "was this result computed on
good data?" answerable after the fact.

Phase 2 records ingestion-side issues. `QualityAssessment` — the per-state rollup with
coverage ratio and staleness percentiles — belongs to Phase 3 with MarketState and is
deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from oipulse.core.clock import ensure_utc

__all__ = ["IssueSeverity", "IssueType", "QualityIssue"]


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class IssueType(StrEnum):
    STALE_PRICE = "stale_price"
    MISSING_OBSERVATION = "missing_observation"
    DUPLICATE_OBSERVATION = "duplicate_observation"
    OUT_OF_ORDER = "out_of_order"
    IMPOSSIBLE_VALUE = "impossible_value"
    NEGATIVE_OI = "negative_oi"
    INVALID_STRIKE = "invalid_strike"
    MISSING_EXPIRY = "missing_expiry"
    DISCONTINUITY = "discontinuity"
    WEBSOCKET_GAP = "websocket_gap"
    RECONNECT_GAP = "reconnect_gap"
    REST_WS_DIVERGENCE = "rest_ws_divergence"
    INCOMPLETE_CHAIN = "incomplete_chain"
    CLOCK_SKEW = "clock_skew"
    #: A capacity decision, not a venue fault. Recorded so absent data is attributable.
    SUBSCRIPTION_DEGRADED = "subscription_degraded"
    #: Identity too weak to support sequence-gap detection (A-1).
    WEAK_IDENTITY = "weak_identity"


@dataclass(frozen=True, slots=True)
class QualityIssue:
    type: IssueType
    severity: IssueSeverity
    detected_at: datetime
    instrument_id: int | None = None
    underlying_id: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    detail: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detected_at", ensure_utc(self.detected_at))
        for f in ("window_start", "window_end"):
            v = getattr(self, f)
            if v is not None:
                object.__setattr__(self, f, ensure_utc(v))

    @property
    def blocks_analytics(self) -> bool:
        """Whether a metric computed over this window should be considered unreliable."""
        return self.severity in (IssueSeverity.DEGRADED, IssueSeverity.CRITICAL)
