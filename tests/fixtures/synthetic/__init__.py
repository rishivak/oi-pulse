"""SYNTHETIC fixtures. NOT recorded Upstox data. See README.md in this directory.

Structure transcribed from the legacy integration that ran against live Upstox v2;
all values invented. Verifies our code, never the provider's behaviour.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SYNTHETIC_CHAIN_MULTI_EXPIRY",
    "SYNTHETIC_CHAIN_RESPONSE",
    "SYNTHETIC_CHAIN_WITH_MALFORMED_ROW",
    "SYNTHETIC_HISTORICAL_OI",
    "SYNTHETIC_WS_TICK_NO_IDENTITY",
    "SYNTHETIC_WS_TICK_WITH_IDENTITY",
    "synthetic_chain",
]

#: Marks every fixture so a reader grepping the test output cannot mistake its origin.
PROVENANCE = "SYNTHETIC — not recorded from Upstox"


def _leg(instrument_key: str, ltp: float, oi: int, iv: float, delta: float) -> dict[str, Any]:
    return {
        "instrument_key": instrument_key,
        "market_data": {
            "ltp": ltp,
            "volume": 12000,
            "oi": oi,
            "prev_oi": oi - 20000,
            "close_price": ltp - 2.5,
            "bid_price": ltp - 0.4,
            "ask_price": ltp + 0.4,
        },
        "option_greeks": {
            "iv": iv,
            "delta": delta,
            "gamma": 0.00081,
            "theta": -9.14,
            "vega": 11.2,
        },
    }


def synthetic_chain(expiry: str, strikes: list[int], spot: float = 25050.4) -> dict[str, Any]:
    """Build a synthetic chain body for one expiry."""
    return {
        "status": "success",
        "_provenance": PROVENANCE,
        "data": [
            {
                "expiry": expiry,
                "strike_price": strike,
                "underlying_key": "NSE_INDEX|Nifty 50",
                "underlying_spot_price": spot,
                "pcr": 1.14,
                "call_options": _leg(f"NSE_FO|CE{strike}|{expiry}", 120.5, 450000, 14.8, 0.52),
                "put_options": _leg(f"NSE_FO|PE{strike}|{expiry}", 98.2, 612000, 15.4, -0.48),
            }
            for strike in strikes
        ],
    }


SYNTHETIC_CHAIN_RESPONSE = synthetic_chain("2026-03-05", [24900, 25000, 25100])

#: Two expiries, to exercise multi-expiry handling rather than front-expiry only.
SYNTHETIC_CHAIN_MULTI_EXPIRY = {
    "2026-03-05": synthetic_chain("2026-03-05", [24900, 25000, 25100]),
    "2026-03-26": synthetic_chain("2026-03-26", [24900, 25000, 25100]),
}

SYNTHETIC_CHAIN_WITH_MALFORMED_ROW = {
    "status": "success",
    "_provenance": PROVENANCE,
    "data": [
        SYNTHETIC_CHAIN_RESPONSE["data"][0],
        {"expiry": "2026-03-05"},  # no strike_price — must be rejected and reported
        {"strike_price": 25200},  # no expiry — must be rejected and reported
        "not-an-object",  # must be rejected and reported
    ],
}

#: A WS frame from a **hypothetical** provider that supplies its own event identity.
#:
#: Upstox V3 does NOT: verification observed `provider_event_id` and `channel_sequence`
#: both absent across two feed sessions. This fixture exists to keep the tier-1 and
#: tier-2 identity paths under test for a future provider that does supply them, and it
#: uses the explicit field names, not guessed ones. An earlier version used `event_id`
#: and `sequence`, which encouraged the adapter to guess at candidate key names and
#: risked promoting a coincidentally-named field to a provider identity.
SYNTHETIC_WS_TICK_WITH_IDENTITY = {
    "_provenance": PROVENANCE,
    "instrument_key": "NSE_FO|CE25000|2026-03-05",
    "channel": "option_chain",
    "provider_event_id": "evt-000001",
    "channel_sequence": 1,
    "exchange_timestamp": "2026-03-03T06:00:00.250000+00:00",
    "market_data": {
        "ltp": 121.0,
        "oi": 451000,
        "volume": 12100,
        "bid_price": 120.6,
        "ask_price": 121.4,
    },
    "option_greeks": {"iv": 14.9, "delta": 0.523, "gamma": 0.00082, "theta": -9.2, "vega": 11.3},
}

#: The same frame with no identity hints — the A-1 degradation case, which must produce
#: WEAK confidence and must NOT produce a sequence-gap claim.
SYNTHETIC_WS_TICK_NO_IDENTITY = {
    "_provenance": PROVENANCE,
    "instrument_key": "NSE_FO|CE25000|2026-03-05",
    "channel": "option_chain",
    "market_data": {"ltp": 121.0, "oi": 451000},
}

SYNTHETIC_HISTORICAL_OI = {
    "status": "success",
    "_provenance": PROVENANCE,
    "data": [
        {"instrument_key": "NSE_FO|CE25000|2026-03-05", "oi": 448000, "close_spot": 25012.3},
        {"instrument_key": "NSE_FO|PE25000|2026-03-05", "oi": 605000, "close_spot": 25012.3},
    ],
}
