"""Layer 5 — Signals.

`docs/design/08-SIGNALS.md`. A signal is an **inference about developing market
structure**, backed by referenced evidence, lifecycle-tracked, explicitly falsifiable,
and accompanied by a contradiction assessment. It is not a trade recommendation and
never carries an unexplained confidence number.

Rules are pure over `RuleContext` — a `MarketState` plus the metric values available at
its knowledge horizon — under the same purity contract as analytics: no database, no
HTTP, no clock, no mutable global state. That is what lets one implementation serve
live evaluation, replay and backtest without a second code path to drift.

Alerts are a **separate layer** (`oipulse/alerts`). Delivery never mutates signal truth:
a signal exists whether or not anyone is listening.

Importing this package loads the rule catalogue, so nothing evaluates off-registry.
"""

from oipulse.signals import catalogue as _catalogue  # noqa: F401  (import for side effect)
from oipulse.signals.rules import RULES

__all__ = ["RULES"]
