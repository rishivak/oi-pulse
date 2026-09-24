"""`RiskState` — the snapshot a risk evaluation reads. Deterministic and addressable.

Phase 9 brief §12 and §14:

> Risk must be reproducible. Do not calculate against mutable "current" state without
> preserving the semantic inputs used.
>
> Identical semantic inputs must produce identical RiskState identity/content.

So `RiskState` is an **immutable value**, not a view onto a live ledger. It is built
once, hashed, and the hash (`risk_state_ref`) is recorded on the decision. A verifier
asking "what did risk see?" gets an answer that cannot have changed since.

### Row-order independence

Every collection is sorted on construction — positions by instrument id, exposures by
key, pending intents by id. `14` of the brief requires identical content from
reordered positions, reordered intents and a different database row order, and the
only reliable way to get that is to refuse to store an order-dependent representation
at all.

### Where the numbers come from

Nothing here computes market data. `15` of the brief is explicit: risk uses the
existing Phase 2-4 paths. Exposure and greeks arrive as **inputs**, derived by the
caller from the canonical `MarketState` and the Phase 4 features. A missing input is
carried as `None` and evaluated as `NOT_EVALUABLE` — never substituted with a stale
price, a latest price, a zero or an estimate.

### Two times, both recorded

`as_of` is market time; `knowledge_time` is what was known. A risk evaluation at K1
must not read a state assembled at K2 > K1, and `RiskState` carries both so the engine
can refuse rather than trust its caller.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

__all__ = [
    "ExposureSnapshot",
    "KillSwitchState",
    "PositionSnapshot",
    "RiskState",
    "StateQualityInput",
    "VenueHealth",
]


class VenueHealth(StrEnum):
    """Broker/venue health (`11` §3's Broker category).

    Phase 9 runs against the paper venue, which has no connectivity to lose, so
    `PAPER_SIMULATED` is its honest value — distinct from `HEALTHY`, which would
    claim a real venue had been checked. `UNKNOWN` is the fail-closed value: a
    policy requiring a healthy venue refuses when health has not been established.
    """

    PAPER_SIMULATED = "PAPER_SIMULATED"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_usable(self) -> bool:
        return self in (VenueHealth.PAPER_SIMULATED, VenueHealth.HEALTHY)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One position, as risk sees it. Signed quantity: positive long, negative short."""

    instrument_id: int
    quantity: int
    average_price: Decimal
    #: Resolved from the instrument version valid at `as_of` by the caller, never
    #: looked up here. None where the grouping is unknown, which makes an
    #: underlying-scoped limit `NOT_EVALUABLE` rather than silently ungrouped.
    underlying_id: int | None = None
    expiry_id: int | None = None
    strike: Decimal | None = None
    #: Mark used for exposure. None when no observed price existed — never a
    #: substituted stale or previous value.
    mark: Decimal | None = None

    @property
    def notional(self) -> Decimal | None:
        if self.mark is None:
            return None
        return abs(self.mark * Decimal(self.quantity))

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "quantity": self.quantity,
            "average_price": str(self.average_price),
            "underlying_id": self.underlying_id,
            "expiry_id": self.expiry_id,
            "strike": None if self.strike is None else str(self.strike),
            "mark": None if self.mark is None else str(self.mark),
        }


@dataclass(frozen=True, slots=True)
class ExposureSnapshot:
    """Portfolio greeks and exposure. Every field optional and separately absent.

    One `None` must not poison the others: a state with delta but no vega can still
    have its delta limit evaluated, and the vega limit reports `NOT_EVALUABLE`.
    """

    gross_notional: Decimal | None = None
    net_notional: Decimal | None = None
    net_delta: Decimal | None = None
    gross_delta: Decimal | None = None
    gross_gamma: Decimal | None = None
    gross_vega: Decimal | None = None
    gross_theta: Decimal | None = None
    #: Gross notional per underlying / expiry / strike, for concentration.
    by_underlying: tuple[tuple[int, Decimal], ...] = ()
    by_expiry: tuple[tuple[int, Decimal], ...] = ()
    by_strike: tuple[tuple[str, Decimal], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "gross_notional": num(self.gross_notional),
            "net_notional": num(self.net_notional),
            "net_delta": num(self.net_delta),
            "gross_delta": num(self.gross_delta),
            "gross_gamma": num(self.gross_gamma),
            "gross_vega": num(self.gross_vega),
            "gross_theta": num(self.gross_theta),
            # Sorted on construction; serialised in that order.
            "by_underlying": {str(k): str(v) for k, v in self.by_underlying},
            "by_expiry": {str(k): str(v) for k, v in self.by_expiry},
            "by_strike": {k: str(v) for k, v in self.by_strike},
        }


@dataclass(frozen=True, slots=True)
class StateQualityInput:
    """What the `MarketState` said about itself. Carried, never recomputed."""

    #: `QualityStatus` value as a string, so `risk` need not import marketstate
    #: internals to be constructible in a test with no market data present.
    status: str = "UNKNOWN"
    coverage_ratio: Decimal | None = None
    staleness: timedelta | None = None
    market_state_ref: str = ""
    build_context_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "coverage_ratio": (None if self.coverage_ratio is None else str(self.coverage_ratio)),
            "staleness_seconds": (
                None if self.staleness is None else self.staleness.total_seconds()
            ),
            "market_state_ref": self.market_state_ref,
            "build_context_id": self.build_context_id,
        }


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    """Global and per-strategy, immediate, operator-triggered (`11` §3).

    Part of the state rather than the policy because it is an operational fact at a
    moment, not a configured limit. It is evaluated as a limit, and when engaged no
    other limit can rescue the intent.
    """

    engaged: bool = False
    #: Strategies individually halted. Sorted on construction.
    halted_strategies: tuple[str, ...] = ()
    reason: str = ""
    engaged_at: datetime | None = None

    def halts(self, strategy_id: str) -> bool:
        return self.engaged or strategy_id in self.halted_strategies

    def as_dict(self) -> dict[str, Any]:
        return {
            "engaged": self.engaged,
            "halted_strategies": list(self.halted_strategies),
            "reason": self.reason,
            "engaged_at": None if self.engaged_at is None else self.engaged_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RiskState:
    """Everything a risk evaluation may read. Immutable, sorted, content-addressed."""

    account_id: str
    #: Market time this state describes.
    as_of: datetime
    #: What was known when it was assembled. An evaluation at an earlier knowledge
    #: horizon must refuse this state rather than read it.
    knowledge_time: datetime
    cash: Decimal
    equity: Decimal
    positions: tuple[PositionSnapshot, ...] = ()
    exposure: ExposureSnapshot = field(default_factory=ExposureSnapshot)
    quality: StateQualityInput = field(default_factory=StateQualityInput)
    kill_switch: KillSwitchState = field(default_factory=KillSwitchState)
    reserved_cash: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    #: Positive magnitude of today's loss, or zero when flat or profitable.
    daily_loss: Decimal = Decimal(0)
    #: Positive magnitude of the drawdown from peak equity.
    drawdown: Decimal = Decimal(0)
    #: Realised plus unrealised loss per strategy, positive magnitude. Sorted.
    loss_by_strategy: tuple[tuple[str, Decimal], ...] = ()
    #: Quantity already committed per strategy, for strategy position limits.
    position_by_strategy: tuple[tuple[str, int], ...] = ()
    #: Instrument -> underlying id, for every instrument the evaluation may need to
    #: group, not only those already held. An opening trade in a new instrument
    #: still belongs to an underlying, and deriving the grouping from the position
    #: book would make every first trade in an underlying ungroupable. Sorted.
    instrument_underlying: tuple[tuple[int, int], ...] = ()
    #: Instrument -> mark, for every instrument the evaluation may need to price,
    #: not only those already held. An opening trade in an instrument with no
    #: position still needs a price to have its notional and cash checked, and
    #: deriving one from the position book would make every first trade
    #: unpriceable. Supplied by the caller from the canonical MarketState; sorted.
    marks: tuple[tuple[int, Decimal], ...] = ()
    #: Intents submitted and not yet terminal. They consume limit headroom even
    #: though nothing has filled -- otherwise two intents in flight can each pass a
    #: check that their sum would fail.
    pending_intent_ids: tuple[str, ...] = ()
    #: Orders created within the policy's rate-limit interval, for the order-rate
    #: limit. A count, because the timestamps are not part of risk's semantics.
    orders_in_interval: int = 0
    venue_health: VenueHealth = VenueHealth.UNKNOWN
    #: True when `as_of` falls on an expiry day, for expiry-day session rules.
    is_expiry_day: bool = False

    def __post_init__(self) -> None:
        if self.knowledge_time < self.as_of:
            raise ValueError(
                f"risk state knowledge_time {self.knowledge_time.isoformat()} precedes "
                f"as_of {self.as_of.isoformat()}; a state cannot know less than the "
                f"market fact it describes is old"
            )
        # Sort every collection so identity cannot depend on insertion or row order.
        object.__setattr__(
            self, "positions", tuple(sorted(self.positions, key=lambda p: p.instrument_id))
        )
        object.__setattr__(self, "loss_by_strategy", tuple(sorted(self.loss_by_strategy)))
        object.__setattr__(self, "position_by_strategy", tuple(sorted(self.position_by_strategy)))
        object.__setattr__(self, "pending_intent_ids", tuple(sorted(self.pending_intent_ids)))
        object.__setattr__(self, "marks", tuple(sorted(self.marks)))
        object.__setattr__(self, "instrument_underlying", tuple(sorted(self.instrument_underlying)))
        object.__setattr__(
            self,
            "kill_switch",
            KillSwitchState(
                engaged=self.kill_switch.engaged,
                halted_strategies=tuple(sorted(self.kill_switch.halted_strategies)),
                reason=self.kill_switch.reason,
                engaged_at=self.kill_switch.engaged_at,
            ),
        )
        object.__setattr__(
            self,
            "exposure",
            ExposureSnapshot(
                gross_notional=self.exposure.gross_notional,
                net_notional=self.exposure.net_notional,
                net_delta=self.exposure.net_delta,
                gross_delta=self.exposure.gross_delta,
                gross_gamma=self.exposure.gross_gamma,
                gross_vega=self.exposure.gross_vega,
                gross_theta=self.exposure.gross_theta,
                by_underlying=tuple(sorted(self.exposure.by_underlying)),
                by_expiry=tuple(sorted(self.exposure.by_expiry)),
                by_strike=tuple(sorted(self.exposure.by_strike)),
            ),
        )

    # ------------------------------------------------------------------ queries

    def underlying_of(self, instrument_id: int) -> int | None:
        """The supplied grouping, or None. Never inferred from the position book."""
        for candidate, underlying in self.instrument_underlying:
            if candidate == instrument_id:
                return underlying
        return None

    def mark_for(self, instrument_id: int) -> Decimal | None:
        """The supplied mark, or None. Never a substituted or inferred value."""
        for candidate, mark in self.marks:
            if candidate == instrument_id:
                return mark
        return None

    def position_in(self, instrument_id: int) -> int:
        for position in self.positions:
            if position.instrument_id == instrument_id:
                return position.quantity
        return 0

    def position_in_underlying(self, underlying_id: int | None) -> int | None:
        """Absolute quantity across an underlying, or None if grouping is unknown.

        Returning None rather than 0 for an unknown grouping is deliberate: 0 would
        pass an underlying limit that was never actually evaluated.
        """
        if underlying_id is None:
            return None
        if any(p.underlying_id is None for p in self.positions):
            return None
        return sum(abs(p.quantity) for p in self.positions if p.underlying_id == underlying_id)

    def strategy_position(self, strategy_id: str) -> int:
        for candidate, quantity in self.position_by_strategy:
            if candidate == strategy_id:
                return quantity
        return 0

    def strategy_loss(self, strategy_id: str) -> Decimal | None:
        for candidate, loss in self.loss_by_strategy:
            if candidate == strategy_id:
                return loss
        return None

    @property
    def available_cash(self) -> Decimal:
        return self.cash - self.reserved_cash

    @property
    def deployed_capital(self) -> Decimal | None:
        """Gross notional currently at work. None when exposure is unavailable."""
        return self.exposure.gross_notional

    # ------------------------------------------------------------------ identity

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "as_of": self.as_of.isoformat(),
            "knowledge_time": self.knowledge_time.isoformat(),
            "cash": str(self.cash),
            "reserved_cash": str(self.reserved_cash),
            "equity": str(self.equity),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "daily_loss": str(self.daily_loss),
            "drawdown": str(self.drawdown),
            "positions": [p.as_dict() for p in self.positions],
            "exposure": self.exposure.as_dict(),
            "quality": self.quality.as_dict(),
            "kill_switch": self.kill_switch.as_dict(),
            "loss_by_strategy": {k: str(v) for k, v in self.loss_by_strategy},
            "position_by_strategy": dict(self.position_by_strategy),
            "instrument_underlying": {str(k): v for k, v in self.instrument_underlying},
            "marks": {str(k): str(v) for k, v in self.marks},
            "pending_intent_ids": list(self.pending_intent_ids),
            "orders_in_interval": self.orders_in_interval,
            "venue_health": self.venue_health.value,
            "is_expiry_day": self.is_expiry_day,
        }

    @property
    def risk_state_ref(self) -> str:
        """The reference recorded on every decision (`11` §3).

        Content-addressed over the whole state. Two states with identical content
        share a ref regardless of how they were assembled, in what order rows
        arrived, or when the process ran — which is exactly the reproducibility
        property `12` and `14` of the brief require.
        """
        return (
            "rst_"
            + hashlib.sha256(
                json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:32]
        )


def build_exposure(
    positions: Sequence[PositionSnapshot],
    *,
    greeks: Mapping[str, Decimal] | None = None,
) -> ExposureSnapshot:
    """Aggregate notional exposure from positions. Deterministic.

    Greeks are **supplied**, not computed: `15` of the brief forbids a second
    analytics engine, and per-position greeks come from the Phase 4 features via the
    caller. A position with no mark contributes to no total, and any such position
    makes the totals `None` rather than understated — a gross exposure that silently
    omitted an unpriced position would read as headroom that does not exist.
    """
    greeks = greeks or {}
    if any(p.mark is None for p in positions):
        return ExposureSnapshot(
            net_delta=greeks.get("net_delta"),
            gross_delta=greeks.get("gross_delta"),
            gross_gamma=greeks.get("gross_gamma"),
            gross_vega=greeks.get("gross_vega"),
            gross_theta=greeks.get("gross_theta"),
        )

    gross = sum((p.notional or Decimal(0) for p in positions), Decimal(0))
    net = sum(((p.mark or Decimal(0)) * Decimal(p.quantity) for p in positions), Decimal(0))

    def group(key: str) -> dict[Any, Decimal]:
        out: dict[Any, Decimal] = {}
        for position in positions:
            value = getattr(position, key)
            if value is None:
                continue
            bucket = str(value) if key == "strike" else value
            out[bucket] = out.get(bucket, Decimal(0)) + (position.notional or Decimal(0))
        return out

    return ExposureSnapshot(
        gross_notional=gross,
        net_notional=net,
        net_delta=greeks.get("net_delta"),
        gross_delta=greeks.get("gross_delta"),
        gross_gamma=greeks.get("gross_gamma"),
        gross_vega=greeks.get("gross_vega"),
        gross_theta=greeks.get("gross_theta"),
        by_underlying=tuple(sorted(group("underlying_id").items())),
        by_expiry=tuple(sorted(group("expiry_id").items())),
        by_strike=tuple(sorted(group("strike").items())),
    )


__all__ += ["build_exposure"]
