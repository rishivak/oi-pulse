"""`UpstoxBrokerAdapter` — the boundary, without the capability.

`18-ROADMAP.md` Phase 10 lists `UpstoxBrokerAdapter` **(flagged off)** as a
deliverable, and `06-UPSTOX_INTEGRATION.md` §10 says it "is implemented **last** and
sits behind a feature flag defaulting to off".

This is that adapter, and it is deliberately incomplete in one specific way: **it
contains no wire format.**

### Why no wire format

Phase 10 brief §28:

> Use only provider capabilities actually established in Phase 2/current official
> documentation. Do not invent provider sequence IDs, event IDs, guaranteed
> idempotency, guaranteed exactly-once delivery, guaranteed real-time ordering.

Phase 2 established the Upstox **market-data** contracts — instruments, option chain,
quotes, historical OHLC and OI, and the V3 binary feed, the last of which was verified
against recorded fixtures (`06` §6). It established **nothing** about the order APIs:
no request encoding, no response shape, no status vocabulary, no error taxonomy, no
statement about whether Upstox deduplicates a repeated submission.

Writing `place_order` here would therefore mean inventing all of it. The invented
version would type-check, pass tests written against the same invention, and be wrong
in ways nobody could see until real money moved through it. So every method that
would touch the network raises, and the mapping layer is absent rather than guessed.

### What this class is for

It occupies the name, declares the capability set (which excludes `LIVE_SUBMIT`), and
gives the OMS a concrete thing to refuse. That means the refusal path is exercised by
tests rather than hypothetical, and it means Phase 11+ finds a boundary to fill rather
than a decision to make.

### The three gates that would have to precede a real implementation

`11-TRADING.md` §9 and `13-FRONTEND_IA.md`/`17-SECURITY.md` §93 define them, and none
is satisfied:

1. `LIVE_EXECUTION_ENABLED` — a code constant, currently `False`.
2. The `LIVE_TRADE` permission, separate from `PAPER_TRADE`. Not implemented.
3. A clean reconciliation run before accepting intents (`18` Phase 10 acceptance:
   "`trader` is not ready until reconciliation is clean").

`tools/check_live_execution_barrier.py` asserts mechanically that this class still
cannot submit, and is mutation-tested.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn

from oipulse.trading.brokers.capability import (
    ExecutionCapability,
    LiveExecutionDisabled,
    require_capability,
)
from oipulse.trading.brokers.protocol import (
    BrokerFill,
    BrokerOrderEvent,
    BrokerOrderRequest,
    BrokerOrderState,
    BrokerPosition,
)

__all__ = ["UPSTOX_ORDER_API_UNVERIFIED", "UpstoxBrokerAdapter"]

#: Stated once, referenced by every refusal, so the reason is identical everywhere
#: and a reader meets the same explanation wherever they hit the boundary.
UPSTOX_ORDER_API_UNVERIFIED = (
    "the Upstox order API has no verified contract in this repository. Phase 2 "
    "established the market-data endpoints and the V3 binary feed against recorded "
    "fixtures; it established nothing about order placement, cancellation, status "
    "vocabulary, error taxonomy or duplicate handling. Implementing this method "
    "would mean inventing that contract, which the Phase 10 brief forbids"
)


@dataclass(frozen=True, slots=True)
class UpstoxBrokerAdapter:
    """The live boundary. Declares the shape; holds none of the capability.

    Frozen and empty by construction: no HTTP client, no session, no token, no
    base URL. There is nothing here to configure into working, which is the point —
    the barrier is the absence of an implementation, not a branch that skips one.
    """

    name: str = "UPSTOX"

    @property
    def capabilities(self) -> frozenset[ExecutionCapability]:
        """Empty.

        Not `{QUERY_PROVIDER}`: reading order state would need the same unverified
        response mapping that writing does. An adapter that could *read* but not
        *write* would still be parsing an invented shape, and a reconciliation run
        against invented data would be worse than no run at all — it would produce
        confident, wrong answers about what the broker holds.
        """
        return frozenset()

    # ------------------------------------------------------------------ submission

    async def place_order(self, request: BrokerOrderRequest) -> NoReturn:
        """Never submits. Raises before anything could reach a network.

        The capability check runs first and raises on `LIVE_SUBMIT` unconditionally,
        so this method cannot return an acknowledgement under any configuration.
        """
        require_capability(self.capabilities, ExecutionCapability.LIVE_SUBMIT, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.place_order: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    async def cancel_order(self, provider_order_id: str) -> NoReturn:
        require_capability(self.capabilities, ExecutionCapability.LIVE_SUBMIT, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.cancel_order: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    async def modify_order(self, provider_order_id: str, changes: dict[str, Any]) -> NoReturn:
        require_capability(self.capabilities, ExecutionCapability.LIVE_SUBMIT, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.modify_order: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    # ------------------------------------------------------- reconciliation surface

    async def get_order(self, provider_order_id: str) -> BrokerOrderState | None:
        """Refuses rather than returning `None`.

        `None` means "the venue does not have this order", which the reconciler
        treats as evidence. Returning it when we simply cannot ask would feed a
        conclusion into reconciliation that no provider ever stated.
        """
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.get_order: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    async def list_orders(self, *, since: datetime) -> Sequence[BrokerOrderState]:
        """Refuses rather than returning an empty list.

        An empty list is a *finding* — it means the broker holds nothing — and the
        reconciler would classify every local order as `MISSING_AT_PROVIDER` and
        act on it. Silence from an unimplemented adapter must never be mistaken for
        a broker saying "I have nothing".
        """
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.list_orders: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    async def list_trades(self, *, since: datetime) -> Sequence[BrokerFill]:
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.list_trades: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    async def get_positions(self) -> Sequence[BrokerPosition]:
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.get_positions: {UPSTOX_ORDER_API_UNVERIFIED}"
        )

    def subscribe_order_updates(self) -> AsyncIterator[BrokerOrderEvent]:
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        raise LiveExecutionDisabled(  # pragma: no cover - the gate above always raises
            f"{self.name}.subscribe_order_updates: {UPSTOX_ORDER_API_UNVERIFIED}"
        )
