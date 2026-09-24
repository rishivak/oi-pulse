"""Rendering a `MarketState` as the API response envelope.

`docs/design/12-API_SPEC.md` §2. Kept out of `api/` deliberately: the envelope is a
pure function of the state, and putting it here means the contract -- both times echoed
back, `semantics` named, quality and provenance on every response -- is testable on an
interpreter with no web stack installed. It also keeps `api/` to parsing, delegation
and transport, which is all that layer is permitted to do (`14-DEPLOYMENT.md` §1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from oipulse.marketstate.state import MarketState

__all__ = ["state_to_envelope"]


def _num(value: Decimal | None) -> str | None:
    """Decimals as strings on the wire.

    JSON numbers are IEEE doubles in most clients, and a rupee price that round-trips
    through a double is no longer the price the venue quoted.
    """
    return None if value is None else str(value)


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _seconds(value: timedelta | None) -> float | None:
    return None if value is None else value.total_seconds()


def state_to_envelope(state: MarketState) -> dict[str, Any]:
    """The response envelope from `12-API_SPEC.md` §2.

    Quality and provenance travel with **every** response: a consumer must not be able
    to render a number without having been told how reliable it is.
    """
    semantics = (
        "knowledge_at" if state.knowledge_horizon == state.market_time else "market_truth_at"
    )
    return {
        "data": {
            "underlying_id": int(state.identity.underlying_id),
            "session_phase": state.session_phase.value,
            "session_date": state.session_date.isoformat() if state.session_date else None,
            "spot": {
                "ltp": _num(state.spot.ltp),
                "change": _num(state.spot.change),
                "prev_close": _num(state.spot.prev_close),
                "open": _num(state.spot.open),
                "high": _num(state.spot.high),
                "low": _num(state.spot.low),
                "observed_at": _ts(state.spot.observed_at),
                "age_seconds": _seconds(state.spot.age),
                "stale": state.spot.stale,
            },
            "futures": [
                {
                    "instrument_id": int(f.instrument_id),
                    "expiry_id": int(f.expiry_id) if f.expiry_id is not None else None,
                    "ltp": _num(f.ltp),
                    "oi": f.oi,
                    "volume": f.volume,
                    "basis": _num(f.basis(state.spot.ltp)),
                    "observed_at": _ts(f.observed_at),
                    "age_seconds": _seconds(f.age),
                    "stale": f.stale,
                }
                for f in state.futures
            ],
            "expiries": [
                {
                    "expiry_id": int(e.expiry_id),
                    "expiry_date": e.expiry_date.isoformat(),
                    "coverage_ratio": e.coverage_ratio,
                    "missing_leg_count": e.missing_leg_count,
                    "aggregates": {
                        "total_call_oi": e.aggregates.total_call_oi,
                        "total_put_oi": e.aggregates.total_put_oi,
                        "pcr": _num(e.aggregates.pcr),
                        "atm_strike": _num(e.aggregates.atm_strike),
                    },
                    "surfaces": {
                        "oi_by_strike": {
                            str(k): list(v) for k, v in e.surfaces.oi_by_strike.items()
                        },
                        "iv_by_strike": {
                            str(k): [_num(v[0]), _num(v[1])]
                            for k, v in e.surfaces.iv_by_strike.items()
                        },
                        "gamma_by_strike": {
                            str(k): [_num(v[0]), _num(v[1])]
                            for k, v in e.surfaces.gamma_by_strike.items()
                        },
                    },
                    "legs": [
                        {
                            "instrument_id": int(leg.instrument_id),
                            "strike": _num(leg.strike),
                            "option_type": leg.option_type,
                            "ltp": _num(leg.ltp),
                            "bid": _num(leg.bid),
                            "ask": _num(leg.ask),
                            "volume": leg.volume,
                            "oi": leg.oi,
                            "provider_prev_oi": leg.provider_prev_oi,
                            "iv": _num(leg.iv),
                            "delta": _num(leg.delta),
                            "gamma": _num(leg.gamma),
                            "theta": _num(leg.theta),
                            "vega": _num(leg.vega),
                            "quote_observed_at": _ts(leg.quote_observed_at),
                            "greeks_observed_at": _ts(leg.greeks_observed_at),
                            "quote_age_seconds": _seconds(leg.quote_age),
                            "greeks_age_seconds": _seconds(leg.greeks_age),
                            "quote_stale": leg.quote_stale,
                            "oi_stale": leg.oi_stale,
                            "greeks_stale": leg.greeks_stale,
                        }
                        for leg in e.legs
                    ],
                }
                for e in state.expiries
            ],
        },
        "meta": {
            "market_time": state.market_time.isoformat(),
            "knowledge_time": state.knowledge_horizon.isoformat(),
            "semantics": semantics,
            "quality": {
                "status": state.quality.status.value,
                "coverage_ratio": state.quality.coverage_ratio,
                "staleness_p95_seconds": _seconds(state.quality.staleness_p95),
                "issues": [
                    {
                        "type": i.type.value,
                        "severity": i.severity.value,
                        "instrument_id": i.instrument_id,
                        "detail": i.detail,
                    }
                    for i in state.quality.issues
                ],
            },
            "coherence_mode": state.coherence.mode.value,
            "coherence": {
                "mode": state.coherence.mode.value,
                "is_cross_sectional": state.coherence.mode.is_cross_sectional,
                "max_component_age_seconds": _seconds(state.coherence.max_component_age),
                "chain_snapshot_ref": state.coherence.chain_snapshot_ref,
                "ws_merge_count": state.coherence.ws_merge_count,
            },
            "provenance": {
                "build_context_id": state.provenance.build_context_id,
                "builder_version": state.provenance.build_context.builder_version,
                "staleness_policy_version": (
                    state.provenance.build_context.staleness_policy_version
                ),
                "feature_set_version": state.provenance.build_context.feature_set_version,
                "observation_refs": list(state.provenance.observation_refs),
                "assembled_at": state.provenance.assembled_at.isoformat(),
                "content_digest": state.content_digest(),
            },
        },
    }
