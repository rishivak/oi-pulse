"""Instrument universes — the subscription model.

`docs/design/01-DOMAIN_MODEL.md` §3. Universes are **system-level, not user-level**: the
market is collected once. Users have watchlists, which are views over what is collected.
The legacy `collector_jobs` row was per `(user, underlying, interval)` and implicitly
front-expiry-only; both are corrected here.

Expiry is a first-class dimension (brief §6), so a universe expresses *which* expiries it
wants rather than assuming the front one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.instruments.models import Expiry, ExpiryType

__all__ = [
    "ALL_EXPIRIES",
    "AllStrikes",
    "AtmRange",
    "DataMode",
    "ExpirySelector",
    "ExplicitExpiries",
    "FrontN",
    "InstrumentUniverse",
    "MoneynessBand",
    "MonthlyN",
    "StrikeSelector",
    "WeeklyExpiries",
]


# --------------------------------------------------------------- expiry selectors


class ExpirySelector(ABC):
    """Chooses expiries from the live set, relative to an `as_of` date.

    Resolution is always relative: "front" is a function of the date, never a stored
    flag (`02-DATA_MODEL.md` §12).
    """

    @abstractmethod
    def select(self, expiries: list[Expiry], as_of: date) -> list[Expiry]: ...

    @staticmethod
    def _live_sorted(expiries: list[Expiry], as_of: date) -> list[Expiry]:
        return sorted(
            (e for e in expiries if e.is_active and not e.is_expired(as_of)),
            key=lambda e: e.expiry_date,
        )


@dataclass(frozen=True, slots=True)
class FrontN(ExpirySelector):
    """The nearest `n` live expiries. `FrontN(1)` is the legacy behaviour."""

    n: int = 1

    def select(self, expiries: list[Expiry], as_of: date) -> list[Expiry]:
        return self._live_sorted(expiries, as_of)[: max(self.n, 0)]


@dataclass(frozen=True, slots=True)
class MonthlyN(ExpirySelector):
    """The nearest `n` monthly expiries — the term-structure workhorse."""

    n: int = 3

    def select(self, expiries: list[Expiry], as_of: date) -> list[Expiry]:
        monthly = [
            e for e in self._live_sorted(expiries, as_of) if e.expiry_type is ExpiryType.MONTHLY
        ]
        return monthly[: max(self.n, 0)]


@dataclass(frozen=True, slots=True)
class WeeklyExpiries(ExpirySelector):
    n: int | None = None

    def select(self, expiries: list[Expiry], as_of: date) -> list[Expiry]:
        weekly = [
            e for e in self._live_sorted(expiries, as_of) if e.expiry_type is ExpiryType.WEEKLY
        ]
        return weekly if self.n is None else weekly[: max(self.n, 0)]


@dataclass(frozen=True, slots=True)
class ExplicitExpiries(ExpirySelector):
    expiry_ids: frozenset[ExpiryId]

    def select(self, expiries: list[Expiry], as_of: date) -> list[Expiry]:
        return [e for e in self._live_sorted(expiries, as_of) if e.id in self.expiry_ids]


class _AllExpiries(ExpirySelector):
    def select(self, expiries: list[Expiry], as_of: date) -> list[Expiry]:
        return self._live_sorted(expiries, as_of)


ALL_EXPIRIES = _AllExpiries()


# --------------------------------------------------------------- strike selectors


class StrikeSelector(ABC):
    """Chooses strikes relative to spot.

    Returns strikes in **ascending distance from ATM**, so a capacity-constrained caller
    can truncate the tail and keep the strikes that matter (`06` §5 degradation ladder).
    """

    @abstractmethod
    def select(self, strikes: list[Decimal], spot: Decimal) -> list[Decimal]: ...

    @staticmethod
    def _by_distance(strikes: list[Decimal], spot: Decimal) -> list[Decimal]:
        return sorted(strikes, key=lambda s: (abs(s - spot), s))


@dataclass(frozen=True, slots=True)
class AtmRange(StrikeSelector):
    """`n` strikes either side of the money."""

    n: int = 10

    def select(self, strikes: list[Decimal], spot: Decimal) -> list[Decimal]:
        if not strikes:
            return []
        ranked = self._by_distance(strikes, spot)
        # 2n + 1 covers n below, n above, and the ATM strike itself.
        return sorted(ranked[: 2 * max(self.n, 0) + 1])


@dataclass(frozen=True, slots=True)
class MoneynessBand(StrikeSelector):
    """Strikes within a fractional band of spot, e.g. 0.9 to 1.1."""

    low: Decimal
    high: Decimal

    def select(self, strikes: list[Decimal], spot: Decimal) -> list[Decimal]:
        if spot <= 0:
            return []
        return sorted(s for s in strikes if self.low <= (s / spot) <= self.high)


class _AllStrikes(StrikeSelector):
    def select(self, strikes: list[Decimal], spot: Decimal) -> list[Decimal]:
        return sorted(strikes)


AllStrikes = _AllStrikes


# ------------------------------------------------------------------- universe


class DataMode(StrEnum):
    """Per-instrument data richness. Drives subscription cost (`06` §5).

    Named to match the provider's own tiers so the planner's arithmetic is auditable
    against the vendor's published limits rather than a translation of them.
    """

    LTPC = "ltpc"  # last price + close
    GREEKS = "greeks"  # LTPC + OI + IV + greeks
    FULL = "full"  # GREEKS + market depth + extended metadata


@dataclass(frozen=True, slots=True)
class InstrumentUniverse:
    """A named, versioned set of instruments under observation.

    System-level: no `user_id`. The market is collected once
    (`02-DATA_MODEL.md` §12).
    """

    name: str
    underlying_ids: tuple[InstrumentId, ...]
    expiry_selector: ExpirySelector
    strike_selector: StrikeSelector
    option_mode: DataMode = DataMode.GREEKS
    future_mode: DataMode = DataMode.GREEKS
    index_mode: DataMode = DataMode.LTPC
    include_futures: bool = True
    is_active: bool = True
    notes: str = field(default="")

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("universe name must be non-empty")
        if not self.underlying_ids:
            raise ValueError("universe must reference at least one underlying")
