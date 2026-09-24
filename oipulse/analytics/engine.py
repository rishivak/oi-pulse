"""Execution — `07-ANALYTICS.md` §6.

```
MarketStateBuilt(checkpoint)
   -> resolve features due at this cadence (sampling_frequency)
   -> topologically order by declared inputs
   -> for each: check quality_requirements -> compute (pure) -> MetricValue
   -> persist batch with observed_at / computed_at / available_at
   -> emit MetricsComputed
```

The first four steps live here and are pure. **Persistence and metric emission live in
the caller**: this package may not import a database or `observability`, whose registry
is process-global mutable state. `ExecutionReport` is the hand-off — it carries
everything a caller needs to persist rows and emit
`analytics_skipped_total` / `feature_unavailable_total` without inspecting internals.

Two rules from §6 are enforced rather than assumed:

* **Ordering is derived from declared inputs**, never hand-maintained. A cycle is an
  error at execution time, not a stack overflow.
* **A feature whose dependency is unavailable is skipped with a recorded reason, not
  computed from a default.** A default here would propagate a fabricated number through
  every downstream feature that consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from oipulse.analytics.context import ComputeContext, Params
from oipulse.analytics.registry import REGISTRY, FeatureSpec, Registry
from oipulse.analytics.values import MetricValue, Unavailable, UnavailableReason

__all__ = ["CyclicDependency", "ExecutionReport", "FeatureEngine", "topological_order"]


class CyclicDependency(ValueError):
    """Declared dependencies form a cycle. Raised rather than recursed into."""


def topological_order(specs: tuple[FeatureSpec, ...]) -> tuple[FeatureSpec, ...]:
    """Order features so every dependency precedes its dependent.

    Ties are broken by `(identifier, version)` so the order is deterministic: two runs
    over the same registry must schedule features identically, or a dependency's value
    could differ between runs purely by scheduling.
    """
    by_key = {spec.key: spec for spec in specs}
    ordered: list[FeatureSpec] = []
    state: dict[tuple[str, int], int] = {}  # 0 unvisited, 1 in progress, 2 done

    def visit(key: tuple[str, int], trail: tuple[tuple[str, int], ...]) -> None:
        mark = state.get(key, 0)
        if mark == 2:
            return
        if mark == 1:
            path = " -> ".join(f"{i}@v{v}" for i, v in (*trail, key))
            raise CyclicDependency(f"dependency cycle: {path}")
        state[key] = 1
        spec = by_key.get(key)
        if spec is not None:
            for dependency in sorted(spec.depends_on):
                if dependency in by_key:
                    visit(dependency, (*trail, key))
            ordered.append(spec)
        state[key] = 2

    for key in sorted(by_key):
        visit(key, ())
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class Skipped:
    """A feature that was not computed, and why. Never silently dropped."""

    spec: FeatureSpec
    reason: UnavailableReason
    detail: str

    @property
    def label(self) -> str:
        return self.spec.label


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Everything one execution produced. The hand-off to an impure caller.

    A caller persists `values`, and emits `analytics_skipped_total` and
    `feature_unavailable_total` from `skipped` and `unavailable`. Nothing here touches
    a store, a clock or a metrics registry.
    """

    values: tuple[MetricValue, ...] = ()
    unavailable: tuple[Unavailable, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    @property
    def computed_count(self) -> int:
        return len(self.values)

    def value_for(self, identifier: str, version: int) -> MetricValue | None:
        for value in self.values:
            if value.feature_id == identifier and value.feature_version == version:
                return value
        return None

    def reasons(self) -> dict[str, str]:
        """`label -> reason`, for logging and for `analytics_skipped_total` labels."""
        out = {s.label: s.reason.value for s in self.skipped}
        out.update(
            {f"{u.feature_id}@v{u.feature_version}": u.reason.value for u in self.unavailable}
        )
        return out


class FeatureEngine:
    """Runs a set of features against one state. Pure: no I/O, no clock, no globals.

    `computed_at` is a parameter, not a reading. An engine that could read the clock
    could not be replayed, which would defeat the single-implementation rule the whole
    analytics layer exists to preserve.
    """

    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or REGISTRY

    @property
    def registry(self) -> Registry:
        return self._registry

    def due_at(
        self, specs: tuple[FeatureSpec, ...], elapsed_seconds: float
    ) -> tuple[FeatureSpec, ...]:
        """Features whose `sampling_frequency` has elapsed.

        A 15-minute feature does not recompute every second (`07` §2); expensive
        features run on their own cadence rather than on every checkpoint (§6).
        """
        return tuple(s for s in specs if elapsed_seconds >= s.sampling_delta.total_seconds())

    def run(
        self,
        state_context: ComputeContext,
        specs: tuple[FeatureSpec, ...] | None = None,
        params_for: dict[tuple[str, int], Params] | None = None,
    ) -> ExecutionReport:
        """Quality-gate, order, compute. Returns everything, including the refusals."""
        chosen = specs if specs is not None else self._registry.all()
        ordered = topological_order(chosen)
        overrides = params_for or {}

        values: list[MetricValue] = []
        unavailable: list[Unavailable] = []
        skipped: list[Skipped] = []
        resolved: dict[str, MetricValue] = {}

        measures = state_context.quality_measures()
        for spec in ordered:
            # ---- quality gating happens BEFORE computation (`07` §2).
            unmet = spec.unmet_requirements(status=state_context.quality_status, measures=measures)
            if unmet:
                skipped.append(
                    Skipped(
                        spec, UnavailableReason.QUALITY_NOT_MET, "; ".join(r.raw for r in unmet)
                    )
                )
                continue

            # ---- a dependency that did not produce a value blocks this feature.
            missing = [f"{i}@v{v}" for i, v in spec.depends_on if f"{i}@v{v}" not in resolved]
            if missing:
                skipped.append(
                    Skipped(
                        spec,
                        UnavailableReason.DEPENDENCY_UNAVAILABLE,
                        f"unresolved: {', '.join(sorted(missing))}",
                    )
                )
                continue

            ctx = ComputeContext(
                state=state_context.state,
                computed_at=state_context.computed_at,
                params=overrides.get(spec.key, state_context.params),
                history=state_context.history,
                dependencies=tuple(
                    (key, resolved[key])
                    for key in sorted(resolved)
                    if key in {f"{i}@v{v}" for i, v in spec.depends_on}
                ),
            )
            outcome = spec.compute(ctx)
            if isinstance(outcome, Unavailable):
                unavailable.append(outcome)
                continue
            produced = outcome if isinstance(outcome, list) else [outcome]
            for value in produced:
                values.append(value)
                resolved[f"{value.feature_id}@v{value.feature_version}"] = value

        return ExecutionReport(tuple(values), tuple(unavailable), tuple(skipped))
