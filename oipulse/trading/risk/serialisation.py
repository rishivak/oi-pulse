"""Rendering risk artifacts for the API. Pure, web-stack-free.

Kept out of `api/` like every other envelope in the system, so the contract stays
testable on an interpreter with no web stack installed.

**Nothing here accepts a decision from outside.** These functions render objects the
engine produced; there is no `dict -> RiskDecisionRecord` direction anywhere in the
codebase. That is the serialisation half of Phase 9 brief §26: a client cannot forge
an approval, substitute an `inputs_digest` or extend an `approved_until`, because
there is no code path that would read one from a request body.

Every envelope states whether the decision was produced by a real evaluation. A
number rendered from an unevaluated pass-through must never look like a risk verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from oipulse.trading.risk.decision import LimitStatus, RiskDecisionRecord
from oipulse.trading.risk.policy import RiskPolicy
from oipulse.trading.risk.state import RiskState

__all__ = [
    "decision_to_dict",
    "decisions_to_dict",
    "limit_status_to_dict",
    "policy_to_dict",
    "state_to_dict",
]


def policy_to_dict(policy: RiskPolicy) -> dict[str, Any]:
    """The policy, with a count of how many limits are actually in force.

    `configured_limits` is surfaced because a policy with three limits set and
    twenty unset looks, in a limits blob, much like one with twenty-three set.
    `18` Phase 9 names over-permissive defaults as the risk; this is the number an
    operator reads to notice one.
    """
    return {
        "data": policy.as_dict(),
        "meta": {
            "policy_digest": policy.policy_digest,
            "configured_limits": policy.limits.configured_count(),
        },
    }


def state_to_dict(state: RiskState) -> dict[str, Any]:
    """The evaluated snapshot, with its content address."""
    return {
        "data": state.as_dict(),
        "meta": {
            "risk_state_ref": state.risk_state_ref,
            "as_of": state.as_of.isoformat(),
            "knowledge_time": state.knowledge_time.isoformat(),
        },
    }


def decision_to_dict(decision: RiskDecisionRecord, *, at: datetime | None = None) -> dict[str, Any]:
    """One decision, with its authorization status resolved at `at`.

    `authorization_status` is derived rather than stored, because a decision is
    immutable and does not become `EXPIRED` — it simply stops being actionable.
    Rendering it requires a time, and when none is given the field is omitted
    rather than defaulted to now: there is no "now" in this system.
    """
    body = decision.as_dict()
    meta: dict[str, Any] = {
        "risk_decision_id": decision.risk_decision_id,
        "evaluated": decision.evaluated,
        "is_approved": decision.is_approved,
        "breach_count": len(decision.breaches()),
        "unevaluable_count": len(decision.unevaluable()),
        "policy": f"{decision.policy_id}@v{decision.policy_version}",
    }
    if at is not None:
        meta["authorization_status"] = decision.authorization_status(at).value
        meta["actionable"] = decision.is_actionable_at(at)
        meta["as_at"] = at.isoformat()
    return {"data": body, "meta": meta}


def decisions_to_dict(
    decisions: Sequence[RiskDecisionRecord], *, at: datetime | None = None
) -> dict[str, Any]:
    """The full appended sequence for an intent (`11` §3).

    The sequence, never the latest verdict alone: re-evaluation is the reason the
    sequence exists, and an endpoint returning only the most recent decision would
    hide exactly the history an audit needs.
    """
    return {
        "data": [decision_to_dict(d, at=at)["data"] for d in decisions],
        "meta": {
            "count": len(decisions),
            "intent_id": decisions[0].intent_id if decisions else None,
            "evaluated": any(d.evaluated for d in decisions),
        },
    }


def limit_status_to_dict(decision: RiskDecisionRecord, policy: RiskPolicy) -> dict[str, Any]:
    """Utilization against every limit (`12-API_SPEC.md` §205: `/risk/status`).

    Grouped by status so the three kinds of "not breached" stay distinguishable:
    passed, not configured, and not evaluable. A status page that showed only a
    green count would report an account with twenty unconfigured limits as
    comfortably within all of them.
    """
    by_status: dict[str, list[dict[str, Any]]] = {s.value: [] for s in LimitStatus}
    for limit in decision.limits_evaluated:
        by_status[limit.status.value].append(limit.as_dict())
    return {
        "data": {
            "policy": policy.label,
            "policy_digest": policy.policy_digest,
            "limits": [limit.as_dict() for limit in decision.limits_evaluated],
            "by_status": by_status,
        },
        "meta": {
            "evaluated": decision.evaluated,
            "passed": len(by_status[LimitStatus.PASSED.value]),
            "breached": len(by_status[LimitStatus.BREACHED.value]),
            # Surfaced beside the others rather than folded into "fine".
            "not_configured": len(by_status[LimitStatus.NOT_CONFIGURED.value]),
            "not_evaluable": len(by_status[LimitStatus.NOT_EVALUABLE.value]),
        },
    }
