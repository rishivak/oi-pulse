"""The seven analytic domains (`07-ANALYTICS.md` §4).

Importing this package registers every feature. Nothing computes off-registry, so this
is the single place the catalogue is assembled.
"""

from oipulse.analytics.domains import (
    futures,
    gamma,
    greeks,
    positioning,
    price,
    structure,
    volatility,
)

__all__ = [
    "futures",
    "gamma",
    "greeks",
    "positioning",
    "price",
    "structure",
    "volatility",
]
