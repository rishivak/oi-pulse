"""The strategy interface — `10-REPLAY.md` §5.

```python
class Strategy(Protocol):
    def on_state(self, ctx: StrategyContext) -> list[TradeIntent]: ...
```

| Available | Not available |
|---|---|
| `state: MarketState` at T | any repository or session |
| `features` — only those with `available_at <= T` | any feature not yet available |
| `signals` — only those with `available_at <= T` | future states or observations |
| `positions`, `account` — as of T | outcome/forward-window data |
| `clock.now()` → T | the wall clock |

> **A strategy cannot reach the database.** It receives a context and returns intents.
> This is what makes look-ahead impossible rather than merely discouraged — there is
> no API surface through which future data could be obtained.

> Requesting an unavailable feature raises `FeatureAccessError`. The backtest fails
> loudly rather than silently producing an optimistic result.

That last point is why `features.get` raises and there is no "return None if not ready"
variant on this surface. A strategy that silently receives `None` for a feature it
believes it has is a strategy whose backtest is quietly wrong; one that crashes is a
strategy whose bug is found in the first run.

The context reuses the **Phase 6 `PointInTimeAccessor`** rather than reimplementing
availability filtering: one enforcement point, one behaviour, in research and backtest
alike.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from oipulse.analytics.values import MetricValue
from oipulse.marketstate.state import MarketState
from oipulse.research.access import FeatureAccessError, PointInTimeAccessor
from oipulse.signals.model import Signal

if TYPE_CHECKING:  # pragma: no cover - avoids a cycle; intents import nothing here
    from oipulse.backtest.intents import TradeIntent

__all__ = [
    "AccountView",
    "FeatureView",
    "SignalView",
    "Strategy",
    "StrategyContext",
    "StrategySpec",
]


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """A strategy's declared identity and configuration.

    Both are part of the backtest's semantic identity: a configuration edit must
    produce a distinguishable result rather than silently redefining what the stored
    one meant.
    """

    strategy_id: str
    version: int
    definition: str
    #: Declared parameters. Content-addressed with the rest.
    config: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"{self.strategy_id}@v{self.version}"

    def get(self, name: str, default: str) -> str:
        for key, value in self.config:
            if key == name:
                return value
        return default

    def decimal(self, name: str, default: str) -> Decimal:
        return Decimal(self.get(name, default))

    @property
    def content_digest(self) -> str:
        return (
            "stg_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "strategy_id": self.strategy_id,
                        "version": self.version,
                        "config": dict(sorted(self.config)),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()[:24]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "definition": self.definition,
            "config": dict(self.config),
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class FeatureView:
    """The only way a strategy reaches a feature. Raises rather than returning None."""

    _accessor: PointInTimeAccessor
    _at: datetime

    def get(self, feature_id: str, version: int, *, scope_ref: str | None = None) -> MetricValue:
        """The pinned feature version as of the decision point, or raise.

        No silent-`None` variant exists on this surface by design: a strategy that
        receives `None` for a feature it believes it has produces a quietly wrong
        backtest, while one that raises produces a bug report.
        """
        return self._accessor.get_feature(feature_id, version, self._at, scope_ref=scope_ref)

    def available(self) -> tuple[MetricValue, ...]:
        """Everything consumable at the decision point, in deterministic order."""
        return self._accessor.available_features(self._at)

    def has(self, feature_id: str, version: int) -> bool:
        """Test availability without raising, for a strategy that branches on it."""
        try:
            self._accessor.get_feature(feature_id, version, self._at)
        except FeatureAccessError:
            return False
        return True


@dataclass(frozen=True, slots=True)
class SignalView:
    """Signals available at the decision point. Same filtering, same enforcement."""

    _accessor: PointInTimeAccessor
    _at: datetime

    def available(self) -> tuple[Signal, ...]:
        return self._accessor.available_signals(self._at)

    def of_type(self, signal_type: str) -> tuple[Signal, ...]:
        return tuple(s for s in self.available() if s.signal_type == signal_type)


@dataclass(frozen=True, slots=True)
class AccountView:
    """Positions and cash as of the decision point. Read-only.

    A strategy cannot mutate the ledger: it returns intents, and the runner applies
    fills. Letting a strategy write positions directly would make the ledger's
    arithmetic unverifiable.
    """

    cash: Decimal
    positions: tuple[tuple[int, int], ...] = ()

    def position(self, instrument_id: int) -> int:
        for candidate, quantity in self.positions:
            if candidate == instrument_id:
                return quantity
        return 0

    @property
    def is_flat(self) -> bool:
        return all(quantity == 0 for _, quantity in self.positions)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything a strategy may see, and nothing else.

    Deliberately holds no repository, no session, no engine and no clock object — only
    `now`, which is the replayed instant. There is no attribute through which future
    data could be reached, which is what makes look-ahead structurally impossible.
    """

    run_id: str
    state: MarketState
    features: FeatureView
    signals: SignalView
    account: AccountView
    #: The replayed instant. `T`, never the wall clock.
    now: datetime
    knowledge_horizon: datetime

    @property
    def market_time(self) -> datetime:
        return self.state.identity.market_time

    @property
    def underlying_id(self) -> int:
        return int(self.state.identity.underlying_id)

    @staticmethod
    def build(
        run_id: str,
        state: MarketState,
        accessor: PointInTimeAccessor,
        account: AccountView,
        *,
        decision_time: datetime,
        knowledge_horizon: datetime,
    ) -> StrategyContext:
        """Assemble a context filtered to the decision point.

        The accessor is narrowed to the knowledge horizon *first*, then every read is
        additionally gated on `decision_time`. Both, not either: the horizon governs
        which rows exist at all, and the decision point governs which of those had
        become available.
        """
        narrowed = accessor.knowledge_at(knowledge_horizon)
        return StrategyContext(
            run_id=run_id,
            state=state,
            features=FeatureView(narrowed, decision_time),
            signals=SignalView(narrowed, decision_time),
            account=account,
            now=decision_time,
            knowledge_horizon=knowledge_horizon,
        )


class Strategy(Protocol):
    """`10` §5. Returns intents; never touches a store, a clock or a broker."""

    @property
    def spec(self) -> StrategySpec: ...

    def on_state(self, ctx: StrategyContext) -> list[TradeIntent]: ...
