"""SYNTHETIC fixtures for Phase 10. Not recorded from Upstox.

Everything here is constructed, and the module name and this docstring say so.

**No provider response shape is invented.** `06-UPSTOX_INTEGRATION.md` established
the Upstox market-data contracts and nothing about the order APIs, so these fixtures
drive the **simulated** venue — which is what `18-ROADMAP.md` Phase 10 asks for
("exercised extensively against the paper adapter's fault injection"). Nothing here
claims to be an Upstox order response, because no such response has been verified.

Recorded provider captures live in `tests/fixtures/recorded/` and cover market data
only.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from oipulse.trading.brokers import (
    PaperBrokerAdapter,
    PaperVenueFaults,
    ProviderOrderStatus,
)
from oipulse.trading.brokers.protocol import BrokerOrderState
from oipulse.trading.execution import PaperExecutionModel
from oipulse.trading.oms import OrderManager, attempt_id_for
from oipulse.trading.orders import PaperOrder
from oipulse.trading.reconciliation import Reconciler, ReconciliationTrigger
from oipulse.trading.risk import RiskDecisionRecord
from tests.phase3._fixtures import at
from tests.phase8 import _fixtures as p8
from tests.phase9 import _fixtures as p9

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

TARGET = p8.TARGET
ACCOUNT_ID = p8.ACCOUNT_ID


def run(coro: Any) -> Any:
    """Drive one coroutine. The OMS surface is async; the tests are not."""
    return asyncio.run(coro)


def intent(**kwargs: Any) -> Any:
    return p8.intent(**kwargs)


def decision(
    it: Any = None, *, validity: timedelta = timedelta(hours=1), at_minute: int = 0
) -> RiskDecisionRecord:
    """A real Phase 9 decision. The OMS consumes it; it is never hand-built.

    Using the actual engine rather than a constructed record is deliberate: the
    authorization gate's whole job is to read a canonical decision correctly, and a
    hand-made one could drift from what the engine actually produces.
    """
    engine = p9.engine(lim=p9.runtime_limits(), validity=validity)
    return engine.evaluate(it or intent(), p9.state(), sequence_no=1, at=at(at_minute))


def order(
    it: Any = None, *, quantity: int = 50, leg_index: int = 0, at_minute: int = 0
) -> PaperOrder:
    resolved = it or intent(quantity=quantity)
    return PaperOrder(
        order_id=PaperOrder.derive_id(resolved.intent_id, leg_index, resolved.account_id),
        account_id=resolved.account_id,
        intent_id=resolved.intent_id,
        instrument_id=resolved.legs[leg_index].instrument_id,
        side=resolved.legs[leg_index].side,
        quantity=resolved.legs[leg_index].quantity,
        order_type=resolved.legs[leg_index].order_type,
        created_at=at(at_minute),
        strategy_id=resolved.strategy_id,
        signal_id=resolved.signal_id,
        signal_version=resolved.signal_version,
    )


def venue(*, faults: PaperVenueFaults | None = None) -> PaperBrokerAdapter:
    return PaperBrokerAdapter(
        execution=PaperExecutionModel(fill_model=p8.fill_model()),
        faults=faults or PaperVenueFaults(),
    )


def oms(*, adapter: PaperBrokerAdapter | None = None) -> OrderManager:
    return OrderManager(adapter=adapter or venue())


def reconciler(manager: OrderManager, adapter: PaperBrokerAdapter) -> Reconciler:
    return Reconciler(manager=manager, adapter=adapter)


def lose_ack_for_first_attempt(order_id: str) -> PaperVenueFaults:
    """Make the first attempt's acknowledgement disappear.

    The order still reaches the venue book, which is the dangerous and realistic
    case: there is something there to find, so a blind resubmission would double a
    live position.
    """
    return PaperVenueFaults(lose_ack_for=frozenset({attempt_id_for(order_id, 1)}))


def drop_first_attempt(order_id: str) -> PaperVenueFaults:
    """Make the first attempt never arrive. Nothing reaches the venue book."""
    return PaperVenueFaults(drop_request_for=frozenset({attempt_id_for(order_id, 1)}))


def reject_first_attempt(order_id: str) -> PaperVenueFaults:
    return PaperVenueFaults(reject_for=frozenset({attempt_id_for(order_id, 1)}))


def provider_state(
    *,
    provider_order_id: str = "paper-000001",
    status: ProviderOrderStatus = ProviderOrderStatus.OPEN,
    quantity: int = 50,
    filled: int = 0,
    attempt: str | None = None,
    raw: str = "open",
) -> BrokerOrderState:
    """A venue view, constructed for mapping tests.

    Constructed values, not a captured Upstox payload — and the `raw_status`
    strings are the *simulated* venue's, not claimed to be any real broker's.
    """
    from oipulse.backtest.intents import Side

    return BrokerOrderState(
        provider_order_id=provider_order_id,
        status=status,
        instrument_id=TARGET,
        side=Side.BUY,
        quantity=quantity,
        filled_quantity=filled,
        client_order_attempt_id=attempt,
        provider_time=at(1),
        received_at=at(1),
        raw_status=raw,
    )


#: Distinguishes "not supplied, make me one" from "explicitly no decision".
#:
#: Using `None` for both would make the no-decision case -- the most important
#: refusal the gate has -- silently untestable, because the fixture would helpfully
#: create a decision exactly when the test wanted none.
_UNSET: Any = object()


async def submit_one(
    manager: OrderManager,
    *,
    it: Any = None,
    dec: RiskDecisionRecord | None = _UNSET,
    ord_: PaperOrder | None = None,
    at_minute: int = 1,
) -> Any:
    resolved_intent = it or intent()
    resolved_decision = decision(resolved_intent) if dec is _UNSET else dec
    return await manager.submit(
        ord_ or order(resolved_intent),
        intent=resolved_intent,
        decision=resolved_decision,
        at=at(at_minute),
    )


async def reconcile_once(
    rec: Reconciler,
    *,
    run_id: str = "rec-1",
    trigger: ReconciliationTrigger = ReconciliationTrigger.MANUAL,
    at_minute: int = 3,
) -> Any:
    return await rec.reconcile(trigger=trigger, since=at(0), at=at(at_minute), run_id=run_id)


__all__ = [
    "ACCOUNT_ID",
    "PROVENANCE",
    "TARGET",
    "at",
    "decision",
    "drop_first_attempt",
    "intent",
    "lose_ack_for_first_attempt",
    "oms",
    "order",
    "provider_state",
    "reconcile_once",
    "reconciler",
    "reject_first_attempt",
    "run",
    "submit_one",
    "venue",
]
