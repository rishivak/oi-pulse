"""Layer 8 — Paper trading.

`docs/design/11-TRADING.md`. The pipeline this package implements, and the part it
deliberately stops at:

```
Signal / Strategy ──► TradeIntent ──► [risk seam] ──► PaperOrder ──► PaperFill
                                                            │
                                                            ▼
                                            position · cash · P&L · journal
```

**Live execution does not exist here, and cannot be switched on.** `11` §9 lists the
safety posture; Phase 8 implements the paper half of it and leaves the live half
*absent* rather than disabled:

* `BrokerAdapter` is a protocol with exactly one implementation, `PaperBrokerAdapter`.
* There is no `UpstoxBrokerAdapter`, no order-submission HTTP call, and no broker
  credential reachable from this package — the import contract forbids the provider
  and credential modules outright.
* An account carries `mode`, per `11` §7, so that no strategy or ledger calculation
  knows whether it is paper or live. Resolving an adapter for `mode=LIVE` raises
  `LiveExecutionUnavailable`: the seam is real, the implementation is not.

A configuration flag cannot turn paper into live, because there is nothing for it to
turn on. `tools/check_paper_trading_safety.py` asserts that mechanically.

**One implementation, not two.** Fills come from the Phase 7 `FillModel` and the
ledger from the Phase 7 `Ledger`, exactly as `11` §7 requires — paper trading is a
rehearsal of the same machinery, not a parallel one that drifts.
"""

from oipulse.trading.accounts import (
    AccountMode,
    AccountStatus,
    LiveExecutionUnavailable,
    PaperAccount,
    PaperAccountConfig,
)
from oipulse.trading.intents import (
    IntentConstraints,
    IntentLeg,
    IntentSource,
    TimeInForce,
    TradeIntent,
    from_strategy_intent,
)
from oipulse.trading.orders import (
    InvalidTransition,
    OrderEvent,
    OrderState,
    PaperOrder,
    is_terminal,
    permitted_transitions,
)

__all__ = [
    "AccountMode",
    "AccountStatus",
    "IntentConstraints",
    "IntentLeg",
    "IntentSource",
    "InvalidTransition",
    "LiveExecutionUnavailable",
    "OrderEvent",
    "OrderState",
    "PaperAccount",
    "PaperAccountConfig",
    "PaperOrder",
    "TimeInForce",
    "TradeIntent",
    "from_strategy_intent",
    "is_terminal",
    "permitted_transitions",
]
