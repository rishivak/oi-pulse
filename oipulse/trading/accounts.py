"""The paper trading account — `11-TRADING.md` §7.

> Paper accounts are `TradingAccount(mode=PAPER)`. A user may run several with
> different risk profiles and strategies simultaneously. Because mode is
> account-level, **no strategy, risk rule or portfolio calculation knows whether it is
> paper or live** — which is what makes the paper results meaningful.

So `mode` exists as a field and `AccountMode.LIVE` exists as a value. That is not a
live-trading capability; it is the *seam* that makes one implementation serve both, and
removing it would guarantee the divergence the design exists to prevent.

What makes live execution impossible in Phase 8 is that **no live adapter exists**.
`resolve_execution_mode` raises `LiveExecutionUnavailable` for anything but `PAPER`,
and there is no code anywhere in the package that could submit an order to a broker.
A configuration flag cannot switch modes because there is nothing to switch to.

Account *configuration* is versioned and content-addressed for the same reason a fill
model is: a result produced under one starting balance and fee schedule is not
comparable with one produced under another, and the identity should say so before
anyone charts them together.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.backtest.costs import CostModel
from oipulse.backtest.fills import FillModel

__all__ = [
    "AccountMode",
    "AccountStatus",
    "LiveExecutionUnavailable",
    "PaperAccount",
    "PaperAccountConfig",
    "resolve_execution_mode",
]


class AccountMode(StrEnum):
    """`11` §7. `LIVE` is declared so the seam is real; it has no implementation."""

    PAPER = "PAPER"
    LIVE = "LIVE"


class AccountStatus(StrEnum):
    """Lifecycle. An account that is not `ACTIVE` accepts no new intent."""

    #: Created but not yet funded/opened for trading.
    INITIALISED = "INITIALISED"
    ACTIVE = "ACTIVE"
    #: Halted by an operator. Resting orders may still be cancelled.
    SUSPENDED = "SUSPENDED"
    #: Terminal. No further transition.
    CLOSED = "CLOSED"

    @property
    def accepts_intents(self) -> bool:
        return self is AccountStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self is AccountStatus.CLOSED


class LiveExecutionUnavailable(RuntimeError):
    """Raised when anything asks for a live execution path.

    Phase 8 is paper-only. This is not a feature flag that an operator can flip: there
    is no live broker adapter in the codebase to flip it on to, and
    `tools/check_paper_trading_safety.py` fails the build if one appears without the
    gates `11` §9 requires.
    """


#: Permitted account lifecycle transitions. A declared table, not scattered `if`s --
#: the same discipline the order state machine uses, for the same reason.
_ACCOUNT_TRANSITIONS: dict[AccountStatus, frozenset[AccountStatus]] = {
    AccountStatus.INITIALISED: frozenset({AccountStatus.ACTIVE, AccountStatus.CLOSED}),
    AccountStatus.ACTIVE: frozenset({AccountStatus.SUSPENDED, AccountStatus.CLOSED}),
    AccountStatus.SUSPENDED: frozenset({AccountStatus.ACTIVE, AccountStatus.CLOSED}),
    AccountStatus.CLOSED: frozenset(),
}


def account_transitions(status: AccountStatus) -> frozenset[AccountStatus]:
    return _ACCOUNT_TRANSITIONS[status]


@dataclass(frozen=True, slots=True)
class PaperAccountConfig:
    """Everything that changes what a paper run means. Content-addressed.

    The fill model and cost model are **required**, not defaulted: `10-REPLAY.md` §6
    forbids hidden execution assumptions, and an account whose slippage or fee
    schedule came from a default would produce numbers nobody could interpret later.
    """

    starting_cash: Decimal
    currency: str
    fill_model: FillModel
    cost_model: CostModel
    #: Reserve cash for resting buy orders, so two orders cannot spend the same rupee.
    #: Declared rather than assumed -- a model without reservation is also defensible,
    #: but it must be stated, because it changes which orders get rejected.
    reserve_cash_on_open_orders: bool = True
    #: Refuse an order whose notional exceeds this. None means no cap *by
    #: configuration* -- it does not mean "unlimited risk", because Phase 9 owns
    #: limits; it means this account declares none.
    max_order_notional: Decimal | None = None
    max_order_quantity: int | None = None

    def __post_init__(self) -> None:
        if self.starting_cash < 0:
            raise ValueError("starting cash cannot be negative")
        if not self.currency:
            raise ValueError("an account must declare its currency")
        if self.max_order_quantity is not None and self.max_order_quantity <= 0:
            raise ValueError("max_order_quantity must be positive when set")

    def as_dict(self) -> dict[str, Any]:
        return {
            "starting_cash": str(self.starting_cash),
            "currency": self.currency,
            "fill_model": self.fill_model.assumptions(),
            "cost_model": self.cost_model.as_dict(),
            "reserve_cash_on_open_orders": self.reserve_cash_on_open_orders,
            "max_order_notional": (
                None if self.max_order_notional is None else str(self.max_order_notional)
            ),
            "max_order_quantity": self.max_order_quantity,
        }

    @property
    def content_digest(self) -> str:
        """Semantic identity of the configuration. No wall clock, no process id."""
        return (
            "cfg_"
            + hashlib.sha256(
                json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:24]
        )


@dataclass(frozen=True, slots=True)
class PaperAccount:
    """Identity, configuration and lifecycle. **No balances.**

    Cash, positions and P&L live in the ledger, which folds them from fills. Keeping
    them off this object is deliberate: `11` §8 requires positions to be *derived from
    fill events, not maintained as a running total that can drift*, and an account
    object carrying a mutable balance is exactly such a total.
    """

    account_id: str
    owner: str
    mode: AccountMode
    config: PaperAccountConfig
    status: AccountStatus = AccountStatus.INITIALISED
    #: Market time the account was opened at. Injected, never read from a clock.
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    label: str = ""
    extras: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must be non-empty")
        if self.mode is not AccountMode.PAPER:
            raise LiveExecutionUnavailable(
                f"account {self.account_id} requests mode {self.mode.value}; Phase 8 "
                f"implements paper trading only and no live broker adapter exists. "
                f"This is not a disabled feature -- there is no live execution path "
                f"in the codebase to enable."
            )

    @property
    def is_paper(self) -> bool:
        return self.mode is AccountMode.PAPER

    def with_status(self, status: AccountStatus, *, at: datetime) -> PaperAccount:
        """Transition, or raise. Invalid transitions are never silently corrected."""
        from dataclasses import replace

        if status not in account_transitions(self.status):
            raise ValueError(
                f"account {self.account_id}: {self.status.value} -> {status.value} is "
                f"not a permitted transition; permitted: "
                f"{sorted(s.value for s in account_transitions(self.status))}"
            )
        if status is AccountStatus.ACTIVE and self.opened_at is None:
            return replace(self, status=status, opened_at=at)
        if status is AccountStatus.CLOSED:
            return replace(self, status=status, closed_at=at)
        return replace(self, status=status)

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "owner": self.owner,
            # Always surfaced, never inferred: a consumer must be told this is paper.
            "mode": self.mode.value,
            "status": self.status.value,
            "label": self.label,
            "config": self.config.as_dict(),
            "config_digest": self.config.content_digest,
            "opened_at": None if self.opened_at is None else self.opened_at.isoformat(),
            "closed_at": None if self.closed_at is None else self.closed_at.isoformat(),
        }


def resolve_execution_mode(account: PaperAccount) -> AccountMode:
    """The single place a mode becomes an execution path.

    Every caller that is about to execute goes through here, so there is exactly one
    line in the codebase where "is this live?" is answered -- and it answers by
    raising. A future Phase 10 adds the live branch here, behind the three documented
    gates, and nowhere else.
    """
    if account.mode is not AccountMode.PAPER:
        raise LiveExecutionUnavailable(f"no execution adapter exists for mode {account.mode.value}")
    return AccountMode.PAPER
