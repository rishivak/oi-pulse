"""Contract economics — resolved from canonical instrument metadata, never assumed.

Phase 11 brief §21:

> Ensure option/futures portfolio valuation respects explicit contract multiplier, lot
> size, tick/price units, expiry, instrument identity. Use canonical instrument
> metadata where the architecture provides it. **Do not silently assume economic
> parameters.**

and `07-ANALYTICS.md` §4.3:

> the lot size resolved from the **instrument version valid at `observed_at`**
> (`01-DOMAIN_MODEL.md` §3). A lot-size revision does not retroactively rewrite
> historical exposure.

### The unit question, answered explicitly

The most dangerous thing this module could do is apply `lot_size` twice. It does not,
and the reason needs stating rather than assuming.

Throughout OI Pulse, an order's `quantity` is **in units, not in lots**. The Phase 7
cost model demonstrates it: a fill of 50 at 106.40 produces a turnover of 5,320, which
is one NIFTY lot's premium — not fifty lots'. So a position of 50 units *is* one lot of
50, and its notional is `price * 50`.

Multiplying that by `lot_size` again would inflate every options position fifty-fold.
`units_per_quantity` is therefore **1**, and it is a named, documented constant rather
than an absence — `07` §4.3's "x lot size" applies to per-contract greeks and open
interest, which are quoted per contract, not to a unit quantity that already counts
them.

`lot_size` is still resolved and carried, because it is needed to *report* a position
in lots and to validate that a quantity is a whole number of them.

### Contract multiplier

`InstrumentVersion` has no `contract_multiplier` field; `07` §4.3 names one. Rather
than invent a field, it is read from `contract_attributes` when the instrument carries
it. When absent the multiplier is **1**, and `multiplier_source` records that it was a
declared default rather than a value from the instrument — which is what makes this
not a silent assumption. For NSE index options that default is correct: there is no
multiplier separate from the lot, and premium x units is the economic value.

### When metadata is missing entirely

`resolve` returns `None`. A position whose instrument version cannot be resolved at the
valuation time is **not valued at a guessed multiplier** — it is reported as unvalued,
exactly like a position with no price. Brief §9's prohibition on substituting values
applies to economics as much as to prices.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.instruments.models import InstrumentVersion

__all__ = [
    "UNITS_PER_QUANTITY",
    "ContractEconomics",
    "MultiplierSource",
    "resolve_economics",
]

#: A quantity of 1 is one unit, not one lot. See the module docstring: multiplying by
#: `lot_size` here would double-count it, because quantities are already in units.
UNITS_PER_QUANTITY = 1


class MultiplierSource(StrEnum):
    """Where the contract multiplier came from. Recorded, never implicit."""

    #: Read from the instrument version's `contract_attributes`.
    INSTRUMENT_METADATA = "INSTRUMENT_METADATA"
    #: No multiplier on the instrument. 1 is the declared default, and saying so is
    #: what stops it being a silent assumption.
    DECLARED_DEFAULT = "DECLARED_DEFAULT"


@dataclass(frozen=True, slots=True)
class ContractEconomics:
    """The economic parameters of one instrument at one moment.

    Carries the version's validity window so a reader can see *which* revision was
    used. `07` §4.3: a lot-size revision must not retroactively rewrite historical
    exposure, and the only way to demonstrate that is to record what was in force.
    """

    instrument_id: int
    lot_size: int
    tick_size: Decimal
    multiplier: Decimal
    multiplier_source: MultiplierSource
    #: The instrument version this came from.
    valid_from: datetime
    valid_to: datetime | None
    symbol: str = ""

    def notional(self, price: Decimal, quantity: int) -> Decimal:
        """Economic value of `quantity` units at `price`.

        `UNITS_PER_QUANTITY` is 1 and appears in the arithmetic deliberately, so a
        reader can see that the unit conversion was considered rather than omitted.
        """
        return price * Decimal(quantity) * self.multiplier * Decimal(UNITS_PER_QUANTITY)

    def lots(self, quantity: int) -> Decimal:
        """How many lots `quantity` units represent. Reporting only."""
        return Decimal(quantity) / Decimal(self.lot_size)

    def is_whole_lots(self, quantity: int) -> bool:
        return quantity % self.lot_size == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "lot_size": self.lot_size,
            "tick_size": str(self.tick_size),
            "multiplier": str(self.multiplier),
            "multiplier_source": self.multiplier_source.value,
            "units_per_quantity": UNITS_PER_QUANTITY,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": None if self.valid_to is None else self.valid_to.isoformat(),
            "symbol": self.symbol,
        }


def resolve_economics(
    instrument_id: int, versions: Sequence[InstrumentVersion], *, at: datetime
) -> ContractEconomics | None:
    """The economics in force at `at`, or None if no version covers it.

    `None` is a real answer and the caller must treat it as one: a position whose
    instrument metadata cannot be resolved is reported unvalued rather than valued on
    a guess. Returning a default `ContractEconomics` here would be the exact silent
    assumption brief §21 forbids.

    Versions are filtered by `covers(at)`, which is `01-DOMAIN_MODEL.md` §3's rule and
    is what keeps a later lot-size revision out of an earlier valuation.
    """
    covering = [
        version
        for version in versions
        if int(version.instrument_id) == instrument_id and version.covers(at)
    ]
    if not covering:
        return None
    if len(covering) > 1:
        # The database enforces non-overlap with an exclusion constraint (`02` §11).
        # In memory it can still happen, and picking one silently would make the
        # valuation depend on iteration order.
        raise ValueError(
            f"instrument {instrument_id} has {len(covering)} overlapping versions "
            f"covering {at.isoformat()}; versions may not overlap "
            f"(01-DOMAIN_MODEL.md §3) and choosing one would make the valuation "
            f"depend on iteration order"
        )

    version = covering[0]
    raw = version.contract_attributes.get("contract_multiplier")
    if raw is None:
        multiplier = Decimal(1)
        source = MultiplierSource.DECLARED_DEFAULT
    else:
        multiplier = Decimal(raw)
        source = MultiplierSource.INSTRUMENT_METADATA
    if multiplier <= 0:
        raise ValueError(
            f"instrument {instrument_id} declares a non-positive contract multiplier "
            f"{multiplier}; a zero or negative multiplier would silently zero or "
            f"invert every valuation of this instrument"
        )

    return ContractEconomics(
        instrument_id=instrument_id,
        lot_size=version.lot_size,
        tick_size=version.tick_size,
        multiplier=multiplier,
        multiplier_source=source,
        valid_from=version.valid_from,
        valid_to=version.valid_to,
        symbol=version.symbol,
    )
