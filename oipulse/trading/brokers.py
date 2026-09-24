"""The broker boundary — protocol only, with one paper implementation.

`11-TRADING.md` §7: "`PaperBrokerAdapter` implements `BrokerAdapter` exactly — same
protocol, same order lifecycle." `11` §9: live trading is disabled by feature flag and
"`UpstoxBrokerAdapter` built last".

Phase 8 goes further than disabling it: **it does not exist.**

There is no live adapter in this codebase, no HTTP client reachable from this package,
and no credential access. The import contract `paper-trading-cannot-reach-a-broker`
forbids `oipulse.marketdata.providers`, `oipulse.marketdata.upstox`, `httpx` and the
credential modules outright, and `tools/check_paper_trading_safety.py` fails the build
if a second `BrokerAdapter` implementation appears without the gates §9 requires.

The distinction matters. A disabled feature is one configuration change away from
being enabled, and a reviewer has to verify every path that reads the flag. An absent
implementation is zero configuration changes away from anything, and a reviewer has to
verify that it is absent — which a guard can do mechanically.

### Why the protocol exists at all in Phase 8

So that Phase 10 adds an implementation rather than a redesign, and so paper and live
can share one test suite (`18-ROADMAP.md` Phase 8: "paper and live adapters satisfy one
shared suite"). Defining the seam now is what stops the live path, when it arrives,
from being a parallel implementation that drifts.

The protocol is deliberately **narrow**: it covers only what Phase 8 exercises.
`submit`, `cancel` and `status` are here because a paper venue does them. Nothing
resembling authentication, funding or withdrawal is, because Phase 8 has no business
describing those and a protocol that declared them would invite an implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from oipulse.marketstate.state import MarketState
from oipulse.trading.accounts import (
    AccountMode,
    LiveExecutionUnavailable,
    PaperAccount,
    resolve_execution_mode,
)
from oipulse.trading.execution import ExecutionOutcome, PaperExecutionModel
from oipulse.trading.intents import IntentConstraints
from oipulse.trading.orders import OrderState, PaperOrder, RejectReason

__all__ = [
    "BrokerAdapter",
    "PaperBrokerAdapter",
    "adapter_for",
]


@runtime_checkable
class BrokerAdapter(Protocol):
    """The seam a venue implements. One implementation exists: the paper one.

    `mode` lets a caller assert what it is talking to. It is a property of the
    adapter rather than a constructor argument, so an adapter cannot be told it is
    something it is not.
    """

    @property
    def name(self) -> str: ...

    @property
    def mode(self) -> AccountMode: ...

    def submit(
        self,
        order: PaperOrder,
        state: MarketState,
        *,
        at: datetime,
        available_cash: Decimal,
        constraints: IntentConstraints | None = ...,
        reference_price: Decimal | None = ...,
    ) -> ExecutionOutcome: ...

    def cancel(self, order: PaperOrder, *, at: datetime, detail: str = ...) -> PaperOrder: ...

    def status(self, order: PaperOrder) -> OrderState: ...


@dataclass(frozen=True, slots=True)
class PaperBrokerAdapter:
    """A simulated venue. Prices through the shared Phase 7 fill model.

    Holds no connection, no session and no credential — there is nothing here to
    authenticate to. `submit` is a pure function of the order, the supplied state and
    the supplied cash, which is why the whole thing is testable without any
    infrastructure and why it cannot possibly reach a real venue.
    """

    execution: PaperExecutionModel
    name: str = "PAPER"

    @property
    def mode(self) -> AccountMode:
        return AccountMode.PAPER

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
