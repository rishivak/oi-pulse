"""The `RiskGate` protocol and the unevaluated pass-through.

Kept as its own module so the *seam* is separable from the *engine*. A caller that
only needs to accept a gate — the paper runtime, say — imports this and nothing else,
which keeps the dependency honest: the runtime depends on the existence of a gate,
not on any particular set of limits.

### The pass-through survives Phase 9, deliberately

`UnevaluatedRiskGate` is what an account runs with when no policy has been
configured. It approves, reports `evaluates_risk=False`, and its decisions carry
`evaluated=False` — which `RiskDecisionRecord.is_actionable_at` treats as never
actionable. So once a real engine exists, a pass-through approval cannot authorize an
order; it can only document that nothing evaluated the intent.

Deleting it would have been the wrong move. An account with no policy is a real
state, and the honest representation of it is a gate that says so, not a missing
object that makes the runtime unconstructible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from oipulse.trading.intents import TradeIntent
from oipulse.trading.risk.decision import RiskDecisionRecord, RiskVerdict
from oipulse.trading.risk.state import RiskState

__all__ = ["UNEVALUATED_RISK", "RiskGate", "UnevaluatedRiskGate"]


@runtime_checkable
class RiskGate(Protocol):
    """Anything that can decide whether an intent may proceed.

    `evaluates_risk` must be True for a gate that applies rules and False for a
    pass-through. Callers read it to decide whether an account may describe itself
    as risk-evaluated, so a real engine and a placeholder stay distinguishable
    without inspecting their decisions.
    """

    @property
    def name(self) -> str: ...

    @property
    def evaluates_risk(self) -> bool: ...

    def evaluate(
        self,
        intent: TradeIntent,
        state: RiskState,
        *,
        sequence_no: int,
        at: datetime,
        knowledge_horizon: datetime | None = ...,
    ) -> RiskDecisionRecord: ...


@dataclass(frozen=True, slots=True)
class UnevaluatedRiskGate:
    """Approves, and says plainly that nothing evaluated the intent.

    Its approvals are **not actionable**: `evaluated=False` makes
    `is_actionable_at` return False, so no order can be authorized by one. That is
    the difference between "no policy is configured" and "the policy permitted it",
    and conflating the two is how an unconfigured account quietly trades unlimited.
    """

    name: str = "UNEVALUATED"

    @property
    def evaluates_risk(self) -> bool:
        return False

    def evaluate(
        self,
        intent: TradeIntent,
        state: RiskState,
        *,
        sequence_no: int,
        at: datetime,
        knowledge_horizon: datetime | None = None,
    ) -> RiskDecisionRecord:
        return RiskDecisionRecord(
            intent_id=intent.intent_id,
            sequence_no=sequence_no,
            verdict=RiskVerdict.APPROVED,
            reason=(
                "no risk policy is configured for this account, so no position "
                "limit, exposure cap, loss limit, concentration rule, data check or "
                "kill switch was applied. This decision documents that absence; it "
                "does not authorize execution."
            ),
            evaluated=False,
            risk_state_ref=state.risk_state_ref,
            risk_evaluation_time=at,
            requested_quantity=intent.total_quantity,
            approved_quantity=intent.total_quantity,
            approved_until=at,
            knowledge_horizon=knowledge_horizon if knowledge_horizon is not None else at,
        )


UNEVALUATED_RISK = UnevaluatedRiskGate()
