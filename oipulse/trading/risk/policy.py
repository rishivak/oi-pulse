"""Risk policy and limits — `11-TRADING.md` §3, `18-ROADMAP.md` Phase 9.

A policy is the **versioned, content-addressed** statement of what an account is
allowed to do. `18` Phase 9 names the risk it exists to manage:

> **Risks** Over-permissive defaults — mitigated by conservative defaults requiring
> explicit relaxation, audited.

Three design choices follow from that, and each is load-bearing.

**There is no default policy object.** `RiskPolicy` must be constructed with an
explicit `RiskLimits`, exactly as a `FillModel` must be constructed with explicit
assumptions. A policy that materialised from nothing would be a set of limits nobody
chose.

**An absent limit is `None`, and `None` is recorded, not skipped.** A limit left
unconfigured evaluates to `NOT_CONFIGURED` and appears in `limits_evaluated` like any
other. That is what makes relaxation *audited*: the decision record shows which limits
were not in force, rather than showing nothing and leaving a reader to infer that
everything was checked.

**The digest covers every limit.** Changing a maximum, a window or the kill switch
produces a different `policy_digest`, so a decision made under the old policy remains
interpretable and two decisions under different policies are never comparable by
accident. `11` §3: historical decision semantics must never silently change.

The limit categories are exactly those `11` §3 tabulates — position, order, capital,
loss, exposure, concentration, data, broker, session, kill switch. None is invented
and none is omitted; where Phase 9 cannot evaluate one honestly it reports
`NOT_EVALUABLE` rather than passing it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

__all__ = [
    "CONSERVATIVE_LIMITS",
    "LimitCategory",
    "RiskLimits",
    "RiskPolicy",
    "SessionWindow",
]


class LimitCategory(StrEnum):
    """`11` §3's table, verbatim. No category is invented."""

    POSITION = "POSITION"
    ORDER = "ORDER"
    CAPITAL = "CAPITAL"
    LOSS = "LOSS"
    EXPOSURE = "EXPOSURE"
    CONCENTRATION = "CONCENTRATION"
    DATA = "DATA"
    BROKER = "BROKER"
    SESSION = "SESSION"
    KILL_SWITCH = "KILL_SWITCH"


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """The trading window a session limit is evaluated against.

    Times are naive wall-clock-of-exchange values compared against the **market
    time** of the evaluation, never against a system clock. Phase 9 reads no clock;
    `tools/check_clock_access.py` enforces that no module can.
    """

    opens_at: time
    closes_at: time
    #: Expiry-day rules (`11` §3). None means no special treatment is configured.
    expiry_day_closes_at: time | None = None

    def contains(self, candidate: time, *, is_expiry_day: bool = False) -> bool:
        closes = (
            self.expiry_day_closes_at
            if is_expiry_day and self.expiry_day_closes_at is not None
            else self.closes_at
        )
        return self.opens_at <= candidate <= closes

    def as_dict(self) -> dict[str, Any]:
        return {
            "opens_at": self.opens_at.isoformat(),
            "closes_at": self.closes_at.isoformat(),
            "expiry_day_closes_at": (
                None if self.expiry_day_closes_at is None else self.expiry_day_closes_at.isoformat()
            ),
        }


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Every limit `11` §3 defines. `None` means *not configured*, and is audited.

    Deliberately flat rather than nested per category: a limit's category is a
    property of the *check*, not of the configuration, and nesting would make
    `limits.max_order_quantity` into `limits.order.max_quantity` for no gain while
    making the digest sensitive to how the nesting was arranged.
    """

    # --- Position (`11` §3: max position per instrument / underlying / strategy)
    max_position_per_instrument: int | None = None
    max_position_per_underlying: int | None = None
    max_position_per_strategy: int | None = None

    # --- Order (max order quantity, max notional, max orders per interval)
    max_order_quantity: int | None = None
    max_order_notional: Decimal | None = None
    max_orders_per_interval: int | None = None
    orders_interval: timedelta = timedelta(minutes=1)

    # --- Capital (max deployed capital, max margin utilization)
    max_deployed_capital: Decimal | None = None
    #: Deployed capital as a fraction of equity, in [0, 1].
    max_capital_utilisation: Decimal | None = None
    #: Gross exposure divided by equity. Named separately from utilisation because
    #: a short position consumes leverage while releasing cash.
    max_leverage: Decimal | None = None

    # --- Loss (max daily loss, max drawdown, max loss per strategy)
    #: Positive magnitudes. A loss of 5000 breaches a max_daily_loss of 4000.
    max_daily_loss: Decimal | None = None
    max_drawdown: Decimal | None = None
    max_loss_per_strategy: Decimal | None = None

    # --- Exposure (max delta, gamma, vega, theta; net and gross)
    max_net_delta: Decimal | None = None
    max_gross_delta: Decimal | None = None
    max_gross_gamma: Decimal | None = None
    max_gross_vega: Decimal | None = None
    max_gross_theta: Decimal | None = None

    # --- Concentration (max share in one underlying / expiry / strike)
    #: Fractions of gross exposure, in [0, 1].
    max_underlying_concentration: Decimal | None = None
    max_expiry_concentration: Decimal | None = None
    max_strike_concentration: Decimal | None = None

    # --- Data (`11` §3: "the one most systems omit")
    reject_unreliable_state: bool = True
    max_state_staleness: timedelta | None = None
    #: Refuse when the state's coverage is below this fraction.
    min_state_coverage: Decimal | None = None

    # --- Broker (health, connectivity, rate-limit headroom)
    require_healthy_venue: bool = True

    # --- Session (trading window, expiry-day rules)
    session: SessionWindow | None = None

    def as_dict(self) -> dict[str, Any]:
        """Canonical form. Every field, including the unset ones.

        Unset limits are serialised as `null` rather than omitted: the digest must
        distinguish "this limit is not configured" from "this version of the schema
        had no such limit", and an omitted key cannot.
        """

        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "max_position_per_instrument": self.max_position_per_instrument,
            "max_position_per_underlying": self.max_position_per_underlying,
            "max_position_per_strategy": self.max_position_per_strategy,
            "max_order_quantity": self.max_order_quantity,
            "max_order_notional": num(self.max_order_notional),
            "max_orders_per_interval": self.max_orders_per_interval,
            "orders_interval_seconds": self.orders_interval.total_seconds(),
            "max_deployed_capital": num(self.max_deployed_capital),
            "max_capital_utilisation": num(self.max_capital_utilisation),
            "max_leverage": num(self.max_leverage),
            "max_daily_loss": num(self.max_daily_loss),
            "max_drawdown": num(self.max_drawdown),
            "max_loss_per_strategy": num(self.max_loss_per_strategy),
            "max_net_delta": num(self.max_net_delta),
            "max_gross_delta": num(self.max_gross_delta),
            "max_gross_gamma": num(self.max_gross_gamma),
            "max_gross_vega": num(self.max_gross_vega),
            "max_gross_theta": num(self.max_gross_theta),
            "max_underlying_concentration": num(self.max_underlying_concentration),
            "max_expiry_concentration": num(self.max_expiry_concentration),
            "max_strike_concentration": num(self.max_strike_concentration),
            "reject_unreliable_state": self.reject_unreliable_state,
            "max_state_staleness_seconds": (
                None
                if self.max_state_staleness is None
                else self.max_state_staleness.total_seconds()
            ),
            "min_state_coverage": num(self.min_state_coverage),
            "require_healthy_venue": self.require_healthy_venue,
            "session": None if self.session is None else self.session.as_dict(),
        }

    def configured_count(self) -> int:
        """How many limits are actually in force. Reported alongside a decision."""
        return sum(
            1
            for key, value in self.as_dict().items()
            if value is not None and key not in {"orders_interval_seconds"}
        )


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """A named, versioned set of limits. Content-addressed.

    `version` is declared by the author and `policy_digest` is computed. Both are
    recorded on every decision: the version is what a human refers to, the digest is
    what makes a silent edit impossible. Bumping the version without changing a limit
    produces a new digest too, which is correct — the claim about the policy changed.
    """

    policy_id: str
    version: int
    limits: RiskLimits
    #: Prose describing the intent of this policy. Part of the digest, because a
    #: policy whose stated purpose changed is a different policy to a reader.
    description: str = ""
    #: Accounts or strategies this policy applies to. Empty means the account's own.
    scope: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("a risk policy must have an identifier")
        if self.version < 1:
            raise ValueError("risk policy version must be >= 1")

    @property
    def label(self) -> str:
        return f"{self.policy_id}@v{self.version}"

    @property
    def policy_digest(self) -> str:
        """Semantic identity. A limit change produces a different value."""
        return (
            "rpol_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "policy_id": self.policy_id,
                        "version": self.version,
                        "description": self.description,
                        "scope": dict(sorted(self.scope)),
                        "limits": self.limits.as_dict(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()[:32]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "label": self.label,
            "description": self.description,
            "scope": dict(self.scope),
            "limits": self.limits.as_dict(),
            "policy_digest": self.policy_digest,
            "configured_limits": self.limits.configured_count(),
        }


#: A conservative starting set, per `18` Phase 9's stated mitigation.
#:
#: **These are deliberately restrictive.** Relaxing one is an explicit edit that
#: produces a new policy digest and is therefore visible in every decision made
#: afterwards. The numbers suit a small retail options account and are an assumption,
#: not a recommendation — an account with a different size needs a different policy,
#: which is a different digest, which is the point.
CONSERVATIVE_LIMITS = RiskLimits(
    max_position_per_instrument=500,
    max_position_per_underlying=2000,
    max_position_per_strategy=2000,
    max_order_quantity=250,
    max_order_notional=Decimal("200000"),
    max_orders_per_interval=10,
    orders_interval=timedelta(minutes=1),
    max_deployed_capital=Decimal("500000"),
    max_capital_utilisation=Decimal("0.5"),
    max_leverage=Decimal("2"),
    max_daily_loss=Decimal("25000"),
    max_drawdown=Decimal("50000"),
    max_loss_per_strategy=Decimal("25000"),
    max_net_delta=Decimal("5000"),
    max_gross_delta=Decimal("10000"),
    max_gross_gamma=Decimal("500"),
    max_gross_vega=Decimal("20000"),
    max_gross_theta=Decimal("20000"),
    max_underlying_concentration=Decimal("0.6"),
    max_expiry_concentration=Decimal("0.8"),
    max_strike_concentration=Decimal("0.4"),
    reject_unreliable_state=True,
    max_state_staleness=timedelta(seconds=30),
    min_state_coverage=Decimal("0.9"),
    require_healthy_venue=True,
    session=SessionWindow(
        opens_at=time(9, 15), closes_at=time(15, 30), expiry_day_closes_at=time(15, 30)
    ),
)
