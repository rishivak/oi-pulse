"""`BacktestRunner` — replay with a strategy attached (`10-REPLAY.md` §1, §7).

> A backtest is replay with a strategy attached and a fill simulator on the end.
> Sharing the mechanism means a backtest cannot diverge from a replay, and neither can
> diverge from live processing — because all three drive the *same* pipeline.

So this runner owns no reconstruction logic. It walks the Phase 7 `ReplayEngine` over
the same timeline a plain replay would use, and adds exactly three things: it asks the
strategy for intents, it passes them through the risk seam, and it schedules them for
simulated execution.

### Decision and execution are separated in time, not just in naming

Brief §10: "A strategy decision is not automatically a fill." An intent decided at step
`T` is filled against the state at the first step at or after `T + latency` — so the
fill price comes from a state the strategy had not seen when it decided. An intent
whose execution time falls past the end of the period **expires unfilled** and is
recorded as such. Filling it against the last available state would be the convenient
answer and a false one: there was no such step.

Within a step the order is fixed and deliberate:

1. execute intents scheduled for this step, applying fills to the ledger,
2. build the strategy context from the now-updated account,
3. ask the strategy for new intents,
4. risk-gate them and schedule them.

The strategy therefore sees the consequences of its earlier decisions before making
new ones, and never sees a fill that has not happened yet.

### Look-ahead is structural

The strategy receives a `StrategyContext` and nothing else — no store, no engine, no
timeline, no clock object. The runner holds those; the strategy cannot reach them. It
is not that look-ahead is checked for, it is that there is no surface through which to
obtain it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from oipulse.backtest.fills import Fill, FillModel, FillOutcome, RejectionReason, simulate_fill
from oipulse.backtest.intents import TradeIntent
from oipulse.backtest.ledger import Ledger, LedgerSnapshot
from oipulse.backtest.result import (
    BacktestExecutionMetadata,
    BacktestResult,
    BacktestStatistics,
)
from oipulse.backtest.risk import UNCONSTRAINED_RISK, RiskGate, RiskVerdict
from oipulse.backtest.strategy import AccountView, Strategy, StrategyContext
from oipulse.marketstate.state import MarketState
from oipulse.replay.engine import ReplayEngine, SteppedState
from oipulse.replay.timeline import ReplayStep, ReplayTimeline
from oipulse.research.access import PointInTimeAccessor

__all__ = ["BacktestRunner", "ScheduledIntent"]


@dataclass(frozen=True, slots=True)
class ScheduledIntent:
    """An approved intent waiting for its execution step.

    Carries the step index it is due at rather than a timestamp, so scheduling is a
    property of the timeline and cannot drift from it.
    """

    intent: TradeIntent
    due_step_index: int


class BacktestRunner:
    """Drives a strategy over a replay timeline. Deterministic end to end."""

    def __init__(
        self,
        engine: ReplayEngine,
        *,
        fill_model: FillModel,
        opening_cash: Decimal,
        risk_gate: RiskGate | None = None,
    ) -> None:
        self._engine = engine
        self._fill_model = fill_model
        self._opening_cash = opening_cash
        self._risk = risk_gate if risk_gate is not None else UNCONSTRAINED_RISK

    # ------------------------------------------------------------------ helpers

    def _execution_step_index(self, timeline: ReplayTimeline, decided_at: datetime) -> int | None:
        """The first step at or after `decided_at + latency`, or None if past the end.

        Returning None rather than clamping to the last step is the whole point: a
        clamped fill would invent an execution that the period does not contain.
        """
        target = decided_at + self._fill_model.latency
        for index, step in enumerate(timeline.steps):
            if step.market_time >= target:
                return index
        return None

    @staticmethod
    def _marks(states: list[MarketState]) -> dict[int, Decimal]:
        """Closing marks from the last states seen. Only observed last-traded prices.

        An instrument with no observed price contributes no mark, so the ledger
        excludes it from unrealized P&L and names it, rather than marking it at cost.
        """
        marks: dict[int, Decimal] = {}
        for state in states:
            for expiry in state.expiries:
                for leg in expiry.legs:
                    if leg.ltp is not None:
                        marks[int(leg.instrument_id)] = leg.ltp
        return marks

    # ---------------------------------------------------------------------- run

    def run(
        self,
        strategy: Strategy,
        timeline: ReplayTimeline,
        accessor: PointInTimeAccessor,
        *,
        from_step_index: int = 0,
        ledger: Ledger | None = None,
        executed_at: datetime | None = None,
    ) -> BacktestResult:
        """Walk the timeline and produce a content-addressed result.

        `ledger` may be supplied to resume a run: brief §17 requires resume to
        continue from carried state rather than from zero, and the ledger is that
        state. `from_step_index` is the matching entry point into the *same* step
        objects, so a resumed run cannot drift from an uninterrupted one.
        """
        context = timeline.context
        book = ledger if ledger is not None else Ledger(opening_cash=self._opening_cash)

        pending: dict[int, list[ScheduledIntent]] = defaultdict(list)
        outcomes: list[FillOutcome] = []
        fills: list[Fill] = []
        intents_generated = 0
        risk_rejected = 0
        expired = 0
        # Equity high-water mark, for drawdown. Seeded with opening equity so a run
        # that only ever loses money still reports a drawdown from its start.
        peak_equity = book.opening_cash
        max_drawdown = Decimal(0)
        last_states: list[MarketState] = []
        coverage_warnings: list[str] = []

        steps = timeline.steps[from_step_index:]
        for offset, step in enumerate(steps):
            index = from_step_index + offset
            stepped = [
                self._engine.state_at(context, step, underlying_id)
                for underlying_id in sorted(context.underlying_ids)
            ]
            last_states = [s.state for s in stepped]
            by_instrument = {s.underlying_id: s for s in stepped}

            # 1. Execute what was decided earlier and is due now.
            for scheduled in pending.pop(index, []):
                outcome = self._execute(scheduled.intent, stepped, book)
                outcomes.append(outcome)
                if outcome.fill is not None:
                    fills.append(outcome.fill)

            # Drawdown is measured after fills, on the equity the run actually had.
            marks = self._marks(last_states)
            snapshot = book.snapshot(as_of=step.market_time, marks=marks)
            peak_equity = max(peak_equity, snapshot.equity)
            max_drawdown = max(max_drawdown, peak_equity - snapshot.equity)

            # 2-4. Decide, gate, schedule.
            for underlying_id in sorted(by_instrument):
                intents = self._decide(
                    strategy, by_instrument[underlying_id], accessor, book, step, context.run_id
                )
                intents_generated += len(intents)
                for intent in intents:
                    decision = self._risk.evaluate(intent)
                    if decision.verdict is RiskVerdict.REJECTED:
                        risk_rejected += 1
                        continue
                    due = self._execution_step_index(timeline, intent.decision_time)
                    if due is None:
                        expired += 1
                        continue
                    pending[due].append(ScheduledIntent(intent=intent, due_step_index=due))

        if expired:
            coverage_warnings.append(
                f"{expired} intent(s) were decided too close to the end of the period for "
                f"the modelled {self._fill_model.latency.total_seconds()}s latency to reach "
                "an execution step; they expired unfilled rather than being filled against "
                "the final state"
            )
        still_pending = sum(len(v) for v in pending.values())
        if still_pending:
            coverage_warnings.append(
                f"{still_pending} approved intent(s) were still scheduled when the run "
                "ended and were never executed"
            )

        final_time = timeline.steps[-1].market_time if timeline.steps else context.period.end
        final = book.snapshot(as_of=final_time, marks=self._marks(last_states))

        return self._result(
            strategy=strategy,
            context_digest=context.content_digest,
            build_context_id=context.build_context_id,
            run_id=context.run_id,
            ledger_snapshot=final,
            fills=tuple(fills),
            outcomes=tuple(outcomes),
            intents_generated=intents_generated,
            risk_rejected=risk_rejected,
            max_drawdown=max_drawdown,
            coverage_warnings=tuple(coverage_warnings),
            executed_at=executed_at,
        )

    # ------------------------------------------------------------------ pieces

    def _decide(
        self,
        strategy: Strategy,
        stepped: SteppedState,
        accessor: PointInTimeAccessor,
        book: Ledger,
        step: ReplayStep,
        run_id: str,
    ) -> list[TradeIntent]:
        """Ask the strategy, with a context narrowed to this decision point."""
        account = AccountView(
            cash=book.cash,
            positions=tuple((p.instrument_id, p.quantity) for p in book.positions()),
        )
        ctx = StrategyContext.build(
            run_id=run_id,
            state=stepped.state,
            accessor=accessor,
            account=account,
            decision_time=step.market_time,
            knowledge_horizon=step.knowledge_horizon,
        )
        return list(strategy.on_state(ctx))

    def _execute(
        self, intent: TradeIntent, stepped: list[SteppedState], book: Ledger
    ) -> FillOutcome:
        """Simulate one intent against the state carrying its instrument.

        The instrument may belong to any of the underlyings being replayed, so each
        state is tried in a deterministic order. A `NO_PRICE_AVAILABLE` from one
        state is not a rejection until every state has been asked.
        """
        last: FillOutcome | None = None
        for candidate in stepped:
            outcome = simulate_fill(intent, candidate.state, self._fill_model)
            if outcome.fill is not None:
                book.apply(outcome.fill)
                return outcome
            if outcome.rejection is not RejectionReason.NO_PRICE_AVAILABLE:
                # A real rejection (stale, unmarketable, no liquidity) is an answer;
                # only "not in this state" is worth asking another state about.
                return outcome
            last = outcome
        return last or FillOutcome(
            intent_id=intent.intent_id,
            fill=None,
            rejection=RejectionReason.NO_PRICE_AVAILABLE,
            detail="no state was available at the execution step",
        )

    def _result(
        self,
        *,
        strategy: Strategy,
        context_digest: str,
        build_context_id: str,
        run_id: str,
        ledger_snapshot: LedgerSnapshot,
        fills: tuple[Fill, ...],
        outcomes: tuple[FillOutcome, ...],
        intents_generated: int,
        risk_rejected: int,
        max_drawdown: Decimal,
        coverage_warnings: tuple[str, ...],
        executed_at: datetime | None,
    ) -> BacktestResult:
        reasons: dict[str, int] = defaultdict(int)
        for outcome in outcomes:
            if outcome.rejection is not None:
                reasons[outcome.rejection.value] += 1

        assumption_based_fills = sum(1 for f in fills if f.assumption_based)
        statistics = BacktestStatistics(
            intents_generated=intents_generated,
            intents_rejected_by_risk=risk_rejected,
            orders_submitted=len(outcomes),
            fills=len(fills),
            partial_fills=sum(1 for f in fills if f.is_partial),
            rejections=sum(1 for o in outcomes if o.rejection is not None),
            assumption_based_fills=assumption_based_fills,
            rejection_reasons=tuple(sorted(reasons.items())),
            gross_pnl=ledger_snapshot.gross_pnl,
            fees=ledger_snapshot.fees,
            slippage_cost=ledger_snapshot.slippage_cost,
            net_pnl=ledger_snapshot.net_pnl,
            realized_pnl=ledger_snapshot.realized_pnl,
            unrealized_pnl=ledger_snapshot.unrealized_pnl,
            max_drawdown=max_drawdown,
            turnover=sum((f.turnover for f in fills), Decimal(0)),
        )

        return BacktestResult(
            run_id=run_id,
            strategy_digest=strategy.spec.content_digest,
            strategy_label=strategy.spec.label,
            replay_digest=context_digest,
            build_context_id=build_context_id,
            fill_model_digest=self._fill_model.content_digest(),
            assumptions=self._fill_model.assumptions(),
            statistics=statistics,
            final_ledger=ledger_snapshot,
            fills=fills,
            outcomes=outcomes,
            assumption_based=assumption_based_fills > 0,
            risk_evaluated=self._risk.evaluates_risk,
            coverage_warnings=coverage_warnings,
            execution=BacktestExecutionMetadata(executed_at=executed_at),
        )
