"""The cost model — `10-REPLAY.md` §6.

```
fees   brokerage, STT, exchange charges, GST, stamp duty
```

Every rate is a **declared, versioned parameter**, never a constant buried in a
calculation. `10` §6 requires the assumption set to be printed alongside every number,
and a rate that lives only in code cannot be printed.

**Nothing here defaults to zero.** `14` of the Phase 7 brief forbids hidden assumptions
such as "zero fees", so a `CostModel` must be constructed with explicit rates. The
supplied `INDIAN_OPTIONS_COSTS` is a *named, versioned* set with its basis stated —
using it is an explicit choice, and its version travels onto the result so a reader can
tell which schedule produced a number.

Gross P&L, each cost component and net P&L are kept **separate** all the way through
(brief §14). Collapsing them would make it impossible to ask whether a strategy is
profitable before costs, which is usually the first question worth asking.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

__all__ = ["INDIAN_OPTIONS_COSTS", "CostBreakdown", "CostModel"]

_PAISE = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    """Round to paise, half-up. Declared rather than left to the default context, so
    two runs on different machines cannot disagree in the last place."""
    return value.quantize(_PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Each component, separately. Never pre-summed.

    A caller that wants the total asks for `total`; one that wants to know how much of
    a loss was STT can see it. Collapsing these at source would destroy that.
    """

    brokerage: Decimal = Decimal(0)
    securities_transaction_tax: Decimal = Decimal(0)
    exchange_charges: Decimal = Decimal(0)
    gst: Decimal = Decimal(0)
    stamp_duty: Decimal = Decimal(0)
    sebi_charges: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return _round(
            self.brokerage
            + self.securities_transaction_tax
            + self.exchange_charges
            + self.gst
            + self.stamp_duty
            + self.sebi_charges
        )

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        return CostBreakdown(
            brokerage=self.brokerage + other.brokerage,
            securities_transaction_tax=(
                self.securities_transaction_tax + other.securities_transaction_tax
            ),
            exchange_charges=self.exchange_charges + other.exchange_charges,
            gst=self.gst + other.gst,
            stamp_duty=self.stamp_duty + other.stamp_duty,
            sebi_charges=self.sebi_charges + other.sebi_charges,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "brokerage": str(_round(self.brokerage)),
            "securities_transaction_tax": str(_round(self.securities_transaction_tax)),
            "exchange_charges": str(_round(self.exchange_charges)),
            "gst": str(_round(self.gst)),
            "stamp_duty": str(_round(self.stamp_duty)),
            "sebi_charges": str(_round(self.sebi_charges)),
            "total": str(self.total),
        }


@dataclass(frozen=True, slots=True)
class CostModel:
    """A named, versioned cost schedule. Every rate explicit.

    `basis` states where the numbers came from in prose, because a rate schedule with
    no provenance is an assumption nobody can audit. It is part of the content
    address, so editing the basis without editing a rate still produces a new version
    — which is correct: the claim about the numbers changed.
    """

    name: str
    version: int
    basis: str
    #: Flat per-order brokerage, or a percentage of turnover, whichever is lower.
    brokerage_flat: Decimal
    brokerage_rate: Decimal
    #: STT on options is charged on the **sell** side, on premium.
    stt_rate_sell: Decimal
    exchange_rate: Decimal
    gst_rate: Decimal
    #: Stamp duty is charged on the **buy** side.
    stamp_duty_rate_buy: Decimal
    sebi_rate: Decimal

    @property
    def label(self) -> str:
        return f"{self.name}@v{self.version}"

    def charge(self, *, turnover: Decimal, is_buy: bool) -> CostBreakdown:
        """Costs for one fill of `turnover` rupees.

        Side matters and is not averaged away: STT falls on the sell, stamp duty on
        the buy. A model that charged both sides the mean would misstate the cost of
        every one-sided day.
        """
        if turnover < 0:
            raise ValueError("turnover cannot be negative; use `is_buy` for direction")

        brokerage = min(self.brokerage_flat, turnover * self.brokerage_rate)
        stt = Decimal(0) if is_buy else turnover * self.stt_rate_sell
        exchange = turnover * self.exchange_rate
        sebi = turnover * self.sebi_rate
        stamp = turnover * self.stamp_duty_rate_buy if is_buy else Decimal(0)
        # GST applies to brokerage, exchange charges and SEBI fees -- not to STT or
        # stamp duty, which are taxes rather than services.
        gst = (brokerage + exchange + sebi) * self.gst_rate

        return CostBreakdown(
            brokerage=_round(brokerage),
            securities_transaction_tax=_round(stt),
            exchange_charges=_round(exchange),
            gst=_round(gst),
            stamp_duty=_round(stamp),
            sebi_charges=_round(sebi),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "basis": self.basis,
            "brokerage_flat": str(self.brokerage_flat),
            "brokerage_rate": str(self.brokerage_rate),
            "stt_rate_sell": str(self.stt_rate_sell),
            "exchange_rate": str(self.exchange_rate),
            "gst_rate": str(self.gst_rate),
            "stamp_duty_rate_buy": str(self.stamp_duty_rate_buy),
            "sebi_rate": str(self.sebi_rate),
        }


#: A named schedule for NSE index options, with its basis stated.
#:
#: **These rates are an assumption, not a fact about your account.** They reflect
#: commonly published discount-broker rates and statutory charges; brokerage in
#: particular is account-specific. The version travels onto every result, so a reader
#: can tell which schedule produced a number, and a different account is a different
#: named model rather than an edit to this one.
INDIAN_OPTIONS_COSTS = CostModel(
    name="INDIAN_OPTIONS_DISCOUNT",
    version=1,
    basis=(
        "Discount-broker flat brokerage plus statutory charges for NSE index options. "
        "Rates are an assumption and are account-specific; verify against your own "
        "contract note before relying on a net figure."
    ),
    brokerage_flat=Decimal("20"),
    brokerage_rate=Decimal("0.0003"),
    stt_rate_sell=Decimal("0.000625"),
    exchange_rate=Decimal("0.00053"),
    gst_rate=Decimal("0.18"),
    stamp_duty_rate_buy=Decimal("0.00003"),
    sebi_rate=Decimal("0.000001"),
)
