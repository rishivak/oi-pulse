"""Reconciliation outcomes, discrepancies and the persisted run.

`11-TRADING.md` §6 makes reconciliation "a first-class subsystem with its own
scheduler, records and tests" and is explicit about what it may and may not do:

> Discrepancies are **never silently corrected** — every one is recorded with its
> resolution, so a pattern of them is visible rather than absorbed.

Phase 10 brief §12 adds the constraint that shapes this module:

> **Do not invent automatic corrective actions without specification authority.**

So a `Discrepancy` carries a `resolution` describing what was done, and the only
resolutions available are the ones `11` §6 step 5 authorises: append order events,
insert missing fills, correct positions. Anything else is `RECORDED_ONLY` — noticed,
persisted, surfaced, and left for a human. Cancelling an unexpected broker order or
flattening an unexpected position are *not* things this subsystem may decide to do.

### The run is content-addressed

Two reconciliations over the same provider evidence and the same local state produce
the same `content_digest`, which is what makes brief §13's idempotency requirement
checkable rather than asserted. Execution metadata is excluded, as everywhere else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "Discrepancy",
    "DiscrepancyKind",
    "ReconciliationOutcome",
    "ReconciliationRun",
    "ReconciliationTrigger",
    "Resolution",
]


class ReconciliationTrigger(StrEnum):
    """`11` §6's trigger table, verbatim. No trigger is invented."""

    EVENT_DRIVEN = "EVENT_DRIVEN"
    STARTUP = "STARTUP"
    PERIODIC = "PERIODIC"
    POST_DISCONNECT = "POST_DISCONNECT"
    SESSION_BOUNDARY = "SESSION_BOUNDARY"
    MANUAL = "MANUAL"


class DiscrepancyKind(StrEnum):
    """How local and provider views differ. Brief §12's list."""

    MATCH = "MATCH"
    #: The provider has progressed further than we recorded.
    PROVIDER_AHEAD = "PROVIDER_AHEAD"
    #: We recorded progress the provider does not show. Almost always a bug on our
    #: side, and never resolved by pushing our view onto the provider.
    OMS_AHEAD = "OMS_AHEAD"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    #: We have an order the provider does not.
    MISSING_AT_PROVIDER = "MISSING_AT_PROVIDER"
    #: The provider has one we do not. `11` §6's "manual broker-side change".
    MISSING_LOCALLY = "MISSING_LOCALLY"
    #: Evidence is insufficient to classify. Not an error, and not a match.
    UNKNOWN = "UNKNOWN"

    @property
    def is_clean(self) -> bool:
        return self is DiscrepancyKind.MATCH


class Resolution(StrEnum):
    """What reconciliation did about a discrepancy.

    Only the three actions `11` §6 step 5 authorises can change anything. Everything
    else is recorded and escalated — brief §12 forbids inventing corrective actions,
    and the corrective action most likely to be invented (cancel the stray order) is
    exactly the one that could lose money.
    """

    #: Nothing to do.
    NONE = "NONE"
    #: An order event was appended to bring local state to provider truth.
    ORDER_STATE_APPLIED = "ORDER_STATE_APPLIED"
    #: A fill the provider reported and we lacked was inserted.
    FILL_INSERTED = "FILL_INSERTED"
    #: A position was corrected to match provider truth.
    POSITION_CORRECTED = "POSITION_CORRECTED"
    #: Noticed, persisted and surfaced. No automatic action is authorised.
    RECORDED_ONLY = "RECORDED_ONLY"
    #: Could not be resolved; the order stays unresolved rather than being guessed.
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """One difference between our view and the venue's, with what was done about it."""

    kind: DiscrepancyKind
    resolution: Resolution
    #: Our order id, when we have one. Absent for `MISSING_LOCALLY`.
    order_id: str | None = None
    #: The venue's id, when it has one. Absent for `MISSING_AT_PROVIDER` and after
    #: a lost acknowledgement.
    provider_order_id: str | None = None
    local_state: str | None = None
    provider_status: str | None = None
    local_value: str | None = None
    provider_value: str | None = None
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        """`11` §6: alert on unresolved or unexpected discrepancies."""
        return self.resolution in (Resolution.RECORDED_ONLY, Resolution.UNRESOLVED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "resolution": self.resolution.value,
            "order_id": self.order_id,
            "provider_order_id": self.provider_order_id,
            "local_state": self.local_state,
            "provider_status": self.provider_status,
            "local_value": self.local_value,
            "provider_value": self.provider_value,
            "detail": self.detail,
            "needs_attention": self.needs_attention,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationOutcome:
    """Summary counters for a run. Derived, never stored independently."""

    matched: int = 0
    discrepancies: int = 0
    resolved: int = 0
    needs_attention: int = 0
    fills_inserted: int = 0
    orders_updated: int = 0
    unresolved_orders: int = 0

    @property
    def is_clean(self) -> bool:
        """`18` Phase 10 acceptance: "`trader` is not ready until reconciliation is
        clean". Clean means nothing needs attention and nothing is unresolved — not
        merely that the run completed."""
        return self.needs_attention == 0 and self.unresolved_orders == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "discrepancies": self.discrepancies,
            "resolved": self.resolved,
            "needs_attention": self.needs_attention,
            "fills_inserted": self.fills_inserted,
            "orders_updated": self.orders_updated,
            "unresolved_orders": self.unresolved_orders,
            "is_clean": self.is_clean,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationRun:
    """One reconciliation, with its evidence. `11` §6 step 6: persist the run.

    `provider_snapshot` is kept because a discrepancy without the evidence that
    produced it cannot be re-examined later, and re-examining is the whole point of
    persisting a run rather than just its conclusion.
    """

    run_id: str
    trigger: ReconciliationTrigger
    #: Market time the run reconciled as of.
    as_of: datetime
    scope: str
    discrepancies: tuple[Discrepancy, ...] = ()
    outcome: ReconciliationOutcome = field(default_factory=ReconciliationOutcome)
    provider_snapshot: tuple[dict[str, Any], ...] = ()
    local_snapshot: tuple[dict[str, Any], ...] = ()
    #: Execution metadata, excluded from the digest like everywhere else.
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def content_digest(self) -> str:
        """Semantic identity of the run.

        Covers the trigger, the scope, the evidence and the conclusions; excludes
        `run_id`, `started_at` and `completed_at`. Two runs over unchanged evidence
        therefore share a digest, which is how brief §13's idempotency requirement
        is demonstrated rather than claimed.
        """
        return (
            "rec_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "trigger": self.trigger.value,
                        "as_of": self.as_of.isoformat(),
                        "scope": self.scope,
                        "discrepancies": [d.as_dict() for d in self.discrepancies],
                        "outcome": self.outcome.as_dict(),
                        "provider_snapshot": list(self.provider_snapshot),
                        "local_snapshot": list(self.local_snapshot),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()[:32]
        )

    def attention_required(self) -> tuple[Discrepancy, ...]:
        return tuple(d for d in self.discrepancies if d.needs_attention)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "content_digest": self.content_digest,
            "trigger": self.trigger.value,
            "as_of": self.as_of.isoformat(),
            "scope": self.scope,
            "discrepancies": [d.as_dict() for d in self.discrepancies],
            "outcome": self.outcome.as_dict(),
            "provider_snapshot": list(self.provider_snapshot),
            "local_snapshot": list(self.local_snapshot),
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "completed_at": (None if self.completed_at is None else self.completed_at.isoformat()),
        }
