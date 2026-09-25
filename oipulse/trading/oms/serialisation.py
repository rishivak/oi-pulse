"""Rendering OMS and reconciliation artifacts for the API. Pure, web-stack-free.

Kept out of `api/` like every other envelope, so the contract stays testable on an
interpreter with no web stack installed.

**Every envelope states the execution posture.** `live_execution_enabled` is always
`False` and always present. Brief §26 forbids misleading "live trading successful"
signals while live execution is disabled; the inverse discipline applies to
responses — a client should never have to infer from silence that this system cannot
place a real order.

**Provider identity is rendered honestly.** A `None` provider order id is emitted as
`null` with an accompanying `provider_identity_known: false`, rather than as an empty
string that a client might display as an id. Brief §8: where provider-side identity
is absent, represent that honestly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from oipulse.trading.brokers.capability import LIVE_EXECUTION_ENABLED
from oipulse.trading.oms.manager import SubmissionOutcome
from oipulse.trading.orders import PaperOrder
from oipulse.trading.reconciliation.model import ReconciliationRun

__all__ = [
    "oms_order_to_dict",
    "oms_orders_to_dict",
    "reconciliation_run_to_dict",
    "reconciliation_runs_to_dict",
    "submission_outcome_to_dict",
]


def _posture(**extra: Any) -> dict[str, Any]:
    """Meta common to every Phase 10 response. Always states the posture."""
    return {
        "live_execution_enabled": LIVE_EXECUTION_ENABLED,
        "execution_mode": "PAPER",
        **extra,
    }


def oms_order_to_dict(order: PaperOrder) -> dict[str, Any]:
    return {
        "data": order.as_dict(),
        "meta": _posture(
            state=order.state.value,
            venue=order.venue.value,
            is_terminal=order.is_terminal,
            is_unresolved=order.is_unresolved,
            # Stated rather than left to be inferred from a null.
            provider_identity_known=order.provider_order_id is not None,
            events=len(order.events),
        ),
    }


def oms_orders_to_dict(orders: Sequence[PaperOrder]) -> dict[str, Any]:
    return {
        "data": [o.as_dict() for o in orders],
        "meta": _posture(
            count=len(orders),
            unresolved=sum(1 for o in orders if o.is_unresolved),
            working=sum(1 for o in orders if o.is_open),
            # Surfaced because an unresolved order blocks its instrument, and a
            # caller wondering why an intent was refused needs to see this number.
            without_provider_identity=sum(1 for o in orders if o.provider_order_id is None),
        ),
    }


def submission_outcome_to_dict(outcome: SubmissionOutcome) -> dict[str, Any]:
    """A submission result, including the refusals and the ambiguities.

    `is_ambiguous` is surfaced explicitly. A client that treated an ambiguous
    outcome as a failure would retry, and retrying from `UNKNOWN` is the single
    most dangerous thing anything in this system can do.
    """
    return {
        "data": outcome.as_dict(),
        "meta": _posture(
            kind=outcome.kind.value,
            submitted=outcome.submitted,
            is_ambiguous=outcome.is_ambiguous,
            resubmission_permitted=False,
        ),
    }


def reconciliation_run_to_dict(run: ReconciliationRun) -> dict[str, Any]:
    """One run, with its evidence and its conclusions."""
    return {
        "data": run.as_dict(),
        "meta": _posture(
            run_id=run.run_id,
            content_digest=run.content_digest,
            is_clean=run.outcome.is_clean,
            needs_attention=len(run.attention_required()),
            # `18` Phase 10 acceptance: readiness is a property of the last run.
            trader_ready=run.outcome.is_clean,
        ),
    }


def reconciliation_runs_to_dict(runs: Sequence[ReconciliationRun]) -> dict[str, Any]:
    return {
        "data": [r.as_dict() for r in runs],
        "meta": _posture(
            count=len(runs),
            clean=sum(1 for r in runs if r.outcome.is_clean),
            latest_clean=bool(runs) and runs[-1].outcome.is_clean,
        ),
    }
