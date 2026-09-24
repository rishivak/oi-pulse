"""`TradeIntent` — what a strategy returns. Not an order, and certainly not a fill.

`10-REPLAY.md` §7 places the intent at the head of the execution path:

```
Strategy.on_state(ctx) -> TradeIntent -> RiskEngine -> OMS -> FillModel -> Fill
```

Phase 7 implements the intent, the fill model and the ledger. The risk engine (Phase 9)
and the OMS state machine (Phase 10) are later phases, and the seam between them is
declared explicitly in `risk.py` rather than filled with a stand-in that could later be
mistaken for the real thing.

Intent identity is **deterministic** — derived from the run, the strategy version, the
decision point and the intent's own content. No UUID, no counter seeded by wall clock:
`13` of the Phase 7 brief requires the simulated order sequence to be identical across
runs, and a random id would break that at the first comparison.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

__all__ = ["OrderType", "Side", "TradeIntent", "intent_digest"]


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for a buy, -1 for a sell. Used by the ledger, never inferred elsewhere."""
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    """Only the two types the fill model can simulate honestly.

    A stop or bracket order needs intra-bar path information that a state sequence
    does not carry; simulating one from point observations would fabricate the
    trigger. They are deliberately absent rather than approximated.
    """

    MARKET = "MARKET"
    LIMIT = "LIMIT"


def intent_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """A strategy's decision. Deterministic identity, no execution assumption baked in."""

    run_id: str
    strategy_id: str
    strategy_version: int
    instrument_id: int
    side: Side
    quantity: int
    order_type: OrderType
    #: The market time the decision was made at.
    decision_time: datetime
    #: What the strategy knew when it decided. Carried so a fill can be checked
    #: against it rather than against whatever the simulator happens to see.
    knowledge_horizon: datetime
    limit_price: Decimal | None = None
    reason: str = ""
    #: References to the signals or features that motivated it. Resolvable, not copied.
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                "intent quantity must be positive; direction is carried by `side`, "
                "not by the sign of the quantity"
            )
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("a LIMIT intent requires a limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError(
                "a MARKET intent must not carry a limit_price; it would be ignored, "
                "which is worse than being refused"
            )
        if self.knowledge_horizon < self.decision_time:
            raise ValueError(
                "knowledge_horizon precedes decision_time; a decision cannot rest on "
                "information from after it was made"
            )

    @property
    def intent_id(self) -> str:
        """Content-addressed. Two identical runs produce identical intent ids."""
        return (
            "ti_"
            + intent_digest(
                {
                    "run_id": self.run_id,
                    "strategy": f"{self.strategy_id}@v{self.strategy_version}",
                    "instrument_id": self.instrument_id,
                    "side": self.side.value,
                    "quantity": self.quantity,
                    "order_type": self.order_type.value,
                    "decision_time": self.decision_time.isoformat(),
                    "knowledge_horizon": self.knowledge_horizon.isoformat(),
                    "limit_price": None if self.limit_price is None else str(self.limit_price),
                }
            )[:32]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "decision_time": self.decision_time.isoformat(),
            "knowledge_horizon": self.knowledge_horizon.isoformat(),
            "limit_price": None if self.limit_price is None else str(self.limit_price),
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }
