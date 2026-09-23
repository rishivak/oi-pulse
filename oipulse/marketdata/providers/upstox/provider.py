"""UpstoxMarketDataProvider — REST + WS behind the canonical contract.

`docs/design/06-UPSTOX_INTEGRATION.md` §1. The only place vendor shapes meet the domain.
Upstox remains the sole provider (CLAUDE.md rule 11).

PARTIALLY EXECUTABLE OFFLINE: construction and normalization delegation are exercised by
fixture-driven tests; anything touching the network is not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date

from oipulse.core.clock import Clock
from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.marketdata.lifecycle import SessionManager
from oipulse.marketdata.observations import HistoricalDailyOI, MarketObservation
from oipulse.marketdata.providers.upstox.normalize import (
    normalize_chain_row,
    normalize_historical_oi,
    normalize_ws_tick,
)
from oipulse.marketdata.providers.upstox.schemas import parse_chain_response
from oipulse.observability.logging import get_logger

log = get_logger(__name__)

__all__ = ["ChainSnapshot", "InstrumentResolver", "UpstoxMarketDataProvider"]


class InstrumentResolver:
    """Maps a vendor key to canonical `InstrumentId`, **as of an instant**.

    Temporal because vendor mappings are historised: a March observation must resolve
    through March's key even after the vendor reissues it (`01` §3).
    """

    def __init__(self) -> None:
        self._by_key: dict[str, list[tuple[object, object, InstrumentId]]] = {}

    def register(
        self,
        vendor_key: str,
        instrument_id: InstrumentId,
        valid_from: object,
        valid_to: object = None,
    ) -> None:
        """Register a mapping, closing any open-ended prior one.

        Mappings for a vendor key must not overlap — the database enforces this with a
        GiST exclusion constraint. When a vendor reissues a key, the previous mapping is
        closed at the new one's `valid_from` rather than being left open, which is what
        actually happens: we learn the key now points elsewhere *from* that moment.

        Without this, an open-ended old mapping keeps matching future instants and a
        reissued key silently resolves to the wrong instrument.
        """
        existing = self._by_key.setdefault(vendor_key, [])
        for index, (prior_from, prior_to, prior_id) in enumerate(existing):
            if prior_to is None and prior_from < valid_from:  # type: ignore[operator]
                existing[index] = (prior_from, valid_from, prior_id)
        existing.append((valid_from, valid_to, instrument_id))
        existing.sort(key=lambda row: row[0])  # type: ignore[arg-type,return-value]

    def resolve(self, vendor_key: str, at: object) -> InstrumentId | None:
        """The mapping in force at *at*.

        Scans newest-first so the most recent applicable mapping wins even if a
        historical overlap slipped past registration.
        """
        for valid_from, valid_to, iid in reversed(self._by_key.get(vendor_key, ())):
            if valid_from <= at and (valid_to is None or at < valid_to):  # type: ignore[operator]
                return iid
        return None


@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """A REST chain response: a cross-sectional consistency set (`04` §4)."""

    underlying_id: InstrumentId
    expiry_id: ExpiryId
    observed_at: object
    ingested_at: object
    legs: tuple[MarketObservation, ...]
    leg_count: int
    expected_leg_count: int | None
    rejected: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        """A partial chain is usable but must never masquerade as a full one."""
        if self.expected_leg_count is None:
            return not self.rejected
        return self.leg_count >= self.expected_leg_count and not self.rejected


class UpstoxMarketDataProvider:
    """Adapter. Transport in `rest.py`/`ws.py`, shapes in `schemas.py`, mapping here."""

    def __init__(
        self,
        rest_client: object,
        clock: Clock,
        resolver: InstrumentResolver,
        sessions: SessionManager,
        ws_client: object | None = None,
    ) -> None:
        self._rest = rest_client
        self._clock = clock
        self._resolver = resolver
        self._sessions = sessions
        self._ws = ws_client

    async def fetch_option_chain(
        self,
        underlying_vendor_key: str,
        underlying_id: InstrumentId,
        expiry_id: ExpiryId,
        expiry: date,
        expected_leg_count: int | None = None,
    ) -> ChainSnapshot:
        """Fetch and normalize one chain into a consistency set."""
        body = await self._rest.get_option_chain(underlying_vendor_key, expiry)  # type: ignore[attr-defined]
        return self.build_chain_snapshot(
            body,
            underlying_id=underlying_id,
            expiry_id=expiry_id,
            expected_leg_count=expected_leg_count,
        )

    def build_chain_snapshot(
        self,
        body: dict,
        *,
        underlying_id: InstrumentId,
        expiry_id: ExpiryId,
        expected_leg_count: int | None = None,
    ) -> ChainSnapshot:
        """Pure: body → snapshot. Split out so fixtures can drive it with no network."""
        rows, rejected = parse_chain_response(body)
        received_at = self._clock.now()
        legs: list[MarketObservation] = []

        for row in rows:
            call_key = row.call_options.instrument_key if row.call_options else None
            put_key = row.put_options.instrument_key if row.put_options else None
            legs.extend(
                normalize_chain_row(
                    row,
                    call_instrument_id=(
                        self._resolver.resolve(call_key, received_at) if call_key else None
                    ),
                    put_instrument_id=(
                        self._resolver.resolve(put_key, received_at) if put_key else None
                    ),
                    clock=self._clock,
                )
            )

        if rejected:
            # Reported, never silently skipped: a chain quietly 30% shorter than it
            # should be looks like a thin market rather than a parsing failure.
            log.warning(
                "chain_rows_rejected",
                extra={"underlying_id": int(underlying_id), "count": len(rejected)},
            )

        return ChainSnapshot(
            underlying_id=underlying_id,
            expiry_id=expiry_id,
            observed_at=received_at,
            ingested_at=received_at,
            legs=tuple(legs),
            leg_count=len(rows),
            expected_leg_count=expected_leg_count,
            rejected=tuple(rejected),
        )

    async def fetch_historical_oi(
        self,
        underlying_vendor_key: str,
        expiry: date,
        on: date,
        resolver_keys: dict[str, InstrumentId],
        session_open: object,
        session_close: object,
    ) -> Sequence[HistoricalDailyOI]:
        """Date-granular OI. Returns `HistoricalDailyOI`, never a quote (AD-26)."""
        body = await self._rest.get_historical_oi(underlying_vendor_key, expiry, on)  # type: ignore[attr-defined]
        out: list[HistoricalDailyOI] = []
        for entry in body.get("data") or []:
            key = entry.get("instrument_key")
            iid = resolver_keys.get(key) if key else None
            if iid is None:
                continue
            out.append(
                normalize_historical_oi(
                    entry,
                    instrument_id=iid,
                    trade_date=on,
                    session_open=session_open,  # type: ignore[arg-type]
                    session_close=session_close,  # type: ignore[arg-type]
                    clock=self._clock,
                )
            )
        return out

    async def stream(
        self, vendor_keys: Sequence[str], mode: str
    ) -> AsyncIterator[MarketObservation]:
        """Live observations, with session identity and gap detection applied."""
        if self._ws is None:
            raise RuntimeError("no websocket client configured")

        async for frame in self._ws.stream(vendor_keys, mode):  # type: ignore[attr-defined]
            session = self._sessions.current
            if session is None:
                continue

            key = frame.payload.get("instrument_key")
            instrument_id = self._resolver.resolve(key, self._clock.now()) if key else None
            if instrument_id is None:
                continue

            observations = normalize_ws_tick(
                frame.payload,
                instrument_id=instrument_id,
                clock=self._clock,
                feed_session_id=session.session_id,
                channel=frame.channel,
                provider_event_id=frame.provider_event_id,
                channel_sequence=frame.channel_sequence,
                received_seq=frame.received_seq,
            )
            for obs in observations:
                # Gap detection is gated on the confidence actually achieved (A-1).
                self._sessions.record_message(
                    frame.channel, frame.channel_sequence, obs.identity.confidence
                )
                yield obs
