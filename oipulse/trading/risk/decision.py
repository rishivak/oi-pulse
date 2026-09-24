"""`RiskDecision` — one immutable, appended verdict on one intent.

`11-TRADING.md` §3:

> **Not one decision per intent.** Risk is re-evaluated on modification, retry,
> changed market conditions, changed quantity or amendment. Each evaluation
> **appends** a new immutable decision. An order references the **exact** decision
> that authorized it via `authorizing_risk_decision_id`.

and:

> Every limit evaluated and recorded even after the first rejection, so the decision
> shows the complete picture rather than the first failure.

Two consequences shape this module.

**Evidence is structured, not prose.** Phase 9 brief §23: free-text explanations are
not sufficient as the only evidence. Every limit produces a `LimitEvaluation` with a
category, an identifier, a status, the configured value, the observed value and the
headroom. `reason` exists for humans and is never the only record of why.

**A decision names exactly one intent.** `intent_id` is part of the identity and part
of the digest. `authorizes()` refuses any other intent, so an approval for intent A
cannot be presented for intent B even when the two are otherwise identical — brief §6.

### Why `EXPIRED` is not a verdict

`11` §3 defines the verdict set as `APPROVED | REJECTED | MODIFIED`, and expiry is
not one of them — correctly, because a decision is immutable and does not change when
time passes. What changes is whether it is still *actionable*. `is_actionable_at`
answers that against a supplied market time, and `authorization_status` renders the
combination as `APPROVED | REJECTED | MODIFIED | EXPIRED | UNEVALUATED` for a reader.
Storing `EXPIRED` would mean mutating a record that the data model makes append-only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.trading.risk.policy import LimitCategory

__all__ = [
    "AuthorizationStatus",
    "LimitEvaluation",
    "LimitStatus",
    "RiskDecisionRecord",
    "RiskVerdict",
]


class RiskVerdict(StrEnum):
    """`11` §3 and `02` §6: the three values the data model stores."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    #: Risk allowed less than was requested. The intent itself is untouched.
    MODIFIED = "MODIFIED"


class LimitStatus(StrEnum):
    """The outcome of one limit check.

    `NOT_CONFIGURED` and `NOT_EVALUABLE` are distinct and both are recorded. The
    first means the policy declares no such limit — a relaxation, visible in the
    audit. The second means the limit is configured but its input was unavailable,
    which is a data problem, not a permission. Collapsing them into "passed" is the
    specific way a risk report comes to overstate what was checked.
    """

    PASSED = "PASSED"
    BREACHED = "BREACHED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_EVALUABLE = "NOT_EVALUABLE"

    @property
    def blocks(self) -> bool:
        return self is LimitStatus.BREACHED


class AuthorizationStatus(StrEnum):
    """What a decision means *right now*, for a reader. Derived, never stored."""

    UNEVALUATED = "UNEVALUATED"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class LimitEvaluation:
    """One limit, checked. The structured evidence behind a verdict."""

    category: LimitCategory
    #: Stable identifier of the limit, e.g. "max_order_quantity".
    limit_id: str
    status: LimitStatus
    #: The configured threshold, as a string so a Decimal survives the wire intact.
    limit_value: str | None = None
    #: What the evaluation actually observed.
    observed_value: str | None = None
    #: Remaining room before the limit binds. None when not computable.
    headroom: str | None = None
    #: Why, when the status needs one. Supplementary to the fields above, never a
    #: substitute for them.
    detail: str = ""

    @property
    def blocks(self) -> bool:
        return self.status.blocks

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "limit_id": self.limit_id,
            "status": self.status.value,
            "limit_value": self.limit_value,
            "observed_value": self.observed_value,
            "headroom": self.headroom,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RiskDecisionRecord:
    """One appended decision. Immutable, sequenced, bound to exactly one intent.

    Carries the four mandatory traceability fields `11` §3 requires and the Phase 9
    brief §5 restates: `risk_state_ref`, `risk_evaluation_time`, `inputs_digest`,
    `approved_until`. Each has a single defined meaning and none is free text.
    """

    intent_id: str
    sequence_no: int
    verdict: RiskVerdict
    #: False whenever no real risk engine evaluated this intent. Kept from Phase 8
    #: so the pass-through gate and a real evaluation stay distinguishable.
    evaluated: bool
    #: Human-readable summary. Supplementary to `limits_evaluated`, never the only
    #: record of why (brief §23).
    reason: str = ""

    # --- the four mandatory fields -------------------------------------------
    #: The `RiskState` **this** evaluation read. Not the intent's checkpoint: `11`
    #: §3 is explicit that they differ and that the second is what matters after a
    #: loss.
    risk_state_ref: str = ""
    #: Market time of the evaluation.
    risk_evaluation_time: datetime | None = None
    #: Digest of the exact semantic inputs: intent, policy, state. Re-deriving it
    #: from the same three must reproduce this value.
    inputs_digest: str = ""
    #: Validity horizon. None when rejected, or when nothing was evaluated.
    approved_until: datetime | None = None

    # --- policy identity ------------------------------------------------------
    policy_id: str = ""
    policy_version: int = 0
    policy_digest: str = ""

    # --- what was requested and what was allowed (brief §11) ------------------
    requested_quantity: int = 0
    #: Zero on a rejection. Less than requested on a MODIFIED decision. The intent
    #: itself is never rewritten -- this records what risk allowed.
    approved_quantity: int = 0
    #: Per-leg approvals for a multi-leg intent, as (leg_index, quantity). Sorted.
    approved_legs: tuple[tuple[int, int], ...] = ()

    limits_evaluated: tuple[LimitEvaluation, ...] = ()
    #: Knowledge horizon the evaluation was permitted to read up to.
    knowledge_horizon: datetime | None = None
    extras: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.sequence_no < 1:
            raise ValueError("risk decision sequence_no must be >= 1")
        if not self.intent_id:
            raise ValueError("a risk decision must name the intent it evaluated")
        if self.approved_quantity < 0:
            raise ValueError("approved_quantity cannot be negative")
        if self.approved_quantity > self.requested_quantity:
            raise ValueError(
                f"approved_quantity {self.approved_quantity} exceeds the requested "
                f"{self.requested_quantity}; risk may reduce an intent, never enlarge it"
            )
        if self.verdict is RiskVerdict.APPROVED and self.approved_until is None:
            raise ValueError(
                "an APPROVED decision must carry approved_until; an approval with no "
                "validity horizon could be submitted against a market that has moved "
                "(11-TRADING.md §3)"
            )
        if self.verdict is RiskVerdict.REJECTED and self.approved_quantity != 0:
            raise ValueError("a REJECTED decision cannot approve a quantity")
        object.__setattr__(self, "approved_legs", tuple(sorted(self.approved_legs)))

    # ------------------------------------------------------------------ queries

    @property
    def is_approved(self) -> bool:
        """True for APPROVED and MODIFIED: both permit *some* quantity."""
        return self.verdict in (RiskVerdict.APPROVED, RiskVerdict.MODIFIED)

    def is_actionable_at(self, at: datetime) -> bool:
        """Whether this approval may still authorize execution at market time `at`.

        The boundary is inclusive: an approval valid *until* T is actionable **at**
        T and not after. `11` §3 requires submitting against an expired approval to
        be refused rather than silently re-approved, and an off-by-one at the
        boundary is the kind of thing that only shows up in production.

        An unevaluated decision is never actionable, whatever its verdict says. A
        pass-through that approved everything must not be able to authorize an
        order once a real engine exists.
        """
        if not self.is_approved or not self.evaluated:
            return False
        if self.approved_quantity <= 0:
            return False
        return self.approved_until is None or at <= self.approved_until

    def authorizes(self, intent_id: str, *, at: datetime) -> bool:
        """Whether this decision authorizes **that** intent at that time.

        Brief §6: an approval belongs to its own intent. Two intents with the same
        symbol, quantity, strategy and timestamp are still two intents, and their
        content-addressed ids differ, so this returns False for the second.
        """
        return intent_id == self.intent_id and self.is_actionable_at(at)

    def authorization_status(self, at: datetime) -> AuthorizationStatus:
        """How a reader should describe this decision at market time `at`."""
        if not self.evaluated:
            return AuthorizationStatus.UNEVALUATED
        if self.verdict is RiskVerdict.REJECTED:
            return AuthorizationStatus.REJECTED
        if not self.is_actionable_at(at):
            return AuthorizationStatus.EXPIRED
        return (
            AuthorizationStatus.MODIFIED
            if self.verdict is RiskVerdict.MODIFIED
            else AuthorizationStatus.APPROVED
        )

    def breaches(self) -> tuple[LimitEvaluation, ...]:
        """Every limit that actually blocked. The answer to "why was it rejected?"."""
        return tuple(limit for limit in self.limits_evaluated if limit.blocks)

    def unevaluable(self) -> tuple[LimitEvaluation, ...]:
        """Limits configured but not checkable. A data problem, not a permission."""
        return tuple(
            limit for limit in self.limits_evaluated if limit.status is LimitStatus.NOT_EVALUABLE
        )

    # ------------------------------------------------------------------ identity

    @property
    def risk_decision_id(self) -> str:
        """Deterministic identity, derived from the intent and the sequence.

        Not a UUID: a re-evaluation after a restart must resolve to the same id so
        the database's `PRIMARY KEY (intent_id, sequence_no)` turns a retry into a
        conflict rather than a second decision.
        """
        return (
            "rdec_"
            + hashlib.sha256(f"{self.intent_id}:{self.sequence_no}".encode()).hexdigest()[:32]
        )

    @property
    def decision_digest(self) -> str:
        """Content address of the decision's meaning.

        Excludes `reason` (prose) and `risk_evaluation_time` (when the work ran).
        Two evaluations of the same intent against the same state under the same
        policy are the same decision even if they ran a second apart, and the digest
        says so.
        """
        return hashlib.sha256(
            json.dumps(
                {
                    "intent_id": self.intent_id,
                    "sequence_no": self.sequence_no,
                    "verdict": self.verdict.value,
                    "evaluated": self.evaluated,
                    "risk_state_ref": self.risk_state_ref,
                    "inputs_digest": self.inputs_digest,
                    "policy_digest": self.policy_digest,
                    "requested_quantity": self.requested_quantity,
                    "approved_quantity": self.approved_quantity,
                    "approved_legs": [list(leg) for leg in self.approved_legs],
                    "limits": [limit.as_dict() for limit in self.limits_evaluated],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk_decision_id": self.risk_decision_id,
            "intent_id": self.intent_id,
            "sequence_no": self.sequence_no,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evaluated": self.evaluated,
            "risk_state_ref": self.risk_state_ref,
            "risk_evaluation_time": (
                None if self.risk_evaluation_time is None else self.risk_evaluation_time.isoformat()
            ),
            "inputs_digest": self.inputs_digest,
            "approved_until": (
                None if self.approved_until is None else self.approved_until.isoformat()
            ),
            "knowledge_horizon": (
                None if self.knowledge_horizon is None else self.knowledge_horizon.isoformat()
            ),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "requested_quantity": self.requested_quantity,
            "approved_quantity": self.approved_quantity,
            "approved_legs": {str(index): qty for index, qty in self.approved_legs},
            "limits_evaluated": [limit.as_dict() for limit in self.limits_evaluated],
            "breaches": [limit.as_dict() for limit in self.breaches()],
            "decision_digest": self.decision_digest,
        }


def inputs_digest_for(*, intent_digest: str, policy_digest: str, risk_state_ref: str) -> str:
    """The `inputs_digest` (`11` §3, brief §12).

    Exactly three things determine a decision: what was asked, what the rules were,
    and what the world looked like. Hashing precisely those means a repeated
    evaluation with identical inputs reproduces the digest, and any change to any of
    them produces a different one. The evaluation *time* is deliberately excluded —
    it is recorded separately as `risk_evaluation_time`, and including it would make
    the digest unable to demonstrate reproducibility, which is its whole purpose.
    """
    return (
        "rin_"
        + hashlib.sha256(
            json.dumps(
                {
                    "intent": intent_digest,
                    "policy": policy_digest,
                    "risk_state": risk_state_ref,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:32]
    )


def decimal_str(value: Decimal | int | None) -> str | None:
    """Uniform rendering for limit values on the wire."""
    return None if value is None else str(value)


__all__ += ["decimal_str", "inputs_digest_for"]
