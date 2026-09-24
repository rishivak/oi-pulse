"""Layer 7b — Backtesting.

`docs/design/10-REPLAY.md` §5 to §7. A backtest is replay with a strategy attached and
a fill simulator on the end, driving the *same* pipeline as live processing.

Two separations are structural rather than policed:

* **A strategy cannot reach the database.** `StrategyContext` exposes a state, the
  features and signals available at `T`, and positions — and no repository, session or
  clock. Look-ahead is impossible because there is no API surface through which future
  data could be obtained, not because it is discouraged.
* **A decision is not a fill.** `Strategy.on_state` returns `TradeIntent`s. Turning one
  into a fill goes through the declared execution model, with every assumption
  recorded on the run.

`BacktestResult` prints the assumption set alongside every number. A backtest is a
statement about specific history under stated assumptions, never an expectation.
"""

from oipulse.backtest.costs import INDIAN_OPTIONS_COSTS, CostBreakdown, CostModel
from oipulse.backtest.fills import Fill, FillModel, FillOutcome, SlippageModel
from oipulse.backtest.intents import OrderType, Side, TradeIntent
from oipulse.backtest.ledger import Ledger, LedgerSnapshot, Position
from oipulse.backtest.result import BacktestResult, BacktestStatistics
from oipulse.backtest.risk import UNCONSTRAINED_RISK, RiskGate, RiskVerdict
from oipulse.backtest.runner import BacktestRunner
from oipulse.backtest.strategy import (
    AccountView,
    Strategy,
    StrategyContext,
    StrategySpec,
)

__all__ = [
    "INDIAN_OPTIONS_COSTS",
    "UNCONSTRAINED_RISK",
    "AccountView",
    "BacktestResult",
    "BacktestRunner",
    "BacktestStatistics",
    "CostBreakdown",
    "CostModel",
    "Fill",
    "FillModel",
    "FillOutcome",
    "Ledger",
    "LedgerSnapshot",
    "OrderType",
    "Position",
    "RiskGate",
    "RiskVerdict",
    "Side",
    "SlippageModel",
    "Strategy",
    "StrategyContext",
    "StrategySpec",
    "TradeIntent",
]
