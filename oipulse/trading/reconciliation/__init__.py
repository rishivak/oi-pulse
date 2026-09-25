"""Layer 10b — reconciliation. `11-TRADING.md` §6.

> Not an error handler. A subsystem with its own scheduler, records and tests.

```
OMS expected state  ──┐
                      ├──► classify ──► resolve (only what §6 step 5 authorises)
provider observed  ───┘         │
                                └──► record everything else, and alert
```

**Broker truth is authoritative, which is not the same as broker-obeyed.** It
governs whose answer wins when both have one; it does not license acting on an
answer the broker never gave. An order the broker holds and we do not is *recorded*,
never cancelled — brief §12 forbids inventing corrective actions, and that is the
one most likely to be invented and most likely to lose money.
"""

from oipulse.trading.reconciliation.engine import Reconciler, startup_gate
from oipulse.trading.reconciliation.mapping import (
    canonical_state_for,
    provider_is_terminal,
    reconciled_state,
)
from oipulse.trading.reconciliation.model import (
    Discrepancy,
    DiscrepancyKind,
    ReconciliationOutcome,
    ReconciliationRun,
    ReconciliationTrigger,
    Resolution,
)

__all__ = [
    "Discrepancy",
    "DiscrepancyKind",
    "Reconciler",
    "ReconciliationOutcome",
    "ReconciliationRun",
    "ReconciliationTrigger",
    "Resolution",
    "canonical_state_for",
    "provider_is_terminal",
    "reconciled_state",
    "startup_gate",
]
