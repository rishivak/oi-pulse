"""Official Upstox V3 Protobuf frame decoder.

Implements `ProtoFrameDecoder` against the official `MarketDataFeed.proto` definition.
Translates binary WebSocket frames into `DecodedFeedMessage` structures for downstream
normalization.

In accordance with Phase 2 invariants and verified protocol facts:
- Upstox V3 provides NO `provider_event_id` and NO `channel_sequence`.
- `currentTs` and `ltt` provide provider/venue timestamps; neither is an event id or sequence.
- All non-supplied fields remain absent (None or omitted), never populated with fake defaults.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from google.protobuf.message import DecodeError

from oipulse.core.errors import OIPulseError
from oipulse.marketdata.providers.upstox.proto import MarketDataFeed_pb2
from oipulse.marketdata.providers.upstox.v3 import (
    DecodedFeedMessage,
    FeedMessageKind,
    ProtoFrameDecoder,
)

__all__ = ["UpstoxV3ProtoDecoder", "V3ProtobufDecodeError"]

_pb: Any = MarketDataFeed_pb2


class V3ProtobufDecodeError(OIPulseError):
    """Raised when a binary frame cannot be parsed by the official protobuf decoder."""


def _dec(val: float | int | None) -> Decimal | None:
    if val is None:
        return None
    return Decimal(str(val))


def _to_utc(ms: int | None) -> datetime | None:
    if ms is None or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class UpstoxV3ProtoDecoder(ProtoFrameDecoder):
    """Production decoder using the official Upstox V3 protobuf definition."""

    proto_revision: str = "com.upstox.marketdatafeederv3udapi.rpc.proto"

    def decode(self, payload: bytes) -> Sequence[DecodedFeedMessage]:
        """Parse raw bytes into a list of DecodedFeedMessage instances."""
        if not payload:
            raise V3ProtobufDecodeError("cannot decode empty binary frame")

        response = cast(Any, _pb.FeedResponse())
        try:
            response.ParseFromString(payload)
        except DecodeError as exc:
            raise V3ProtobufDecodeError(f"malformed protobuf payload: {exc}") from exc

        current_ts = _to_utc(response.currentTs) if response.currentTs > 0 else None

        # 1. Market Info frames (feed status, segment status)
        if response.type == _pb.Type.market_info:
            market_info = response.marketInfo
            fields: dict[str, Any] = {}
            if market_info.segmentStatus:
                fields["segment_status"] = {
                    seg: _pb.MarketStatus.Name(status)
                    for seg, status in market_info.segmentStatus.items()
                }
            if market_info.casMarketStatus:
                fields["cas_market_status"] = {
                    seg: {"status": info.status, "updated_time": info.updatedTime}
                    for seg, info in market_info.casMarketStatus.items()
                }
            if market_info.preOpenSessionStatus:
                fields["pre_open_session_status"] = {
                    seg: {"status": info.status, "updated_time": info.updatedTime}
                    for seg, info in market_info.preOpenSessionStatus.items()
                }

            return [
                DecodedFeedMessage(
                    kind=FeedMessageKind.MARKET_INFO,
                    instrument_key=None,
                    fields=fields,
                    provider_timestamp=current_ts,
                    raw={"type": "market_info", "fields": fields},
                )
            ]

        # Determine feed kind
        if response.type == _pb.Type.initial_feed:
            kind = FeedMessageKind.INITIAL_SNAPSHOT
        elif response.type == _pb.Type.live_feed:
            kind = FeedMessageKind.LIVE_FEED
        else:
            kind = FeedMessageKind.UNKNOWN

        messages: list[DecodedFeedMessage] = []
        for instrument_key, feed in response.feeds.items():
            feed_union = feed.WhichOneof("FeedUnion")
            fields = {}
            venue_ts: datetime | None = None
            raw_dict: dict[str, Any] = {
                "request_mode": _pb.RequestMode.Name(feed.requestMode)
                if feed.requestMode is not None
                else None
            }

            if feed_union == "ltpc":
                ltpc = feed.ltpc
                fields.update(self._decode_ltpc(ltpc))
                venue_ts = _to_utc(ltpc.ltt) or current_ts
                raw_dict["ltpc"] = fields

            elif feed_union == "firstLevelWithGreeks":
                first = feed.firstLevelWithGreeks
                ltpc_fields = self._decode_ltpc(first.ltpc)
                fields.update(ltpc_fields)
                venue_ts = _to_utc(first.ltpc.ltt) or current_ts

                first_depth = first.firstDepth
                if first_depth.bidP > 0:
                    fields["bid_price"] = _dec(first_depth.bidP)
                if first_depth.bidQ > 0:
                    fields["bid_qty"] = int(first_depth.bidQ)
                if first_depth.askP > 0:
                    fields["ask_price"] = _dec(first_depth.askP)
                if first_depth.askQ > 0:
                    fields["ask_qty"] = int(first_depth.askQ)

                greeks = self._decode_option_greeks(first.optionGreeks)
                if first.iv > 0:
                    fields["iv"] = _dec(first.iv)
                    greeks["iv"] = _dec(first.iv)
                if greeks:
                    fields["option_greeks"] = greeks

                if first.vtt > 0:
                    fields["volume"] = int(first.vtt)
                if first.oi > 0:
                    fields["oi"] = int(first.oi)
                raw_dict["first_level_with_greeks"] = fields

            elif feed_union == "fullFeed":
                full = feed.fullFeed
                full_union = full.WhichOneof("FullFeedUnion")

                if full_union == "marketFF":
                    mff = full.marketFF
                    ltpc_fields = self._decode_ltpc(mff.ltpc)
                    fields.update(ltpc_fields)
                    venue_ts = _to_utc(mff.ltpc.ltt) or current_ts

                    # Market level quotes / depth
                    if mff.marketLevel and mff.marketLevel.bidAskQuote:
                        top = mff.marketLevel.bidAskQuote[0]
                        if top.bidP > 0:
                            fields["bid_price"] = _dec(top.bidP)
                        if top.bidQ > 0:
                            fields["bid_qty"] = int(top.bidQ)
                        if top.askP > 0:
                            fields["ask_price"] = _dec(top.askP)
                        if top.askQ > 0:
                            fields["ask_qty"] = int(top.askQ)

                        fields["depth"] = [
                            {
                                "bid_price": _dec(q.bidP),
                                "bid_qty": int(q.bidQ),
                                "ask_price": _dec(q.askP),
                                "ask_qty": int(q.askQ),
                            }
                            for q in mff.marketLevel.bidAskQuote
                        ]

                    greeks = self._decode_option_greeks(mff.optionGreeks)
                    if mff.iv > 0:
                        fields["iv"] = _dec(mff.iv)
                        greeks["iv"] = _dec(mff.iv)
                    if greeks:
                        fields["option_greeks"] = greeks

                    if mff.vtt > 0:
                        fields["volume"] = int(mff.vtt)
                    if mff.oi > 0:
                        fields["oi"] = int(mff.oi)
                    if mff.atp > 0:
                        fields["atp"] = _dec(mff.atp)
                    if mff.tbq > 0:
                        fields["total_buy_qty"] = int(mff.tbq)
                    if mff.tsq > 0:
                        fields["total_sell_qty"] = int(mff.tsq)

                    raw_dict["market_ff"] = fields

                elif full_union == "indexFF":
                    iff = full.indexFF
                    ltpc_fields = self._decode_ltpc(iff.ltpc)
                    fields.update(ltpc_fields)
                    venue_ts = _to_utc(iff.ltpc.ltt) or current_ts

                    if iff.marketOHLC and iff.marketOHLC.ohlc:
                        candle = iff.marketOHLC.ohlc[0]
                        if candle.open > 0:
                            fields["open"] = _dec(candle.open)
                        if candle.high > 0:
                            fields["high"] = _dec(candle.high)
                        if candle.low > 0:
                            fields["low"] = _dec(candle.low)
                        if candle.close > 0:
                            fields["close"] = _dec(candle.close)

                    raw_dict["index_ff"] = fields

            if venue_ts is None:
                venue_ts = current_ts

            messages.append(
                DecodedFeedMessage(
                    kind=kind,
                    instrument_key=instrument_key,
                    fields=fields,
                    provider_timestamp=venue_ts,
                    raw=raw_dict,
                )
            )

        return messages

    def _decode_ltpc(self, ltpc: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if ltpc.ltp > 0:
            out["ltp"] = _dec(ltpc.ltp)
        if ltpc.cp > 0:
            out["close_price"] = _dec(ltpc.cp)
        if ltpc.ltq > 0:
            out["last_trade_qty"] = int(ltpc.ltq)
        if ltpc.HasField("iep"):
            out["iep"] = _dec(ltpc.iep.value)
        return out

    def _decode_option_greeks(self, greeks: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # Only populate when greek values are non-default or supplied
        if greeks.delta != 0.0 or greeks.theta != 0.0 or greeks.gamma != 0.0 or greeks.vega != 0.0:
            out["delta"] = _dec(greeks.delta)
            out["theta"] = _dec(greeks.theta)
            out["gamma"] = _dec(greeks.gamma)
            out["vega"] = _dec(greeks.vega)
            if greeks.rho != 0.0:
                out["rho"] = _dec(greeks.rho)
        return out
