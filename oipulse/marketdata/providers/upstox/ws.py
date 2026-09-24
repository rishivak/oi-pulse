"""Upstox WebSocket client (V2 JSON) — **NOT THE PRODUCTION PATH**.

.. warning::

   External verification established that the Upstox **V2** market-data WebSocket is
   discontinued and that **V3** is the live feed, carrying binary Protobuf rather than
   JSON. This module parses JSON and is therefore retained only for the reconnect,
   backoff and session-lifecycle behaviour it already has under test — behaviour the V3
   client reuses. It must not be wired into the ingestor.

   The production feed adapter is `providers/upstox/v3.py`. A Protobuf frame parsed as
   JSON does not fail cleanly; it raises or silently yields nothing, and either way the
   result is missing market data, so there is no fallback from V3 to this module.

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

**Provider identity (settled for V3).** Upstox V3 supplies neither a provider event id
nor a channel sequence; verification observed both absent across two feed sessions.
`extract_identity_hints` therefore no longer guesses at candidate key names. It reads
only the two keys a provider would have to declare explicitly, and returns `None`
otherwise — which resolves identity to the OI Pulse-derived content digest with `WEAK`
confidence, where sequence-based gap detection is neither performed nor claimed.

Nothing here synthesizes a `provider_event_id` or a `channel_sequence` from a local
counter, a hash, or a timestamp.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
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
    """Pull `(provider_event_id, channel_sequence, venue_timestamp)` if actually present.

    Previously this tried a list of guessed key names — `id`, `seq`, `msg_id` and
    others — on the theory that one of them might be the provider's event id. That was
    wrong in a way worth naming: a coincidentally-named field would have been promoted
    to a *provider* identity with STRONG confidence, enabling sequence gap detection
    over a value the provider never meant as a sequence. Upstox V3 is now known to
    supply neither field, so the guessing has no upside and a silent, severe downside.

    Only explicitly-named provider fields are read. `None` is the expected outcome for
    Upstox and degrades identity to the OI Pulse-derived digest, which is recorded.

    `venue_timestamp` is still read from the documented exchange-timestamp keys: a
    provider timestamp is preserved where genuinely supplied, and it is a *timestamp*,
    never an identity or an ordering authority.
    """
    raw_event_id = message.get("provider_event_id")
    event_id = str(raw_event_id) if raw_event_id else None

    raw_sequence = message.get("channel_sequence")
    sequence = raw_sequence if isinstance(raw_sequence, int) else None

    timestamp = None
    for key in ("exchange_timestamp", "feed_time", "ts"):
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
        connect_factory: Callable[[], Awaitable[Any]] | None = None,
        authorize: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self._token = access_token
        self._clock = clock
        self._sessions = sessions
        self._config = config or WsConfig()
        # Injectable so a fixture-driven transport can exercise the loop offline.
        self._connect_factory = connect_factory
        self._authorize = authorize
        self._received = count(1)
        # An Event rather than a bool: `stop()` is called from a different task while
        # `stream()` is awaiting, which is exactly what an Event is for. It also keeps
        # the loop condition honest under static analysis -- a plain attribute read is
        # narrowed to False by the enclosing `while not self._stopping`, which made the
        # `if self._stopping: break` inside the loop look unreachable when it is the
        # normal way a stopped stream exits.
        self._stop = asyncio.Event()

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
        while not self._stop.is_set():
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

            if self._stop.is_set():
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

        while not self._stop.is_set():
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
        self._stop.set()
