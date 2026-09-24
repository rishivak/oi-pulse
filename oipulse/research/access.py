"""Point-in-time enforcement — `09-RESEARCH.md` §2, "Enforcement, not convention".

```python
class FeatureAccessError(Exception): ...


def get_feature(self, feature_id, version, at: Timestamp) -> MetricValue:
    mv = self._lookup(feature_id, version, at)
    if mv.available_at > at:
        raise FeatureAccessError(
            f"{feature_id}@{version} available at {mv.available_at}, "
            f"requested at {at} — look-ahead refused"
        )
    return mv
```

> The strategy API exposes **only** features passing
> `tradable_information_at(decision_time)`. A strategy cannot request an unavailable
> feature and ignore a warning, because there is no path by which it receives one.
> Researcher discipline is not a control.

That last sentence is the design constraint this module implements. Everything here
**raises**; nothing warns and nothing silently substitutes. The three failure modes it
closes:

* asking for a feature before it was available (look-ahead);
* receiving a *different version* than the one pinned (silent formula drift);
* receiving a later correction as though it existed at the original knowledge horizon.

No clock, no store. Values are supplied; the accessor only decides what may be seen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from oipulse.analytics.values import MetricValue
from oipulse.core.errors import OIPulseError
from oipulse.signals.model import Signal

__all__ = [
    "FeatureAccessError",
    "PointInTimeAccessor",
    "SignalAccessError",
    "sorted_metrics",
]


class FeatureAccessError(OIPulseError):
    """A feature was requested before it was available. Look-ahead refused."""


class SignalAccessError(OIPulseError):
    """A signal was requested before it was available. Look-ahead refused."""


@dataclass(frozen=True, slots=True)
class PointInTimeAccessor:
    """Serves only what was legitimately knowable at a decision point.

    Holds the full historical set and filters on request rather than being handed a
    pre-filtered slice: the refusal has to happen at the point of *asking*, so that an
    incorrect request is an error rather than an empty result a caller might treat as
    "no data".
    """

    metrics: tuple[MetricValue, ...] = ()
    signals: tuple[Signal, ...] = ()

    # ------------------------------------------------------------------ features

    def get_feature(
        self,
        feature_id: str,
        version: int,
        at: datetime,
        *,
        scope_ref: str | None = None,
    ) -> MetricValue:
        """The pinned feature version as of `at`, or raise.

        Version-exact: asking for v2 and silently receiving v3 is the silent formula
        drift `09` §6 lists as a bias. A v3 row is not a match for a v2 request.
        """
        candidates = [
            m
            for m in self.metrics
            if m.feature_id == feature_id
            and m.feature_version == version
            and (scope_ref is None or m.scope.ref == scope_ref)
        ]
        if not candidates:
            raise FeatureAccessError(
                f"{feature_id}@v{version} is not present in this dataset"
                + (f" for scope {scope_ref}" if scope_ref else "")
            )

        visible = [m for m in candidates if m.available_at <= at]
        if not visible:
            earliest = min(candidates, key=lambda m: m.available_at)
            raise FeatureAccessError(
                f"{feature_id}@v{version} available at "
                f"{earliest.available_at.isoformat()}, requested at {at.isoformat()} "
                f"— look-ahead refused"
            )
        # Latest *available* value, not latest value: a correction ingested after `at`
        # must not be substituted for what was believed then (`09` §6).
        return max(visible, key=lambda m: (m.available_at, m.observed_at))

    def try_feature(
        self, feature_id: str, version: int, at: datetime, *, scope_ref: str | None = None
    ) -> MetricValue | None:
        """`get_feature` without raising, for callers that treat absence as a state.

        Distinct method rather than a flag, so the refusing path stays the default and
        an author has to opt out of it explicitly.
        """
        try:
            return self.get_feature(feature_id, version, at, scope_ref=scope_ref)
        except FeatureAccessError:
            return None

    def available_features(self, at: datetime) -> tuple[MetricValue, ...]:
        """Everything consumable at `at`, in a deterministic order."""
        visible = [m for m in self.metrics if m.available_at <= at]
        return tuple(
            sorted(
                visible,
                key=lambda m: (
                    m.feature_id,
                    m.feature_version,
                    m.scope.ref,
                    m.observed_at,
                ),
            )
        )

    # ------------------------------------------------------------------- signals

    def get_signal(self, signal_id: str, at: datetime) -> Signal:
        for candidate in self.signals:
            if candidate.signal_id != signal_id:
                continue
            if candidate.available_at > at:
                raise SignalAccessError(
                    f"signal {signal_id} available at "
                    f"{candidate.available_at.isoformat()}, requested at "
                    f"{at.isoformat()} — look-ahead refused"
                )
            return candidate
        raise SignalAccessError(f"signal {signal_id} is not present in this dataset")

    def available_signals(self, at: datetime) -> tuple[Signal, ...]:
        visible = [s for s in self.signals if s.available_at <= at]
        return tuple(sorted(visible, key=lambda s: (s.available_at, s.signal_id)))

    # --------------------------------------------------------------- bitemporal

    def knowledge_at(self, knowledge_horizon: datetime) -> PointInTimeAccessor:
        """A narrower accessor holding only what was known by `knowledge_horizon`.

        Returns a new accessor rather than mutating: two studies at different horizons
        must be able to run side by side over one loaded history without one of them
        narrowing the other's view.
        """
        return PointInTimeAccessor(
            metrics=tuple(m for m in self.metrics if m.knowledge_horizon <= knowledge_horizon),
            signals=tuple(
                s for s in self.signals if s.identity.knowledge_horizon <= knowledge_horizon
            ),
        )

    def coverage(self) -> tuple[int, int]:
        return (len(self.metrics), len(self.signals))


def sorted_metrics(values: Sequence[MetricValue]) -> tuple[MetricValue, ...]:
    """Deterministic ordering. Input order must never reach a result."""
    return tuple(
        sorted(
            values,
            key=lambda m: (
                m.feature_id,
                m.feature_version,
                m.scope.kind.value,
                m.scope.ref,
                m.observed_at,
                m.knowledge_horizon,
                m.inputs_digest,
            ),
        )
    )
