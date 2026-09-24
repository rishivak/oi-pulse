"""`ComputeContext` — everything a pure analytic is allowed to see.

`docs/design/07-ANALYTICS.md` §1. An analytic may consume **only** explicit inputs. It
cannot query a store for history, so history arrives here as a tuple of prior
`MarketState`s; it cannot read the clock, so `computed_at` arrives here too; it cannot
look up a dependency, so resolved dependencies arrive here.

That is what makes one implementation serve live processing, replay, research and
backtest. A context assembled from a replay harness and one assembled from the live
processor are indistinguishable to the analytic, which is the property that removes
backtest/live divergence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from oipulse.analytics.values import MetricValue
from oipulse.marketstate.staleness import QualityStatus
from oipulse.marketstate.state import MarketState

__all__ = ["ComputeContext", "Params"]

#: Sentinel distinguishing "no default given" from "default is None". Without it,
#: `get("convention", None)` and a genuinely absent parameter are indistinguishable,
#: and a mistyped name silently yields a value computed under the wrong convention.
_MISSING: Any = object()


@dataclass(frozen=True, slots=True)
class Params:
    """Declared parameters for one computation.

    Immutable and explicitly keyed. `get` raises on an unknown key rather than
    returning a default, because a mistyped parameter name that silently falls back to
    a default produces a plausible number computed under the wrong convention.
    """

    values: tuple[tuple[str, Any], ...] = ()

    @staticmethod
    def of(**kwargs: Any) -> Params:
        return Params(tuple(sorted(kwargs.items())))

    def get(self, name: str, default: Any = _MISSING) -> Any:
        for key, value in self.values:
            if key == name:
                return value
        if default is _MISSING:
            raise KeyError(f"parameter {name!r} was not supplied")
        return default

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class ComputeContext:
    """The complete, explicit input set for one feature computation.

    `history` is ordered oldest-first and must cover the feature's lookback; a feature
    whose window is not covered returns `INSUFFICIENT_HISTORY` rather than computing
    over whatever happens to be present. Fabricating a value from a short window is
    worse than withholding it (`07` §4.2).
    """

    state: MarketState
    computed_at: datetime
    params: Params = field(default_factory=Params)
    #: Prior states within the lookback, oldest first. Never fetched by the analytic.
    history: tuple[MarketState, ...] = ()
    #: Resolved dependency values, keyed `"IDENTIFIER@vN"`.
    dependencies: tuple[tuple[str, MetricValue], ...] = ()

    # ------------------------------------------------------------------ times

    @property
    def market_time(self) -> datetime:
        return self.state.identity.market_time

    @property
    def knowledge_horizon(self) -> datetime:
        return self.state.identity.knowledge_horizon

    @property
    def build_context_id(self) -> str:
        return self.state.identity.build_context_id

    @property
    def quality_status(self) -> QualityStatus:
        return self.state.quality.status

    @property
    def raw_input_ingested_at(self) -> datetime | None:
        """Input readiness for the raw observations behind this state (`07` §3).

        `ingested_at`, never `observed_at`. `None` when the state contains no
        observations, which is not the same as "ready at the epoch".
        """
        latest = self.state.provenance.max_input_ingested_at
        for prior in self.history:
            candidate = prior.provenance.max_input_ingested_at
            if candidate is not None and (latest is None or candidate > latest):
                latest = candidate
        return latest

    def lookback_end(self, lookback: timedelta) -> datetime:
        """The window closes at `market_time`; its start is `market_time - lookback`.

        The *end* is what availability depends on: a window is complete when its last
        instant has passed, which for a state built at `T` is `T`.
        """
        return self.market_time

    def lookback_start(self, lookback: timedelta) -> datetime:
        return self.market_time - lookback

    # ------------------------------------------------------------- dependencies

    def dependency(self, identifier: str, version: int) -> MetricValue | None:
        key = f"{identifier}@v{version}"
        for name, value in self.dependencies:
            if name == key:
                return value
        return None

    def dependency_available_at(self) -> tuple[datetime, ...]:
        """Every dependency's own `available_at`, so availability propagates (§3)."""
        return tuple(value.available_at for _, value in sorted(self.dependencies))

    # ----------------------------------------------------------------- history

    def covers(self, lookback: timedelta) -> bool:
        """Does the supplied history reach back far enough for this window?

        A zero lookback is covered by the current state alone. Otherwise the oldest
        supplied state must be at or before the window start.
        """
        if lookback == timedelta(0):
            return True
        if not self.history:
            return False
        return self.history[0].identity.market_time <= self.lookback_start(lookback)

    def reference_state(self, lookback: timedelta) -> MarketState | None:
        """The state at (or immediately after) the window start.

        This is the point-in-time reference that `OI_CHANGE` and friends compare
        against. It is resolved from supplied history, never from a stored `prev_*`
        column -- a stored previous value cannot be reconstructed for an arbitrary past
        moment, and would silently leak the *current* previous value into a historical
        computation (`04` §6, `07` §4.1).
        """
        start = self.lookback_start(lookback)
        candidates = [s for s in self.history if s.identity.market_time <= start]
        if candidates:
            return candidates[-1]
        return self.history[0] if self.history else None

    # -------------------------------------------------------------- quality

    def quality_measures(self) -> dict[str, float]:
        """Measurable quantities a `quality_requirements` entry can name.

        Kept explicit rather than reflective: a requirement naming something not in
        this dict is treated as unmet, so the set of checkable properties is a
        deliberate list rather than whatever attribute happens to exist.
        """
        expiries = self.state.expiries
        oi_legs = sum(1 for e in expiries for leg in e.legs if leg.oi is not None)
        greek_legs = sum(1 for e in expiries for leg in e.legs if leg.iv is not None)
        quote_legs = sum(1 for e in expiries for leg in e.legs if leg.ltp is not None)
        expected = sum(len(e.legs) + e.missing_leg_count for e in expiries)
        return {
            "coverage": self.state.quality.coverage_ratio,
            "oi_coverage": (oi_legs / expected) if expected else 0.0,
            "greeks_coverage": (greek_legs / expected) if expected else 0.0,
            "quote_coverage": (quote_legs / expected) if expected else 0.0,
            "history_points": float(len(self.history)),
        }

    def digest_payload(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Canonical description of the inputs, for `inputs_digest`."""
        payload: dict[str, Any] = {
            "state": self.state.content_digest(),
            "history": [s.content_digest() for s in self.history],
            "dependencies": {name: v.inputs_digest for name, v in sorted(self.dependencies)},
            "params": {k: str(v) for k, v in sorted(self.params.as_dict().items())},
        }
        if extra:
            payload["extra"] = {k: _stringify(v) for k, v in sorted(extra.items())}
        return payload


def _stringify(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_stringify(v) for v in value]
    return value
