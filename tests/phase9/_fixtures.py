"""SYNTHETIC fixtures for Phase 9. Not recorded from Upstox.

Everything here is constructed, and the module name and this docstring say so.
Recorded provider captures live in `tests/fixtures/recorded/`.

Risk is evaluable with **no trading infrastructure present** (`11-TRADING.md` §3),
so most of these build a `RiskState` directly rather than through a runtime. The
integration fixtures that do use a runtime are marked as such.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from oipulse.backtest.intents import OrderType, Side
from oipulse.trading.intents import (
    IntentConstraints,
    IntentLeg,
    IntentSource,
    TradeIntent,
)
from oipulse.trading.risk import (
    CONSERVATIVE_LIMITS,
    ExposureSnapshot,
    KillSwitchState,
    PositionSnapshot,
    RiskEngine,
    RiskLimits,
    RiskPolicy,
    RiskState,
    SessionWindow,
    StateQualityInput,
    VenueHealth,
)
from tests.phase3._fixtures import at

PROVENANCE = "SYNTHETIC — not recorded from Upstox"

ACCOUNT_ID = "acc-risk"
INSTRUMENT = 1002
OTHER_INSTRUMENT = 1004
UNDERLYING = 100
EXPIRY = 10

#: Wide enough that a test can turn on exactly the limit it cares about. Every
#: limit is still *configured* -- a `None` would report NOT_CONFIGURED and change
#: what the decision says, which is the opposite of an inert baseline.
WIDE_LIMITS = RiskLimits(
    max_position_per_instrument=1_000_000,
    max_position_per_underlying=1_000_000,
    max_position_per_strategy=1_000_000,
    max_order_quantity=1_000_000,
    max_order_notional=Decimal("1e15"),
    max_orders_per_interval=1_000_000,
    max_deployed_capital=Decimal("1e15"),
    max_capital_utilisation=Decimal("1e9"),
    max_leverage=Decimal("1e9"),
    max_daily_loss=Decimal("1e15"),
    max_drawdown=Decimal("1e15"),
    max_loss_per_strategy=Decimal("1e15"),
    max_net_delta=Decimal("1e15"),
    max_gross_delta=Decimal("1e15"),
    max_gross_gamma=Decimal("1e15"),
    max_gross_vega=Decimal("1e15"),
    max_gross_theta=Decimal("1e15"),
    max_underlying_concentration=Decimal("1"),
    max_expiry_concentration=Decimal("1"),
    max_strike_concentration=Decimal("1"),
    reject_unreliable_state=True,
    max_state_staleness=timedelta(hours=24),
    min_state_coverage=Decimal("0"),
    require_healthy_venue=True,
    session=SessionWindow(opens_at=time(0, 0), closes_at=time(23, 59, 59)),
)


#: A fully-populated, all-zero exposure: the honest picture of an empty book.
#:
#: Every field is present, so the default state passes rather than failing closed.
#: `ExposureSnapshot()` with its `None` defaults means "we do not know", which is a
#: refusal -- correct behaviour, but the wrong baseline for a test that wants to
#: isolate one limit.
FLAT_EXPOSURE = ExposureSnapshot(
    gross_notional=Decimal(0),
    net_notional=Decimal(0),
    net_delta=Decimal(0),
    gross_delta=Decimal(0),
    gross_gamma=Decimal(0),
    gross_vega=Decimal(0),
    gross_theta=Decimal(0),
)


def exposure(
    *,
    gross: str = "0",
    net: str = "0",
    net_delta: str = "0",
    gross_delta: str = "0",
    gross_gamma: str = "0",
    gross_vega: str = "0",
    gross_theta: str = "0",
    by_underlying: tuple[tuple[int, Decimal], ...] | None = None,
    by_expiry: tuple[tuple[int, Decimal], ...] | None = None,
    by_strike: tuple[tuple[str, Decimal], ...] | None = None,
) -> ExposureSnapshot:
    """A **complete** exposure snapshot.

    Every field populated, because a partial one correctly makes the corresponding
    limits `NOT_EVALUABLE` and fails the whole evaluation closed. A test isolating
    one limit needs the others evaluable, so the buckets default to a single
    bucket holding the whole gross rather than to empty.
    """
    total = Decimal(gross)
    single_u = by_underlying if by_underlying is not None else ((UNDERLYING, total),)
    single_e = by_expiry if by_expiry is not None else ((EXPIRY, total),)
    single_s = by_strike if by_strike is not None else (("25000", total),)
    return ExposureSnapshot(
        gross_notional=total,
        net_notional=Decimal(net),
        net_delta=Decimal(net_delta),
        gross_delta=Decimal(gross_delta),
        gross_gamma=Decimal(gross_gamma),
        gross_vega=Decimal(gross_vega),
        gross_theta=Decimal(gross_theta),
        by_underlying=single_u,
        by_expiry=single_e,
        by_strike=single_s,
    )


def limits(**overrides: object) -> RiskLimits:
    """`WIDE_LIMITS` with specific limits tightened, so one thing is under test."""
    import dataclasses

    return dataclasses.replace(WIDE_LIMITS, **overrides)  # type: ignore[arg-type]


def runtime_limits(**overrides: object) -> RiskLimits:
    """Limits shaped for the **paper runtime**, not for a hand-built state.

    The greek caps are left unset rather than set wide. The runtime supplies no
    portfolio greeks -- `15` of the brief forbids a second analytics engine -- so a
    configured greek limit is `NOT_EVALUABLE` and fails the evaluation closed. That
    is correct behaviour; a test exercising something else needs those limits
    genuinely absent, which the audit records as a relaxation.
    """
    import dataclasses

    base = dataclasses.replace(
        WIDE_LIMITS,
        max_net_delta=None,
        max_gross_delta=None,
        max_gross_gamma=None,
        max_gross_vega=None,
        max_gross_theta=None,
        session=None,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def policy(
    *,
    policy_id: str = "TEST_POLICY",
    version: int = 1,
    lim: RiskLimits | None = None,
    description: str = "Test policy.",
) -> RiskPolicy:
    return RiskPolicy(
        policy_id=policy_id,
        version=version,
        limits=lim if lim is not None else WIDE_LIMITS,
        description=description,
    )


def engine(
    *,
    lim: RiskLimits | None = None,
    pol: RiskPolicy | None = None,
    validity: timedelta = timedelta(seconds=30),
    allow_resizing: bool = False,
) -> RiskEngine:
    return RiskEngine(
        policy=pol if pol is not None else policy(lim=lim),
        approval_validity=validity,
        allow_resizing=allow_resizing,
    )


def conservative_engine() -> RiskEngine:
    """The shipped conservative defaults, exercised as a real policy."""
    return RiskEngine(
        policy=RiskPolicy(
            policy_id="CONSERVATIVE",
            version=1,
            limits=CONSERVATIVE_LIMITS,
            description="Shipped conservative defaults.",
        )
    )


def position(
    instrument_id: int = INSTRUMENT,
    quantity: int = 0,
    *,
    mark: str | None = "100",
    average_price: str = "100",
    underlying_id: int | None = UNDERLYING,
    expiry_id: int | None = EXPIRY,
    strike: str | None = "25000",
) -> PositionSnapshot:
    return PositionSnapshot(
        instrument_id=instrument_id,
        quantity=quantity,
        average_price=Decimal(average_price),
        underlying_id=underlying_id,
        expiry_id=expiry_id,
        strike=None if strike is None else Decimal(strike),
        mark=None if mark is None else Decimal(mark),
    )


def state(
    *,
    as_of_minute: int = 0,
    knowledge_minute: int | None = None,
    cash: str = "1000000",
    equity: str = "1000000",
    positions: tuple[PositionSnapshot, ...] = (),
    exposure: ExposureSnapshot | None = None,
    quality_status: str = "OK",
    coverage: str | None = "1.0",
    staleness: timedelta | None = timedelta(seconds=1),
    kill_switch: KillSwitchState | None = None,
    marks: tuple[tuple[int, Decimal], ...] | None = None,
    venue: VenueHealth = VenueHealth.PAPER_SIMULATED,
    **kwargs: object,
) -> RiskState:
    """A healthy state by default, so a test that tightens one limit isolates it."""
    resolved_marks = (
        marks
        if marks is not None
        else ((INSTRUMENT, Decimal(100)), (OTHER_INSTRUMENT, Decimal(100)))
    )
    return RiskState(
        account_id=ACCOUNT_ID,
        as_of=at(as_of_minute),
        knowledge_time=at(knowledge_minute if knowledge_minute is not None else as_of_minute),
        cash=Decimal(cash),
        equity=Decimal(equity),
        positions=positions,
        exposure=exposure if exposure is not None else FLAT_EXPOSURE,
        quality=StateQualityInput(
            status=quality_status,
            coverage_ratio=None if coverage is None else Decimal(coverage),
            staleness=staleness,
            market_state_ref="mst_test",
            build_context_id="bc-test",
        ),
        kill_switch=kill_switch if kill_switch is not None else KillSwitchState(),
        marks=resolved_marks,
        instrument_underlying=(
            (INSTRUMENT, UNDERLYING),
            (OTHER_INSTRUMENT, UNDERLYING),
        ),
        venue_health=venue,
        **kwargs,  # type: ignore[arg-type]
    )


def intent(
    *,
    instrument_id: int = INSTRUMENT,
    side: Side = Side.BUY,
    quantity: int = 10,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
    decision_minute: int = 0,
    knowledge_minute: int | None = None,
    legs: tuple[IntentLeg, ...] | None = None,
    strategy_id: str = "TEST_STRATEGY",
    account_id: str = ACCOUNT_ID,
    source_ref: str = "SIG-1",
) -> TradeIntent:
    decided = at(decision_minute)
    return TradeIntent(
        account_id=account_id,
        source=IntentSource.SIGNAL,
        source_ref=source_ref,
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
        knowledge_time=at(knowledge_minute if knowledge_minute is not None else decision_minute),
        decision_time=decided,
        constraints=IntentConstraints(),
        rationale_ref="sig:TEST:1",
        state_checkpoint_ref="ckpt-test",
        build_context_id="bc-test",
        signal_id="TEST_SIGNAL",
        signal_version=1,
        strategy_id=strategy_id,
        strategy_version=2,
        reason="fixture intent",
        evidence_refs=("mv:TEST@v1",),
    )


def evaluate(
    *,
    eng: RiskEngine | None = None,
    it: TradeIntent | None = None,
    st: RiskState | None = None,
    sequence_no: int = 1,
    at_minute: int = 0,
    knowledge_horizon: datetime | None = None,
) -> object:
    """Run one evaluation with everything defaulted to healthy."""
    return (eng or engine()).evaluate(
        it or intent(),
        st or state(),
        sequence_no=sequence_no,
        at=at(at_minute),
        knowledge_horizon=knowledge_horizon,
    )


def breached(decision: object) -> set[str]:
    return {limit.limit_id for limit in decision.breaches()}  # type: ignore[attr-defined]


def unevaluable(decision: object) -> set[str]:
    return {limit.limit_id for limit in decision.unevaluable()}  # type: ignore[attr-defined]


__all__ = [
    "ACCOUNT_ID",
    "EXPIRY",
    "FLAT_EXPOSURE",
    "INSTRUMENT",
    "OTHER_INSTRUMENT",
    "PROVENANCE",
    "UNDERLYING",
    "WIDE_LIMITS",
    "at",
    "breached",
    "conservative_engine",
    "engine",
    "evaluate",
    "exposure",
    "intent",
    "limits",
    "policy",
    "position",
    "runtime_limits",
    "state",
    "unevaluable",
]
