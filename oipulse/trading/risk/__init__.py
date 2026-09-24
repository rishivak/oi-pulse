"""Layer 9 — the risk gate. `docs/design/11-TRADING.md` §3.

```
TradeIntent ──► RiskEngine.evaluate(intent, state) ──► RiskDecision ──► execution
                                                              │
                                             APPROVED | MODIFIED | REJECTED
```

**The component that says "no" depends on nothing it constrains.** `11` §3 forbids
`trading/risk` from importing the OMS, any broker adapter or any strategy module. The
import contract `risk-is-independent` enforces it, and everything here is a pure
function of an intent, a state and a policy — no database, no market feed, no clock.

### What Phase 9 added to the Phase 8 seam

Phase 8 declared `RiskGate`, `RiskDecisionRecord` and `UNEVALUATED_RISK`, and said
plainly that no engine existed. Phase 9 fills that seam:

* `RiskPolicy` / `RiskLimits` — versioned, content-addressed, conservative by default.
* `RiskState` — an immutable, sorted, content-addressed snapshot (`risk_state_ref`).
* `RiskEngine` — evaluates every limit `11` §3 tabulates, in a declared order,
  without short-circuiting.
* Structured `LimitEvaluation` evidence, because free text is not an audit trail.

`UnevaluatedRiskGate` is **kept**, not deleted. It is what an account runs with when
no policy has been configured, it reports `evaluates_risk=False`, and its decisions
are never actionable — `RiskDecisionRecord.is_actionable_at` returns False for any
unevaluated decision, so a pass-through cannot authorize an order now that a real
engine exists.

### The protocol widened

`RiskGate.evaluate` now takes the `RiskState` as well as the intent. Phase 8's
signature could not: it had no state to pass. `11` §3's "it receives an intent plus
context" is that context, and passing it explicitly is what keeps the engine pure and
`inputs_digest` honest — an engine that fetched its own state could not prove what it
read.
"""

from oipulse.trading.risk.decision import (
    AuthorizationStatus,
    LimitEvaluation,
    LimitStatus,
    RiskDecisionRecord,
    RiskVerdict,
    inputs_digest_for,
)
from oipulse.trading.risk.engine import RiskEngine, RiskInputsUnavailable
from oipulse.trading.risk.gate import UNEVALUATED_RISK, RiskGate, UnevaluatedRiskGate
from oipulse.trading.risk.policy import (
    CONSERVATIVE_LIMITS,
    LimitCategory,
    RiskLimits,
    RiskPolicy,
    SessionWindow,
)
from oipulse.trading.risk.state import (
    ExposureSnapshot,
    KillSwitchState,
    PositionSnapshot,
    RiskState,
    StateQualityInput,
    VenueHealth,
    build_exposure,
)

__all__ = [
    "CONSERVATIVE_LIMITS",
    "UNEVALUATED_RISK",
    "AuthorizationStatus",
    "ExposureSnapshot",
    "KillSwitchState",
    "LimitCategory",
    "LimitEvaluation",
    "LimitStatus",
    "PositionSnapshot",
    "RiskDecisionRecord",
    "RiskEngine",
    "RiskGate",
    "RiskInputsUnavailable",
    "RiskLimits",
    "RiskPolicy",
    "RiskState",
    "RiskVerdict",
    "SessionWindow",
    "StateQualityInput",
    "UnevaluatedRiskGate",
    "VenueHealth",
    "build_exposure",
    "inputs_digest_for",
]
