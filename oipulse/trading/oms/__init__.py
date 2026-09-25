"""Layer 10a — the OMS. `11-TRADING.md` §4 and §5.

Owns the canonical order lifecycle between an approved risk decision and a venue.

```
approved RiskDecision ──► OMS order ──► adapter ──► ack | reject | SILENCE
                                                            │
                                                     UNKNOWN ──► PENDING_RECONCILIATION
```

Two rules do most of the work here.

**Silence is not rejection.** A lost acknowledgement produces `UNKNOWN`, and the
state machine contains no edge from `UNKNOWN` to anything submittable. Never
resubmitting is therefore a property of the graph, not a policy someone enforces.

**Risk is consumed, never re-derived.** `authorize_submission` asks the Phase 9
`RiskDecisionRecord` the questions it already answers. A second risk implementation
here would drift from the first, which `11` §3 exists to prevent.
"""

from oipulse.trading.oms.authorization import (
    AuthorizationRefusal,
    SubmissionAuthorization,
    authorize_submission,
)
from oipulse.trading.oms.manager import (
    OrderManager,
    SubmissionOutcome,
    SubmissionResultKind,
    attempt_id_for,
)

__all__ = [
    "AuthorizationRefusal",
    "OrderManager",
    "SubmissionAuthorization",
    "SubmissionOutcome",
    "SubmissionResultKind",
    "attempt_id_for",
    "authorize_submission",
]
