"""`BacktestResult` — the content-addressed backtest artifact.

`10-REPLAY.md` §6:

> `BacktestResult` prints the assumption set alongside every number. A backtest is a
> statement about specific history under stated assumptions, never an expectation.

Three things follow, and each is structural rather than a convention to remember.

**The assumption set is part of the artifact, not a footnote.** The fill model's full
parameter set, the cost schedule, whether any fill was assumption-based, and whether a
risk engine evaluated anything at all are all fields — and all inside the content hash.
Changing a slippage parameter produces a different result identity, so two runs cannot
be compared as like-for-like when they were not.

**Execution metadata is excluded from identity** by living in a separate object that
the hash function never receives (the same device as `09-RESEARCH.md` §7's
`StudyResult`). A field added to `ExecutionMetadata` later cannot leak into semantic
identity by accident, because the exclusion is a type boundary rather than a
maintained list.

**Limits travel with the numbers.** `10` §9 states the honest limits of a replay
backtest; `coverage_warnings` and the assumption flags carry the run-specific ones onto
the artifact, so a result read six months later still says what it rests on.

Deliberately absent: any "expected return", forward projection, or annualised
extrapolation from the sample. `10` §9 -- fill simulation "is a model ... never
presented as what would certainly have happened".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from oipulse.backtest.fills import Fill, FillOutcome
from oipulse.backtest.ledger import LedgerSnapshot

__all__ = [
    "BacktestExecutionMetadata",
    "BacktestResult",
    "BacktestStatistics",
    "backtest_digest",
]


def backtest_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class BacktestStatistics:
    """Descriptive statistics over what happened. No projection, no annualisation.

    Every field is a count or a sum over the actual fills of the actual run. A ratio
    that would require assuming the future -- expectancy, annualised return, a Sharpe
    computed from a handful of trades -- is not here, because `10` §9 forbids
    presenting the model's output as what would certainly have happened.
    """

    intents_generated: int = 0
    intents_rejected_by_risk: int = 0
    orders_submitted: int = 0
    fills: int = 0
    partial_fills: int = 0
    rejections: int = 0
    assumption_based_fills: int = 0
    #: Rejections grouped by reason, sorted for determinism.
    rejection_reasons: tuple[tuple[str, int], ...] = ()
    gross_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    slippage_cost: Decimal = Decimal(0)
    net_pnl: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)
    turnover: Decimal = Decimal(0)

    @property
    def fill_rate(self) -> float:
        """Fills per submitted order. Zero orders means zero, not a division error."""
        return 0.0 if self.orders_submitted == 0 else self.fills / self.orders_submitted

    def as_dict(self) -> dict[str, Any]:
        return {
            "intents_generated": self.intents_generated,
            "intents_rejected_by_risk": self.intents_rejected_by_risk,
            "orders_submitted": self.orders_submitted,
            "fills": self.fills,
            "partial_fills": self.partial_fills,
            "rejections": self.rejections,
            "assumption_based_fills": self.assumption_based_fills,
            "rejection_reasons": dict(self.rejection_reasons),
            "fill_rate": self.fill_rate,
            "gross_pnl": str(self.gross_pnl),
            "fees": str(self.fees),
            "slippage_cost": str(self.slippage_cost),
            "net_pnl": str(self.net_pnl),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "max_drawdown": str(self.max_drawdown),
            "turnover": str(self.turnover),
        }


@dataclass(frozen=True, slots=True)
class BacktestExecutionMetadata:
    """When the run happened. **Never passed to the hash function.**

    Same device as `09` §7: the exclusion is enforced by the type boundary, so a
    field added here later cannot quietly become part of result identity.
    """

    executed_at: datetime | None = None
    duration: timedelta | None = None
    engine_version: str = "1.0.0"
    host: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "executed_at": None if self.executed_at is None else self.executed_at.isoformat(),
            "duration_seconds": (None if self.duration is None else self.duration.total_seconds()),
            "engine_version": self.engine_version,
            "host": self.host,
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """An immutable backtest artifact: numbers and the assumptions behind them."""

    run_id: str
    strategy_digest: str
    strategy_label: str
    #: The replay context's digest — period, universe, build context, knowledge mode.
    replay_digest: str
    build_context_id: str
    fill_model_digest: str
    #: The full, printable assumption set (`10` §6).
    assumptions: dict[str, Any]
    statistics: BacktestStatistics
    final_ledger: LedgerSnapshot
    fills: tuple[Fill, ...] = ()
    outcomes: tuple[FillOutcome, ...] = ()
    #: True if **any** fill priced off an assumed spread rather than an observed quote.
    assumption_based: bool = False
    #: False whenever no real risk engine evaluated the intents (Phase 9 pending).
    risk_evaluated: bool = False
    #: Run-specific limits: gaps, backfilled periods, unmarked positions.
    coverage_warnings: tuple[str, ...] = ()
    execution: BacktestExecutionMetadata = field(default_factory=BacktestExecutionMetadata)

    @property
    def content_hash(self) -> str:
        """Semantic identity. Excludes execution metadata; includes every assumption.

        Two runs of the same strategy over the same period under the same fill model
        hash identically. Change the slippage parameter, the cost schedule, the build
        context or the strategy config and the hash moves — which is the point: those
        runs are not comparable, and the identity says so before anyone charts them
        side by side.
        """
        return backtest_digest(
            {
                "strategy_digest": self.strategy_digest,
                "replay_digest": self.replay_digest,
                "build_context_id": self.build_context_id,
                "fill_model_digest": self.fill_model_digest,
                "assumptions": self.assumptions,
                "statistics": self.statistics.as_dict(),
                "final_ledger": self.final_ledger.as_dict(),
                "fills": [f.as_dict() for f in self.fills],
                "assumption_based": self.assumption_based,
                "risk_evaluated": self.risk_evaluated,
                "coverage_warnings": list(self.coverage_warnings),
            }
        )

    def caveats(self) -> tuple[str, ...]:
        """Everything a reader must know before believing a number here.

        Assembled rather than stored so it cannot drift from the flags it describes.
        """
        notes: list[str] = []
        if self.assumption_based:
            notes.append(
                f"{self.statistics.assumption_based_fills} fill(s) were priced against an "
                "assumed spread because no bid/ask was observed; these results are not "
                "equivalent to a run over full-fidelity quote data"
            )
        if not self.risk_evaluated:
            notes.append(
                "no risk engine evaluated these intents (Phase 9 is not implemented), so "
                "no position limit, exposure cap or drawdown rule constrained the run"
            )
        if self.final_ledger.unmarked_instruments:
            notes.append(
                "unrealized P&L excludes "
                f"{len(self.final_ledger.unmarked_instruments)} position(s) with no "
                "closing mark; they are named in the ledger snapshot"
            )
        notes.extend(self.coverage_warnings)
        notes.append(
            "fill simulation is a model reported with its assumptions, never a statement "
            "of what would certainly have happened"
        )
        return tuple(notes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "content_hash": self.content_hash,
            "strategy": self.strategy_label,
            "strategy_digest": self.strategy_digest,
            "replay_digest": self.replay_digest,
            "build_context_id": self.build_context_id,
            "fill_model_digest": self.fill_model_digest,
            "assumptions": self.assumptions,
            "statistics": self.statistics.as_dict(),
            "final_ledger": self.final_ledger.as_dict(),
            "fills": [f.as_dict() for f in self.fills],
            "assumption_based": self.assumption_based,
            "risk_evaluated": self.risk_evaluated,
            "caveats": list(self.caveats()),
            "execution": self.execution.as_dict(),
        }
