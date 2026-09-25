"""The broker boundary — `06-UPSTOX_INTEGRATION.md` §1, `11-TRADING.md` §7 and §9.

```
OMS ──► BrokerAdapter ──┬──► PaperBrokerAdapter    (SIMULATE, QUERY_PROVIDER)
                        └──► UpstoxBrokerAdapter   (no capabilities; cannot submit)
```

**Live execution is off, and the barrier is structural.** `LIVE_EXECUTION_ENABLED` is
a module constant, but the flag is the weaker half: `UpstoxBrokerAdapter` holds no
HTTP client, no credential and no wire format, because Phase 2 established the Upstox
*market-data* contracts and nothing about the order APIs. Phase 10 does not invent
them (brief §28), so every submitting method raises. A flag can be flipped; an absent
implementation cannot be enabled.

`tools/check_live_execution_barrier.py` asserts that mechanically and is
mutation-tested.
"""

from oipulse.trading.brokers.capability import (
    LIVE_EXECUTION_ENABLED,
    ExecutionCapability,
    LiveExecutionDisabled,
    require_capability,
)
from oipulse.trading.brokers.paper import (
    PaperBrokerAdapter,
    PaperVenueFaults,
    adapter_for,
)
from oipulse.trading.brokers.protocol import (
    BrokerAck,
    BrokerAdapter,
    BrokerFill,
    BrokerOrderEvent,
    BrokerOrderRequest,
    BrokerOrderState,
    BrokerPosition,
    BrokerSubmissionTimeout,
    ProviderOrderStatus,
)
from oipulse.trading.brokers.upstox import (
    UPSTOX_ORDER_API_UNVERIFIED,
    UpstoxBrokerAdapter,
)

__all__ = [
    "LIVE_EXECUTION_ENABLED",
    "UPSTOX_ORDER_API_UNVERIFIED",
    "BrokerAck",
    "BrokerAdapter",
    "BrokerFill",
    "BrokerOrderEvent",
    "BrokerOrderRequest",
    "BrokerOrderState",
    "BrokerPosition",
    "BrokerSubmissionTimeout",
    "ExecutionCapability",
    "LiveExecutionDisabled",
    "PaperBrokerAdapter",
    "PaperVenueFaults",
    "ProviderOrderStatus",
    "UpstoxBrokerAdapter",
    "adapter_for",
    "require_capability",
]
