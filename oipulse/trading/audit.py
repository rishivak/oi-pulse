"""The answerable chain — `11-TRADING.md` §10, Phase 8 brief §14.

> For any historical trade, by foreign-key traversal ... Eleven questions, one join
> path, no log archaeology.

This module assembles that traversal for a paper trade. It resolves references; it
does not recompute anything. If a link is missing the answer is *"not recorded"*, never
a reconstruction — a plausible guess in an audit trail is worse than a gap, because a
gap is visible.

The questions the Phase 8 brief §14 requires an answer to, and where each comes from:

| Question | Source |
|---|---|
| why was the trade created? | `intent.reason`, `intent.rationale_ref` |
| which signal fired? | `intent.signal_id` |
| which signal version? | `intent.signal_version` |
| which strategy version? | `intent.strategy_id` / `strategy_version` |
| what market/knowledge time? | `intent.market_time` / `knowledge_time` |
| what evidence supported it? | `intent.evidence_refs` |
| what BuildContext? | `intent.build_context_id` |
| what order configuration? | `order.config_digest`, execution assumptions |
| why did the fill occur? | `fill.price_source`, `slippage_model`, assumptions |
| what changed in the account? | the ledger delta on either side of the fill |
| what authorised it? | the risk decision sequence |
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from oipulse.backtest.fills import Fill
from oipulse.trading.intents import TradeIntent
from oipulse.trading.orders import PaperOrder
from oipulse.trading.risk import RiskDecisionRecord

__all__ = ["AuditChain", "build_audit_chain"]

#: Returned wherever a reference was never recorded. A single sentinel, so a reader
#: can distinguish "we did not record this" from "this was empty" at a glance.
NOT_RECORDED = "not recorded"


@dataclass(frozen=True, slots=True)
class AuditChain:
    """One trade, explained end to end. Assembled from references, never inferred."""

    intent: TradeIntent
    risk_decisions: tuple[RiskDecisionRecord, ...]
    order: PaperOrder
    fills: tuple[Fill, ...]
    execution_assumptions: dict[str, Any]

    @property
    def filled_quantity(self) -> int:
        return sum(f.quantity for f in self.fills)

    @property
    def gross_consideration(self) -> Decimal:
        return sum((f.price * Decimal(f.quantity) for f in self.fills), Decimal(0))

    @property
    def total_costs(self) -> Decimal:
        return sum((f.costs.total for f in self.fills), Decimal(0))

    @property
    def is_assumption_based(self) -> bool:
        """True if any fill was priced against an assumed spread (`10` §6).

        Carried into the audit answer because a P&L figure resting on an invented
        spread is not the same kind of number as one resting on an observed quote.
        """
        return any(f.assumption_based for f in self.fills)

    @property
    def risk_evaluated(self) -> bool:
        return any(d.evaluated for d in self.risk_decisions)

    def answers(self) -> dict[str, Any]:
        """The brief's §14 questions, answered. `not recorded` where a link is absent."""
        return {
            "why_was_the_trade_created": self.intent.reason or NOT_RECORDED,
            "which_signal_fired": self.intent.signal_id or NOT_RECORDED,
            "which_signal_version": self.intent.signal_version or NOT_RECORDED,
            "which_strategy": (
                f"{self.intent.strategy_id}@v{self.intent.strategy_version}"
                if self.intent.strategy_id
                else NOT_RECORDED
            ),
            "market_time": self.intent.market_time.isoformat(),
            "knowledge_time": self.intent.knowledge_time.isoformat(),
            "decision_time": self.intent.decision_time.isoformat(),
            "what_evidence_supported_it": list(self.intent.evidence_refs) or NOT_RECORDED,
            "what_build_context": self.intent.build_context_id or NOT_RECORDED,
            "what_market_state": self.intent.state_checkpoint_ref or NOT_RECORDED,
            "what_authorised_it": [d.as_dict() for d in self.risk_decisions],
            "risk_evaluated": self.risk_evaluated,
            "what_order_configuration": {
                "config_digest": self.order.config_digest or NOT_RECORDED,
                "order_type": self.order.order_type.value,
                "limit_price": (
                    None if self.order.limit_price is None else str(self.order.limit_price)
                ),
                "execution_assumptions": self.execution_assumptions,
            },
            "why_did_the_fill_occur": [
                {
                    "price": str(f.price),
                    "reference_price": str(f.reference_price),
                    "price_source": f.price_source.value,
                    "slippage_model": f.slippage_model.value,
                    "slippage_per_unit": str(f.slippage_per_unit),
                    "assumption_based": f.assumption_based,
                    "filled_at": f.filled_at.isoformat(),
                }
                for f in self.fills
            ]
            or NOT_RECORDED,
            "what_changed_in_the_account": {
                "filled_quantity": self.filled_quantity,
                "gross_consideration": str(self.gross_consideration),
                "costs": str(self.total_costs),
                "instrument_id": self.order.instrument_id,
                "side": self.order.side.value,
            },
            "order_history": [e.as_dict() for e in self.order.events],
            "assumption_based": self.is_assumption_based,
            "mode": "PAPER",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.as_dict(),
            "risk_decisions": [d.as_dict() for d in self.risk_decisions],
            "order": self.order.as_dict(),
            "fills": [f.as_dict() for f in self.fills],
            "answers": self.answers(),
        }


def build_audit_chain(
    order: PaperOrder,
    intent: TradeIntent,
    *,
    risk_decisions: tuple[RiskDecisionRecord, ...],
    fills: tuple[Fill, ...],
    execution_assumptions: dict[str, Any],
) -> AuditChain:
    """Assemble the chain for one order.

    Refuses to assemble a chain whose order and intent do not match: a mismatched
    pair would produce an explanation of a trade that never happened, which is the
    single worst thing an audit trail can do.
    """
    if order.intent_id != intent.intent_id:
        raise ValueError(
            f"order {order.order_id} belongs to intent {order.intent_id}, not "
            f"{intent.intent_id}; refusing to assemble a chain across two trades"
        )
    return AuditChain(
        intent=intent,
        risk_decisions=risk_decisions,
        order=order,
        fills=fills,
        execution_assumptions=execution_assumptions,
    )
