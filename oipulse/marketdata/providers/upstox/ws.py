"""Upstox WebSocket client — transport only.

`docs/design/06-UPSTOX_INTEGRATION.md` §6.

    connect → authenticate → subscribe → stream
      ├── sequence discontinuity → WEBSOCKET_GAP → out-of-band REST recovery
      ├── heartbeat watchdog → force reconnect
      └── disconnect → new feed_session_id → RECONNECT_GAP → REST resync

**All lifecycle decisions live in `marketdata/lifecycle.py`**, which is pure and fully
tested offline. This module moves bytes and delegates. That split is deliberate: it means
the reconnect, gap-detection and session-identity behaviour is verified without a socket,
and the soak has a small, specific set of *provider* facts to confirm rather than the
whole behaviour to discover.

NOT EXECUTABLE IN THE DEVELOPMENT SANDBOX: requires `websockets` and network access.

**Assumptions this module does not make (constraint D).** Whether Upstox supplies a
per-event id or a per-channel sequence is **unverified** (A-1). `_extract_identity_hints`
returns whatever is present and nothing more; when neither is present, identity resolves
to a content hash with `WEAK` confidence and sequence-based gap detection is not
performed and not claimed.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from itertools import count
from typing import Any

from oipulse.core.clock import Clock
from oipulse.core.errors import OIPulseError
from oipulse.marketdata.lifecycle import ConnectionState, SessionManager
from oipulse.observability.logging import get_logger
from oipulse.observability.metrics import (
    METRICS,
    WS_CONNECTION_STATE,
    WS_RECONNECTS,
)

log = get_logger(__name__)

__all__ = ["UpstoxWebSocketClient", "WsConfig", "WsFrame", "extract_identity_hints"]


class WsConnectionError(OIPulseError):
    pass


@dataclass(frozen=True, slots=True)
class WsConfig:
    authorize_path: str = "/feed/market-data-feed/authorize"
    heartbeat_budget: timedelta = timedelta(seconds=10)
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    max_reconnect_attempts: int | None = None  # None = retry indefinitely


@dataclass(frozen=True, slots=True)
class WsFrame:
    """One decoded provider message, with whatever identity hints it carried."""

    channel: str
    payload: dict[str, Any]
    provider_event_id: str | None
    channel_sequence: int | None
    venue_timestamp: Any | None
    received_seq: int


def extract_identity_hints(
    message: dict[str, Any],
) -> tuple[str | None, int | None, Any | None]:
    """Pull `(provider_event_id, channel_sequence, venue_timestamp)` if present.

    Candidate key names are tried because the exact field names are **unverified**
    (A-1, A-3). Returning `None` is a normal, expected outcome, not a failure — it
    degrades identity confidence to WEAK, which is recorded rather than hidden.

    The soak's job is to replace this speculative key list with the observed one.
    """
    event_id = None
    for key in ("event_id", "eventId", "id", "msg_id", "messageId"):
        value = message.get(key)
        if value:
            event_id = str(value)
            break

    sequence = None
    for key in ("sequence", "seq", "sequence_number", "sequenceNumber", "channel_seq"):
        value = message.get(key)
        if isinstance(value, int):
            sequence = value
            break

    timestamp = None
    for key in ("ts", "timestamp", "exchange_timestamp", "feed_time", "et"):
        value = message.get(key)
        if value:
            timestamp = value
            break

    return event_id, sequence, timestamp


class UpstoxWebSocketClient:
    """Managed WebSocket feed with reconnect, watchdog and session identity."""

    def __init__(
        self,
        access_token: str,
        clock: Clock,
        sessions: SessionManager,
        config: WsConfig | None = None,
        connect_factory: Callable[..., Any] | None = None,
        authorize: Callable[[], Any] | None = None,
    ) -> None:
        self._token = access_token
        self._clock = clock
        self._sessions = sessions
        self._config = config or WsConfig()
        # Injectable so a fixture-driven transport can exercise the loop offline.
        self._connect_factory = connect_factory
        self._authorize = authorize
        self._received = count(1)
        self._stopping = False

    async def _resolve_socket_url(self) -> str:
        if self._authorize is not None:
            return await self._authorize()
        raise WsConnectionError(
            "no authorize callable supplied; the feed URL must be obtained from the "
            "Upstox authorize endpoint before connecting"
        )

    async def _open(self) -> Any:
        if self._connect_factory is None:
            import websockets  # lazily imported: absent in the offline sandbox

            url = await self._resolve_socket_url()
            return await websockets.connect(url)
        return await self._connect_factory()

    async def stream(self, vendor_keys: Sequence[str], mode: str) -> AsyncIterator[WsFrame]:
        """Yield frames forever, reconnecting with backoff.

        A new `feed_session_id` is opened on every connection, which is what makes
        sequence numbers meaningful: they are comparable only within a session. The
        outage window between sessions is recorded as a `RECONNECT_GAP`.
        """
        attempt = 0
        while not self._stopping:
            try:
                self._sessions.transition(ConnectionState.CONNECTING)
                socket = await self._open()

                self._sessions.transition(ConnectionState.AUTHENTICATING)
                self._sessions.transition(ConnectionState.SUBSCRIBING)
                await self._send_subscription(socket, vendor_keys, mode)

                self._sessions.transition(ConnectionState.STREAMING)
                session = self._sessions.open_session()
                METRICS.set_gauge(WS_CONNECTION_STATE, 1.0)
                log.info(
                    "ws_session_opened",
                    extra={
                        "feed_session_id": session.session_id,
                        "ordinal": session.ordinal,
                        "instruments": len(vendor_keys),
                        "mode": mode,
                    },
                )
                attempt = 0

                async for frame in self._read_frames(socket):
                    yield frame

            except Exception as exc:
                log.warning("ws_error", extra={"error": str(exc)[:200]})
            finally:
                METRICS.set_gauge(WS_CONNECTION_STATE, 0.0)
                self._sessions.close_session(reason="connection ended")
                if self._sessions.state is ConnectionState.STREAMING:
                    self._sessions.transition(ConnectionState.DISCONNECTED)

            if self._stopping:
                break

            attempt += 1
            if (
                self._config.max_reconnect_attempts is not None
                and attempt > self._config.max_reconnect_attempts
            ):
                raise WsConnectionError(f"giving up after {attempt} reconnect attempts")

            METRICS.inc(WS_RECONNECTS)
            delay = min(
                self._config.reconnect_base_delay * (2 ** (attempt - 1)),
                self._config.reconnect_max_delay,
            ) + random.uniform(0, 0.5)
            log.info("ws_reconnect_scheduled", extra={"attempt": attempt, "delay_s": delay})
            await asyncio.sleep(delay)

    async def _send_subscription(self, socket: Any, vendor_keys: Sequence[str], mode: str) -> None:
        import json

        await socket.send(
            json.dumps(
                {
                    "guid": "oipulse",
                    "method": "sub",
                    "data": {"mode": mode, "instrumentKeys": list(vendor_keys)},
                }
            )
        )

    async def _read_frames(self, socket: Any) -> AsyncIterator[WsFrame]:
        """Decode messages and apply the heartbeat watchdog."""
        import json

        while not self._stopping:
            try:
                raw = await asyncio.wait_for(
                    socket.recv(),
                    timeout=self._config.heartbeat_budget.total_seconds(),
                )
            except TimeoutError:
                verdict = self._sessions.check_watchdog()
                if verdict.stale:
                    self._sessions.record_stale(
                        f"no message for {verdict.silence}; forcing reconnect"
                    )
                    return
                continue

            message = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if not isinstance(message, dict):
                continue

            event_id, sequence, venue_ts = extract_identity_hints(message)
            channel = str(message.get("channel") or message.get("type") or "default")

            yield WsFrame(
                channel=channel,
                payload=message,
                provider_event_id=event_id,
                channel_sequence=sequence,
                venue_timestamp=venue_ts,
                received_seq=next(self._received),
            )

    def stop(self) -> None:
        self._stopping = True
