"""The limit checks — pure functions over (intent, state, policy).

`11-TRADING.md` §3:

> Deterministic and ordered; **every limit evaluated and recorded even after the
> first rejection**, so the decision shows the complete picture rather than the first
> failure. Pure with respect to its inputs (intent + portfolio + state + limits),
> making it exhaustively testable.

So each check is a function returning a `LimitEvaluation`, the engine runs all of
them in a declared order, and none short-circuits. A risk report that stopped at the
first breach would tell an operator to fix one number and resubmit into a second
breach they were never shown.

### The four statuses, and why the distinction matters

* `PASSED` — configured, evaluated, within the limit.
* `BREACHED` — configured, evaluated, exceeded. Blocks.
* `NOT_CONFIGURED` — the policy declares no such limit. A **relaxation**, recorded
  so it is audited rather than invisible.
* `NOT_EVALUABLE` — configured, but an input was missing. A **data problem**. It does
  not pass, and the engine's fail-closed rule turns it into a refusal.

Collapsing the last two into `PASSED` is the specific way a risk report comes to
overstate what was checked, so they are separate values and both are stored.

### No substitution

Brief §15: where an input is unavailable, nothing is substituted — not a stale price,
not the latest price, not zero, not a previous value, not an estimate. The check
returns `NOT_EVALUABLE` and says which input was missing.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from oipulse.trading.intents import TradeIntent
from oipulse.trading.risk.decision import LimitEvaluation, LimitStatus, decimal_str
from oipulse.trading.risk.policy import LimitCategory, RiskLimits
from oipulse.trading.risk.state import RiskState

__all__ = ["ALL_CHECKS", "LimitCheck", "evaluate_all"]

#: A check reads the intent, the state and the limits, and returns one evaluation.
LimitCheck = Callable[[TradeIntent, RiskState, RiskLimits], LimitEvaluation]


def _not_configured(category: LimitCategory, limit_id: str) -> LimitEvaluation:
    return LimitEvaluation(
        category=category,
        limit_id=limit_id,
        status=LimitStatus.NOT_CONFIGURED,
        detail="the active policy declares no such limit",
    )


def _not_evaluable(
    category: LimitCategory, limit_id: str, missing: str, limit_value: object = None
) -> LimitEvaluation:
    return LimitEvaluation(
        category=category,
        limit_id=limit_id,
        status=LimitStatus.NOT_EVALUABLE,
        limit_value=None if limit_value is None else str(limit_value),
        detail=(
            f"{missing} is unavailable; the limit is configured but cannot be checked, "
            f"and no value was substituted for it"
        ),
    )


def _compare(
    category: LimitCategory,
    limit_id: str,
    *,
    observed: Decimal | int,
    limit: Decimal | int,
    detail: str = "",
) -> LimitEvaluation:
    """The common shape: observed must not exceed limit. Headroom always reported."""
    breached = Decimal(observed) > Decimal(limit)
    return LimitEvaluation(
        category=category,
        limit_id=limit_id,
        status=LimitStatus.BREACHED if breached else LimitStatus.PASSED,
        limit_value=decimal_str(limit),
        observed_value=decimal_str(observed),
        headroom=decimal_str(Decimal(limit) - Decimal(observed)),
        detail=detail
        or (
            f"observed {observed} against a limit of {limit}"
            if breached
            else f"within the limit ({observed} of {limit})"
        ),
    )


def _intent_quantity(intent: TradeIntent) -> int:
    return intent.total_quantity


def _intent_notional(intent: TradeIntent, state: RiskState) -> Decimal | None:
    """Notional of the intent, from limit prices or marks. None when unpriceable.

    A MARKET leg has no limit price, so its notional needs a mark from the state. If
    the state has none, the whole intent is unpriceable and every notional-based
    limit reports `NOT_EVALUABLE` rather than being computed from the legs that
    happen to be priced.
    """
    total = Decimal(0)
    for leg in intent.legs:
        # A LIMIT leg prices itself. A MARKET leg needs a supplied mark -- and if
        # none exists, the intent is unpriceable rather than partially priced: a
        # notional summed over only the legs that happened to have a price would
        # understate the exposure and read as headroom that is not there.
        price = (
            leg.limit_price if leg.limit_price is not None else state.mark_for(leg.instrument_id)
        )
        if price is None:
            return None
        total += price * Decimal(leg.quantity)
    return total


# --------------------------------------------------------------------- POSITION


def check_position_per_instrument(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    """Resulting absolute position per instrument, including this intent.

    Measured on the *resulting* position, not the current one: a limit that only
    looked at what is already held would let every individual order through while
    the total marched past the cap.
    """
    limit = limits.max_position_per_instrument
    if limit is None:
        return _not_configured(LimitCategory.POSITION, "max_position_per_instrument")

    worst = 0
    for leg in intent.legs:
        resulting = abs(state.position_in(leg.instrument_id) + leg.quantity * leg.side.sign)
        worst = max(worst, resulting)
    return _compare(
        LimitCategory.POSITION, "max_position_per_instrument", observed=worst, limit=limit
    )


def check_position_per_underlying(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_position_per_underlying
    if limit is None:
        return _not_configured(LimitCategory.POSITION, "max_position_per_underlying")

    worst = 0
    for leg in intent.legs:
        # From the supplied grouping, not the position book: an opening trade in
        # an underlying we do not yet hold still belongs to that underlying, and
        # deriving the grouping from held positions would make every first trade
        # in an underlying unevaluable.
        underlying = state.underlying_of(leg.instrument_id)
        current = state.position_in_underlying(underlying)
        if underlying is None or current is None:
            return _not_evaluable(
                LimitCategory.POSITION,
                "max_position_per_underlying",
                "the underlying grouping for this instrument or for a held position",
                limit,
            )
        worst = max(worst, current + leg.quantity)
    return _compare(
        LimitCategory.POSITION, "max_position_per_underlying", observed=worst, limit=limit
    )


def check_position_per_strategy(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_position_per_strategy
    if limit is None:
        return _not_configured(LimitCategory.POSITION, "max_position_per_strategy")
    if not intent.strategy_id:
        return _not_evaluable(
            LimitCategory.POSITION,
            "max_position_per_strategy",
            "the intent's strategy identity",
            limit,
        )
    resulting = abs(state.strategy_position(intent.strategy_id)) + _intent_quantity(intent)
    return _compare(
        LimitCategory.POSITION, "max_position_per_strategy", observed=resulting, limit=limit
    )


# ------------------------------------------------------------------------ ORDER


def check_order_quantity(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_order_quantity
    if limit is None:
        return _not_configured(LimitCategory.ORDER, "max_order_quantity")
    worst = max(leg.quantity for leg in intent.legs)
    return _compare(LimitCategory.ORDER, "max_order_quantity", observed=worst, limit=limit)


def check_order_notional(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_order_notional
    if limit is None:
        return _not_configured(LimitCategory.ORDER, "max_order_notional")
    notional = _intent_notional(intent, state)
    if notional is None:
        return _not_evaluable(
            LimitCategory.ORDER, "max_order_notional", "a price for every leg", limit
        )
    return _compare(LimitCategory.ORDER, "max_order_notional", observed=notional, limit=limit)


def check_order_rate(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    """Orders already placed in the policy's interval, plus this intent's legs."""
    limit = limits.max_orders_per_interval
    if limit is None:
        return _not_configured(LimitCategory.ORDER, "max_orders_per_interval")
    resulting = state.orders_in_interval + len(intent.legs)
    return _compare(
        LimitCategory.ORDER,
        "max_orders_per_interval",
        observed=resulting,
        limit=limit,
        detail=(
            f"{state.orders_in_interval} order(s) already placed in the last "
            f"{limits.orders_interval.total_seconds():.0f}s plus {len(intent.legs)} "
            f"from this intent"
        ),
    )


# ---------------------------------------------------------------------- CAPITAL


def check_deployed_capital(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_deployed_capital
    if limit is None:
        return _not_configured(LimitCategory.CAPITAL, "max_deployed_capital")
    deployed = state.deployed_capital
    if deployed is None:
        return _not_evaluable(
            LimitCategory.CAPITAL, "max_deployed_capital", "gross exposure", limit
        )
    notional = _intent_notional(intent, state)
    if notional is None:
        return _not_evaluable(
            LimitCategory.CAPITAL, "max_deployed_capital", "a price for every leg", limit
        )
    return _compare(
        LimitCategory.CAPITAL, "max_deployed_capital", observed=deployed + notional, limit=limit
    )


def check_capital_utilisation(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_capital_utilisation
    if limit is None:
        return _not_configured(LimitCategory.CAPITAL, "max_capital_utilisation")
    deployed = state.deployed_capital
    if deployed is None:
        return _not_evaluable(
            LimitCategory.CAPITAL, "max_capital_utilisation", "gross exposure", limit
        )
    if state.equity <= 0:
        return _not_evaluable(
            LimitCategory.CAPITAL,
            "max_capital_utilisation",
            "positive equity to divide by",
            limit,
        )
    notional = _intent_notional(intent, state)
    if notional is None:
        return _not_evaluable(
            LimitCategory.CAPITAL, "max_capital_utilisation", "a price for every leg", limit
        )
    return _compare(
        LimitCategory.CAPITAL,
        "max_capital_utilisation",
        observed=(deployed + notional) / state.equity,
        limit=limit,
    )


def check_leverage(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    limit = limits.max_leverage
    if limit is None:
        return _not_configured(LimitCategory.CAPITAL, "max_leverage")
    gross = state.exposure.gross_notional
    if gross is None:
        return _not_evaluable(LimitCategory.CAPITAL, "max_leverage", "gross exposure", limit)
    if state.equity <= 0:
        return _not_evaluable(
            LimitCategory.CAPITAL, "max_leverage", "positive equity to divide by", limit
        )
    notional = _intent_notional(intent, state)
    if notional is None:
        return _not_evaluable(LimitCategory.CAPITAL, "max_leverage", "a price for every leg", limit)
    return _compare(
        LimitCategory.CAPITAL,
        "max_leverage",
        observed=(gross + notional) / state.equity,
        limit=limit,
    )


def check_cash_sufficiency(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    """Buying power against **available** cash, not total.

    Always configured -- an account cannot opt out of being able to pay. Sells are
    excluded from the requirement because they release cash rather than consume it.
    """
    required = Decimal(0)
    for leg in intent.legs:
        if leg.side.sign < 0:
            continue
        price = (
            leg.limit_price if leg.limit_price is not None else state.mark_for(leg.instrument_id)
        )
        if price is None:
            return _not_evaluable(
                LimitCategory.CAPITAL,
                "cash_sufficiency",
                "a price for every buy leg",
                state.available_cash,
            )
        required += price * Decimal(leg.quantity)

    return _compare(
        LimitCategory.CAPITAL,
        "cash_sufficiency",
        observed=required,
        limit=state.available_cash,
        detail=(
            f"requires {required} against {state.available_cash} available "
            f"({state.cash} cash less {state.reserved_cash} reserved)"
        ),
    )


# ------------------------------------------------------------------------- LOSS


def _loss_check(limit: Decimal | None, observed: Decimal, limit_id: str) -> LimitEvaluation:
    if limit is None:
        return _not_configured(LimitCategory.LOSS, limit_id)
    return _compare(LimitCategory.LOSS, limit_id, observed=observed, limit=limit)


def check_daily_loss(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _loss_check(limits.max_daily_loss, state.daily_loss, "max_daily_loss")


def check_drawdown(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _loss_check(limits.max_drawdown, state.drawdown, "max_drawdown")


def check_strategy_loss(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_loss_per_strategy
    if limit is None:
        return _not_configured(LimitCategory.LOSS, "max_loss_per_strategy")
    if not intent.strategy_id:
        return _not_evaluable(
            LimitCategory.LOSS, "max_loss_per_strategy", "the intent's strategy identity", limit
        )
    loss = state.strategy_loss(intent.strategy_id)
    if loss is None:
        # No recorded loss for a strategy that has not traded is genuinely zero,
        # not missing -- distinct from an unavailable input.
        loss = Decimal(0)
    return _compare(LimitCategory.LOSS, "max_loss_per_strategy", observed=loss, limit=limit)


# --------------------------------------------------------------------- EXPOSURE


def _exposure_check(
    limit: Decimal | None, observed: Decimal | None, limit_id: str, missing: str
) -> LimitEvaluation:
    if limit is None:
        return _not_configured(LimitCategory.EXPOSURE, limit_id)
    if observed is None:
        return _not_evaluable(LimitCategory.EXPOSURE, limit_id, missing, limit)
    return _compare(LimitCategory.EXPOSURE, limit_id, observed=abs(observed), limit=limit)


def check_net_delta(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _exposure_check(
        limits.max_net_delta, state.exposure.net_delta, "max_net_delta", "portfolio net delta"
    )


def check_gross_delta(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _exposure_check(
        limits.max_gross_delta,
        state.exposure.gross_delta,
        "max_gross_delta",
        "portfolio gross delta",
    )


def check_gross_gamma(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _exposure_check(
        limits.max_gross_gamma,
        state.exposure.gross_gamma,
        "max_gross_gamma",
        "portfolio gross gamma",
    )


def check_gross_vega(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _exposure_check(
        limits.max_gross_vega,
        state.exposure.gross_vega,
        "max_gross_vega",
        "portfolio gross vega",
    )


def check_gross_theta(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    return _exposure_check(
        limits.max_gross_theta,
        state.exposure.gross_theta,
        "max_gross_theta",
        "portfolio gross theta",
    )


# ---------------------------------------------------------------- CONCENTRATION


def _concentration(
    buckets: tuple[tuple[object, Decimal], ...],
    gross: Decimal | None,
    limit: Decimal | None,
    limit_id: str,
    missing: str,
) -> LimitEvaluation:
    if limit is None:
        return _not_configured(LimitCategory.CONCENTRATION, limit_id)
    if gross is None:
        return _not_evaluable(LimitCategory.CONCENTRATION, limit_id, missing, limit)
    if gross <= 0:
        # An empty book is not concentrated. Checked *before* the bucket test,
        # because an empty book legitimately has no buckets either -- treating
        # that as unevaluable would fail-closed every account that holds nothing,
        # which is the one book that is definitely within every concentration cap.
        return LimitEvaluation(
            category=LimitCategory.CONCENTRATION,
            limit_id=limit_id,
            status=LimitStatus.PASSED,
            limit_value=str(limit),
            observed_value="0",
            detail="no gross exposure, so nothing is concentrated",
        )
    if not buckets:
        # Priced exposure exists but nothing says how it is grouped: a real gap.
        return _not_evaluable(LimitCategory.CONCENTRATION, limit_id, missing, limit)
    largest = max(value for _, value in buckets)
    return _compare(LimitCategory.CONCENTRATION, limit_id, observed=largest / gross, limit=limit)


def check_underlying_concentration(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    return _concentration(
        state.exposure.by_underlying,
        state.exposure.gross_notional,
        limits.max_underlying_concentration,
        "max_underlying_concentration",
        "per-underlying exposure",
    )


def check_expiry_concentration(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    return _concentration(
        state.exposure.by_expiry,
        state.exposure.gross_notional,
        limits.max_expiry_concentration,
        "max_expiry_concentration",
        "per-expiry exposure",
    )


def check_strike_concentration(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    return _concentration(
        state.exposure.by_strike,
        state.exposure.gross_notional,
        limits.max_strike_concentration,
        "max_strike_concentration",
        "per-strike exposure",
    )


# ------------------------------------------------------------------------- DATA


def check_state_quality(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    """`11` §3: "the one most systems omit and it belongs here".

    Trading on a `MarketState` known to be unreliable is a risk decision, and this
    is where that judgement is made explicit and recorded.
    """
    if not limits.reject_unreliable_state:
        return _not_configured(LimitCategory.DATA, "reject_unreliable_state")
    status = state.quality.status.upper()
    if status == "UNKNOWN":
        return _not_evaluable(
            LimitCategory.DATA, "reject_unreliable_state", "the market state's quality", True
        )
    breached = status == "UNRELIABLE"
    return LimitEvaluation(
        category=LimitCategory.DATA,
        limit_id="reject_unreliable_state",
        status=LimitStatus.BREACHED if breached else LimitStatus.PASSED,
        limit_value="not UNRELIABLE",
        observed_value=status,
        detail=(
            "the market state is marked UNRELIABLE" if breached else f"state quality is {status}"
        ),
    )


def check_state_staleness(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    limit = limits.max_state_staleness
    if limit is None:
        return _not_configured(LimitCategory.DATA, "max_state_staleness")
    staleness = state.quality.staleness
    if staleness is None:
        return _not_evaluable(
            LimitCategory.DATA,
            "max_state_staleness",
            "the market state's staleness",
            limit.total_seconds(),
        )
    return _compare(
        LimitCategory.DATA,
        "max_state_staleness",
        observed=Decimal(str(staleness.total_seconds())),
        limit=Decimal(str(limit.total_seconds())),
        detail=f"state is {staleness.total_seconds():.1f}s stale",
    )


def check_state_coverage(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    """Coverage below the floor is a breach. Note the inverted comparison."""
    limit = limits.min_state_coverage
    if limit is None:
        return _not_configured(LimitCategory.DATA, "min_state_coverage")
    coverage = state.quality.coverage_ratio
    if coverage is None:
        return _not_evaluable(
            LimitCategory.DATA, "min_state_coverage", "the market state's coverage", limit
        )
    breached = coverage < limit
    return LimitEvaluation(
        category=LimitCategory.DATA,
        limit_id="min_state_coverage",
        status=LimitStatus.BREACHED if breached else LimitStatus.PASSED,
        limit_value=str(limit),
        observed_value=str(coverage),
        headroom=str(coverage - limit),
        detail=f"coverage {coverage} against a floor of {limit}",
    )


# ----------------------------------------------------------------------- BROKER


def check_venue_health(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    """`11` §3's Broker category.

    Phase 9 runs against the paper venue, whose honest health is
    `PAPER_SIMULATED` -- deliberately distinct from `HEALTHY`, which would claim a
    real venue had been checked. `UNKNOWN` does not pass: health that was never
    established is not health.
    """
    if not limits.require_healthy_venue:
        return _not_configured(LimitCategory.BROKER, "require_healthy_venue")
    health = state.venue_health
    if health is None:  # pragma: no cover - typed non-optional, defensive only
        return _not_evaluable(LimitCategory.BROKER, "require_healthy_venue", "venue health")
    return LimitEvaluation(
        category=LimitCategory.BROKER,
        limit_id="require_healthy_venue",
        status=LimitStatus.PASSED if health.is_usable else LimitStatus.BREACHED,
        limit_value="usable venue",
        observed_value=health.value,
        detail=(
            f"venue reports {health.value}"
            if health.is_usable
            else f"venue is {health.value}; refusing to authorize"
        ),
    )


# ---------------------------------------------------------------------- SESSION


def check_session_window(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> LimitEvaluation:
    """Evaluated against **market time**, never a system clock."""
    window = limits.session
    if window is None:
        return _not_configured(LimitCategory.SESSION, "session_window")
    moment = state.as_of.timetz().replace(tzinfo=None)
    inside = window.contains(moment, is_expiry_day=state.is_expiry_day)
    return LimitEvaluation(
        category=LimitCategory.SESSION,
        limit_id="session_window",
        status=LimitStatus.PASSED if inside else LimitStatus.BREACHED,
        limit_value=f"{window.opens_at.isoformat()}-{window.closes_at.isoformat()}",
        observed_value=moment.isoformat(),
        detail=("inside the trading window" if inside else "outside the configured trading window"),
    )


# ------------------------------------------------------------------ KILL SWITCH


def check_kill_switch(intent: TradeIntent, state: RiskState, limits: RiskLimits) -> LimitEvaluation:
    """Global and per-strategy, immediate (`11` §3).

    Always evaluated -- a kill switch that could be switched off by policy would
    not be a kill switch.
    """
    switch = state.kill_switch
    halted = switch.halts(intent.strategy_id)
    return LimitEvaluation(
        category=LimitCategory.KILL_SWITCH,
        limit_id="kill_switch",
        status=LimitStatus.BREACHED if halted else LimitStatus.PASSED,
        limit_value="not engaged",
        observed_value="ENGAGED" if halted else "CLEAR",
        detail=(
            (switch.reason or "the kill switch is engaged")
            if halted
            else "the kill switch is clear"
        ),
    )


#: Declared evaluation order. Fixed, so two evaluations of the same inputs produce
#: an identically ordered evidence list and the decision digest is stable.
ALL_CHECKS: tuple[LimitCheck, ...] = (
    check_kill_switch,
    check_session_window,
    check_venue_health,
    check_state_quality,
    check_state_staleness,
    check_state_coverage,
    check_order_quantity,
    check_order_notional,
    check_order_rate,
    check_position_per_instrument,
    check_position_per_underlying,
    check_position_per_strategy,
    check_cash_sufficiency,
    check_deployed_capital,
    check_capital_utilisation,
    check_leverage,
    check_daily_loss,
    check_drawdown,
    check_strategy_loss,
    check_net_delta,
    check_gross_delta,
    check_gross_gamma,
    check_gross_vega,
    check_gross_theta,
    check_underlying_concentration,
    check_expiry_concentration,
    check_strike_concentration,
)


def evaluate_all(
    intent: TradeIntent, state: RiskState, limits: RiskLimits
) -> tuple[LimitEvaluation, ...]:
    """Run every check, in the declared order. Nothing short-circuits.

    `11` §3 requires the complete picture rather than the first failure, so a
    breach does not stop the remaining checks. The cost is trivial and the benefit
    is that an operator sees everything that is wrong at once.
    """
    return tuple(check(intent, state, limits) for check in ALL_CHECKS)
