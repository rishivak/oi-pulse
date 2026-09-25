"""Provider status → canonical OMS state. One explicit, testable function.

Phase 10 brief §11:

> Implement deterministic mapping between provider state and canonical OMS state.
> **Do not silently force provider truth into the local state machine when evidence
> is insufficient.**

That last clause is why this returns `OrderState | None` rather than always a state.
`ProviderOrderStatus.UNKNOWN` maps to `None` — meaning *the provider told us nothing
usable* — and the caller records the absence instead of picking a state. A mapping
that always produced an answer would be guessing, and a guess here becomes a position
the ledger believes in.

### Why the vocabularies are separate at all

`ProviderOrderStatus` is the venue's; `OrderState` is ours. A shared enum would make
the mapping invisible and would embed the assumption that the two vocabularies
correspond one-to-one. They do not: our machine has `SUBMITTING`, `CANCEL_PENDING`,
`UNKNOWN` and `PENDING_RECONCILIATION`, which describe *our* relationship to the
order, and no venue has an opinion about those.
"""

from __future__ import annotations

from oipulse.trading.brokers.protocol import BrokerOrderState, ProviderOrderStatus
from oipulse.trading.orders import OrderState

__all__ = ["canonical_state_for", "provider_is_terminal", "reconciled_state"]

#: The declared mapping. A table, not a chain of `if`s, so an unmapped status is a
#: missing key rather than a silent fall-through to a default.
_MAPPING: dict[ProviderOrderStatus, OrderState | None] = {
    ProviderOrderStatus.OPEN: OrderState.OPEN,
    ProviderOrderStatus.PARTIALLY_FILLED: OrderState.PARTIALLY_FILLED,
    ProviderOrderStatus.FILLED: OrderState.FILLED,
    ProviderOrderStatus.CANCELLED: OrderState.CANCELLED,
    ProviderOrderStatus.REJECTED: OrderState.REJECTED,
    ProviderOrderStatus.EXPIRED: OrderState.EXPIRED,
    # Insufficient evidence. Deliberately not a state.
    ProviderOrderStatus.UNKNOWN: None,
}


def canonical_state_for(status: ProviderOrderStatus) -> OrderState | None:
    """The OMS state this provider status establishes, or None if it establishes none."""
    return _MAPPING[status]


def provider_is_terminal(status: ProviderOrderStatus) -> bool:
    mapped = canonical_state_for(status)
    return mapped is not None and mapped in (
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    )


def reconciled_state(observed: BrokerOrderState) -> OrderState | None:
    """The state a provider observation establishes, accounting for quantities.

    A venue reporting `OPEN` on an order with a non-zero filled quantity is
    describing a partial fill regardless of the word it used. The quantities are
    harder evidence than the status string, so they win — but only to *narrow* the
    answer, never to invent one where the status gave none.
    """
    mapped = canonical_state_for(observed.status)
    if mapped is None:
        return None
    if mapped is OrderState.OPEN and observed.filled_quantity > 0:
        return (
            OrderState.FILLED
            if observed.filled_quantity >= observed.quantity
            else OrderState.PARTIALLY_FILLED
        )
    return mapped
