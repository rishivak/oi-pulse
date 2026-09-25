"""The paper venue — the Phase 8 execution surface plus the Phase 10 provider surface.

`06-UPSTOX_INTEGRATION.md` §1: "The paper adapter implements `BrokerAdapter` exactly,
so nothing upstream knows which is in use." Phase 8 implemented the half that produces
fills; Phase 10 adds the half a *venue* has — acknowledgements, queryable order state,
trades, positions and push updates — so the OMS and the reconciler can be exercised
against something that behaves like a provider without one existing.

`18-ROADMAP.md` Phase 10 asks for exactly that: live stays disabled "until it is
exercised extensively against the paper adapter's fault injection".

### Fault injection is the point, not a convenience

`11-TRADING.md` §5 calls network ambiguity "a fact, not an exception", and §7 notes
the paper adapter "can be instructed to simulate `UNKNOWN`, so the reconciliation path
is exercised rather than theoretical". `PaperVenueFaults` is that instruction. It can
make an acknowledgement time out, a submission reject, or — the nastiest and most
realistic case — the order **succeed at the venue while the answer is lost**, so the
reconciler has something genuinely there to find.

### Two surfaces, one adapter, deliberately

* `submit` / `cancel` / `status` — synchronous, used by the Phase 8 paper runtime,
  which owns the ledger and the fill model. Unchanged and still verified.
* `place_order` / `get_order` / `list_orders` / ... — the async `BrokerAdapter`
  protocol, used by the Phase 10 OMS and reconciler.

Splitting these into two classes would have meant two paper venues whose views of the
same order could disagree, which is the divergence `06` §1 exists to prevent. One
object holds one book.

This adapter reaches no network: `tools/check_live_execution_barrier.py` asserts that
no module under `trading/` can.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.marketstate.state import MarketState
from oipulse.trading.accounts import (
    AccountMode,
    LiveExecutionUnavailable,
    PaperAccount,
    resolve_execution_mode,
)
from oipulse.trading.brokers.capability import ExecutionCapability, require_capability
from oipulse.trading.brokers.protocol import (
    BrokerAck,
    BrokerFill,
    BrokerOrderEvent,
    BrokerOrderRequest,
    BrokerOrderState,
    BrokerPosition,
    BrokerSubmissionTimeout,
    ProviderOrderStatus,
)
from oipulse.trading.execution import ExecutionOutcome, PaperExecutionModel
from oipulse.trading.intents import IntentConstraints
from oipulse.trading.orders import OrderState, PaperOrder, RejectReason

__all__ = [
    "PaperBrokerAdapter",
    "PaperVenueFaults",
    "adapter_for",
]


@dataclass(frozen=True, slots=True)
class PaperVenueFaults:
    """Instructions for how the simulated venue should misbehave.

    Every fault is a *declared* condition keyed on the attempt id, not a random
    draw: `11` §5's scenarios must be reproducible, and a venue that failed
    randomly would make a reconciliation test flaky rather than rigorous.
    """

    #: Attempts whose acknowledgement is lost. The order **still reaches the book**,
    #: which is the realistic and dangerous case: something is there to find.
    lose_ack_for: frozenset[str] = frozenset()
    #: Attempts the venue never receives at all. Nothing reaches the book, so
    #: reconciliation must conclude the order does not exist.
    drop_request_for: frozenset[str] = frozenset()
    #: Attempts the venue explicitly rejects. An answer, not an absence.
    reject_for: frozenset[str] = frozenset()
    #: Provider order ids the venue has silently cancelled -- `11` §6's
    #: "broker-side cancellation" and "manual broker-side change".
    venue_cancelled: frozenset[str] = frozenset()
    #: Provider order ids the venue will not report at all, modelling a missed
    #: event or a query that comes up empty.
    hide_from_queries: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, Any]:
        return {
            "lose_ack_for": sorted(self.lose_ack_for),
            "drop_request_for": sorted(self.drop_request_for),
            "reject_for": sorted(self.reject_for),
            "venue_cancelled": sorted(self.venue_cancelled),
            "hide_from_queries": sorted(self.hide_from_queries),
        }


@dataclass
class PaperBrokerAdapter:
    """A simulated venue. Prices through the shared Phase 7 fill model.

    Holds no connection, no session and no credential — there is nothing here to
    authenticate to. `submit` is a pure function of the order, the supplied state and
    the supplied cash, which is why the whole thing is testable without any
    infrastructure and why it cannot possibly reach a real venue.
    """

    execution: PaperExecutionModel
    name: str = "PAPER"
    faults: PaperVenueFaults = field(default_factory=PaperVenueFaults)
    #: The venue's own book: provider_order_id -> state. Separate from the OMS's
    #: record on purpose -- reconciliation only means something if the two can
    #: disagree, and a shared object could not.
    _book: dict[str, BrokerOrderState] = field(default_factory=dict, repr=False)
    _trades: list[BrokerFill] = field(default_factory=list, repr=False)
    _events: list[BrokerOrderEvent] = field(default_factory=list, repr=False)
    _next_id: int = field(default=1, repr=False)

    @property
    def mode(self) -> AccountMode:
        return AccountMode.PAPER

    @property
    def capabilities(self) -> frozenset[ExecutionCapability]:
        """Simulation and provider queries. **Never** `LIVE_SUBMIT`."""
        return frozenset({ExecutionCapability.SIMULATE, ExecutionCapability.QUERY_PROVIDER})

    def submit(
        self,
        order: PaperOrder,
        state: MarketState,
        *,
        at: datetime,
        available_cash: Decimal,
        constraints: IntentConstraints | None = None,
        reference_price: Decimal | None = None,
    ) -> ExecutionOutcome:
        """Accept and attempt to fill. Never reaches a network.

        An order arriving in `CREATED` is accepted first, so the audit trail shows
        the acceptance as its own event rather than jumping straight to a fill.
        """
        working = order
        if working.state is OrderState.CREATED:
            working = working.transition(OrderState.ACCEPTED, at=at, trigger="paper_venue_accept")
        return self.execution.execute(
            working,
            state,
            at=at,
            available_cash=available_cash,
            constraints=constraints,
            reference_price=reference_price,
        )

    def cancel(self, order: PaperOrder, *, at: datetime, detail: str = "") -> PaperOrder:
        """Cancel, or raise `InvalidTransition` if the order is already terminal.

        A cancel of a filled order is an error, not a no-op: the caller believes
        something is outstanding that is not, and silently succeeding would let that
        belief persist.
        """
        return order.cancel(at=at, detail=detail or "cancelled by request")

    def status(self, order: PaperOrder) -> OrderState:
        """The paper venue holds no state of its own; the order *is* the record."""
        return order.state

    # ------------------------------------------------------- Phase 10 provider surface

    def _allocate_provider_id(self) -> str:
        """Deterministic within a venue instance, and clearly the venue's own.

        Prefixed `paper-` so a provider id from the simulated venue can never be
        mistaken for one a real broker issued -- brief §8 requires provider
        identity to be represented honestly, and that includes saying whose it is.
        """
        allocated = f"paper-{self._next_id:06d}"
        self._next_id += 1
        return allocated

    async def place_order(self, request: BrokerOrderRequest) -> BrokerAck:
        """Accept a request into the venue book, honouring the declared faults.

        Three distinct failure shapes, because they demand different responses:

        * **dropped** -- nothing reaches the book, and the caller gets a timeout.
          Reconciliation will correctly find no order.
        * **lost ack** -- the order **does** reach the book and the caller gets a
          timeout. This is `11` §5's dangerous case, and the reason "never assume
          rejected" is a hard rule: resubmitting here duplicates a live position.
        * **rejected** -- an answer. Unambiguous, and terminal.
        """
        require_capability(self.capabilities, ExecutionCapability.SIMULATE, who=self.name)
        attempt = request.client_order_attempt_id

        if attempt in self.faults.drop_request_for:
            raise BrokerSubmissionTimeout(
                f"request {attempt} never reached the venue; no order exists there"
            )

        provider_order_id = self._allocate_provider_id()
        self._book[provider_order_id] = BrokerOrderState(
            provider_order_id=provider_order_id,
            status=ProviderOrderStatus.OPEN,
            instrument_id=request.instrument_id,
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0,
            client_order_attempt_id=attempt,
            provider_time=request.requested_at,
            raw_status="open",
        )

        if attempt in self.faults.reject_for:
            self._book[provider_order_id] = replace(
                self._book[provider_order_id],
                status=ProviderOrderStatus.REJECTED,
                raw_status="rejected",
            )
            return BrokerAck(
                client_order_attempt_id=attempt,
                accepted=False,
                provider_order_id=provider_order_id,
                provider_status=ProviderOrderStatus.REJECTED,
                provider_time=request.requested_at,
                received_at=request.requested_at,
                reason="venue rejected the order",
            )

        if attempt in self.faults.lose_ack_for:
            # The order is in the book. The answer is not coming.
            raise BrokerSubmissionTimeout(
                f"acknowledgement for {attempt} was lost; the venue MAY hold this order"
            )

        return BrokerAck(
            client_order_attempt_id=attempt,
            accepted=True,
            provider_order_id=provider_order_id,
            provider_status=ProviderOrderStatus.OPEN,
            provider_time=request.requested_at,
            received_at=request.requested_at,
        )

    async def cancel_order(self, provider_order_id: str) -> BrokerAck:
        """Request a cancel. The venue's answer is what decides, not the request."""
        require_capability(self.capabilities, ExecutionCapability.SIMULATE, who=self.name)
        existing = self._book.get(provider_order_id)
        if existing is None:
            return BrokerAck(
                client_order_attempt_id="",
                accepted=False,
                provider_order_id=provider_order_id,
                reason="no such order at the venue",
            )
        if existing.status is ProviderOrderStatus.FILLED:
            # The cancel lost the race. Reporting success here would tell the OMS
            # it had no position when it has one.
            return BrokerAck(
                client_order_attempt_id=existing.client_order_attempt_id or "",
                accepted=False,
                provider_order_id=provider_order_id,
                provider_status=ProviderOrderStatus.FILLED,
                reason="already filled; the cancel arrived too late",
            )
        self._book[provider_order_id] = replace(
            existing, status=ProviderOrderStatus.CANCELLED, raw_status="cancelled"
        )
        return BrokerAck(
            client_order_attempt_id=existing.client_order_attempt_id or "",
            accepted=True,
            provider_order_id=provider_order_id,
            provider_status=ProviderOrderStatus.CANCELLED,
        )

    async def modify_order(self, provider_order_id: str, changes: dict[str, Any]) -> BrokerAck:
        """Declared by the protocol; the simulated venue does not implement it.

        `11` §4 does not define an amend path and the Phase 10 brief §15 makes
        cancel/replace conditional on the specification requiring it. Rather than
        invent amend semantics, this refuses -- an honest "not supported" beats a
        plausible guess about how a venue would behave.
        """
        require_capability(self.capabilities, ExecutionCapability.SIMULATE, who=self.name)
        return BrokerAck(
            client_order_attempt_id="",
            accepted=False,
            provider_order_id=provider_order_id,
            reason=(
                "the simulated venue does not support modify; no amend semantics are "
                "defined by the approved design, and inventing them would model a "
                "venue behaviour nobody verified"
            ),
        )

    async def get_order(self, provider_order_id: str) -> BrokerOrderState | None:
        """The venue's view, or None when it has none. None is a real answer."""
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        if provider_order_id in self.faults.hide_from_queries:
            return None
        return self._resolved(self._book.get(provider_order_id))

    async def list_orders(self, *, since: datetime) -> Sequence[BrokerOrderState]:
        """Reconciliation truth for orders (`06` §1, `11` §6).

        Sorted by provider id so two runs over an unchanged book produce an
        identical sequence -- reconciliation idempotency (brief §13) starts here.
        """
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        out = [
            resolved
            for key in sorted(self._book)
            if key not in self.faults.hide_from_queries
            and (resolved := self._resolved(self._book[key])) is not None
            and (resolved.provider_time is None or resolved.provider_time >= since)
        ]
        return tuple(out)

    async def list_trades(self, *, since: datetime) -> Sequence[BrokerFill]:
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        return tuple(
            fill
            for fill in sorted(self._trades, key=lambda f: f.dedup_key)
            if fill.provider_time is None or fill.provider_time >= since
        )

    async def get_positions(self) -> Sequence[BrokerPosition]:
        """Folded from the venue's own trades, sorted for determinism."""
        require_capability(self.capabilities, ExecutionCapability.QUERY_PROVIDER, who=self.name)
        totals: dict[int, int] = {}
        for fill in self._trades:
            totals[fill.instrument_id] = totals.get(fill.instrument_id, 0) + (
                fill.quantity * fill.side.sign
            )
        return tuple(
            BrokerPosition(instrument_id=iid, quantity=qty)
            for iid, qty in sorted(totals.items())
            if qty
        )

    async def subscribe_order_updates(self) -> AsyncIterator[BrokerOrderEvent]:
        """Replays whatever the venue has emitted. A **hint** stream (`11` §6).

        Deliberately allows the same event to be yielded more than once and makes
        no ordering promise: missed and duplicated events are assumed possible, and
        a consumer that relied on this being exact would be relying on something no
        provider has guaranteed.
        """
        for event in tuple(self._events):
            yield event

    def _resolved(self, state: BrokerOrderState | None) -> BrokerOrderState | None:
        """Apply venue-side changes the OMS was never told about (`11` §6)."""
        if state is None:
            return None
        if state.provider_order_id in self.faults.venue_cancelled:
            return replace(
                state, status=ProviderOrderStatus.CANCELLED, raw_status="cancelled_by_venue"
            )
        return state

    # ------------------------------------------------------------- venue-side effects

    def venue_fill(
        self,
        provider_order_id: str,
        *,
        quantity: int,
        price: Decimal,
        at: datetime,
        provider_fill_id: str | None = None,
        fees: Decimal = Decimal(0),
    ) -> BrokerFill:
        """Record an execution at the venue. Test-facing, and honest about identity.

        `provider_fill_id` defaults to `None` rather than to a generated value: a
        venue that supplies no execution id is a real case, and fabricating one
        would hide that the dedup basis is weaker (brief §14 and §28).
        """
        existing = self._book[provider_order_id]
        filled = existing.filled_quantity + quantity
        self._book[provider_order_id] = replace(
            existing,
            filled_quantity=filled,
            status=(
                ProviderOrderStatus.FILLED
                if filled >= existing.quantity
                else ProviderOrderStatus.PARTIALLY_FILLED
            ),
            average_price=price,
            raw_status="complete" if filled >= existing.quantity else "partial",
        )
        fill = BrokerFill(
            provider_fill_id=provider_fill_id,
            provider_order_id=provider_order_id,
            instrument_id=existing.instrument_id,
            side=existing.side,
            quantity=quantity,
            price=price,
            fees=fees,
            provider_time=at,
            received_at=at,
        )
        self._trades.append(fill)
        self._events.append(
            BrokerOrderEvent(
                provider_order_id=provider_order_id,
                status=self._book[provider_order_id].status,
                provider_time=at,
                received_at=at,
                filled_quantity=filled,
            )
        )
        return fill

    def venue_order_count(self) -> int:
        return len(self._book)


def adapter_for(account: PaperAccount, execution: PaperExecutionModel) -> PaperBrokerAdapter:
    """Resolve an account's adapter. The single place mode becomes an execution path.

    Every caller about to execute goes through here, so there is exactly one function
    in the codebase that answers "is this live?" — and for anything but `PAPER` it
    raises `LiveExecutionUnavailable`. Phase 10 adds its branch here, behind the three
    documented gates, and nowhere else.
    """
    mode = resolve_execution_mode(account)
    if mode is not AccountMode.PAPER:  # pragma: no cover - resolve_ already raised
        raise LiveExecutionUnavailable(f"no adapter for mode {mode.value}")
    return PaperBrokerAdapter(execution=execution)


#: Re-exported so a caller handling refusal does not have to import `accounts`.
__all__ += ["LiveExecutionUnavailable", "RejectReason"]
