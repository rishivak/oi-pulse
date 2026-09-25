"""Position reconciliation — the Phase 10 to Phase 11 boundary, now closed.

Phase 10 implemented order and fill reconciliation and **explicitly deferred
positions**; that was recorded as limitation #3 of the Phase 10 report. This module
is that deferral discharged.

`11-TRADING.md` §6 scopes reconciliation to three things and this is the third:

```
local OMS  ↔  broker order state
local fills ↔ broker trades
local positions ↔ broker positions      ← here
```

### What may be corrected, and what may only be recorded

`11` §6 step 4 makes the broker authoritative for positions and step 5 authorises
"correct positions". Brief §18 narrows it: *"Do not automatically mutate canonical
portfolio state from an ambiguous provider discrepancy unless the design authorizes
it."*

The two clauses resolve cleanly. A discrepancy is **unambiguous** when both sides
have a position in the same instrument and disagree only about quantity — the broker
wins, and the correction is applied and recorded. Everything else is ambiguous:

| Kind | Resolution | Why |
|---|---|---|
| `MATCH` | nothing | they agree |
| `QUANTITY_MISMATCH` | **corrected** | unambiguous; broker is authoritative |
| `SIDE_MISMATCH` | recorded | long vs short is not a rounding difference; something is badly wrong and overwriting destroys the evidence |
| `MISSING_AT_PROVIDER` | recorded | we think we hold something the broker does not. Zeroing it locally would make an untracked real position invisible |
| `MISSING_LOCALLY` | recorded | the broker holds something we cannot explain. Creating it locally would invent a cost basis nobody paid |
| `UNKNOWN` | recorded | evidence insufficient to classify |

`MISSING_LOCALLY` deserves the emphasis. Adopting a broker position requires a cost
basis, and the broker's average price is not our cost basis — it may include fills
from before our records, or from another system. Inventing one would corrupt every
realised P&L computed afterwards.

### Idempotency (brief §19)

A correction is keyed on `(position key, observed quantity)`. Running twice against
unchanged provider evidence applies nothing the second time, transitions nothing and
duplicates no P&L. The applied-set lives on the reconciler so it survives across runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.trading.brokers.protocol import BrokerPosition
from oipulse.trading.portfolio.positions import PortfolioPosition, PositionKey

__all__ = [
    "PositionDiscrepancy",
    "PositionDiscrepancyKind",
    "PositionReconciler",
    "PositionReconciliationRun",
    "PositionResolution",
]


class PositionDiscrepancyKind(StrEnum):
    """Exactly the six brief §18 defines. None is invented."""

    MATCH = "MATCH"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    MISSING_AT_PROVIDER = "MISSING_AT_PROVIDER"
    MISSING_LOCALLY = "MISSING_LOCALLY"
    UNKNOWN = "UNKNOWN"

    @property
    def is_clean(self) -> bool:
        return self is PositionDiscrepancyKind.MATCH


class PositionResolution(StrEnum):
    """What was done. Only `POSITION_CORRECTED` changes anything."""

    NONE = "NONE"
    #: `11` §6 step 5's authorised action, applied only when unambiguous.
    POSITION_CORRECTED = "POSITION_CORRECTED"
    #: Noticed, persisted, surfaced. No automatic action is authorised.
    RECORDED_ONLY = "RECORDED_ONLY"
    #: Evidence insufficient even to classify.
    UNRESOLVED = "UNRESOLVED"

    @property
    def needs_attention(self) -> bool:
        return self in (PositionResolution.RECORDED_ONLY, PositionResolution.UNRESOLVED)


@dataclass(frozen=True, slots=True)
class PositionDiscrepancy:
    """One difference between our position and the broker's."""

    kind: PositionDiscrepancyKind
    resolution: PositionResolution
    instrument_id: int
    position_id: str | None = None
    local_quantity: int | None = None
    provider_quantity: int | None = None
    #: The broker's average price, recorded but **never adopted** as our basis.
    provider_average_price: Decimal | None = None
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.resolution.needs_attention

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "resolution": self.resolution.value,
            "instrument_id": self.instrument_id,
            "position_id": self.position_id,
            "local_quantity": self.local_quantity,
            "provider_quantity": self.provider_quantity,
            "provider_average_price": (
                None if self.provider_average_price is None else str(self.provider_average_price)
            ),
            "detail": self.detail,
            "needs_attention": self.needs_attention,
        }


@dataclass(frozen=True, slots=True)
class PositionReconciliationRun:
    """One position-reconciliation pass, with its evidence.

    Content-addressed over the evidence and the conclusions, excluding `run_id` and
    the execution timestamps — so a repeat over unchanged provider state produces
    the same digest, which is how brief §19's idempotency is demonstrated.
    """

    run_id: str
    account_id: str
    portfolio_id: str
    as_of: datetime
    discrepancies: tuple[PositionDiscrepancy, ...] = ()
    provider_snapshot: tuple[dict[str, Any], ...] = ()
    local_snapshot: tuple[dict[str, Any], ...] = ()
    corrections_applied: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def matched(self) -> int:
        return sum(1 for d in self.discrepancies if d.kind.is_clean)

    @property
    def mismatched(self) -> int:
        return sum(1 for d in self.discrepancies if not d.kind.is_clean)

    @property
    def needs_attention(self) -> tuple[PositionDiscrepancy, ...]:
        return tuple(d for d in self.discrepancies if d.needs_attention)

    @property
    def is_clean(self) -> bool:
        """Nothing outstanding. A correction that was *applied* is not outstanding."""
        return not self.needs_attention

    @property
    def content_digest(self) -> str:
        return (
            "prec_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "account_id": self.account_id,
                        "portfolio_id": self.portfolio_id,
                        "as_of": self.as_of.isoformat(),
                        "discrepancies": [d.as_dict() for d in self.discrepancies],
                        "provider_snapshot": list(self.provider_snapshot),
                        "local_snapshot": list(self.local_snapshot),
                        "corrections_applied": self.corrections_applied,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()[:32]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "content_digest": self.content_digest,
            "account_id": self.account_id,
            "portfolio_id": self.portfolio_id,
            "as_of": self.as_of.isoformat(),
            "discrepancies": [d.as_dict() for d in self.discrepancies],
            "matched": self.matched,
            "mismatched": self.mismatched,
            "corrections_applied": self.corrections_applied,
            "needs_attention": len(self.needs_attention),
            "is_clean": self.is_clean,
            "provider_snapshot": list(self.provider_snapshot),
            "local_snapshot": list(self.local_snapshot),
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "completed_at": (None if self.completed_at is None else self.completed_at.isoformat()),
        }


@dataclass
class PositionReconciler:
    """Compares our positions with the broker's. Holds the applied-correction set."""

    #: `(position_id, observed_quantity)` pairs already applied. Keyed on the
    #: observation, not just the position, so a genuine later change is a new
    #: correction while a re-report of the same one is not.
    _applied: set[str] = field(default_factory=set, repr=False)
    _runs: list[PositionReconciliationRun] = field(default_factory=list, repr=False)

    @property
    def corrections_applied(self) -> int:
        return len(self._applied)

    def runs(self) -> tuple[PositionReconciliationRun, ...]:
        return tuple(self._runs)

    def reconcile(
        self,
        local: Sequence[PortfolioPosition],
        provider: Sequence[BrokerPosition],
        *,
        account_id: str,
        portfolio_id: str,
        at: datetime,
        run_id: str,
        apply_corrections: bool = True,
    ) -> tuple[PositionReconciliationRun, Mapping[PositionKey, int]]:
        """Classify every difference. Returns the run and any corrections to apply.

        Corrections are **returned, not written**: this module holds no position
        book, so the caller applies them. That keeps the reconciler pure and means
        an operator can run it in report-only mode by ignoring the mapping —
        `apply_corrections=False` also records them as `RECORDED_ONLY` so the
        distinction appears in the audit rather than only in the caller's choice.
        """
        by_instrument_local = {p.key.instrument_id: p for p in local}
        by_instrument_provider = {p.instrument_id: p for p in provider}

        discrepancies: list[PositionDiscrepancy] = []
        corrections: dict[PositionKey, int] = {}

        # Sorted union, so the run's digest cannot depend on iteration order.
        for instrument_id in sorted(set(by_instrument_local) | set(by_instrument_provider)):
            ours = by_instrument_local.get(instrument_id)
            theirs = by_instrument_provider.get(instrument_id)

            if ours is not None and theirs is None:
                discrepancies.append(
                    PositionDiscrepancy(
                        kind=PositionDiscrepancyKind.MISSING_AT_PROVIDER,
                        resolution=PositionResolution.RECORDED_ONLY,
                        instrument_id=instrument_id,
                        position_id=ours.key.position_id,
                        local_quantity=ours.quantity,
                        detail=(
                            "we hold a position the broker does not report. Recorded "
                            "rather than zeroed: if the broker is right we have an "
                            "accounting error, and if we are right zeroing it would "
                            "make a real position invisible"
                        ),
                    )
                )
                continue

            if ours is None and theirs is not None:
                discrepancies.append(
                    PositionDiscrepancy(
                        kind=PositionDiscrepancyKind.MISSING_LOCALLY,
                        resolution=PositionResolution.RECORDED_ONLY,
                        instrument_id=instrument_id,
                        provider_quantity=theirs.quantity,
                        provider_average_price=theirs.average_price,
                        detail=(
                            "the broker holds a position we have no record of. "
                            "Recorded, never adopted: creating it locally would need "
                            "a cost basis, and the broker's average price is not ours "
                            "-- it may include fills from before our records or from "
                            "another system, and adopting it would corrupt every "
                            "realised P&L computed afterwards"
                        ),
                    )
                )
                continue

            assert ours is not None and theirs is not None
            if ours.quantity == theirs.quantity:
                discrepancies.append(
                    PositionDiscrepancy(
                        kind=PositionDiscrepancyKind.MATCH,
                        resolution=PositionResolution.NONE,
                        instrument_id=instrument_id,
                        position_id=ours.key.position_id,
                        local_quantity=ours.quantity,
                        provider_quantity=theirs.quantity,
                    )
                )
                continue

            # Opposite signs, both non-zero: not a quantity difference.
            if ours.quantity * theirs.quantity < 0:
                discrepancies.append(
                    PositionDiscrepancy(
                        kind=PositionDiscrepancyKind.SIDE_MISMATCH,
                        resolution=PositionResolution.RECORDED_ONLY,
                        instrument_id=instrument_id,
                        position_id=ours.key.position_id,
                        local_quantity=ours.quantity,
                        provider_quantity=theirs.quantity,
                        provider_average_price=theirs.average_price,
                        detail=(
                            "we and the broker disagree about direction. This is not "
                            "a rounding difference and overwriting it would destroy "
                            "the evidence of whatever caused it"
                        ),
                    )
                )
                continue

            # Same direction, different size: unambiguous, broker authoritative.
            correction_key = f"{ours.key.position_id}:{theirs.quantity}"
            already = correction_key in self._applied
            if apply_corrections and not already:
                corrections[ours.key] = theirs.quantity
                self._applied.add(correction_key)

            discrepancies.append(
                PositionDiscrepancy(
                    kind=PositionDiscrepancyKind.QUANTITY_MISMATCH,
                    resolution=(
                        PositionResolution.POSITION_CORRECTED
                        if apply_corrections
                        else PositionResolution.RECORDED_ONLY
                    ),
                    instrument_id=instrument_id,
                    position_id=ours.key.position_id,
                    local_quantity=ours.quantity,
                    provider_quantity=theirs.quantity,
                    provider_average_price=theirs.average_price,
                    detail=(
                        f"same direction, different size; the broker is authoritative "
                        f"({ours.quantity} -> {theirs.quantity})"
                        + (" (already applied by an earlier run)" if already else "")
                    ),
                )
            )

        run = PositionReconciliationRun(
            run_id=run_id,
            account_id=account_id,
            portfolio_id=portfolio_id,
            as_of=at,
            discrepancies=tuple(discrepancies),
            provider_snapshot=tuple(
                p.as_dict() for p in sorted(provider, key=lambda p: p.instrument_id)
            ),
            local_snapshot=tuple(p.as_dict() for p in sorted(local, key=lambda p: p.key.sort_key)),
            corrections_applied=len(corrections),
        )
        self._runs.append(run)
        return run, corrections
