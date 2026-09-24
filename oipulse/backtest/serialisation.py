"""Rendering backtest artifacts for the API. Pure, web-stack-free.

`10-REPLAY.md` §6: "`BacktestResult` prints the assumption set alongside every number."

That is enforced here rather than left to a client. `result_to_dict` puts the
assumption set, the two honesty flags and the caveat list in `meta`, beside the
statistics — so a client that renders a P&L figure has necessarily also received what
it rests on. A renderer can still choose to hide them, but it cannot fail to be told.

The equity curve is deliberately **not** synthesised from the final result: it is built
from a sequence of ledger snapshots the runner actually produced. Interpolating a curve
between a start and an end point would be inventing the path, which is the same class
of error as interpolating over a replay gap.
"""

from __future__ import annotations

from typing import Any

from oipulse.backtest.fills import Fill, FillOutcome
from oipulse.backtest.ledger import LedgerSnapshot
from oipulse.backtest.result import BacktestResult

__all__ = [
    "equity_curve_to_dict",
    "fill_to_dict",
    "outcome_to_dict",
    "result_to_dict",
    "trades_to_dict",
]


def fill_to_dict(fill: Fill) -> dict[str, Any]:
    return fill.as_dict()


def outcome_to_dict(outcome: FillOutcome) -> dict[str, Any]:
    return outcome.as_dict()


def result_to_dict(result: BacktestResult) -> dict[str, Any]:
    """The full artifact, with its assumptions in `meta` beside the numbers."""
    return {
        "data": result.as_dict(),
        "meta": {
            "content_hash": result.content_hash,
            "run_id": result.run_id,
            "strategy": result.strategy_label,
            "build_context_id": result.build_context_id,
            # The two flags that decide how much weight a reader may put on the
            # numbers. Always present, never inferred from the statistics.
            "assumption_based": result.assumption_based,
            "risk_evaluated": result.risk_evaluated,
            "assumptions": result.assumptions,
            "caveats": list(result.caveats()),
        },
    }


def trades_to_dict(result: BacktestResult) -> dict[str, Any]:
    """Every outcome, filled or not.

    Rejections are included rather than filtered out: a strategy whose orders mostly
    could not be filled is a finding, and a trades endpoint that returned only fills
    would hide it behind a healthy-looking list.
    """
    return {
        "data": [outcome_to_dict(o) for o in result.outcomes],
        "meta": {
            "run_id": result.run_id,
            "fills": len(result.fills),
            "rejections": result.statistics.rejections,
            "partial_fills": result.statistics.partial_fills,
            "assumption_based_fills": result.statistics.assumption_based_fills,
        },
    }


def equity_curve_to_dict(snapshots: tuple[LedgerSnapshot, ...], *, run_id: str) -> dict[str, Any]:
    """The curve as actually measured, point by point. Nothing interpolated.

    `unmarked` travels with each point because a point whose unrealized P&L excludes
    an unmarked position is not comparable with one where everything was marked, and
    a curve that hid the difference would show a spurious step when a mark returned.
    """
    return {
        "data": [
            {
                "as_of": snapshot.as_of.isoformat(),
                "equity": str(snapshot.equity),
                "cash": str(snapshot.cash),
                "realized_pnl": str(snapshot.realized_pnl),
                "unrealized_pnl": str(snapshot.unrealized_pnl),
                "fees": str(snapshot.fees),
                "net_pnl": str(snapshot.net_pnl),
                "unmarked": len(snapshot.unmarked_instruments),
            }
            for snapshot in snapshots
        ],
        "meta": {
            "run_id": run_id,
            "points": len(snapshots),
            "interpolated": False,
        },
    }
