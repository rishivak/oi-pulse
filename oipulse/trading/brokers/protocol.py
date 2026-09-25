"""The `BrokerAdapter` protocol and the provider value types.

`06-UPSTOX_INTEGRATION.md` §1 defines the protocol verbatim:

```python
class BrokerAdapter(Protocol):
    async def place_order(request: BrokerOrderRequest) -> BrokerAck
    async def cancel_order(broker_order_id) -> BrokerAck
    async def modify_order(broker_order_id, changes) -> BrokerAck
    async def get_order(broker_order_id) -> BrokerOrderState
    async def list_orders(*, since) -> list[BrokerOrderState]     # reconciliation
    async def list_trades(*, since) -> list[BrokerFill]           # reconciliation
    async def get_positions() -> list[BrokerPosition]             # reconciliation
    def subscribe_order_updates() -> AsyncIterator[BrokerOrderEvent]
```

> The `list_orders` / `list_trades` / `get_positions` methods exist **specifically
> for reconciliation** and are not optional parts of the protocol.

### Provider identity is represented honestly

Brief §8 and §28 forbid inventing provider identifiers, sequence numbers or ordering
guarantees. `06` §6 already recorded the verified finding for market data: *Upstox V3
supplies no provider identity and no provider ordering.* Nothing equivalent has been
verified for the order APIs, so:

* `provider_order_id` is `str | None`. `None` means the provider has not told us one
  — the normal state after a lost acknowledgement, and the reason `UNKNOWN` exists.
* There is no `provider_sequence` field anywhere. Adding one would imply an ordering
  guarantee nobody has verified.
* `BrokerOrderEvent` carries `received_at` alongside `provider_time`, and the two
  are never conflated. `11` §6: order-update events are a *hint* that accelerates
  reconciliation, never the sole source of truth.

### `ProviderOrderStatus` is the provider's vocabulary, not ours

It deliberately does not mirror `OrderState`. Mapping between them is a separate,
testable function (`reconciliation/mapping.py`), and keeping the vocabularies apart is
what makes an unmappable provider status visible rather than silently coerced.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from oipulse.backtest.intents import OrderType, Side
from oipulse.trading.brokers.capability import ExecutionCapability

__all__ = [
    "BrokerAck",
    "BrokerAdapter",
    "BrokerFill",
    "BrokerOrderEvent",
    "BrokerOrderRequest",
    "BrokerOrderState",
    "BrokerPosition",
    "BrokerSubmissionTimeout",
    "ProviderOrderStatus",
]


class ProviderOrderStatus(StrEnum):
    """A venue's own vocabulary for an order.

    Deliberately not `OrderState`. A provider says "complete"; our machine says
    `FILLED`; translating between them is an explicit, tested mapping rather than a
    shared enum that hides the assumption that the vocabularies align.

    `UNKNOWN` is a real value, not a placeholder: it is what a venue returns when it
    has no opinion, and what we record when a response could not be interpreted.
    """

    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    #: The venue reports the order but cannot say what state it is in.
    UNKNOWN = "UNKNOWN"


class BrokerSubmissionTimeout(Exception):
    """The request went out and no answer came back.

    Distinct from a rejection, and the distinction is the whole of `11` §5. A
    rejection is an answer; this is the absence of one, and the order may or may not
    exist at the venue. Callers must move the order to `UNKNOWN` — never to a
    terminal state, and never back to a resubmittable one.
    """


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    """What we ask a venue to do.

    `client_order_attempt_id` is **our** identity for this attempt. `06` §10 is
    explicit that it gives us local dedup and audit and does **not** oblige the
    provider to reject a duplicate; nothing in this codebase claims otherwise.
    """

    client_order_attempt_id: str
    #: The OMS order this attempt belongs to. Several attempts may share it only
    #: after reconciliation has established that no order exists at the venue.
    order_id: str
    instrument_id: int
    side: Side
    quantity: int
    order_type: OrderType
    limit_price: Decimal | None = None
    #: Market time the request was formed at. Never a wall-clock reading.
    requested_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_order_attempt_id": self.client_order_attempt_id,
            "order_id": self.order_id,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": None if self.limit_price is None else str(self.limit_price),
            "requested_at": (None if self.requested_at is None else self.requested_at.isoformat()),
        }


@dataclass(frozen=True, slots=True)
class BrokerAck:
    """A venue's answer to a request. Its arrival is itself the information.

    `provider_order_id` may be `None` even on an acknowledgement — some venues
    acknowledge receipt before assigning an id. That is represented rather than
    papered over with a placeholder.
    """

    client_order_attempt_id: str
    accepted: bool
    provider_order_id: str | None = None
    provider_status: ProviderOrderStatus | None = None
    #: When the venue says it happened.
    provider_time: datetime | None = None
    #: When we received the answer. Not market time (brief §16).
    received_at: datetime | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_order_attempt_id": self.client_order_attempt_id,
            "accepted": self.accepted,
            "provider_order_id": self.provider_order_id,
            "provider_status": (
                None if self.provider_status is None else self.provider_status.value
            ),
            "provider_time": (
                None if self.provider_time is None else self.provider_time.isoformat()
            ),
            "received_at": None if self.received_at is None else self.received_at.isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BrokerOrderState:
    """The venue's view of one order. **Authoritative** during reconciliation.

    `11` §6: "broker is authoritative for order state, fills and positions". That is
    a statement about whose answer wins, not about how confident the answer is — an
    authoritative `UNKNOWN` is still `UNKNOWN`.
    """

    provider_order_id: str
    status: ProviderOrderStatus
    instrument_id: int
    side: Side
    quantity: int
    filled_quantity: int
    average_price: Decimal | None = None
    #: Present only when the venue echoes it back. Many do not, so matching a
    #: provider order to a local one may have to go through the instrument and
    #: quantity instead -- which is weaker evidence, and is recorded as such.
    client_order_attempt_id: str | None = None
    provider_time: datetime | None = None
    received_at: datetime | None = None
    raw_status: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_order_id": self.provider_order_id,
            "status": self.status.value,
            "raw_status": self.raw_status,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "average_price": None if self.average_price is None else str(self.average_price),
            "client_order_attempt_id": self.client_order_attempt_id,
            "provider_time": (
                None if self.provider_time is None else self.provider_time.isoformat()
            ),
            "received_at": None if self.received_at is None else self.received_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BrokerFill:
    """One execution as the venue reports it.

    `provider_fill_id` is the dedup key `11` §6 requires ("insert missing fills,
    idempotent on `broker_fill_id`"). It is `str | None` because a venue may not
    supply one; where it is absent, a content digest stands in and the *weaker*
    basis for dedup is recorded rather than hidden.
    """

    provider_fill_id: str | None
    provider_order_id: str
    instrument_id: int
    side: Side
    quantity: int
    price: Decimal
    fees: Decimal = Decimal(0)
    #: When the venue says the execution happened.
    provider_time: datetime | None = None
    #: When we learned of it.
    received_at: datetime | None = None

    @property
    def dedup_key(self) -> str:
        """What makes this fill the same fill.

        Prefers the provider's own id. Falls back to a content digest, which is
        genuinely weaker: two identical executions a millisecond apart would
        collapse. `has_provider_identity` exposes which basis was used so a caller
        can tell how much the dedup is worth.
        """
        import hashlib

        if self.provider_fill_id:
            return f"pfid:{self.provider_fill_id}"
        digest = hashlib.sha256(
            "|".join(
                [
                    self.provider_order_id,
                    str(self.instrument_id),
                    self.side.value,
                    str(self.quantity),
                    str(self.price),
                    "" if self.provider_time is None else self.provider_time.isoformat(),
                ]
            ).encode()
        ).hexdigest()[:32]
        return f"digest:{digest}"

    @property
    def has_provider_identity(self) -> bool:
        return bool(self.provider_fill_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_fill_id": self.provider_fill_id,
            "has_provider_identity": self.has_provider_identity,
            "dedup_key": self.dedup_key,
            "provider_order_id": self.provider_order_id,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": str(self.price),
            "fees": str(self.fees),
            "provider_time": (
                None if self.provider_time is None else self.provider_time.isoformat()
            ),
            "received_at": None if self.received_at is None else self.received_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """The venue's view of a position. Authoritative during reconciliation."""

    instrument_id: int
    quantity: int
    average_price: Decimal | None = None
    provider_time: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "quantity": self.quantity,
            "average_price": None if self.average_price is None else str(self.average_price),
            "provider_time": (
                None if self.provider_time is None else self.provider_time.isoformat()
            ),
        }


@dataclass(frozen=True, slots=True)
class BrokerOrderEvent:
    """A push notification from the venue. A **hint**, never the sole truth.

    `11` §6: "Order-update WebSocket events are treated as *hints* that accelerate
    reconciliation, never as the sole source of truth. Missed events are assumed
    possible."

    There is deliberately no sequence number. `06` §6 verified that Upstox V3 supplies
    no provider ordering for market data, and nothing has been verified for order
    updates; a sequence field would imply a guarantee nobody has established.
    """

    provider_order_id: str
    status: ProviderOrderStatus
    provider_time: datetime | None = None
    received_at: datetime | None = None
    filled_quantity: int | None = None
    raw: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_order_id": self.provider_order_id,
            "status": self.status.value,
            "provider_time": (
                None if self.provider_time is None else self.provider_time.isoformat()
            ),
            "received_at": None if self.received_at is None else self.received_at.isoformat(),
            "filled_quantity": self.filled_quantity,
            "raw": dict(self.raw),
        }


@runtime_checkable
class BrokerAdapter(Protocol):
    """`06` §1, in full. The paper adapter implements it exactly.

    > The paper adapter implements `BrokerAdapter` exactly, so nothing upstream
    > knows which is in use.

    `capabilities` is the Phase 10 addition: an adapter declares what it can do, and
    the OMS asks the gate rather than inspecting the adapter's type. That is what
    keeps "is this live?" answerable in one place.
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[ExecutionCapability]: ...

    async def place_order(self, request: BrokerOrderRequest) -> BrokerAck: ...

    async def cancel_order(self, provider_order_id: str) -> BrokerAck: ...

    async def modify_order(self, provider_order_id: str, changes: dict[str, Any]) -> BrokerAck: ...

    async def get_order(self, provider_order_id: str) -> BrokerOrderState | None: ...

    # --- reconciliation surface. `06` §1: not optional parts of the protocol.
    async def list_orders(self, *, since: datetime) -> Sequence[BrokerOrderState]: ...

    async def list_trades(self, *, since: datetime) -> Sequence[BrokerFill]: ...

    async def get_positions(self) -> Sequence[BrokerPosition]: ...

    def subscribe_order_updates(self) -> AsyncIterator[BrokerOrderEvent]: ...
