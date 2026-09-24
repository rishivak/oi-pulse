"""Layer 4 — Analytics.

`docs/design/07-ANALYTICS.md`. Every analytic is a **pure function** of an explicitly
supplied input set:

    compute(state, params) -> MetricValue | Unavailable

Purity is not stylistic. It is what makes one implementation serve live processing,
reconstruction, replay, research and backtest with no divergence between a "live" and a
"historical" code path — the classic source of backtest/live mismatch. It is enforced by
the `analytics-is-pure` contract in `tools/check_import_boundaries.py`, which was armed
in Phase 1 and becomes mandatory now: this package may import `marketstate` and `core`
only, and never a database, an HTTP client or the clock.

Consequences visible throughout this package:

* `computed_at` is **passed in**, never read. An analytic that could read the clock
  could not be replayed.
* Metrics and persistence live in the caller. `observability` is not importable here,
  because its registry is process-global mutable state.
* Anything needing history receives it as an explicit tuple of prior states.

Importing this package loads the domain modules, which is what populates `REGISTRY`.
Nothing computes off-registry, so the catalogue must be complete before any execution;
loading it on package import means a caller cannot accidentally run against a partially
populated registry and silently produce fewer features than it asked for.
"""

from oipulse.analytics import domains as _domains  # noqa: F401  (import for side effect)
from oipulse.analytics.registry import REGISTRY

__all__ = ["REGISTRY"]
