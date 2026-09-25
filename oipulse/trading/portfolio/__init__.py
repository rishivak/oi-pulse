"""Layer 11 — portfolio and attribution. `11-TRADING.md` §8.

```
canonical fills ──► PositionBook ──► valuation (from MarketState) ──► snapshot
                          │                                              │
                          └──► position reconciliation          attribution + RESIDUAL
```

Answers the five questions `18-ROADMAP.md` Phase 11 poses: what is held, what it is
worth, how P&L changed, who contributed, and **what remains unexplained**.

### Three properties the rest of the layer is built around

**Positions are a fold, never a running total.** `11` §8. The fold is the verified
Phase 7 `Ledger`, reused rather than reimplemented, so a portfolio position and a
backtest position cannot disagree about what a trade cost. The cost-basis method is
weighted average — inherited, not chosen here (brief §7).

**Valuation is point-in-time by construction.** `value_positions` takes a
`MarketState`, which is already scoped to `(T, K, BuildContext)`. There is no
parameter through which a later price could arrive, so brief §10's "never substitute
a future price" is structural rather than a rule to remember.

**The residual is computed, never balanced.** `residual = total - sum(components)`.
No component is adjusted to make the sum come out, and a large residual is reported
prominently — `18` Phase 11 calls it information, not something to hide.
"""

from oipulse.trading.portfolio.attribution import (
    ATTRIBUTION_METHOD,
    ATTRIBUTION_METHOD_VERSION,
    UNATTRIBUTED,
    AttributionBucket,
    AttributionComponent,
    AttributionResult,
    AttributionSlice,
    ComponentAmount,
    GreekInputs,
    attribute_position,
    group_by,
    roll_up,
)
from oipulse.trading.portfolio.economics import (
    UNITS_PER_QUANTITY,
    ContractEconomics,
    MultiplierSource,
    resolve_economics,
)
from oipulse.trading.portfolio.positions import (
    CostBasisMethod,
    PortfolioPosition,
    PositionBook,
    PositionKey,
    PositionStatus,
)
from oipulse.trading.portfolio.reconciliation import (
    PositionDiscrepancy,
    PositionDiscrepancyKind,
    PositionReconciler,
    PositionReconciliationRun,
    PositionResolution,
)
from oipulse.trading.portfolio.snapshot import (
    PortfolioGreeks,
    PortfolioSnapshot,
    ReturnInputs,
    concentration_of,
    drawdown_from,
    margin_utilisation_from,
)
from oipulse.trading.portfolio.valuation import (
    MarkSource,
    PositionValuation,
    UnvaluedReason,
    ValuationRefused,
    ValuationResult,
    marks_from_state,
    value_positions,
)

__all__ = [
    "ATTRIBUTION_METHOD",
    "ATTRIBUTION_METHOD_VERSION",
    "UNATTRIBUTED",
    "UNITS_PER_QUANTITY",
    "AttributionBucket",
    "AttributionComponent",
    "AttributionResult",
    "AttributionSlice",
    "ComponentAmount",
    "ContractEconomics",
    "CostBasisMethod",
    "GreekInputs",
    "MarkSource",
    "MultiplierSource",
    "PortfolioGreeks",
    "PortfolioPosition",
    "PortfolioSnapshot",
    "PositionBook",
    "PositionDiscrepancy",
    "PositionDiscrepancyKind",
    "PositionKey",
    "PositionReconciler",
    "PositionReconciliationRun",
    "PositionResolution",
    "PositionStatus",
    "PositionValuation",
    "ReturnInputs",
    "UnvaluedReason",
    "ValuationRefused",
    "ValuationResult",
    "attribute_position",
    "concentration_of",
    "drawdown_from",
    "group_by",
    "margin_utilisation_from",
    "marks_from_state",
    "resolve_economics",
    "roll_up",
    "value_positions",
]
