"""SYNTHETIC fixtures for Phase 8. Not recorded from Upstox.

Everything here is constructed, and the module name and this docstring say so.
Recorded provider captures live in `tests/fixtures/recorded/` and nothing in this
file may be described as one.

Built on the Phase 3 fixtures deliberately: paper trading must drive the same builder
live processing uses, so the states these tests trade against are assembled by the
verified Phase 3 path rather than by a test-only construction.
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

from oipulse.backtest.costs import INDIAN_OPTIONS_COSTS, CostModel
from oipulse.backtest.fills import FillModel, SlippageModel
from oipulse.backtest.intents import OrderType, Side
from oipulse.events.inbox import InMemoryInbox
from oipulse.marketdata.store.memory import InMemoryObservationStore
from oipulse.marketstate.checkpoints import StateService
from oipulse.marketstate.state import MarketState
from oipulse.trading.accounts import (
    AccountMode,
    AccountStatus,
    PaperAccount,
    PaperAccountConfig,
)
from oipulse.trading.execution import PaperExecutionModel
from oipulse.trading.intents import (
    IntentConstraints,
    IntentLeg,
    IntentSource,
    TimeInForce,
    TradeIntent,
)
from oipulse.trading.risk import (
    RiskEngine,
    RiskGate,
    RiskLimits,
    RiskPolicy,
    SessionWindow,
)
from oipulse.trading.runtime import PaperTradingRuntime
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

ACCOUNT_ID = "acc-test"
#: The ATM call in the first expiry. Named once so a test can say what it trades.
TARGET = leg_id(0, 1, True)
#: A second instrument, for multi-leg and position-ordering tests.
OTHER = leg_id(0, 2, True)


def observations(
    *,
    minutes: int = 4,
    base_price: int = 100,
    step_price: int = 5,
    with_quotes: bool = True,
    volume: int = 12000,
) -> list[object]:
    """A chain observed once a minute.

    `with_quotes=False` drops bid/ask to simulate a backfill-only period, where the
    fill model must fall back to a declared assumption and flag the fill (`10` §6).
    """
    from dataclasses import replace

    rows: list[object] = []
    for minute in range(minutes):
        observed = at(minute)
        rows.append(index_obs(observed))
        for strike_index in range(3):
            for is_call in (True, False):
                instrument = leg_id(0, strike_index, is_call)
                quote = quote_obs(
                    instrument,
                    observed,
                    ltp=str(base_price + minute * step_price + strike_index),
                    volume=volume,
                )
                if not with_quotes:
                    quote = replace(quote, bid=None, ask=None)
                rows.append(quote)
                rows.append(greeks_obs(instrument, observed))
    return rows


def service(rows: list[object]) -> StateService:
    """A `StateService` over the verified Phase 3 builder."""
    store = InMemoryObservationStore()
    store.append(rows)  # type: ignore[arg-type]
    return StateService(builder(store))


def state_at(rows: list[object], minute: int) -> MarketState:
    return service(rows).get_state(UNDERLYING, at(minute), at(minute))


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
        name="PAPER_TEST",
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


def config(
    *,
    starting_cash: Decimal = Decimal(500000),
    max_order_quantity: int | None = None,
    max_order_notional: Decimal | None = None,
    model: FillModel | None = None,
) -> PaperAccountConfig:
    return PaperAccountConfig(
        starting_cash=starting_cash,
        currency="INR",
        fill_model=model or fill_model(),
        cost_model=INDIAN_OPTIONS_COSTS,
        max_order_quantity=max_order_quantity,
        max_order_notional=max_order_notional,
    )


def account(
    *,
    account_id: str = ACCOUNT_ID,
    status: AccountStatus = AccountStatus.ACTIVE,
    cfg: PaperAccountConfig | None = None,
) -> PaperAccount:
    created = PaperAccount(
        account_id=account_id,
        owner="tester",
        mode=AccountMode.PAPER,
        config=cfg or config(),
    )
    if status is AccountStatus.INITIALISED:
        return created
    active = created.with_status(AccountStatus.ACTIVE, at=at(0))
    return active if status is AccountStatus.ACTIVE else active.with_status(status, at=at(0))


#: A real, explicitly-configured policy that permits what these tests trade.
#:
#: Phase 9 made an *unevaluated* approval non-actionable: an account with no risk
#: policy can no longer place an order, which is the fail-closed barrier the phase
#: exists to build. These Phase 8 tests are about execution, fills and the ledger,
#: not about risk, so they attach a real engine whose limits are wide enough not to
#: interfere -- wide, but genuinely evaluated, never bypassed.
#:
#: Only the limits these tests actually exercise are relaxed. The data, session and
#: kill-switch checks stay on, so a Phase 8 test cannot accidentally pass because
#: risk was switched off.
PERMISSIVE_LIMITS = RiskLimits(
    max_order_quantity=1_000_000,
    max_position_per_instrument=1_000_000,
    max_deployed_capital=Decimal("1e12"),
    max_leverage=Decimal("1e9"),
    max_daily_loss=Decimal("1e12"),
    reject_unreliable_state=True,
    require_healthy_venue=True,
    session=SessionWindow(opens_at=time(0, 0), closes_at=time(23, 59, 59)),
)


def permissive_policy(*, limits: RiskLimits | None = None) -> RiskPolicy:
    return RiskPolicy(
        policy_id="PHASE8_TEST",
        version=1,
        limits=limits or PERMISSIVE_LIMITS,
        description="Wide limits so Phase 8 execution tests are not gated by risk.",
    )


def permissive_engine(*, limits: RiskLimits | None = None) -> RiskEngine:
    return RiskEngine(
        policy=permissive_policy(limits=limits), approval_validity=timedelta(hours=12)
    )


def runtime(
    *,
    acct: PaperAccount | None = None,
    model: FillModel | None = None,
    risk_gate: RiskGate | None = None,
    reject_on_unreliable_state: bool = True,
    unevaluated_risk: bool = False,
) -> PaperTradingRuntime:
    """A paper runtime with a real risk engine unless told otherwise.

    `unevaluated_risk=True` returns a runtime with the Phase 8 pass-through gate,
    which after Phase 9 can no longer authorize an order. Tests that assert that
    barrier use it deliberately.
    """
    resolved = acct or account()
    gate = risk_gate
    if gate is None and not unevaluated_risk:
        gate = permissive_engine()
    return PaperTradingRuntime(
        resolved,
        execution=PaperExecutionModel(
            fill_model=model or resolved.config.fill_model,
            reject_on_unreliable_state=reject_on_unreliable_state,
        ),
        inbox=InMemoryInbox(),
        risk_gate=gate,
    )


def intent(
    *,
    account_id: str = ACCOUNT_ID,
    instrument_id: int = TARGET,
    side: Side = Side.BUY,
    quantity: int = 50,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
    decision_minute: int = 0,
    knowledge_minute: int | None = None,
    legs: tuple[IntentLeg, ...] | None = None,
    constraints: IntentConstraints | None = None,
    time_in_force: TimeInForce = TimeInForce.DAY,
    client_order_intent_id: str = "",
    build_context_id: str = "bc-test",
) -> TradeIntent:
    """A signal-sourced intent with the full audit reference set populated."""
    decided = at(decision_minute)
    known = at(knowledge_minute if knowledge_minute is not None else decision_minute)
    return TradeIntent(
        account_id=account_id,
        source=IntentSource.SIGNAL,
        source_ref="SIG-TEST",
        legs=legs
        or (
            IntentLeg(
                instrument_id=instrument_id,
                side=side,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
            ),
        ),
        market_time=decided,
        knowledge_time=known,
        decision_time=decided,
        time_in_force=time_in_force,
        constraints=constraints or IntentConstraints(),
        rationale_ref="sig:PUT_OI_SURGE:1",
        state_checkpoint_ref="ckpt-test",
        build_context_id=build_context_id,
        signal_id="PUT_OI_SURGE",
        signal_version=1,
        strategy_id="TEST_STRATEGY",
        strategy_version=2,
        client_order_intent_id=client_order_intent_id,
        reason="put OI surge above the declared threshold",
        evidence_refs=("mv:PUT_OI_MIGRATION@v1",),
    )


def submit(
    rt: PaperTradingRuntime,
    rows: list[object],
    it: TradeIntent,
    *,
    decision_minute: int = 0,
    execution_minute: int = 1,
    at_minute: int | None = None,
) -> object:
    """Submit with the decision and execution states kept explicitly separate."""
    svc = service(rows)
    return rt.submit(
        it,
        decision_state=svc.get_state(UNDERLYING, at(decision_minute), at(decision_minute)),
        execution_state=svc.get_state(UNDERLYING, at(execution_minute), at(execution_minute)),
        at=at(at_minute if at_minute is not None else execution_minute),
    )


def marks(rows: list[object], minute: int) -> dict[int, Decimal]:
    state = state_at(rows, minute)
    return {
        int(leg.instrument_id): leg.ltp
        for expiry in state.expiries
        for leg in expiry.legs
        if leg.ltp is not None
    }


__all__ = [
    "ACCOUNT_ID",
    "OTHER",
    "PERMISSIVE_LIMITS",
    "PROVENANCE",
    "TARGET",
    "account",
    "at",
    "config",
    "fill_model",
    "intent",
    "marks",
    "observations",
    "permissive_engine",
    "permissive_policy",
    "runtime",
    "service",
    "state_at",
    "submit",
]
