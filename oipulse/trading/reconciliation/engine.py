"""The reconciler — `11-TRADING.md` §6, implemented as its seven steps.

```
1. snapshot broker truth: list_orders(since), list_trades(since), get_positions()
2. snapshot local state for the same scope
3. diff -> discrepancies, classified
4. resolve: broker is authoritative for order state, fills and positions
5. apply: append order events, insert missing fills (idempotent on broker_fill_id),
          correct positions
6. persist the run: broker snapshot, discrepancies, resolutions
7. emit ReconciliationCompleted; alert on unresolved or unexpected discrepancies
```

Steps 1-5 are `reconcile()`. Step 6 is the returned `ReconciliationRun`, which the
caller persists — the reconciler holds no database, for the same reason nothing else
in `trading/` does. Step 7 is the caller's too; `attention_required()` is what it
alerts on.

### Broker-authoritative does not mean broker-obeyed

`11` §6 step 4 says the broker is authoritative. That governs *whose answer wins*
when both have one — it does not license acting on an answer the broker did not give.
Three places where that distinction is load-bearing:

* A provider status of `UNKNOWN` maps to no state, so the order stays unresolved
  rather than being moved somewhere plausible.
* `MISSING_LOCALLY` — an order at the broker we have no record of — is **recorded,
  never cancelled**. Brief §12 forbids inventing corrective actions, and cancelling
  a stray broker order is the corrective action most likely to be invented and most
  likely to lose money.
* `OMS_AHEAD` — we show progress the broker does not — is recorded, not rolled back.
  It almost always means a bug on our side, and quietly rewriting local state would
  destroy the evidence of it.

### Idempotency

Brief §13: running twice over unchanged provider state must produce identical
semantic results. Two mechanisms:

* Fills are applied through an **applied-set keyed on `BrokerFill.dedup_key`**,
  which prefers the provider's own execution id and falls back to a content digest.
  The fallback is genuinely weaker and says so.
* Order-state application is a no-op when the order is already in the target state,
  and goes through the transition table when it is not — so a second run makes no
  transition and appends no event.

The run's `content_digest` excludes execution metadata, so two runs over identical
evidence produce the same digest. That is the test, not the claim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from oipulse.trading.brokers.protocol import (
    BrokerAdapter,
    BrokerFill,
    BrokerOrderState,
)
from oipulse.trading.oms.manager import OrderManager
from oipulse.trading.orders import InvalidTransition, OrderState, PaperOrder
from oipulse.trading.reconciliation.mapping import reconciled_state
from oipulse.trading.reconciliation.model import (
    Discrepancy,
    DiscrepancyKind,
    ReconciliationOutcome,
    ReconciliationRun,
    ReconciliationTrigger,
    Resolution,
)

__all__ = ["Reconciler", "startup_gate"]


@dataclass
class Reconciler:
    """Compares OMS expectation with provider observation and records the difference.

    Holds the applied-fill set across runs, which is what makes repeated
    reconciliation idempotent rather than merely repeatable.
    """

    manager: OrderManager
    adapter: BrokerAdapter
    #: Dedup keys of fills already applied. Survives across runs by living here.
    _applied_fills: set[str] = field(default_factory=set, repr=False)
    _runs: list[ReconciliationRun] = field(default_factory=list, repr=False)

    @property
    def applied_fill_count(self) -> int:
        return len(self._applied_fills)

    def runs(self) -> tuple[ReconciliationRun, ...]:
        return tuple(self._runs)

    async def reconcile(
        self,
        *,
        trigger: ReconciliationTrigger,
        since: datetime,
        at: datetime,
        run_id: str,
        scope: str = "orders",
    ) -> ReconciliationRun:
        """One reconciliation pass. Steps 1 to 5 of `11` §6."""
        # 1. Broker truth.
        provider_orders = list(await self.adapter.list_orders(since=since))
        provider_fills = list(await self.adapter.list_trades(since=since))

        # 2. Local state, for the same scope.
        local_orders = self.manager.orders()

        # 3-5. Diff, classify, resolve, apply.
        discrepancies: list[Discrepancy] = []
        by_provider_id = {o.provider_order_id: o for o in provider_orders}
        matched_provider_ids: set[str] = set()

        # Provider identity first: an order whose acknowledgement was lost has no
        # provider id, and without adopting it the fill pass below cannot attribute
        # the venue's executions to it.
        self._adopt_identities(local_orders, provider_orders, at=at)

        # Fills BEFORE order status, and the order matters.
        #
        # A fill is the *evidence* for a status: an order is FILLED because
        # executions filled it. Applying the status first drives the order to a
        # terminal state, after which `apply_fill` correctly refuses -- leaving an
        # order that claims FILLED with a filled quantity of zero, and a fill
        # recorded as an unresolvable discrepancy. Applying fills first lets them
        # move the order themselves, and the status pass then has nothing left to
        # reconcile, which is the coherent outcome.
        fills_inserted = self._apply_fills(provider_fills, discrepancies, at=at)

        for order in self.manager.orders():
            discrepancy, consumed = self._reconcile_order(
                order, by_provider_id, provider_orders, at=at
            )
            discrepancies.append(discrepancy)
            if consumed is not None:
                matched_provider_ids.add(consumed)

        # Orders the broker has that we do not. `11` §6's "manual broker-side change".
        for observed in provider_orders:
            if observed.provider_order_id in matched_provider_ids:
                continue
            discrepancies.append(
                Discrepancy(
                    kind=DiscrepancyKind.MISSING_LOCALLY,
                    # Recorded, never acted on. Brief §12.
                    resolution=Resolution.RECORDED_ONLY,
                    provider_order_id=observed.provider_order_id,
                    provider_status=observed.status.value,
                    detail=(
                        "the venue holds an order with no local record. It is recorded "
                        "and surfaced; no automatic action is authorised, because "
                        "cancelling an order we do not understand could realise a loss "
                        "nobody chose"
                    ),
                )
            )

        outcome = self._summarise(discrepancies, fills_inserted)
        run = ReconciliationRun(
            run_id=run_id,
            trigger=trigger,
            as_of=at,
            scope=scope,
            discrepancies=tuple(discrepancies),
            outcome=outcome,
            provider_snapshot=tuple(o.as_dict() for o in provider_orders),
            local_snapshot=tuple(o.as_dict() for o in local_orders),
        )
        self._runs.append(run)
        return run

    def _adopt_identities(
        self,
        local_orders: Sequence[PaperOrder],
        provider_orders: Sequence[BrokerOrderState],
        *,
        at: datetime,
    ) -> None:
        """Learn provider ids for orders that never received one.

        The payoff of matching on our attempt id: an order whose acknowledgement
        was lost becomes addressable again, so it can be cancelled, its fills can
        be attributed to it, and later runs match it on the strong basis.
        """
        for order in local_orders:
            if order.provider_order_id is not None or not order.client_order_attempt_id:
                continue
            for observed in provider_orders:
                if observed.client_order_attempt_id == order.client_order_attempt_id:
                    self.manager.adopt_provider_identity(
                        order.order_id,
                        provider_order_id=observed.provider_order_id,
                        at=at,
                    )
                    break

    # ------------------------------------------------------------------ per order

    def _reconcile_order(
        self,
        order: PaperOrder,
        by_provider_id: Mapping[str, BrokerOrderState],
        provider_orders: Sequence[BrokerOrderState],
        *,
        at: datetime,
    ) -> tuple[Discrepancy, str | None]:
        """Classify one local order against provider evidence."""
        observed = self._match(order, by_provider_id, provider_orders)

        if observed is None:
            # Two very different situations share this shape, and conflating them
            # would be a serious error.
            if order.is_unresolved:
                # We never learned a provider id and the venue shows nothing. The
                # evidence says the order does not exist there -- which is the one
                # circumstance in which a resubmission would be safe. We still do
                # not resubmit here: that is the caller's decision, made with this
                # finding in hand.
                return (
                    Discrepancy(
                        kind=DiscrepancyKind.MISSING_AT_PROVIDER,
                        resolution=Resolution.RECORDED_ONLY,
                        order_id=order.order_id,
                        local_state=order.state.value,
                        detail=(
                            "an unresolved order is absent from the venue. The evidence "
                            "supports the conclusion that it was never accepted, but no "
                            "resubmission is performed here -- that is an explicit "
                            "decision for the caller, not a side effect of reconciling"
                        ),
                    ),
                    None,
                )
            if order.is_terminal:
                # A terminal local order the venue no longer lists is unremarkable;
                # venues age out completed orders.
                return (
                    Discrepancy(
                        kind=DiscrepancyKind.MATCH,
                        resolution=Resolution.NONE,
                        order_id=order.order_id,
                        local_state=order.state.value,
                        detail="terminal locally and no longer listed by the venue",
                    ),
                    None,
                )
            return (
                Discrepancy(
                    kind=DiscrepancyKind.MISSING_AT_PROVIDER,
                    resolution=Resolution.RECORDED_ONLY,
                    order_id=order.order_id,
                    local_state=order.state.value,
                    detail="we believe this order is working; the venue does not list it",
                ),
                None,
            )

        target = reconciled_state(observed)

        if target is None:
            # The venue has the order but cannot say what state it is in. Brief §11:
            # do not force provider truth into the local machine without evidence.
            return (
                Discrepancy(
                    kind=DiscrepancyKind.UNKNOWN,
                    resolution=Resolution.UNRESOLVED,
                    order_id=order.order_id,
                    provider_order_id=observed.provider_order_id,
                    local_state=order.state.value,
                    provider_status=observed.status.value,
                    detail=(
                        "the venue reports the order but states no usable status; "
                        "no state is inferred"
                    ),
                ),
                observed.provider_order_id,
            )

        if order.state is target:
            # Still check the quantities: a matching status with a differing fill
            # count is a real discrepancy that a status-only comparison would miss.
            if order.filled_quantity != observed.filled_quantity:
                return (
                    Discrepancy(
                        kind=DiscrepancyKind.QUANTITY_MISMATCH,
                        resolution=Resolution.RECORDED_ONLY,
                        order_id=order.order_id,
                        provider_order_id=observed.provider_order_id,
                        local_state=order.state.value,
                        provider_status=observed.status.value,
                        local_value=str(order.filled_quantity),
                        provider_value=str(observed.filled_quantity),
                        detail="states agree but filled quantities do not",
                    ),
                    observed.provider_order_id,
                )
            return (
                Discrepancy(
                    kind=DiscrepancyKind.MATCH,
                    resolution=Resolution.NONE,
                    order_id=order.order_id,
                    provider_order_id=observed.provider_order_id,
                    local_state=order.state.value,
                    provider_status=observed.status.value,
                ),
                observed.provider_order_id,
            )

        return self._apply_provider_state(order, observed, target, at=at)

    def _apply_provider_state(
        self,
        order: PaperOrder,
        observed: BrokerOrderState,
        target: OrderState,
        *,
        at: datetime,
    ) -> tuple[Discrepancy, str | None]:
        """Move local state to provider truth, or record why we could not."""
        ahead = self._who_is_ahead(order.state, target)

        try:
            self.manager.apply_provider_state(
                order.order_id,
                state=target,
                provider_status=observed.status.value,
                at=at,
            )
        except InvalidTransition as exc:
            # The venue claims something our machine forbids from here. That is a
            # mapping or a sequencing problem, not a licence to force the state.
            return (
                Discrepancy(
                    kind=DiscrepancyKind.STATUS_MISMATCH,
                    resolution=Resolution.UNRESOLVED,
                    order_id=order.order_id,
                    provider_order_id=observed.provider_order_id,
                    local_state=order.state.value,
                    provider_status=observed.status.value,
                    detail=f"provider state is unreachable from local state: {exc}",
                ),
                observed.provider_order_id,
            )

        return (
            Discrepancy(
                kind=ahead,
                resolution=Resolution.ORDER_STATE_APPLIED,
                order_id=order.order_id,
                provider_order_id=observed.provider_order_id,
                local_state=order.state.value,
                provider_status=observed.status.value,
                detail=f"local state advanced to {target.value} on provider evidence",
            ),
            observed.provider_order_id,
        )

    @staticmethod
    def _who_is_ahead(local: OrderState, target: OrderState) -> DiscrepancyKind:
        """Whether the provider or the OMS had progressed further.

        Ordered by how far through its life an order is. Useful because the two
        directions mean opposite things: `PROVIDER_AHEAD` is normal (we missed an
        event), while `OMS_AHEAD` means we recorded something that did not happen.
        """
        progression = [
            OrderState.CREATED,
            OrderState.SUBMITTING,
            OrderState.SUBMITTED,
            OrderState.ACCEPTED,
            OrderState.OPEN,
            OrderState.CANCEL_PENDING,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
        ]
        # An unresolved order knows nothing, so anything the provider says is
        # further along. Ranking it below CREATED rather than leaving it
        # unrankable is what makes the common case -- a lost acknowledgement whose
        # order was in fact accepted -- report PROVIDER_AHEAD, which is what
        # actually happened, instead of an uninformative STATUS_MISMATCH.
        if local in (OrderState.UNKNOWN, OrderState.PENDING_RECONCILIATION):
            return DiscrepancyKind.PROVIDER_AHEAD

        try:
            local_rank = progression.index(local)
            target_rank = progression.index(target)
        except ValueError:
            # One of them is terminal-but-not-filled; "ahead" is not meaningful
            # between CANCELLED and REJECTED, so report the difference without a
            # direction rather than inventing an ordering.
            return DiscrepancyKind.STATUS_MISMATCH
        if target_rank > local_rank:
            return DiscrepancyKind.PROVIDER_AHEAD
        if target_rank < local_rank:
            return DiscrepancyKind.OMS_AHEAD
        return DiscrepancyKind.MATCH

    @staticmethod
    def _match(
        order: PaperOrder,
        by_provider_id: Mapping[str, BrokerOrderState],
        provider_orders: Sequence[BrokerOrderState],
    ) -> BrokerOrderState | None:
        """Find the venue's record of this order.

        Two bases, strongest first. The provider order id is definitive. The
        attempt id is nearly so, and matters because it is the **only** way to
        recognise an order whose acknowledgement we lost -- we never learned its
        provider id, so without this the order would look missing and a
        resubmission would look safe.

        There is deliberately no third, weaker basis. Matching on instrument and
        quantity would pair our order with somebody else's identical one, and a
        wrong match here attributes a stranger's fill to our ledger.
        """
        if order.provider_order_id is not None:
            return by_provider_id.get(order.provider_order_id)
        if order.client_order_attempt_id:
            for observed in provider_orders:
                if observed.client_order_attempt_id == order.client_order_attempt_id:
                    return observed
        return None

    # ------------------------------------------------------------------- fills

    def _apply_fills(
        self,
        provider_fills: Sequence[BrokerFill],
        discrepancies: list[Discrepancy],
        *,
        at: datetime,
    ) -> int:
        """Insert fills we lack, idempotent on the fill's dedup key (`11` §6 step 5).

        Sorted by dedup key so two runs apply them in the same order — without
        that, an out-of-order provider response could produce a different average
        price and break the idempotency the run digest is supposed to demonstrate.
        """
        inserted = 0
        for fill in sorted(provider_fills, key=lambda f: f.dedup_key):
            if fill.dedup_key in self._applied_fills:
                continue

            order = self._order_for_provider_id(fill.provider_order_id)
            if order is None:
                discrepancies.append(
                    Discrepancy(
                        kind=DiscrepancyKind.MISSING_LOCALLY,
                        resolution=Resolution.RECORDED_ONLY,
                        provider_order_id=fill.provider_order_id,
                        provider_value=str(fill.quantity),
                        detail=(
                            "the venue reports a fill for an order we have no record "
                            "of; recorded rather than applied, because applying it "
                            "would create a position with no traceable cause"
                        )
                        + (
                            ""
                            if fill.has_provider_identity
                            else " (and this fill carries no provider execution id, so "
                            "its dedup basis is a content digest, which is weaker)"
                        ),
                    )
                )
                # Marked applied regardless: re-reporting the same unattributable
                # fill on every run would flood the discrepancy list.
                self._applied_fills.add(fill.dedup_key)
                continue

            try:
                self.manager.record_fill(
                    order.order_id, quantity=fill.quantity, price=fill.price, at=at
                )
            except (ValueError, InvalidTransition) as exc:
                discrepancies.append(
                    Discrepancy(
                        kind=DiscrepancyKind.QUANTITY_MISMATCH,
                        resolution=Resolution.UNRESOLVED,
                        order_id=order.order_id,
                        provider_order_id=fill.provider_order_id,
                        provider_value=str(fill.quantity),
                        detail=f"provider fill could not be applied: {exc}",
                    )
                )
                self._applied_fills.add(fill.dedup_key)
                continue

            self._applied_fills.add(fill.dedup_key)
            inserted += 1
            discrepancies.append(
                Discrepancy(
                    kind=DiscrepancyKind.PROVIDER_AHEAD,
                    resolution=Resolution.FILL_INSERTED,
                    order_id=order.order_id,
                    provider_order_id=fill.provider_order_id,
                    provider_value=str(fill.quantity),
                    detail=(
                        f"inserted a provider fill of {fill.quantity} at {fill.price}"
                        + (
                            ""
                            if fill.has_provider_identity
                            else "; no provider execution id, so dedup rests on a content digest"
                        )
                    ),
                )
            )
        return inserted

    def _order_for_provider_id(self, provider_order_id: str) -> PaperOrder | None:
        for order in self.manager.orders():
            if order.provider_order_id == provider_order_id:
                return order
        return None

    @staticmethod
    def _summarise(
        discrepancies: Sequence[Discrepancy], fills_inserted: int
    ) -> ReconciliationOutcome:
        return ReconciliationOutcome(
            matched=sum(1 for d in discrepancies if d.kind.is_clean),
            discrepancies=sum(1 for d in discrepancies if not d.kind.is_clean),
            resolved=sum(
                1
                for d in discrepancies
                if d.resolution
                in (
                    Resolution.ORDER_STATE_APPLIED,
                    Resolution.FILL_INSERTED,
                    Resolution.POSITION_CORRECTED,
                )
            ),
            needs_attention=sum(1 for d in discrepancies if d.needs_attention),
            fills_inserted=fills_inserted,
            orders_updated=sum(
                1 for d in discrepancies if d.resolution is Resolution.ORDER_STATE_APPLIED
            ),
            unresolved_orders=sum(
                1 for d in discrepancies if d.resolution is Resolution.UNRESOLVED
            ),
        )


def startup_gate(run: ReconciliationRun | None) -> tuple[bool, str]:
    """`18` Phase 10: "`trader` is not ready until reconciliation is clean".

    Returns `(ready, reason)`. **No run at all is not ready** — the absence of
    evidence is not evidence of agreement, and a process that accepted intents
    before reconciling would be trading on an assumption about what the broker
    holds.
    """
    if run is None:
        return (
            False,
            "no reconciliation has run; the broker's state is unknown and no intent "
            "may be accepted until it is established",
        )
    if not run.outcome.is_clean:
        attention = len(run.attention_required())
        return (
            False,
            f"reconciliation run {run.run_id} is not clean: "
            f"{run.outcome.unresolved_orders} unresolved order(s) and {attention} "
            f"discrepancy(ies) needing attention",
        )
    return (True, f"reconciliation run {run.run_id} is clean")
