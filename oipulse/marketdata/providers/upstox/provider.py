"""UpstoxMarketDataProvider — REST + WS behind the canonical contract.

`docs/design/06-UPSTOX_INTEGRATION.md` §1. The only place vendor shapes meet the domain.
Upstox remains the sole provider (CLAUDE.md rule 11).

PARTIALLY EXECUTABLE OFFLINE: construction and normalization delegation are exercised by
fixture-driven tests; anything touching the network is not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Protocol

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

__all__ = [
    "ChainSnapshot",
    "ExpiryBinding",
    "InstrumentResolver",
    "RestTransport",
    "UpstoxMarketDataProvider",
    "WsTransport",
]


class RestTransport(Protocol):
    """The REST surface this adapter needs.

    A structural type rather than a concrete import: the adapter is tested with a
    fixture-backed transport, and typing the dependency as `object` with
    `# type: ignore[attr-defined]` at each call site suppressed exactly the errors that
    would catch a signature drift between the two.
    """

    async def get_option_chain(self, instrument_key: str, expiry: date) -> dict[str, Any]: ...

    async def get_historical_oi(
        self, instrument_key: str, expiry: date, on: date
    ) -> dict[str, Any]: ...


class WsTransport(Protocol):
    """The WebSocket surface this adapter needs."""

    def stream(self, vendor_keys: Sequence[str], mode: str) -> AsyncIterator[Any]: ...


#: (valid_from, valid_to, instrument_id). `valid_to` None means "still in force".
_MappingWindow = tuple[datetime, datetime | None, InstrumentId]


class InstrumentResolver:
    """Maps a vendor key to canonical `InstrumentId`, **as of an instant**.

    Temporal because vendor mappings are historised: a March observation must resolve
    through March's key even after the vendor reissues it (`01` §3).
    """

    def __init__(self) -> None:
        self._by_key: dict[str, list[_MappingWindow]] = {}

    def register(
        self,
        vendor_key: str,
        instrument_id: InstrumentId,
        valid_from: datetime,
        valid_to: datetime | None = None,
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
            if prior_to is None and prior_from < valid_from:
                existing[index] = (prior_from, valid_from, prior_id)
        existing.append((valid_from, valid_to, instrument_id))
        existing.sort(key=lambda row: row[0])

    def resolve(self, vendor_key: str, at: datetime) -> InstrumentId | None:
        """The mapping in force at *at*.

        Scans newest-first so the most recent applicable mapping wins even if a
        historical overlap slipped past registration.
        """
        for valid_from, valid_to, iid in reversed(self._by_key.get(vendor_key, ())):
            if valid_from <= at and (valid_to is None or at < valid_to):
                return iid
        return None


@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """A REST chain response: a cross-sectional consistency set (`04` §4)."""

    underlying_id: InstrumentId
    expiry_id: ExpiryId
    observed_at: datetime
    ingested_at: datetime
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


@dataclass(frozen=True, slots=True)
class ExpiryBinding:
    """Everything needed to re-fetch one expiry's chain from a canonical `ExpiryId`.

    Recovery is triggered by a gap, and a gap only knows canonical ids. Without this
    binding the provider cannot turn `expiry_id` back into the vendor key and date the
    REST call needs.
    """

    expiry_id: ExpiryId
    underlying_id: InstrumentId
    underlying_vendor_key: str
    expiry: date
    expected_leg_count: int | None = None


class UnknownExpiry(LookupError):
    """Recovery was asked for an expiry the provider has no binding for.

    Raised rather than returning an empty snapshot: an empty chain is indistinguishable
    from a market with no open interest, and recovery silently producing one would turn
    a wiring mistake into missing data that looks like real data.
    """


class UpstoxMarketDataProvider:
    """Adapter. Transport in `rest.py`/`ws.py`, shapes in `schemas.py`, mapping here."""

    def __init__(
        self,
        rest_client: RestTransport,
        clock: Clock,
        resolver: InstrumentResolver,
        sessions: SessionManager,
        ws_client: WsTransport | None = None,
    ) -> None:
        self._rest = rest_client
        self._clock = clock
        self._resolver = resolver
        self._sessions = sessions
        self._ws = ws_client
        self._expiries: dict[int, ExpiryBinding] = {}

    # ------------------------------------------------------------------ bindings

    def register_expiry(self, binding: ExpiryBinding) -> None:
        """Bind a canonical `ExpiryId` to the vendor key and date REST needs."""
        self._expiries[int(binding.expiry_id)] = binding

    @property
    def bound_expiries(self) -> tuple[int, ...]:
        return tuple(sorted(self._expiries))

    async def fetch_option_chain(
        self,
        underlying_vendor_key: str,
        underlying_id: InstrumentId,
        expiry_id: ExpiryId,
        expiry: date,
        expected_leg_count: int | None = None,
    ) -> ChainSnapshot:
        """Fetch and normalize one chain into a consistency set."""
        body = await self._rest.get_option_chain(underlying_vendor_key, expiry)
        return self.build_chain_snapshot(
            body,
            underlying_id=underlying_id,
            expiry_id=expiry_id,
            expected_leg_count=expected_leg_count,
        )

    def build_chain_snapshot(
        self,
        body: dict[str, Any],
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
        session_open: time,
        session_close: time,
    ) -> Sequence[HistoricalDailyOI]:
        """Date-granular OI. Returns `HistoricalDailyOI`, never a quote (AD-26)."""
        body = await self._rest.get_historical_oi(underlying_vendor_key, expiry, on)
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
                    session_open=session_open,
                    session_close=session_close,
                    clock=self._clock,
                )
            )
        return out

    async def fetch_recovery_chain(self, expiry_id: int) -> ChainSnapshot:
        """Out-of-band re-fetch of one expiry's chain after a gap.

        The collector calls this when `SessionManager` reports a discontinuity. It was
        previously absent from this class: the collector typed its provider as `object`
        and suppressed the resulting `attr-defined` error, so the missing method
        surfaced only at runtime, as an `AttributeError` caught by the collector's
        broad `except` and logged as `recovery_fetch_failed`. Recovery would have been
        permanently dead while reporting itself merely as a failed attempt.

        Deliberately the same code path as the routine snapshot fetch. A separate
        recovery path would be exercised only during incidents, which is the worst time
        to discover it diverged.
        """
        binding = self._expiries.get(int(expiry_id))
        if binding is None:
            raise UnknownExpiry(
                f"expiry_id {expiry_id} has no vendor binding; "
                f"call register_expiry() during collector wiring"
            )
        return await self.fetch_option_chain(
            binding.underlying_vendor_key,
            binding.underlying_id,
            binding.expiry_id,
            binding.expiry,
            expected_leg_count=binding.expected_leg_count,
        )

    async def stream(
        self, vendor_keys: Sequence[str], mode: str
    ) -> AsyncIterator[MarketObservation]:
        """Live observations, with session identity and gap detection applied."""
        if self._ws is None:
            raise RuntimeError("no websocket client configured")

        async for frame in self._ws.stream(vendor_keys, mode):
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
