"""Upstox v2 payload shapes.

**Provenance of these shapes.** They are transcribed from
`backend/app/integrations/upstox/schemas.py`, the legacy integration that ran against the
live Upstox v2 API. That is real evidence of the response structure, not a guess — but it
is **second-hand and not re-verified in Phase 2**, and it may be incomplete: the legacy
models were written to consume a subset, so absence of a field here is not evidence the
provider omits it.

Consequences, recorded rather than assumed (constraint D):

* Field *presence* is treated as probable, never certain. Every field is optional and
  normalization tolerates absence.
* Anything unrecognised is preserved in `raw_extra` rather than dropped, so a schema
  addition is never silently lost before we notice it (`06` §8).
* Provider **event ids** and **channel sequence numbers** do not appear in the legacy
  REST models at all. Whether the WebSocket feed supplies them is assumption A-1 and is
  resolved by the soak, not here.

Stdlib dataclasses rather than Pydantic: this module is parsed defensively from `dict`
anyway, and keeping the dependency out means the normalizer is testable with no install.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

__all__ = [
    "KNOWN_GREEK_FIELDS",
    "KNOWN_MARKET_FIELDS",
    "UpstoxMarketData",
    "UpstoxOptionChainRow",
    "UpstoxOptionGreeks",
    "UpstoxOptionLeg",
    "parse_chain_response",
    "split_known",
]

# Fields the legacy integration consumed. Used to decide what lands in `raw_extra`.
KNOWN_MARKET_FIELDS = frozenset(
    {
        "ltp",
        "volume",
        "oi",
        "close_price",
        "bid_price",
        "ask_price",
        "prev_oi",
        "bid_qty",
        "ask_qty",
    }
)

KNOWN_GREEK_FIELDS = frozenset({"vega", "theta", "gamma", "delta", "iv", "rho"})


def _dec(value: Any) -> Decimal | None:
    """Coerce a provider number to Decimal, via `str` to avoid float error.

    JSON gives us floats; `Decimal(0.1)` is not `Decimal("0.1")`. Routing through `str`
    keeps the value the provider actually sent rather than its binary approximation.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def split_known(payload: dict[str, Any], known: frozenset[str]) -> dict[str, Any]:
    """Return the fields *not* in `known` — the raw-extra remainder."""
    return {k: v for k, v in payload.items() if k not in known}


@dataclass(frozen=True, slots=True)
class UpstoxMarketData:
    ltp: Decimal | None = None
    volume: int | None = None
    oi: int | None = None
    close_price: Decimal | None = None
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    prev_oi: int | None = None
    raw_extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict[str, Any] | None) -> UpstoxMarketData | None:
        if not payload:
            return None
        return cls(
            ltp=_dec(payload.get("ltp")),
            volume=_int(payload.get("volume")),
            oi=_int(payload.get("oi")),
            close_price=_dec(payload.get("close_price")),
            bid_price=_dec(payload.get("bid_price")),
            ask_price=_dec(payload.get("ask_price")),
            bid_qty=_int(payload.get("bid_qty")),
            ask_qty=_int(payload.get("ask_qty")),
            prev_oi=_int(payload.get("prev_oi")),
            raw_extra=split_known(payload, KNOWN_MARKET_FIELDS),
        )


@dataclass(frozen=True, slots=True)
class UpstoxOptionGreeks:
    """All five greeks are captured.

    The legacy pipeline parsed this exact block and persisted only `iv`, discarding
    delta, gamma, theta and vega — data that cannot be recovered once the moment passes.
    """

    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    raw_extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict[str, Any] | None) -> UpstoxOptionGreeks | None:
        if not payload:
            return None
        return cls(
            iv=_dec(payload.get("iv")),
            delta=_dec(payload.get("delta")),
            gamma=_dec(payload.get("gamma")),
            theta=_dec(payload.get("theta")),
            vega=_dec(payload.get("vega")),
            rho=_dec(payload.get("rho")),
            raw_extra=split_known(payload, KNOWN_GREEK_FIELDS),
        )


@dataclass(frozen=True, slots=True)
class UpstoxOptionLeg:
    instrument_key: str | None = None
    market_data: UpstoxMarketData | None = None
    option_greeks: UpstoxOptionGreeks | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any] | None) -> UpstoxOptionLeg | None:
        if not payload:
            return None
        return cls(
            instrument_key=payload.get("instrument_key"),
            market_data=UpstoxMarketData.parse(payload.get("market_data")),
            option_greeks=UpstoxOptionGreeks.parse(payload.get("option_greeks")),
        )


@dataclass(frozen=True, slots=True)
class UpstoxOptionChainRow:
    strike_price: Decimal
    expiry: str
    underlying_key: str | None = None
    underlying_spot_price: Decimal | None = None
    pcr: Decimal | None = None
    call_options: UpstoxOptionLeg | None = None
    put_options: UpstoxOptionLeg | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> UpstoxOptionChainRow | None:
        strike = _dec(payload.get("strike_price"))
        expiry = payload.get("expiry")
        if strike is None or not expiry:
            # A row without a strike or expiry cannot be attached to an instrument.
            # Dropping it is correct; recording why is the caller's job.
            return None
        return cls(
            strike_price=strike,
            expiry=str(expiry),
            underlying_key=payload.get("underlying_key"),
            underlying_spot_price=_dec(payload.get("underlying_spot_price")),
            pcr=_dec(payload.get("pcr")),
            call_options=UpstoxOptionLeg.parse(payload.get("call_options")),
            put_options=UpstoxOptionLeg.parse(payload.get("put_options")),
        )


def parse_chain_response(
    body: dict[str, Any],
) -> tuple[list[UpstoxOptionChainRow], list[str]]:
    """Parse a `/option/chain` body into rows plus a list of rejection reasons.

    Malformed rows are reported, not silently skipped: a chain that is quietly 30%
    shorter than it should be looks like a thin market rather than a parsing failure.
    """
    rows: list[UpstoxOptionChainRow] = []
    rejected: list[str] = []
    for index, raw in enumerate(body.get("data") or []):
        if not isinstance(raw, dict):
            rejected.append(f"row {index}: not an object")
            continue
        parsed = UpstoxOptionChainRow.parse(raw)
        if parsed is None:
            rejected.append(f"row {index}: missing strike_price or expiry")
            continue
        rows.append(parsed)
    return rows, rejected
