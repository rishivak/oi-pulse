"""The signal catalogue — `08-SIGNALS.md` §6.

Importing this package registers every rule. Nothing evaluates off-registry.

> All are **observations about market structure**. None is named for a trade
> direction, and that naming discipline is deliberate — it keeps the layer honest.
"""

from oipulse.signals.catalogue import (
    cross_market,
    positioning,
    structure,
    volatility,
)

__all__ = ["cross_market", "positioning", "structure", "volatility"]
