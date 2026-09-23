"""Vendor payload → canonical observations.

`docs/design/06-UPSTOX_INTEGRATION.md` §8. This module is the **only** place vendor
shapes exist. Everything downstream sees canonical types.

Three rules it exists to enforce:

1. **Every field the vendor provides is captured.** The legacy pipeline parsed greeks,
   bid/ask and `prev_oi` and then discarded them. Unrecognised fields go to `raw_extra`
   rather than being dropped.
2. **A paired CE/PE chain row becomes two per-leg observations**, with `option_type` as
   data rather than as a column prefix. The legacy `call_*`/`put_*` column pairs are why
   every per-leg operation there is written twice.
3. **`observed_at` and `ingested_at` are set from different sources** and never
   conflated: `observed_at` from the venue where available, `ingested_at` from the
   injected clock at the moment of receipt.

**Venue timestamp caveat (assumption A-3).** The legacy REST models carry no venue
timestamp on chain rows. Where the provider supplies none, `observed_at` falls back to
receipt time and the substitution is **recorded per observation** in `raw_extra`, because
it materially weakens the bitemporal guarantee and must not be silent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from oipulse.core.clock import Clock, ensure_utc
from oipulse.core.ids import InstrumentId
from oipulse.marketdata.identity import resolve_identity
from oipulse.marketdata.observations import (
    GreeksObservation,
    HistoricalDailyOI,
    IndexObservation,
    MarketObservation,
    QuoteObservation,
    Source,
)
from oipulse.marketdata.providers.upstox.schemas import (
    UpstoxOptionChainRow,
    UpstoxOptionLeg,
)

__all__ = [
    "TIMESTAMP_FALLBACK_KEY",
    "normalize_chain_row",
    "normalize_historical_oi",
    "normalize_index_tick",
    "normalize_ws_tick",
]

#: Marks an observation whose `observed_at` was substituted with receipt time.
TIMESTAMP_FALLBACK_KEY = "_observed_at_fallback"


def _resolve_observed_at(
    venue_timestamp: datetime | None, received_at: datetime, raw_extra: dict[str, Any]
) -> datetime:
    """Venue time when supplied; receipt time otherwise, recorded either way."""
    if venue_timestamp is not None:
        return ensure_utc(venue_timestamp)
    raw_extra[TIMESTAMP_FALLBACK_KEY] = (
        "provider supplied no venue timestamp; observed_at set to receipt time "
        "(assumption A-3, unverified)"
    )
    return received_at


def _leg_observations(
    leg: UpstoxOptionLeg | None,
    instrument_id: InstrumentId,
    observed_at: datetime,
    received_at: datetime,
    source: Source,
    received_seq: int | None,
    hints: dict[str, Any] | None = None,
) -> list[MarketObservation]:
    """Split one vendor leg into a quote observation and a greeks observation.

    They are separate rows because they have different update cadences and different
    staleness budgets (`04-MARKETSTATE.md` §3). Fusing them would force the tighter
    budget onto the slower field, or the looser budget onto the faster one.
    """
    if leg is None:
        return []

    # Identity hints (provider event id, feed session, channel sequence) are applied at
    # construction rather than by rebuilding the observation afterwards: a frozen
    # slots-dataclass cannot be reliably reconstructed from `__slots__`, which omits
    # inherited fields.
    hints = hints or {}
    out: list[MarketObservation] = []
    md = leg.market_data
    if md is not None:
        extra = dict(md.raw_extra)
        payload = {
            "ltp": str(md.ltp),
            "oi": md.oi,
            "volume": md.volume,
            "bid": str(md.bid_price),
            "ask": str(md.ask_price),
        }
        out.append(
            QuoteObservation(
                instrument_id=instrument_id,
                observed_at=observed_at,
                ingested_at=received_at,
                source=source,
                identity=resolve_identity(
                    instrument_id=int(instrument_id),
                    observed_at=observed_at,
                    source=source.value,
                    payload=payload,
                    received_seq=received_seq,
                    **hints,
                ),
                raw_extra=extra,
                ltp=md.ltp,
                bid=md.bid_price,
                ask=md.ask_price,
                bid_qty=md.bid_qty,
                ask_qty=md.ask_qty,
                volume=md.volume,
                oi=md.oi,
                provider_prev_oi=md.prev_oi,
                prev_close=md.close_price,
            )
        )

    gk = leg.option_greeks
    if gk is not None:
        out.append(
            GreeksObservation(
                instrument_id=instrument_id,
                observed_at=observed_at,
                ingested_at=received_at,
                source=source,
                identity=resolve_identity(
                    instrument_id=int(instrument_id),
                    observed_at=observed_at,
                    source=source.value,
                    payload={
                        "kind": "greeks",
                        "iv": str(gk.iv),
                        "delta": str(gk.delta),
                        "gamma": str(gk.gamma),
                        "theta": str(gk.theta),
                        "vega": str(gk.vega),
                    },
                    received_seq=received_seq,
                    **hints,
                ),
                raw_extra=dict(gk.raw_extra),
                iv=gk.iv,
                delta=gk.delta,
                gamma=gk.gamma,
                theta=gk.theta,
                vega=gk.vega,
                rho=gk.rho,
            )
        )
    return out


def normalize_chain_row(
    row: UpstoxOptionChainRow,
    *,
    call_instrument_id: InstrumentId | None,
    put_instrument_id: InstrumentId | None,
    clock: Clock,
    venue_timestamp: datetime | None = None,
    received_seq: int | None = None,
) -> list[MarketObservation]:
    """One chain row → up to four observations (CE quote, CE greeks, PE quote, PE greeks).

    Instrument ids are resolved by the caller from the vendor mapping valid at
    `observed_at`, so this function never performs identity lookup itself — that keeps
    normalization pure and lets the resolution be temporal.
    """
    received_at = clock.now()
    shared_extra: dict[str, Any] = {}
    observed_at = _resolve_observed_at(venue_timestamp, received_at, shared_extra)

    out: list[MarketObservation] = []
    for leg, iid in (
        (row.call_options, call_instrument_id),
        (row.put_options, put_instrument_id),
    ):
        if iid is None:
            continue
        for obs in _leg_observations(
            leg, iid, observed_at, received_at, Source.REST_CHAIN, received_seq
        ):
            if shared_extra:
                obs.raw_extra.update(shared_extra)
            out.append(obs)
    return out


def normalize_ws_tick(
    payload: dict[str, Any],
    *,
    instrument_id: InstrumentId,
    clock: Clock,
    feed_session_id: str,
    channel: str,
    provider_event_id: str | None = None,
    channel_sequence: int | None = None,
    received_seq: int | None = None,
    venue_timestamp: datetime | None = None,
) -> list[MarketObservation]:
    """A live tick → canonical observations.

    `provider_event_id` and `channel_sequence` are passed through to identity
    resolution, which degrades to a content hash with `WEAK` confidence when the
    provider supplies neither (assumption A-1). Nothing here assumes they exist.
    """
    from oipulse.marketdata.providers.upstox.schemas import (
        UpstoxMarketData,
        UpstoxOptionGreeks,
    )

    received_at = clock.now()
    extra: dict[str, Any] = {}
    observed_at = _resolve_observed_at(venue_timestamp, received_at, extra)

    md = UpstoxMarketData.parse(payload.get("market_data") or payload)
    gk = UpstoxOptionGreeks.parse(payload.get("option_greeks"))
    leg = UpstoxOptionLeg(
        instrument_key=payload.get("instrument_key"), market_data=md, option_greeks=gk
    )

    # Hints are passed down so identity is resolved once, at construction. When the
    # provider supplies neither an event id nor a channel sequence, resolution degrades
    # to a content hash with WEAK confidence — recorded, never assumed away (A-1).
    hints = {
        "provider_event_id": provider_event_id,
        "feed_session_id": feed_session_id,
        "channel": channel,
        "channel_sequence": channel_sequence,
    }

    observations = _leg_observations(
        leg, instrument_id, observed_at, received_at, Source.WS, received_seq, hints
    )
    if extra:
        for obs in observations:
            obs.raw_extra.update(extra)
    return observations


def normalize_index_tick(
    payload: dict[str, Any],
    *,
    instrument_id: InstrumentId,
    clock: Clock,
    source: Source = Source.WS,
    feed_session_id: str | None = None,
    channel: str | None = None,
    channel_sequence: int | None = None,
    provider_event_id: str | None = None,
    venue_timestamp: datetime | None = None,
) -> IndexObservation:
    received_at = clock.now()
    extra: dict[str, Any] = {}
    observed_at = _resolve_observed_at(venue_timestamp, received_at, extra)

    def dec(key: str) -> Decimal | None:
        v = payload.get(key)
        return None if v is None else Decimal(str(v))

    return IndexObservation(
        instrument_id=instrument_id,
        observed_at=observed_at,
        ingested_at=received_at,
        source=source,
        identity=resolve_identity(
            instrument_id=int(instrument_id),
            observed_at=observed_at,
            source=source.value,
            payload={"ltp": str(payload.get("ltp"))},
            provider_event_id=provider_event_id,
            feed_session_id=feed_session_id,
            channel=channel,
            channel_sequence=channel_sequence,
        ),
        raw_extra=extra,
        ltp=dec("ltp"),
        prev_close=dec("close_price") or dec("prev_close"),
        open=dec("open"),
        high=dec("high"),
        low=dec("low"),
    )


def normalize_historical_oi(
    payload: dict[str, Any],
    *,
    instrument_id: InstrumentId,
    trade_date: date,
    session_open: time,
    session_close: time,
    clock: Clock,
) -> HistoricalDailyOI:
    """Historical OI → a **date-granular** observation.

    The endpoint is date-based, so the result carries an explicit
    `valid_from`/`valid_to` session interval and `observation_date`. `observed_at` is set
    to `valid_to` for ordering only; the granularity claim is made by the interval, not
    by that timestamp (AD-26).

    `ingested_at` is the backfill run time, which is what makes the row correctly
    invisible to `knowledge_at(T)` for any T before the backfill — no special-case code
    is needed for that, it falls out of the bitemporal model.
    """
    received_at = clock.now()
    valid_from = datetime.combine(trade_date, session_open, tzinfo=UTC)
    valid_to = datetime.combine(trade_date, session_close, tzinfo=UTC)
    if valid_to <= valid_from:
        valid_to = valid_from + timedelta(hours=6, minutes=15)

    oi = payload.get("oi")
    spot = payload.get("close_spot") or payload.get("underlying_spot_price")

    return HistoricalDailyOI(
        instrument_id=instrument_id,
        observed_at=valid_to,
        ingested_at=received_at,
        source=Source.REST_HIST_OI,
        identity=resolve_identity(
            instrument_id=int(instrument_id),
            observed_at=valid_to,
            source=Source.REST_HIST_OI.value,
            payload={"date": trade_date.isoformat(), "oi": oi},
        ),
        raw_extra={
            k: v
            for k, v in payload.items()
            if k not in {"oi", "close_spot", "underlying_spot_price"}
        },
        observation_date=trade_date,
        valid_from=valid_from,
        valid_to=valid_to,
        oi=None if oi is None else int(oi),
        close_spot=None if spot is None else Decimal(str(spot)),
    )
