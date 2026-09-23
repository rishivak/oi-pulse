"""Instrument identity, metadata versions and vendor mappings.

`docs/design/01-DOMAIN_MODEL.md` §3 and `19-DECISIONS.md` AD-06. Three concepts, kept
apart:

* **Identity** (`Instrument`) is permanent. A March observation and a September
  observation point at the same row.
* **Metadata** (`InstrumentVersion`) is historised. A lot-size revision creates a *new
  version*; reconstructing March's exposure resolves March's version and therefore
  March's lot size. Mutating in place — as the legacy system does — silently rewrites
  history, and its seeded lot sizes are already stale.
* **Vendor identity** (`VendorMapping`) is historised and external. An Upstox
  `instrument_key` is a vendor concern; binding the domain to `"NSE_INDEX|Nifty 50"`
  makes instrument identity a vendor concern, which it is not.

There is no "NIFTY special case" anywhere. An index is an instrument; NIFTY is a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from oipulse.core.clock import ensure_utc
from oipulse.core.ids import ExpiryId, InstrumentId

__all__ = [
    "Exchange",
    "Expiry",
    "ExpiryType",
    "Instrument",
    "InstrumentType",
    "InstrumentVersion",
    "OptionType",
    "VendorMapping",
]


class InstrumentType(StrEnum):
    INDEX = "index"
    EQUITY = "equity"
    FUTURE = "future"
    OPTION = "option"


class OptionType(StrEnum):
    CALL = "CE"
    PUT = "PE"


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"


class ExpiryType(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True, slots=True)
class Expiry:
    """A contract expiry. First-class, per the brief's §6.

    `days_to_expiry` and "front"/"next" are **derived relative to an `as_of`**, never
    stored: "front expiry" means something different in March than today, and storing it
    would be a point-in-time violation.
    """

    id: ExpiryId
    underlying_id: InstrumentId
    expiry_date: date
    expiry_type: ExpiryType
    is_active: bool = True

    def days_to_expiry(self, as_of: date) -> int:
        return (self.expiry_date - as_of).days

    def is_expired(self, as_of: date) -> bool:
        return self.expiry_date < as_of


@dataclass(frozen=True, slots=True)
class Instrument:
    """Stable identity only. Never mutated.

    Contract attributes that *define* the instrument (strike, option type, expiry) live
    here because changing one would make it a different instrument. Attributes that
    merely *describe* it (lot size, tick size, symbol) live in `InstrumentVersion`
    because a venue can revise them without the contract becoming a different contract.
    """

    id: InstrumentId
    instrument_type: InstrumentType
    # Options and futures reference their underlying; an index references nothing.
    underlying_id: InstrumentId | None = None
    expiry_id: ExpiryId | None = None
    strike: Decimal | None = None
    option_type: OptionType | None = None

    def __post_init__(self) -> None:
        if self.instrument_type is InstrumentType.OPTION:
            missing = [
                n
                for n, v in (
                    ("underlying_id", self.underlying_id),
                    ("expiry_id", self.expiry_id),
                    ("strike", self.strike),
                    ("option_type", self.option_type),
                )
                if v is None
            ]
            if missing:
                raise ValueError(f"option instrument requires {', '.join(missing)}")
            if self.strike is not None and self.strike <= 0:
                raise ValueError(f"strike must be positive, got {self.strike}")
        if self.instrument_type is InstrumentType.FUTURE and (
            self.underlying_id is None or self.expiry_id is None
        ):
            raise ValueError("future instrument requires underlying_id and expiry_id")

    @property
    def is_derivative(self) -> bool:
        return self.instrument_type in (InstrumentType.OPTION, InstrumentType.FUTURE)


@dataclass(frozen=True, slots=True)
class InstrumentVersion:
    """Historised metadata. Versions for one instrument may not overlap in time.

    `valid_to` of None means "current". The overlap constraint is enforced in the
    database with an exclusion constraint (`02-DATA_MODEL.md` §11); this class enforces
    only internal coherence.
    """

    instrument_id: InstrumentId
    valid_from: datetime
    valid_to: datetime | None
    symbol: str
    exchange: Exchange
    lot_size: int
    tick_size: Decimal
    trading_symbol: str | None = None
    contract_attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_from", ensure_utc(self.valid_from))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", ensure_utc(self.valid_to))
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be after valid_from")
        if self.lot_size <= 0:
            raise ValueError(f"lot_size must be positive, got {self.lot_size}")

    def covers(self, at: datetime) -> bool:
        at = ensure_utc(at)
        return self.valid_from <= at and (self.valid_to is None or at < self.valid_to)


@dataclass(frozen=True, slots=True)
class VendorMapping:
    """Historised external identity.

    Vendor keys change format and get reissued. Keeping the mapping temporal means a
    March observation resolves through March's key even after the vendor reissues it.
    """

    instrument_id: InstrumentId
    vendor: str
    vendor_key: str
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_from", ensure_utc(self.valid_from))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", ensure_utc(self.valid_to))
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be after valid_from")

    def covers(self, at: datetime) -> bool:
        at = ensure_utc(at)
        return self.valid_from <= at and (self.valid_to is None or at < self.valid_to)
