"""Upstox market-data feed **V3** — lifecycle, subscription and the decoder boundary.

`docs/design/06-UPSTOX_INTEGRATION.md`. External verification established that the V2
market-data WebSocket path is discontinued and V3 is the live feed, that V3 carries
**binary Protobuf** frames rather than JSON, and that V3 authorization returns the
authorized WebSocket URI.

Lifecycle, in order:

    OAuth token
      -> V3 market-data authorize endpoint
      -> authorized WebSocket URI
      -> connect
      -> subscribe (V3 request format)
      -> receive binary Protobuf frames
      -> decode with the official V3 proto definition
      -> normalize to canonical observations

**The decode step is deliberately not implemented, and it fails closed.**

Decoding Protobuf requires the official `.proto` definition: message names, field
numbers and wire types. This repository does not have it, the development sandbox has no
network access to obtain it, and the `protobuf` runtime is not installable here. Writing
field numbers from memory would be exactly the fabrication of unobserved provider
behaviour that the project forbids, and a wrong field number does not fail loudly — it
silently yields a plausible number for the wrong field, which then lands in durable
market data.

So `ProtoFrameDecoder` is a Protocol with no production implementation, and
`UpstoxV3FeedClient` **requires one to be injected**. Constructing the client without a
decoder raises `ProtoDecoderUnavailable` at construction time rather than at the first
frame. There is no JSON fallback: parsing a Protobuf frame as JSON is not a degraded
mode, it is a bug.

What *is* implemented and testable here: the authorize call, the URI handoff, the
subscription request construction, frame routing, session and identity handling, and the
refusal conditions.

**Unverified against the live protocol** (no network, no credentials): the authorize
response field name and the exact subscription message shape. Both are isolated in
single functions, each marked, so verification against a real endpoint touches one place.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import count
from typing import Any, Protocol

from oipulse.core.clock import Clock
from oipulse.core.errors import OIPulseError
from oipulse.marketdata.lifecycle import ConnectionState, SessionManager
from oipulse.observability.logging import get_logger

log = get_logger(__name__)

__all__ = [
    "AUTHORIZE_PATH",
    "DecodedFeedMessage",
    "FeedMessageKind",
    "ProtoDecoderUnavailable",
    "ProtoFrameDecoder",
    "UpstoxV3FeedClient",
    "V3AuthorizationError",
    "V3SubscriptionMode",
    "build_subscribe_request",
    "build_unsubscribe_request",
]

#: V3 market-data authorize endpoint, relative to the REST base.
#: UNVERIFIED against the live API in this environment.
AUTHORIZE_PATH = "/feed/market-data-feed/authorize"


class V3SubscriptionMode(StrEnum):
    """Subscription modes this phase uses.

    Recorded as **observed provider vocabulary, not architectural truth**: the
    `SubscriptionPlanner` owns capacity and decides what may be subscribed before any
    subscription is sent. Mode names here do not imply a limit.
    """

    LTPC = "ltpc"
    #: Full depth/Greeks payload. Phase 2 needs this for option analytics.
    FULL = "full"
    OPTION_GREEKS = "option_greeks"


class FeedMessageKind(StrEnum):
    """What a decoded frame turned out to be.

    `MARKET_INFO` frames carry feed/market status rather than instrument data and must
    not be normalized into observations; conflating them would write status rows into
    the observation store.
    """

    MARKET_INFO = "market_info"
    INITIAL_SNAPSHOT = "initial_snapshot"
    LIVE_FEED = "live_feed"
    UNKNOWN = "unknown"


class V3AuthorizationError(OIPulseError):
    """The authorize call did not yield a usable WebSocket URI."""


class ProtoDecoderUnavailable(OIPulseError):
    """No Protobuf decoder was supplied, so V3 frames cannot be decoded.

    Raised at construction, not at the first frame. A client that connects and only
    then discovers it cannot decode has already opened a feed session and started a
    gap it will have to explain.
    """


@dataclass(frozen=True, slots=True)
class DecodedFeedMessage:
    """One decoded V3 frame, provider-shaped, before normalization.

    `fields` holds the decoded payload for one instrument. **Absent fields are absent**:
    a key that the frame did not carry must not appear here with a zero or a default,
    because a fabricated zero is indistinguishable from a real zero downstream.

    `provider_timestamp` is populated only where the frame actually supplies one.

    There is deliberately no `provider_event_id` and no `channel_sequence`: Upstox V3
    supplies neither, and adding the attributes would invite something to fill them.
    """

    kind: FeedMessageKind
    instrument_key: str | None
    fields: dict[str, Any] = field(default_factory=dict)
    provider_timestamp: Any | None = None
    #: Everything decoded, kept whole so the OI Pulse content digest is deterministic
    #: and reproducible from the frame alone.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_market_data(self) -> bool:
        return self.kind in (FeedMessageKind.LIVE_FEED, FeedMessageKind.INITIAL_SNAPSHOT)


class ProtoFrameDecoder(Protocol):
    """Decodes one binary V3 frame into provider-shaped messages.

    Implemented against the official V3 `.proto`. Injected rather than imported so the
    protocol definition, which is provider-owned and versioned by the provider, is not
    baked into this adapter.
    """

    #: Identifies which proto revision produced this decoder, recorded on captures.
    proto_revision: str

    def decode(self, payload: bytes) -> Sequence[DecodedFeedMessage]: ...


class RestAuthorizer(Protocol):
    """The REST surface needed to obtain the authorized feed URI."""

    async def get_json(self, path: str) -> dict[str, Any]: ...


class WebSocketConnection(Protocol):
    """The transport surface, so the client is testable without a real socket."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> bytes: ...
    async def close(self) -> None: ...


def _guid() -> str:
    """Per-request correlation id for a subscription message."""
    return uuid.uuid4().hex


def build_subscribe_request(
    instrument_keys: Sequence[str],
    mode: V3SubscriptionMode,
    *,
    guid: str | None = None,
) -> str:
    """Construct the V3 subscription message.

    UNVERIFIED against the live protocol in this environment: no credentials and no
    reachable endpoint. Isolated in this one function precisely so that verifying it
    against a real feed is a single-place change, and so nothing else in the adapter
    encodes an assumption about the wire format.

    The subscription *control* channel is JSON even though the *data* frames are
    Protobuf; the two must not be confused, which is why decoding lives behind
    `ProtoFrameDecoder` and never touches this path.
    """
    if not instrument_keys:
        raise ValueError("a subscription must name at least one instrument")
    return json.dumps(
        {
            "guid": guid or _guid(),
            "method": "sub",
            "data": {"mode": mode.value, "instrumentKeys": list(instrument_keys)},
        },
        separators=(",", ":"),
    )


def build_unsubscribe_request(instrument_keys: Sequence[str], *, guid: str | None = None) -> str:
    """Construct the V3 unsubscribe message. UNVERIFIED, as above."""
    if not instrument_keys:
        raise ValueError("an unsubscription must name at least one instrument")
    return json.dumps(
        {
            "guid": guid or _guid(),
            "method": "unsub",
            "data": {"instrumentKeys": list(instrument_keys)},
        },
        separators=(",", ":"),
    )


def extract_feed_uri(authorize_response: dict[str, Any]) -> str:
    """Pull the authorized WebSocket URI out of the authorize response.

    UNVERIFIED field path. Two documented shapes are accepted and anything else raises,
    rather than returning a plausible-looking empty string that would fail later as a
    connection error and be misread as an outage.
    """
    data = authorize_response.get("data")
    if isinstance(data, dict):
        uri = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
        if isinstance(uri, str) and uri:
            return uri
    uri = authorize_response.get("authorized_redirect_uri")
    if isinstance(uri, str) and uri:
        return uri
    raise V3AuthorizationError(
        "authorize response contained no authorized WebSocket URI; "
        f"keys present: {sorted(authorize_response)}"
    )


class UpstoxV3FeedClient:
    """Managed V3 feed: authorize, connect, subscribe, route decoded frames.

    Requires a `ProtoFrameDecoder`. There is no JSON fallback and no default decoder,
    so this client cannot silently misparse a binary frame.
    """

    def __init__(
        self,
        rest: RestAuthorizer,
        clock: Clock,
        sessions: SessionManager,
        decoder: ProtoFrameDecoder | None,
        connect: Any = None,
    ) -> None:
        if decoder is None:
            raise ProtoDecoderUnavailable(
                "UpstoxV3FeedClient requires a Protobuf decoder built from the official "
                "Upstox V3 .proto definition. None is bundled: the definition is "
                "provider-owned and was not available when this adapter was written. "
                "Supply one rather than falling back to JSON -- V3 frames are binary, "
                "and parsing them as JSON yields wrong values, not an error."
            )
        self._rest = rest
        self._clock = clock
        self._sessions = sessions
        self._decoder = decoder
        self._connect = connect
        self._received: Iterator[int] = count(1)
        self._stopping = False

    @property
    def proto_revision(self) -> str:
        return self._decoder.proto_revision

    async def authorize(self) -> str:
        """OAuth token -> authorized WebSocket URI."""
        self._sessions.transition(ConnectionState.AUTHENTICATING)
        response = await self._rest.get_json(AUTHORIZE_PATH)
        uri = extract_feed_uri(response)
        # The URI is a bearer credential in query form; never logged.
        log.info("v3_authorized", extra={"proto_revision": self.proto_revision})
        return uri

    async def subscribe(
        self,
        connection: WebSocketConnection,
        instrument_keys: Sequence[str],
        mode: V3SubscriptionMode,
    ) -> None:
        """Send the subscription. Capacity was already decided by SubscriptionPlanner.

        This method does not enforce a count limit: provider limits are observations to
        be recorded, not architectural truths to be hardcoded (`06` §5). The planner
        refuses an oversized universe before this is reached.
        """
        self._sessions.transition(ConnectionState.SUBSCRIBING)
        await connection.send(build_subscribe_request(instrument_keys, mode))
        self._sessions.transition(ConnectionState.STREAMING)

    async def frames(self, connection: WebSocketConnection) -> AsyncIterator[DecodedFeedMessage]:
        """Yield decoded market-data messages.

        `MARKET_INFO` frames are logged and dropped rather than normalized: they carry
        feed status, not instrument data.

        No identity hints are extracted, because Upstox V3 supplies none. Identity is
        resolved downstream from the decoded payload digest, with the local
        `received_seq` recorded separately as diagnostics and never used for ordering.
        """
        while not self._stopping:
            payload = await connection.recv()
            if not payload:
                continue
            for message in self._decoder.decode(payload):
                if message.kind is FeedMessageKind.MARKET_INFO:
                    log.info("v3_market_info", extra={"fields": sorted(message.fields)})
                    continue
                if message.kind is FeedMessageKind.UNKNOWN:
                    # Reported, never silently dropped: an unrecognised frame kind means
                    # the proto revision moved and the decoder is behind.
                    log.warning(
                        "v3_unknown_frame_kind",
                        extra={"proto_revision": self.proto_revision},
                    )
                    continue
                yield message

    def next_received_seq(self) -> int:
        """Local monotonic arrival counter. Diagnostics only, never an ordering key."""
        return next(self._received)

    def stop(self) -> None:
        self._stopping = True
