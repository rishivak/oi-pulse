"""Rendering portfolio artifacts for the API. Pure, web-stack-free.

Two disciplines run through every envelope here.

**Both times, always.** Brief §26: expose `market_time` and `knowledge_time` where
required, and do not expose latest-knowledge shortcuts for historical requests. Every
portfolio response carries both, because a valuation at T on knowledge to K is a
different object from one at T on everything now known, and a client given only T
cannot tell which it received.

**The residual and the completeness flag are never optional.** A P&L total travels
with its residual; a valuation travels with `is_complete` and the instruments it
could not price. Omitting either when it happens to be favourable is how a partial
book comes to read as a whole one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from oipulse.trading.portfolio.attribution import AttributionResult, AttributionSlice
from oipulse.trading.portfolio.positions import PortfolioPosition
from oipulse.trading.portfolio.reconciliation import PositionReconciliationRun
from oipulse.trading.portfolio.snapshot import PortfolioSnapshot
from oipulse.trading.portfolio.valuation import ValuationResult

__all__ = [
    "attribution_to_dict",
    "greeks_to_dict",
    "pnl_to_dict",
    "position_reconciliation_to_dict",
    "positions_to_dict",
    "slices_to_dict",
    "snapshot_to_dict",
    "valuation_to_dict",
]


def _times(market_time: Any, knowledge_time: Any) -> dict[str, Any]:
    """Both times on every response (brief §26). Never one without the other."""
    return {
        "market_time": market_time.isoformat(),
        "knowledge_time": knowledge_time.isoformat(),
    }


def snapshot_to_dict(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    return {
        "data": snapshot.as_dict(),
        "meta": {
            **_times(snapshot.market_time, snapshot.knowledge_time),
            "content_digest": snapshot.content_digest,
            "build_context_id": snapshot.build_context_id,
            # The honesty flag, beside every total it qualifies.
            "is_complete": snapshot.is_complete,
            "unvalued_instruments": [
                p.position.key.instrument_id for p in snapshot.valuation.unvalued
            ],
            "margin_basis": snapshot.margin_basis,
        },
    }


def positions_to_dict(
    positions: Sequence[PortfolioPosition], *, market_time: Any, knowledge_time: Any
) -> dict[str, Any]:
    return {
        "data": [p.as_dict() for p in positions],
        "meta": {
            **_times(market_time, knowledge_time),
            "count": len(positions),
            "open": sum(1 for p in positions if p.quantity != 0),
            # Surfaced because a position without economics cannot be valued, and
            # a caller reading a total needs to know how many were excluded.
            "without_economics": sum(1 for p in positions if p.economics is None),
        },
    }


def valuation_to_dict(valuation: ValuationResult) -> dict[str, Any]:
    return {
        "data": valuation.as_dict(),
        "meta": {
            **_times(valuation.market_time, valuation.knowledge_time),
            "build_context_id": valuation.build_context_id,
            "market_state_ref": valuation.market_state_ref,
            "state_quality": valuation.state_quality,
            "is_complete": valuation.is_complete,
            "valued": len(valuation.valued),
            "unvalued": len(valuation.unvalued),
        },
    }


def pnl_to_dict(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    """Gross, fees and net kept separate all the way out (brief §17)."""
    return {
        "data": {
            **snapshot.returns.as_dict(),
            "equity": str(snapshot.equity),
            "drawdown": str(snapshot.drawdown),
        },
        "meta": {
            **_times(snapshot.market_time, snapshot.knowledge_time),
            "is_complete": snapshot.is_complete,
            # Named explicitly so no client mistakes it for TWR or MWR.
            "return_methodology": "SIMPLE_PERIOD",
        },
    }


def greeks_to_dict(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    """Portfolio greeks, with how many positions actually contributed.

    A total aggregated over three of ten positions is not a portfolio greek, and
    `is_complete` on the greeks block is what says so.
    """
    return {
        "data": snapshot.greeks.as_dict(),
        "meta": {
            **_times(snapshot.market_time, snapshot.knowledge_time),
            "is_complete": snapshot.greeks.is_complete,
            "positions_included": snapshot.greeks.positions_included,
            "positions_total": snapshot.greeks.positions_total,
        },
    }


def attribution_to_dict(
    result: AttributionResult, *, market_time: Any, knowledge_time: Any
) -> dict[str, Any]:
    """One decomposition. The residual is in `meta`, not buried in the components."""
    return {
        "data": result.as_dict(),
        "meta": {
            **_times(market_time, knowledge_time),
            "method": result.method,
            "method_version": result.method_version,
            # Promoted to meta deliberately: `18` Phase 11 says report it
            # prominently, and a number nested three levels down is not prominent.
            "residual": str(result.residual),
            "residual_fraction": (
                None if result.residual_fraction is None else str(result.residual_fraction)
            ),
            "reconciles": result.reconciles,
            "uncomputed_components": [c.value for c in result.uncomputed_components],
        },
    }


def slices_to_dict(
    slices: Mapping[str, AttributionSlice] | Sequence[AttributionSlice],
    *,
    market_time: Any,
    knowledge_time: Any,
) -> dict[str, Any]:
    """A sliced attribution. Unattributed performance is listed, never dropped."""
    values = list(slices.values()) if isinstance(slices, Mapping) else list(slices)
    return {
        "data": [s.as_dict() for s in values],
        "meta": {
            **_times(market_time, knowledge_time),
            "count": len(values),
            # Brief §15: performance with no identifiable owner is surfaced.
            "unattributed_slices": sum(1 for s in values if s.is_unattributed),
            "total_residual": str(
                sum((s.result.residual for s in values), start=values[0].result.residual * 0)
                if values
                else 0
            ),
        },
    }


def position_reconciliation_to_dict(run: PositionReconciliationRun) -> dict[str, Any]:
    return {
        "data": run.as_dict(),
        "meta": {
            "run_id": run.run_id,
            "content_digest": run.content_digest,
            "as_of": run.as_of.isoformat(),
            "is_clean": run.is_clean,
            "needs_attention": len(run.needs_attention),
            "corrections_applied": run.corrections_applied,
        },
    }
