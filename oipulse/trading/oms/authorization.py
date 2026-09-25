"""The submission gate — what must be true before an order may reach a venue.

Phase 10 brief §6 and §17:

> OMS order creation/submission MUST require: exact TradeIntent, exact RiskDecision,
> approved status, valid `approved_until`, correct intent binding ...
> **Do not duplicate risk logic. Consume the canonical RiskDecision.**

So this module contains **no limit evaluation**. It asks the Phase 9
`RiskDecisionRecord` the questions it already knows how to answer — `is_approved`,
`is_actionable_at`, `authorizes` — and refuses when any of them says no. Re-deriving
any part of that judgement here would create a second risk implementation for the two
to drift apart, which is exactly what `11-TRADING.md` §3 forbids.

### Why a separate module rather than an `if` in the manager

Because the refusal needs to be *enumerable*. `AuthorizationRefusal` names each way a
submission can be forbidden, so a caller, a metric and an audit record all use the
same vocabulary, and a new refusal reason cannot be added without appearing in all
three.

### The four refusals brief §6 requires, and how each is detected

| Attempt | Detected by |
|---|---|
| no decision | `decision is None` |
| rejected decision | `RiskDecisionRecord.is_approved` |
| expired decision | `RiskDecisionRecord.is_actionable_at(at)` |
| wrong-intent decision | `RiskDecisionRecord.authorizes(intent_id, at=...)` |

A fifth, which the brief does not name but the Phase 9 model makes possible: an
**unevaluated** decision. `is_actionable_at` already returns False for one, so a
pass-through approval cannot authorize a submission either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from oipulse.trading.intents import TradeIntent
from oipulse.trading.risk import RiskDecisionRecord

__all__ = ["AuthorizationRefusal", "SubmissionAuthorization", "authorize_submission"]


class AuthorizationRefusal(StrEnum):
    """Every way a submission can be forbidden. One vocabulary, three consumers."""

    NO_DECISION = "NO_DECISION"
    DECISION_REJECTED = "DECISION_REJECTED"
    DECISION_EXPIRED = "DECISION_EXPIRED"
    DECISION_NOT_EVALUATED = "DECISION_NOT_EVALUATED"
    WRONG_INTENT = "WRONG_INTENT"
    #: The order does not belong to the intent the decision evaluated.
    ORDER_INTENT_MISMATCH = "ORDER_INTENT_MISMATCH"
    #: Nothing was approved, so there is nothing to submit.
    ZERO_APPROVED_QUANTITY = "ZERO_APPROVED_QUANTITY"
    #: The order asks for more than risk allowed.
    QUANTITY_EXCEEDS_APPROVAL = "QUANTITY_EXCEEDS_APPROVAL"
    #: The adapter cannot do what is being asked of it.
    CAPABILITY_DENIED = "CAPABILITY_DENIED"


@dataclass(frozen=True, slots=True)
class SubmissionAuthorization:
    """The gate's answer. A refusal is a result, never an exception.

    Raising would lose the refusal from the audit trail, and *why a submission did
    not happen* is exactly the question asked after an incident.
    """

    authorized: bool
    intent_id: str
    order_id: str
    refusal: AuthorizationRefusal | None = None
    detail: str = ""
    #: The decision that authorized it, when one did. Recorded on the order so it
    #: names the exact approval rather than "the latest one for this intent".
    decision_id: str = ""
    decision_sequence: int | None = None
    approved_quantity: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "intent_id": self.intent_id,
            "order_id": self.order_id,
            "refusal": None if self.refusal is None else self.refusal.value,
            "detail": self.detail,
            "decision_id": self.decision_id,
            "decision_sequence": self.decision_sequence,
            "approved_quantity": self.approved_quantity,
        }


def authorize_submission(
    *,
    intent: TradeIntent,
    decision: RiskDecisionRecord | None,
    order_id: str,
    order_intent_id: str,
    order_quantity: int,
    at: datetime,
) -> SubmissionAuthorization:
    """Decide whether this order may be submitted. Pure.

    `at` is market time, supplied. Nothing here reads a clock — the expiry check is
    against the decision's own `approved_until`, compared with the instant the
    caller says it is acting at.

    The checks run in a fixed order from cheapest and most fundamental outward, and
    the **first** refusal is returned rather than all of them. Unlike risk
    evaluation, where `11` §3 wants the complete picture, an unauthorized submission
    is a single hard stop: there is no partial authorization to report on, and
    enumerating further problems with an order that is never going out is noise.
    """

    def refuse(refusal: AuthorizationRefusal, detail: str) -> SubmissionAuthorization:
        return SubmissionAuthorization(
            authorized=False,
            intent_id=intent.intent_id,
            order_id=order_id,
            refusal=refusal,
            detail=detail,
        )

    # The order must belong to the intent being authorized. Checked first because
    # every later check is meaningless if the order is not this intent's.
    if order_intent_id != intent.intent_id:
        return refuse(
            AuthorizationRefusal.ORDER_INTENT_MISMATCH,
            f"order {order_id} belongs to intent {order_intent_id}, not {intent.intent_id}",
        )

    if decision is None:
        return refuse(
            AuthorizationRefusal.NO_DECISION,
            "no risk decision exists for this intent; submission requires an "
            "approved decision from the intent's own sequence",
        )

    # Wrong-intent before verdict: a decision for another intent tells us nothing
    # about this one, whatever it says.
    if decision.intent_id != intent.intent_id:
        return refuse(
            AuthorizationRefusal.WRONG_INTENT,
            f"decision {decision.risk_decision_id} evaluated intent "
            f"{decision.intent_id}, not {intent.intent_id}; an approval belongs to "
            f"its own intent",
        )

    if not decision.evaluated:
        return refuse(
            AuthorizationRefusal.DECISION_NOT_EVALUATED,
            "the decision records that no risk engine evaluated this intent; an "
            "unevaluated approval authorizes nothing",
        )

    if not decision.is_approved:
        return refuse(
            AuthorizationRefusal.DECISION_REJECTED,
            f"decision {decision.risk_decision_id} is {decision.verdict.value}: {decision.reason}",
        )

    if not decision.is_actionable_at(at):
        return refuse(
            AuthorizationRefusal.DECISION_EXPIRED,
            f"decision {decision.risk_decision_id} is not actionable at "
            f"{at.isoformat()} (status "
            f"{decision.authorization_status(at).value}); re-evaluation is required "
            f"rather than submission against a stale approval",
        )

    if decision.approved_quantity <= 0:
        return refuse(
            AuthorizationRefusal.ZERO_APPROVED_QUANTITY,
            "the decision approved no quantity",
        )

    if order_quantity > decision.approved_quantity:
        return refuse(
            AuthorizationRefusal.QUANTITY_EXCEEDS_APPROVAL,
            f"order asks for {order_quantity} but risk approved {decision.approved_quantity}",
        )

    return SubmissionAuthorization(
        authorized=True,
        intent_id=intent.intent_id,
        order_id=order_id,
        decision_id=decision.risk_decision_id,
        decision_sequence=decision.sequence_no,
        approved_quantity=decision.approved_quantity,
    )
