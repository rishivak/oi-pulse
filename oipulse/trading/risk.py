"""The risk seam — minimal, and deliberately not a risk engine.

`11-TRADING.md` §1 puts risk in the path as the only gate:

```
TradeIntent → RiskEngine → RiskDecision (immutable sequence) → OMS → BrokerAdapter
```

Phase 9 implements that engine. Phase 8 implements the **shape of the seam** and
nothing behind it, because a simplified stand-in would be worse than an obvious gap:
it would approve intents the real engine would refuse, and it would look enough like a
risk engine to be forgotten about.

### What is here

* `RiskGate` — the protocol Phase 9 fills.
* `RiskDecisionRecord` — one appended decision, carrying the three context fields
  `11` §3 requires (`risk_state_ref`, `risk_evaluation_time`, `inputs_digest`) plus
  `approved_until`. The *shape* is right so Phase 9 does not have to migrate it.
* `UNEVALUATED_RISK` — a pass-through that approves everything, names itself, and
  reports `evaluated=False`. Every account and order it touches is flagged.

### Independence

`11` §3: "`trading/risk` may not import `trading/oms`, any broker adapter, or any
strategy module ... the component that says 'no' must not depend on the components it
constrains." This module therefore imports the intent and nothing else from the
trading package, and the import contract `risk-is-independent` enforces it.

### Sequence, not a single verdict

`11` §3: "**Not one decision per intent.** Risk is re-evaluated on modification,
retry, changed market conditions ... Each evaluation **appends** a new immutable
decision." `RiskDecisionRecord` therefore carries `sequence_no` from the start, even
though Phase 8's pass-through only ever emits sequence 1. Retrofitting a sequence onto
a single-verdict model later would mean rewriting every stored decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from oipulse.trading.intents import TradeIntent

__all__ = [
    "UNEVALUATED_RISK",
    "RiskDecisionRecord",
    "RiskGate",
    "RiskVerdict",
    "UnevaluatedRiskGate",
]


class RiskVerdict(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    #: Declared because `11` §3 defines it. No Phase 8 gate emits it.
    MODIFIED = "MODIFIED"


@dataclass(frozen=True, slots=True)
class RiskDecisionRecord:
    """One appended decision. Immutable, sequenced, and self-describing.

    Carries the context *this* evaluation saw, not the intent's. `11` §3 is explicit
    about why: the intent's checkpoint records what the strategy saw, which is not
    what a later risk evaluation saw, and after a loss the second is the question
    that matters.
    """

    intent_id: str
    sequence_no: int
    verdict: RiskVerdict
    reason: str
    #: False whenever no real risk engine evaluated this intent.
    evaluated: bool
    #: The MarketState *this* evaluation read. Empty while Phase 9 is pending.
    risk_state_ref: str = ""
    #: Market time of the evaluation.
    risk_evaluation_time: datetime | None = None
    #: Digest of portfolio + limits + state inputs.
    inputs_digest: str = ""
    #: Validity window. None when rejected, or when nothing was evaluated.
    approved_until: datetime | None = None
    #: Every limit evaluated, with its value. Empty while Phase 9 is pending.
    limits_evaluated: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.sequence_no < 1:
            raise ValueError("risk decision sequence_no must be >= 1")

    @property
    def is_approved(self) -> bool:
        return self.verdict is RiskVerdict.APPROVED

    def is_actionable_at(self, at: datetime) -> bool:
        """`11` §3: submitting against an expired approval is refused.

        An approval with no `approved_until` is actionable only because nothing
        evaluated it; once Phase 9 lands, every approval carries a window.
        """
        if not self.is_approved:
            return False
        return self.approved_until is None or at <= self.approved_until

    @property
    def decision_digest(self) -> str:
        """Content address. Excludes nothing that changes the decision's meaning."""
        return hashlib.sha256(
            json.dumps(
                {
                    "intent_id": self.intent_id,
                    "sequence_no": self.sequence_no,
                    "verdict": self.verdict.value,
                    "evaluated": self.evaluated,
                    "risk_state_ref": self.risk_state_ref,
                    "inputs_digest": self.inputs_digest,
                    "limits": dict(self.limits_evaluated),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "sequence_no": self.sequence_no,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evaluated": self.evaluated,
            "risk_state_ref": self.risk_state_ref,
            "risk_evaluation_time": (
                None if self.risk_evaluation_time is None else self.risk_evaluation_time.isoformat()
            ),
            "inputs_digest": self.inputs_digest,
            "approved_until": (
                None if self.approved_until is None else self.approved_until.isoformat()
            ),
            "limits_evaluated": dict(self.limits_evaluated),
            "decision_digest": self.decision_digest,
        }


@runtime_checkable
class RiskGate(Protocol):
    """The seam Phase 9 fills.

    `evaluates_risk` must be True for any gate that applies rules and False for a
    pass-through. The runtime reads it to decide whether an account may describe
    itself as risk-evaluated, so a future engine cannot be mistaken for the
    placeholder or vice versa.
    """

    @property
    def name(self) -> str: ...

    @property
    def evaluates_risk(self) -> bool: ...

    def evaluate(
        self, intent: TradeIntent, *, sequence_no: int, at: datetime
    ) -> RiskDecisionRecord: ...


@dataclass(frozen=True, slots=True)
class UnevaluatedRiskGate:
    """Approves every intent and says so. Named so nobody mistakes it for an engine."""

    name: str = "UNEVALUATED"

    @property
    def evaluates_risk(self) -> bool:
        return False

    def evaluate(
        self, intent: TradeIntent, *, sequence_no: int, at: datetime
    ) -> RiskDecisionRecord:
        return RiskDecisionRecord(
            intent_id=intent.intent_id,
            sequence_no=sequence_no,
            verdict=RiskVerdict.APPROVED,
            reason=(
                "no risk engine is wired: Phase 9 is not implemented, so no position "
                "limit, exposure cap, loss limit, stale-data check or kill switch was "
                "applied to this intent"
            ),
            evaluated=False,
            risk_evaluation_time=at,
        )


UNEVALUATED_RISK = UnevaluatedRiskGate()
