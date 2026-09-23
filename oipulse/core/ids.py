"""Typed identifiers.

Internal identity is deliberately separate from vendor identity
(`docs/design/01-DOMAIN_MODEL.md` §3): an Upstox `instrument_key` is an *external*
identifier recorded in a mapping table, never the thing the domain keys on. Binding the
domain to a vendor string makes instrument identity a vendor concern, which it is not.
"""

from __future__ import annotations

import uuid
from typing import NewType

__all__ = [
    "BuildContextId",
    "CausationId",
    "CorrelationId",
    "EventId",
    "ExpiryId",
    "InstrumentId",
    "new_correlation_id",
    "new_event_id",
]

InstrumentId = NewType("InstrumentId", int)
ExpiryId = NewType("ExpiryId", int)

# Content-addressable: derived from the assembly configuration rather than allocated.
# The derivation itself belongs to Phase 3, where MarketState assembly is introduced;
# the alias exists here so earlier layers can reference the type without inventing one.
BuildContextId = NewType("BuildContextId", str)

EventId = NewType("EventId", str)
CorrelationId = NewType("CorrelationId", str)
CausationId = NewType("CausationId", str)


def new_event_id() -> EventId:
    """Allocate a domain event id."""
    return EventId(str(uuid.uuid4()))


def new_correlation_id() -> CorrelationId:
    """Allocate a correlation id at the head of a causal chain.

    A tick that produces a state, a metric, a signal, an alert and an order all share
    one of these (`03-EVENT_MODEL.md` §3).
    """
    return CorrelationId(str(uuid.uuid4()))
