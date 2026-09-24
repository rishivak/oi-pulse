"""The risk seam — declared, not implemented.

`10-REPLAY.md` §7 puts the **production** risk engine in the backtest path:

```
TradeIntent -> RiskEngine.evaluate() -> RiskDecision -> OMS -> FillModel
```

That engine is Phase 9 and the OMS state machine is Phase 10. Neither exists yet, and
Phase 7 must not build a stand-in for them: a simplified risk check would pass a
backtest that the real engine would reject, which is precisely the failure §7 exists
to prevent, and a plausible-looking placeholder is more dangerous than an obvious gap
because it invites being forgotten.

So this module defines the **protocol** and one explicitly-named default:
`UNCONSTRAINED_RISK`, which approves everything and says so. A run using it is flagged
`risk_evaluated=False`, and that flag travels onto `BacktestResult`. A reader can
therefore tell, from the result alone, that no position limit, no exposure cap and no
drawdown rule was applied -- rather than having to know which phase the repository was
at when the run was made.

When Phase 9 lands, it supplies a `RiskGate` and the seam closes without the call
sites moving.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from oipulse.backtest.intents import TradeIntent

__all__ = [
    "UNCONSTRAINED_RISK",
    "RiskGate",
    "RiskVerdict",
    "UnconstrainedRiskGate",
]


class RiskVerdict(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class RiskDecisionStub:
    """The minimum a Phase 7 backtest needs to record a risk outcome.

    Deliberately *not* named `RiskDecision`: `11-RISK.md` defines that entity with
    `risk_state_ref`, `risk_evaluation_time` and `inputs_digest`, and Phase 9 owns it.
    Taking the name here would leave a weaker type sitting where the real one belongs.
    """

    intent_id: str
    verdict: RiskVerdict
    reason: str
    #: False whenever no real risk engine evaluated this intent.
    evaluated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evaluated": self.evaluated,
        }


@runtime_checkable
class RiskGate(Protocol):
    """The seam Phase 9 fills.

    `evaluated` on the returned decision must be True for any gate that actually
    applies rules, and False for a pass-through. The runner reads it to decide
    whether the run may describe itself as risk-evaluated.
    """

    @property
    def name(self) -> str: ...

    @property
    def evaluates_risk(self) -> bool: ...

    def evaluate(self, intent: TradeIntent) -> RiskDecisionStub: ...


@dataclass(frozen=True, slots=True)
class UnconstrainedRiskGate:
    """Approves every intent. Named so that nobody mistakes it for a risk engine."""

    name: str = "UNCONSTRAINED"

    @property
    def evaluates_risk(self) -> bool:
        return False

    def evaluate(self, intent: TradeIntent) -> RiskDecisionStub:
        return RiskDecisionStub(
            intent_id=intent.intent_id,
            verdict=RiskVerdict.APPROVED,
            reason=(
                "no risk engine is wired: Phase 9 is not implemented, so no position "
                "limit, exposure cap or drawdown rule was applied to this intent"
            ),
            evaluated=False,
        )


UNCONSTRAINED_RISK = UnconstrainedRiskGate()
