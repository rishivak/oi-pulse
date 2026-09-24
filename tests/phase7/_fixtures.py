"""SYNTHETIC fixtures for Phase 7. Not recorded from Upstox.

Everything here is constructed, and the module name and this docstring say so.
Recorded provider captures live in `tests/fixtures/recorded/` and nothing in this file
may be described as one.

Built on the Phase 3 fixtures deliberately: a replay must drive the *same* builder
live processing does (`10-REPLAY.md` §1), so the states these tests replay are
assembled by the verified Phase 3 path rather than by a test-only construction.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from oipulse.backtest.costs import INDIAN_OPTIONS_COSTS, CostModel
from oipulse.backtest.fills import FillModel, SlippageModel
from oipulse.backtest.intents import OrderType, Side, TradeIntent
from oipulse.backtest.strategy import StrategyContext, StrategySpec
from oipulse.marketdata.store.memory import InMemoryObservationStore
from oipulse.marketstate.checkpoints import StateService
from oipulse.replay.context import KnowledgeMode, ReplayContext, ReplayPeriod, StepMode
from oipulse.replay.engine import ReplayEngine
from oipulse.replay.timeline import ReplayTimeline, build_timeline
from tests.phase3._fixtures import (
    UNDERLYING,
    at,
    builder,
    greeks_obs,
    index_obs,
    leg_id,
    quote_obs,
)

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

BUILD_CONTEXT = "bc-phase7-test"
#: The ATM call in the first expiry. Named once so a test can say what it trades.
TARGET = leg_id(0, 1, True)


def observations(
    *,
    minutes: int = 6,
    ingest_lag: timedelta = timedelta(0),
    base_price: int = 100,
    step_price: int = 5,
    with_quotes: bool = True,
) -> list[object]:
    """A chain observed once a minute.

    `ingest_lag` separates `observed_at` from `ingested_at`, which is what makes the
    two-axis visibility rule testable: with a zero lag the rule is trivially
    satisfiable by either axis alone.

    `with_quotes=False` drops bid/ask to simulate a backfill-only period, where the
    fill model must fall back to a declared assumption and flag the run (`10` §6).
    """
    rows: list[object] = []
    for minute in range(minutes):
        observed = at(minute)
        ingested = observed + ingest_lag
        rows.append(index_obs(observed, ingested))
        for strike_index in range(3):
            for is_call in (True, False):
                instrument = leg_id(0, strike_index, is_call)
                quote = quote_obs(
                    instrument,
                    observed,
                    ingested,
                    ltp=str(base_price + minute * step_price + strike_index),
                )
                if not with_quotes:
                    quote = _strip_quote(quote)
                rows.append(quote)
                rows.append(greeks_obs(instrument, observed, ingested))
    return rows


def _strip_quote(quote: object) -> object:
    """Remove bid/ask, keeping the last traded price.

    Models a historical-OI/OHLC backfill window (`06-UPSTOX_INTEGRATION.md` §7),
    where no two-sided quote was ever recorded and inventing one would be fabrication.
    """
    from dataclasses import replace

    return replace(quote, bid=None, ask=None)  # type: ignore[type-var]


def store_with(rows: list[object]) -> InMemoryObservationStore:
    store = InMemoryObservationStore()
    store.append(rows)  # type: ignore[arg-type]
    return store


def service(rows: list[object]) -> StateService:
    """A `StateService` over the verified Phase 3 builder."""
    return StateService(builder(store_with(rows)))


def context(
    *,
    run_id: str = "run-test",
    minutes: int = 6,
    step_mode: StepMode = StepMode.EVENT,
    knowledge_mode: KnowledgeMode = KnowledgeMode.LOCKSTEP,
    pinned: datetime | None = None,
    build_context_id: str = BUILD_CONTEXT,
    interval: timedelta = timedelta(minutes=1),
) -> ReplayContext:
    return ReplayContext(
        run_id=run_id,
        underlying_ids=(int(UNDERLYING),),
        period=ReplayPeriod(start=at(0), end=at(minutes - 1)),
        build_context_id=build_context_id,
        step_mode=step_mode,
        knowledge_mode=knowledge_mode,
        pinned_knowledge_horizon=pinned,
        interval=interval,
    )


def timeline(rows: list[object], ctx: ReplayContext | None = None) -> ReplayTimeline:
    return build_timeline(ctx or context(), rows)  # type: ignore[arg-type]


def engine(rows: list[object]) -> ReplayEngine:
    return ReplayEngine(service(rows))


def fill_model(
    *,
    latency: timedelta = timedelta(seconds=60),
    slippage_model: SlippageModel = SlippageModel.TOUCH,
    slippage_parameter: Decimal = Decimal("0"),
    assumed_spread_fraction: Decimal = Decimal("0.01"),
    partial_fills_enabled: bool = False,
    max_participation_rate: Decimal = Decimal("0.1"),
    rejection_rate: Decimal = Decimal("0"),
    reject_on_stale_quote: bool = False,
    cost_model: CostModel = INDIAN_OPTIONS_COSTS,
) -> FillModel:
    """Every parameter named. There is no default `FillModel` anywhere in the code."""
    return FillModel(
        name="TEST_FILL_MODEL",
        version=1,
        latency=latency,
        slippage_model=slippage_model,
        slippage_parameter=slippage_parameter,
        assumed_spread_fraction=assumed_spread_fraction,
        partial_fills_enabled=partial_fills_enabled,
        max_participation_rate=max_participation_rate,
        rejection_rate=rejection_rate,
        reject_on_stale_quote=reject_on_stale_quote,
        cost_model=cost_model,
    )


class BuyOnceStrategy:
    """Buys a fixed quantity at the first step it sees, then does nothing."""

    def __init__(self, *, quantity: int = 50, instrument_id: int = TARGET) -> None:
        self.spec = StrategySpec(
            strategy_id="BUY_ONCE",
            version=1,
            definition="Buy a fixed quantity at the first step, then hold.",
            config=(("quantity", str(quantity)),),
        )
        self._quantity = quantity
        self._instrument = instrument_id
        self.seen: list[datetime] = []
        self._done = False

    def on_state(self, ctx: StrategyContext) -> list[TradeIntent]:
        self.seen.append(ctx.now)
        if self._done:
            return []
        self._done = True
        return [
            TradeIntent(
                run_id=ctx.run_id,
                strategy_id=self.spec.strategy_id,
                strategy_version=self.spec.version,
                instrument_id=self._instrument,
                side=Side.BUY,
                quantity=self._quantity,
                order_type=OrderType.MARKET,
                decision_time=ctx.now,
                knowledge_horizon=ctx.knowledge_horizon,
                reason="fixture strategy",
            )
        ]


class RecordingStrategy:
    """Records the account it was shown at each step. Places no orders."""

    def __init__(self) -> None:
        self.spec = StrategySpec(
            strategy_id="RECORDER", version=1, definition="Observes and records."
        )
        self.contexts: list[StrategyContext] = []

    def on_state(self, ctx: StrategyContext) -> list[TradeIntent]:
        self.contexts.append(ctx)
        return []
