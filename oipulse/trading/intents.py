"""`TradeIntent` — the universal seam (`11-TRADING.md` §2).

```
TradeIntent
├── id, client_order_intent_id
├── account_id                  → mode PAPER | LIVE
├── source                      STRATEGY | SIGNAL | MANUAL
├── source_ref                  signal id, strategy run, user
├── legs[]                      instrument, side, qty, order_type, limit/trigger
├── time_in_force
├── constraints                 max_slippage, valid_until, all_or_none
├── rationale_ref               → the signal/evidence that motivated it
├── state_checkpoint_ref        ← what the system knew when deciding
└── created_at
```

**Multi-leg is first-class.** `11` §2: "an options platform whose unit is a single
order cannot express a spread." That is why this is a distinct type from the Phase 7
`oipulse.backtest.intents.TradeIntent`, which is single-leg by construction and is the
*strategy's* output rather than the account-scoped seam. `from_strategy_intent` lifts
one into the other explicitly, so the two never drift into each other silently and
neither had to be rewritten.

**`state_checkpoint_ref` closes the research loop.** Every intent links back to exactly
the market picture that produced it, so "what did we know when we made this decision?"
is a join rather than log archaeology (`11` §10).

The three times are all recorded and all distinct: `market_time` is when the market
fact was true, `knowledge_time` is what the decider knew, `decision_time` is when it
decided. None of them is a wall-clock reading — Phase 8 injects them, and
`tools/check_clock_access.py` enforces that no module here can read a clock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from oipulse.backtest.intents import OrderType, Side

if TYPE_CHECKING:  # pragma: no cover
    from oipulse.backtest.intents import TradeIntent as StrategyIntent

__all__ = [
    "IntentConstraints",
    "IntentLeg",
    "IntentSource",
    "OrderType",
    "Side",
    "TimeInForce",
    "TradeIntent",
    "from_strategy_intent",
]


class IntentSource(StrEnum):
    STRATEGY = "STRATEGY"
    SIGNAL = "SIGNAL"
    MANUAL = "MANUAL"


class TimeInForce(StrEnum):
    """Only what the paper execution model can honour honestly.

    `GTC` is deliberately absent: a good-till-cancelled paper order would rest across
    sessions, and simulating what happens to it overnight needs data the observation
    store does not have. `DAY` and `IOC` are both fully determinable from a session's
    observations.
    """

    DAY = "DAY"
    #: Immediate-or-cancel: fill what can be filled now, cancel the rest.
    IOC = "IOC"


@dataclass(frozen=True, slots=True)
class IntentLeg:
    """One leg. A spread is several of these in one intent, priced as a whole."""

    instrument_id: int
    side: Side
    quantity: int
    order_type: OrderType
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                "leg quantity must be positive; direction is carried by `side`, not "
                "by the sign of the quantity"
            )
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("a LIMIT leg requires a limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError(
                "a MARKET leg must not carry a limit_price; it would be ignored, "
                "which is worse than being refused"
            )
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": None if self.limit_price is None else str(self.limit_price),
        }


@dataclass(frozen=True, slots=True)
class IntentConstraints:
    """`11` §2. Every constraint the execution model must honour, stated up front."""

    #: Maximum acceptable slippage per unit against the decision reference price.
    #: Exceeded means the order is rejected, not filled worse.
    max_slippage: Decimal | None = None
    #: Market time after which the intent must not be executed. Expiry, not a timeout.
    valid_until: datetime | None = None
    #: Fill entirely or not at all. A multi-leg spread almost always wants this: a
    #: half-filled spread is a different position from the one that was intended.
    all_or_none: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_slippage": None if self.max_slippage is None else str(self.max_slippage),
            "valid_until": None if self.valid_until is None else self.valid_until.isoformat(),
            "all_or_none": self.all_or_none,
        }


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """An instruction to trade, not a trade. Deterministically identified.

    Identity is content-addressed over the account, source, legs, times and
    constraints -- never a UUID or a counter. The same decision, reprocessed after a
    restart, produces the same `intent_id`, which is what makes the whole downstream
    chain idempotent rather than merely careful (§16 of the Phase 8 brief).
    """

    account_id: str
    source: IntentSource
    #: Signal id, strategy run id, or user id -- whatever produced this.
    source_ref: str
    legs: tuple[IntentLeg, ...]
    #: When the market fact the decision rests on was true.
    market_time: datetime
    #: What the decider knew. Reused Phase 5 semantics: a signal's knowledge horizon
    #: is its own semantic value and is carried, never re-derived.
    knowledge_time: datetime
    #: When the decision was made. An action timestamp, not a fifth time dimension.
    decision_time: datetime
    time_in_force: TimeInForce = TimeInForce.DAY
    constraints: IntentConstraints = field(default_factory=IntentConstraints)
    #: The signal / evidence that motivated it. Resolvable, not copied.
    rationale_ref: str = ""
    #: The MarketState this decision saw. Closes the research loop (`11` §2).
    state_checkpoint_ref: str = ""
    build_context_id: str = ""
    #: Which signal, and which version of it. Both, because a rule edit changes what
    #: the signal meant and a result must stay interpretable after it.
    signal_id: str = ""
    signal_version: int = 0
    strategy_id: str = ""
    strategy_version: int = 0
    #: Caller-supplied dedup key. When absent, `intent_id` serves the same purpose.
    client_order_intent_id: str = ""
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("an intent must have at least one leg")
        if not self.account_id:
            raise ValueError("an intent must name its account")
        if self.knowledge_time < self.market_time:
            raise ValueError(
                f"knowledge_time {self.knowledge_time.isoformat()} precedes market_time "
                f"{self.market_time.isoformat()}; a decision cannot know less than the "
                f"market fact it rests on is old"
            )
        if self.decision_time < self.market_time:
            raise ValueError(
                "decision_time precedes market_time; a decision cannot be made before "
                "the market fact it rests on was true"
            )
        if (
            self.constraints.valid_until is not None
            and self.constraints.valid_until < self.decision_time
        ):
            raise ValueError("valid_until precedes decision_time; the intent would be born expired")

    # ------------------------------------------------------------------ identity

    @property
    def intent_id(self) -> str:
        """Content-addressed. Two identical decisions produce one intent, not two."""
        return "int_" + self.content_digest[:32]

    @property
    def content_digest(self) -> str:
        """Semantic identity.

        Excludes `reason` and `evidence_refs`: prose and pointers describe a decision
        but do not change what it instructs. Includes every time, because an
        identical instruction made at a different moment or on different knowledge is
        a different decision.
        """
        return hashlib.sha256(
            json.dumps(
                {
                    "account_id": self.account_id,
                    "source": self.source.value,
                    "source_ref": self.source_ref,
                    "legs": [leg.as_dict() for leg in self.legs],
                    "market_time": self.market_time.isoformat(),
                    "knowledge_time": self.knowledge_time.isoformat(),
                    "decision_time": self.decision_time.isoformat(),
                    "time_in_force": self.time_in_force.value,
                    "constraints": self.constraints.as_dict(),
                    "state_checkpoint_ref": self.state_checkpoint_ref,
                    "build_context_id": self.build_context_id,
                    "signal": f"{self.signal_id}@v{self.signal_version}",
                    "strategy": f"{self.strategy_id}@v{self.strategy_version}",
                    "client_order_intent_id": self.client_order_intent_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @property
    def idempotency_key(self) -> str:
        """What dedup is keyed on. The client's key when given, else the content."""
        return self.client_order_intent_id or self.intent_id

    # ---------------------------------------------------------------- properties

    @property
    def is_multi_leg(self) -> bool:
        return len(self.legs) > 1

    @property
    def total_quantity(self) -> int:
        return sum(leg.quantity for leg in self.legs)

    def has_expired(self, at: datetime) -> bool:
        """Against **market time**, never a wall clock."""
        valid_until = self.constraints.valid_until
        return valid_until is not None and at > valid_until

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "client_order_intent_id": self.client_order_intent_id,
            "account_id": self.account_id,
            "source": self.source.value,
            "source_ref": self.source_ref,
            "legs": [leg.as_dict() for leg in self.legs],
            "time_in_force": self.time_in_force.value,
            "constraints": self.constraints.as_dict(),
            "market_time": self.market_time.isoformat(),
            "knowledge_time": self.knowledge_time.isoformat(),
            "decision_time": self.decision_time.isoformat(),
            "rationale_ref": self.rationale_ref,
            "state_checkpoint_ref": self.state_checkpoint_ref,
            "build_context_id": self.build_context_id,
            "signal_id": self.signal_id,
            "signal_version": self.signal_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "content_digest": self.content_digest,
        }


def from_strategy_intent(
    intent: StrategyIntent,
    *,
    account_id: str,
    market_time: datetime,
    state_checkpoint_ref: str = "",
    build_context_id: str = "",
    signal_id: str = "",
    signal_version: int = 0,
    rationale_ref: str = "",
    time_in_force: TimeInForce = TimeInForce.DAY,
    valid_for: timedelta | None = None,
) -> TradeIntent:
    """Lift a Phase 7 strategy intent into the account-scoped trading seam.

    The two types are deliberately distinct. The Phase 7 intent is what a *strategy*
    produces: one leg, no account, no authority. This one is what the *account*
    acts on: multi-leg, audited, and carrying the references `11` §2 requires. An
    explicit conversion keeps both honest -- the strategy surface does not grow an
    account field it has no business knowing about, and the trading seam does not
    inherit a single-leg limitation that would make a spread inexpressible.

    Nothing is invented here. Every field either comes from the strategy intent or is
    supplied by the caller, which is the component that actually knows the account
    and the checkpoint.
    """
    return TradeIntent(
        account_id=account_id,
        source=IntentSource.STRATEGY,
        source_ref=f"{intent.strategy_id}@v{intent.strategy_version}",
        legs=(
            IntentLeg(
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                order_type=intent.order_type,
                limit_price=intent.limit_price,
            ),
        ),
        market_time=market_time,
        knowledge_time=intent.knowledge_horizon,
        decision_time=intent.decision_time,
        time_in_force=time_in_force,
        constraints=IntentConstraints(
            valid_until=None if valid_for is None else intent.decision_time + valid_for
        ),
        rationale_ref=rationale_ref,
        state_checkpoint_ref=state_checkpoint_ref,
        build_context_id=build_context_id,
        signal_id=signal_id,
        signal_version=signal_version,
        strategy_id=intent.strategy_id,
        strategy_version=intent.strategy_version,
        reason=intent.reason,
        evidence_refs=intent.evidence_refs,
    )
