"""Rendering paper-trading artifacts for the API. Pure, web-stack-free.

Kept out of `api/` for the same reason the MarketState, signal, research, replay and
backtest envelopes are: the shape is a pure function of the artifact, so the contract
stays testable on an interpreter with no web stack installed.

**Every envelope says `PAPER`.** Phase 8 brief §19: "Every endpoint must clearly
identify paper mode." That is enforced here rather than left to each route, so a new
route cannot forget. `_paper_meta` is the single place the claim is made, and the
guard `tools/check_paper_trading_safety.py` asserts no envelope emits any other mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from oipulse.backtest.fills import Fill
from oipulse.trading.accounts import PaperAccount
from oipulse.trading.audit import AuditChain
from oipulse.trading.intents import TradeIntent
from oipulse.trading.journal import JOURNAL_WRITER_IMPLEMENTED
from oipulse.trading.ledger import PaperLedgerSnapshot
from oipulse.trading.orders import PaperOrder
from oipulse.trading.risk import RiskDecisionRecord

__all__ = [
    "account_to_dict",
    "audit_to_dict",
    "fills_to_dict",
    "intent_to_dict",
    "journal_entry_to_dict",
    "journal_page_to_dict",
    "order_to_dict",
    "orders_to_dict",
    "positions_to_dict",
    "snapshot_to_dict",
]

#: The one place the mode claim is made. Every envelope includes it.
_MODE = "PAPER"


def _paper_meta(**extra: Any) -> dict[str, Any]:
    """Meta common to every paper-trading response.

    `live_execution_available` is stated explicitly and is always False. A consumer
    should never have to infer from the absence of a field that this system cannot
    place a real order.
    """
    return {"mode": _MODE, "live_execution_available": False, **extra}


def account_to_dict(account: PaperAccount, *, risk_evaluated: bool = False) -> dict[str, Any]:
    """The account, with the flag that qualifies every number it will ever produce.

    `risk_evaluated` is False while Phase 9 is pending, and it travels on the
    account rather than only on individual trades: a reader looking at an equity
    curve needs to know no limit constrained any of it.
    """
    return {
        "data": account.as_dict(),
        "meta": _paper_meta(
            risk_evaluated=risk_evaluated,
            caveats=[]
            if risk_evaluated
            else [
                "no risk engine evaluated this account's intents (Phase 9 is not "
                "implemented), so no position limit, exposure cap, loss limit or "
                "kill switch constrained any trade"
            ],
        ),
    }


def intent_to_dict(
    intent: TradeIntent, *, decisions: Sequence[RiskDecisionRecord] = ()
) -> dict[str, Any]:
    """The intent with its **full** risk decision sequence (`12` §199, `11` §3).

    The sequence, not the latest verdict: `11` §3 requires each evaluation to append,
    and an endpoint that returned only the most recent would hide the re-evaluations
    that are the entire reason the sequence exists.
    """
    return {
        "data": intent.as_dict(),
        "meta": _paper_meta(
            risk_decisions=[d.as_dict() for d in decisions],
            risk_evaluated=any(d.evaluated for d in decisions),
        ),
    }


def order_to_dict(order: PaperOrder) -> dict[str, Any]:
    return {
        "data": order.as_dict(),
        "meta": _paper_meta(
            state=order.state.value,
            is_terminal=order.is_terminal,
            events=len(order.events),
        ),
    }


def orders_to_dict(orders: Sequence[PaperOrder]) -> dict[str, Any]:
    """All orders, terminal ones included.

    A list filtered to open orders would hide every rejection, and a strategy whose
    orders are mostly rejected is a finding rather than a quiet absence.
    """
    return {
        "data": [o.as_dict() for o in orders],
        "meta": _paper_meta(
            count=len(orders),
            rejected=sum(1 for o in orders if o.reject_reason is not None),
            open=sum(1 for o in orders if o.is_open),
        ),
    }


def fills_to_dict(fills: Sequence[Fill]) -> dict[str, Any]:
    assumption_based = sum(1 for f in fills if f.assumption_based)
    return {
        "data": [f.as_dict() for f in fills],
        "meta": _paper_meta(
            count=len(fills),
            # Non-zero means some prices came from an assumed spread rather than an
            # observed quote, and the P&L is not comparable with a full-fidelity run.
            assumption_based_fills=assumption_based,
        ),
    }


def positions_to_dict(snapshot: PaperLedgerSnapshot) -> dict[str, Any]:
    return {
        "data": [p.as_dict() for p in snapshot.positions],
        "meta": _paper_meta(
            account_id=snapshot.account_id,
            as_of=snapshot.as_of.isoformat(),
            count=len(snapshot.positions),
            unmarked_instruments=list(snapshot.inner.unmarked_instruments),
        ),
    }


def snapshot_to_dict(snapshot: PaperLedgerSnapshot) -> dict[str, Any]:
    """Cash, equity and P&L, with the components kept separate (`14` of the brief)."""
    return {
        "data": snapshot.as_dict(),
        "meta": _paper_meta(
            account_id=snapshot.account_id,
            as_of=snapshot.as_of.isoformat(),
            # Named, so a reader knows the unrealized figure excludes them rather
            # than silently treating them as break-even.
            unmarked_instruments=list(snapshot.inner.unmarked_instruments),
        ),
    }


def audit_to_dict(chain: AuditChain) -> dict[str, Any]:
    """The answerable chain (`11` §10)."""
    return {
        "data": chain.as_dict(),
        "meta": _paper_meta(
            risk_evaluated=chain.risk_evaluated,
            assumption_based=chain.is_assumption_based,
        ),
    }


# --------------------------------------------------------------------- journal


def journal_page_to_dict(page: Any, *, account_id: str) -> dict[str, Any]:
    """A page of journal entries, with why it looks the way it does.

    `meta.availability` is the field that matters. An empty `data` means one of two
    unrelated things — the account recorded no cash movement, or nothing in this
    build writes journal entries at all — and a client that could not tell them
    apart would read a system gap as a quiet account. `12-API_SPEC.md` §3 specifies
    the resource; `oipulse/trading/journal.py` explains why the distinction is
    carried rather than flattened.
    """
    return {
        "data": [entry.as_dict() for entry in page.entries],
        "meta": {
            "account_id": account_id,
            "count": len(page.entries),
            "availability": page.availability.value,
            "next_cursor": page.next_cursor,
            # Stated on every response so the absence is a property of the answer
            # rather than something a reader has to know already.
            "writer_implemented": JOURNAL_WRITER_IMPLEMENTED,
        },
    }


def journal_entry_to_dict(entry: Any) -> dict[str, Any]:
    return {
        "data": entry.as_dict(),
        "meta": {"writer_implemented": JOURNAL_WRITER_IMPLEMENTED},
    }
