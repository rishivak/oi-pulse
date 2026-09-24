"""`RuleContext` — everything a pure signal rule may see.

`08-SIGNALS.md` §4: *Rules are pure over `RuleContext` (a state plus its available
metric values) — same purity contract as analytics, same testability, same single
implementation across live, replay and backtest.*

Two properties make the point-in-time gate real rather than aspirational:

* **Metric values are filtered by availability before the rule sees them.** A rule
  cannot reach past its knowledge horizon, because nothing beyond it is in the context.
  A value whose `available_at` is later than the horizon is *withheld*, and the rule
  observes it as absent — which is what it was, at that moment.
* **Feature versions are pinned.** `requires_features` names exact versions, so a
  feature bumping to v3 does not silently change a rule's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from oipulse.analytics.values import MetricValue
from oipulse.marketstate.staleness import QualityStatus
from oipulse.marketstate.state import MarketState
from oipulse.signals.model import MetricRef, signal_digest

__all__ = ["RuleConfig", "RuleContext", "metric_ref_of"]


def metric_ref_of(value: MetricValue) -> MetricRef:
    """Turn a computed value into the reference evidence stores.

    A reference, not a copy: the evidence chain must resolve by join to the actual
    `metric_values` row, so that explainability survives the UI and the renderer.
    """
    return MetricRef(
        feature_id=value.feature_id,
        feature_version=value.feature_version,
        scope_kind=value.scope.kind.value,
        scope_ref=value.scope.ref,
        observed_at=value.observed_at,
        knowledge_horizon=value.knowledge_horizon,
        build_context_id=value.build_context_id,
        inputs_digest=value.inputs_digest,
        available_at=value.available_at,
    )


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """A rule's thresholds and settings, content-addressed.

    The digest is what stops a threshold edit silently rewriting history: change a
    number and the signals produced afterwards carry a different `config_digest`, so
    research can tell the two populations apart instead of averaging them together.
    """

    values: tuple[tuple[str, Any], ...] = ()

    @staticmethod
    def of(**kwargs: Any) -> RuleConfig:
        return RuleConfig(tuple(sorted(kwargs.items())))

    def get(self, name: str, default: Any = None) -> Any:
        for key, value in self.values:
            if key == name:
                return value
        return default

    def decimal(self, name: str, default: str) -> Decimal:
        raw = self.get(name, default)
        return Decimal(str(raw))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)

    @property
    def digest(self) -> str:
        return "cfg_" + signal_digest({k: str(v) for k, v in self.values})[:24]


@dataclass(frozen=True, slots=True)
class RuleContext:
    """A `MarketState` plus the metric values available at its knowledge horizon."""

    state: MarketState
    #: The **signal's** knowledge horizon, which is not the state's. The state's
    #: horizon governs which observations built it; this one governs which derived
    #: values had become available by the time the signal was evaluated. Defaults to
    #: the state's when not supplied, which is the strictest reading.
    knowledge_horizon_override: datetime | None = None
    #: Keyed `"IDENTIFIER@vN"`. Already availability-filtered; see `available_only`.
    metrics: tuple[tuple[str, MetricValue], ...] = ()
    config: RuleConfig = field(default_factory=RuleConfig)
    #: Prior evaluations of the same rule, oldest first. Supplied, never fetched --
    #: used for persistence criteria ("N consecutive evaluations").
    prior_strengths: tuple[Decimal, ...] = ()

    @property
    def market_time(self) -> datetime:
        return self.state.identity.market_time

    @property
    def knowledge_horizon(self) -> datetime:
        if self.knowledge_horizon_override is not None:
            return self.knowledge_horizon_override
        return self.state.identity.knowledge_horizon

    @property
    def build_context_id(self) -> str:
        return self.state.identity.build_context_id

    @property
    def quality_status(self) -> QualityStatus:
        return self.state.quality.status

    @property
    def underlying_id(self) -> int:
        return int(self.state.identity.underlying_id)

    def metric(self, identifier: str, version: int) -> MetricValue | None:
        """The pinned feature version, or `None` if it was not available.

        Version-exact by design: asking for v2 and silently receiving v3 is the drift
        `08` §4 pins feature versions to prevent.
        """
        key = f"{identifier}@v{version}"
        for name, value in self.metrics:
            if name == key:
                return value
        return None

    def has(self, identifier: str, version: int) -> bool:
        return self.metric(identifier, version) is not None

    def numeric(self, identifier: str, version: int) -> Decimal | None:
        """A pinned metric's value when it is numeric, else `None`.

        Never coerces: a categorical value asked for as a number is absent, not zero.
        """
        value = self.metric(identifier, version)
        if value is None:
            return None
        if isinstance(value.value, Decimal):
            return value.value
        if isinstance(value.value, int) and not isinstance(value.value, bool):
            return Decimal(value.value)
        return None

    def categorical(self, identifier: str, version: int) -> str | None:
        value = self.metric(identifier, version)
        return value.value if value is not None and isinstance(value.value, str) else None

    def latest_input_available_at(self) -> datetime | None:
        """The last moment any consumed input became available.

        Feeds the signal's own `available_at`, so availability propagates from feature
        to signal exactly as it propagates from feature to feature (`07` §3).
        """
        times = [value.available_at for _, value in self.metrics]
        return max(times) if times else None

    def inputs_digest(self) -> str:
        """Digest over the resolved inputs actually consumed, in a stable order."""
        return signal_digest(
            {
                "state": self.state.content_digest(),
                "metrics": {name: value.inputs_digest for name, value in sorted(self.metrics)},
                "config": self.config.digest,
                "prior_strengths": [str(s) for s in self.prior_strengths],
            }
        )

    @staticmethod
    def available_only(
        values: tuple[MetricValue, ...], knowledge_horizon: datetime
    ) -> tuple[tuple[str, MetricValue], ...]:
        """Keep only values that were actually available by the horizon.

        This is the point-in-time gate, applied once, before any rule runs. A metric
        whose `available_at` is later than `K` had not been computed yet at `K`; a
        signal that consumed it would be actionable on information nobody held. Sorted
        so iteration order can never affect a decision.
        """
        kept = [
            (f"{v.feature_id}@v{v.feature_version}", v)
            for v in values
            if v.available_at <= knowledge_horizon
        ]
        return tuple(sorted(kept, key=lambda pair: pair[0]))
