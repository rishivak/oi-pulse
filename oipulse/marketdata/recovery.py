"""REST recovery and REST/WS coherence.

`docs/design/04-MARKETSTATE.md` §4 and `06-UPSTOX_INTEGRATION.md` §6.

REST chain responses and WS ticks have genuinely different consistency properties, and
the ingestion layer must not paper over the difference:

* A `chain_snapshot` is a **cross-sectional consistency set** — all legs as the venue
  reported them in one response, mutually consistent.
* A WS tick stream is **per-instrument only** — no guarantee that two legs are from the
  same instant.

This module owns the merge rule, divergence detection and the post-gap recovery trigger.
It deliberately does **not** build a `MarketState` — that is Phase 3. What it produces is
a coherence *descriptor* recorded alongside observations, so Phase 3 can assemble state
knowing which regime the data came from rather than having to guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from oipulse.core.clock import Clock, ensure_utc
from oipulse.dataquality.issues import IssueSeverity, IssueType, QualityIssue
from oipulse.marketdata.lifecycle import GapKind, GapRecord

__all__ = [
    "CoherenceMode",
    "DivergenceReport",
    "RecoveryPlan",
    "RecoveryTrigger",
    "assess_divergence",
    "coherence_mode_for",
    "plan_recovery",
]


class CoherenceMode(StrEnum):
    """Which consistency regime produced a set of observations.

    Recorded so a consumer can distinguish "these legs were mutually consistent" from
    "these legs are each individually fresh but were never observed together". An
    apparently precise state must never hide cross-sectional inconsistency.
    """

    SNAPSHOT_ANCHORED = "snapshot_anchored"
    STREAM_ONLY = "stream_only"
    SNAPSHOT_STALE = "snapshot_stale"
    RECOVERING = "recovering"


class RecoveryTrigger(StrEnum):
    WEBSOCKET_GAP = "websocket_gap"
    RECONNECT = "reconnect"
    STALE_FEED = "stale_feed"
    DIVERGENCE = "divergence"
    SCHEDULED = "scheduled"


def coherence_mode_for(
    *,
    now: datetime,
    anchor_observed_at: datetime | None,
    anchor_max_age: timedelta,
    recovering: bool = False,
) -> CoherenceMode:
    """Classify the regime currently in force.

    `anchor_max_age` defaults (at the call site) to 2x the chain poll interval: beyond
    that the snapshot is no longer trustworthy as a cross-sectional reference, even
    though its individual values may still be within their staleness budgets. Those are
    different claims and are tracked separately.
    """
    if recovering:
        return CoherenceMode.RECOVERING
    if anchor_observed_at is None:
        return CoherenceMode.STREAM_ONLY
    age = ensure_utc(now) - ensure_utc(anchor_observed_at)
    return (
        CoherenceMode.SNAPSHOT_ANCHORED if age <= anchor_max_age else CoherenceMode.SNAPSHOT_STALE
    )


@dataclass(frozen=True, slots=True)
class DivergenceReport:
    """Comparison of a freshly-arrived snapshot against WS-derived values."""

    compared: int
    diverged: int
    worst_field: str | None = None
    worst_ratio: Decimal | None = None
    instrument_ids: tuple[int, ...] = ()

    @property
    def ratio(self) -> float:
        return self.diverged / self.compared if self.compared else 0.0

    @property
    def material(self) -> bool:
        return self.diverged > 0


def assess_divergence(
    snapshot_values: dict[int, dict[str, Decimal | None]],
    stream_values: dict[int, dict[str, Decimal | None]],
    *,
    tolerance: Decimal = Decimal("0.005"),
) -> DivergenceReport:
    """Compare snapshot against stream per field, within a fractional tolerance.

    **The snapshot wins.** It is the venue's own mutually-consistent view; a WS-derived
    value that disagrees reflects either a missed message or a handling bug, and in
    either case the snapshot is the better estimate (`04` §4).

    Persistent divergence indicates a WS handling bug and escalates operationally — a
    single mismatch is noise, a sustained pattern is a defect.
    """
    compared = 0
    diverged = 0
    worst_field: str | None = None
    worst_ratio: Decimal | None = None
    offenders: list[int] = []

    for instrument_id, snap in snapshot_values.items():
        stream = stream_values.get(instrument_id)
        if not stream:
            continue
        for field_name, snap_value in snap.items():
            stream_value = stream.get(field_name)
            if snap_value is None or stream_value is None:
                continue
            compared += 1
            if snap_value == 0:
                continue
            delta = abs(snap_value - stream_value) / abs(snap_value)
            if delta > tolerance:
                diverged += 1
                offenders.append(instrument_id)
                if worst_ratio is None or delta > worst_ratio:
                    worst_ratio = delta
                    worst_field = field_name

    return DivergenceReport(
        compared=compared,
        diverged=diverged,
        worst_field=worst_field,
        worst_ratio=worst_ratio,
        instrument_ids=tuple(sorted(set(offenders))),
    )


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """What to re-fetch, why, and what to record about the hole."""

    trigger: RecoveryTrigger
    underlying_ids: tuple[int, ...]
    expiry_ids: tuple[int, ...]
    out_of_band: bool
    issues: tuple[QualityIssue, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def is_noop(self) -> bool:
        return not self.underlying_ids and not self.expiry_ids


def plan_recovery(
    gap: GapRecord,
    *,
    clock: Clock,
    underlying_ids: tuple[int, ...],
    expiry_ids: tuple[int, ...],
) -> RecoveryPlan:
    """Turn a detected gap into an out-of-band REST fetch plus a permanent record.

    Two things happen, and both matter:

    1. A chain fetch is triggered **out of band** — outside the normal poll cadence, so
       recovery is not queued behind routine polling while the state is degraded.
    2. The gap window is recorded permanently. Observations inside it are **never
       fabricated or interpolated**; the absence is the honest answer, and research must
       be able to see it later.
    """
    severity = {
        GapKind.WEBSOCKET_GAP: IssueSeverity.DEGRADED,
        GapKind.RECONNECT_GAP: IssueSeverity.DEGRADED,
        GapKind.STALE_FEED: IssueSeverity.WARNING,
    }[gap.kind]

    issue_type = {
        GapKind.WEBSOCKET_GAP: IssueType.WEBSOCKET_GAP,
        GapKind.RECONNECT_GAP: IssueType.RECONNECT_GAP,
        GapKind.STALE_FEED: IssueType.STALE_PRICE,
    }[gap.kind]

    trigger = {
        GapKind.WEBSOCKET_GAP: RecoveryTrigger.WEBSOCKET_GAP,
        GapKind.RECONNECT_GAP: RecoveryTrigger.RECONNECT,
        GapKind.STALE_FEED: RecoveryTrigger.STALE_FEED,
    }[gap.kind]

    issue = QualityIssue(
        type=issue_type,
        severity=severity,
        detected_at=clock.now(),
        window_start=gap.window_start,
        window_end=gap.window_end,
        detail=gap.detail or gap.kind.value,
        context={
            "feed_session_id": gap.feed_session_id,
            "channel": gap.channel,
            "expected_sequence": gap.expected_sequence,
            "observed_sequence": gap.observed_sequence,
            "missing_count": gap.missing_count,
        },
    )

    return RecoveryPlan(
        trigger=trigger,
        underlying_ids=underlying_ids,
        expiry_ids=expiry_ids,
        out_of_band=True,
        issues=(issue,),
        detail=(
            f"{gap.kind.value}: re-fetching {len(expiry_ids)} chain(s) out of band; "
            "the gap window is recorded and will not be interpolated"
        ),
    )
