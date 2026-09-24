"""WebSocket lifecycle and gap detection — as a deterministic state machine.

`docs/design/06-UPSTOX_INTEGRATION.md` §6:

    connect → authenticate → subscribe → stream
      ├── sequence discontinuity → WEBSOCKET_GAP → out-of-band REST recovery
      ├── heartbeat watchdog → force reconnect
      └── disconnect → new feed_session_id → RECONNECT_GAP → REST resync

The transport is separated from the logic deliberately. Everything that decides *what a
gap is*, *when to recover* and *how sessions relate* lives here and is testable without a
socket; `providers/upstox/ws.py` only moves bytes. That split is what lets the reconnect
and gap behaviour be verified offline, and it is why the soak has something specific to
confirm rather than everything to discover.

**Constraint C.** `channel_sequence` is scoped to a feed session and resets on reconnect.
Gap detection therefore operates strictly *within* a session; a sequence "jump" across a
reconnect is not a gap, it is a new session. Cross-session ordering uses
`feed_session_ordinal`, assigned once and stored (`10-REPLAY.md` §3).

**Constraint D.** Under `IdentityConfidence.WEAK` there is no sequence, so sequence-based
gap detection is **not performed and not claimed**. The session reports its confidence so
the data-quality surface can say what coverage actually means.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import count

from oipulse.core.clock import Clock, ensure_utc
from oipulse.marketdata.identity import IdentityConfidence

__all__ = [
    "ConnectionState",
    "FeedSession",
    "GapKind",
    "GapRecord",
    "SessionManager",
    "WatchdogVerdict",
]


class ConnectionState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    SUBSCRIBING = "subscribing"
    STREAMING = "streaming"
    RECOVERING = "recovering"
    DISCONNECTED = "disconnected"
    STOPPED = "stopped"


_PERMITTED: dict[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.IDLE: frozenset({ConnectionState.CONNECTING, ConnectionState.STOPPED}),
    ConnectionState.CONNECTING: frozenset(
        {ConnectionState.AUTHENTICATING, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.AUTHENTICATING: frozenset(
        {ConnectionState.SUBSCRIBING, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.SUBSCRIBING: frozenset(
        {ConnectionState.STREAMING, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.STREAMING: frozenset(
        {ConnectionState.RECOVERING, ConnectionState.DISCONNECTED, ConnectionState.STOPPED}
    ),
    # Recovery resolves back into streaming; a failure during recovery disconnects.
    ConnectionState.RECOVERING: frozenset(
        {ConnectionState.STREAMING, ConnectionState.DISCONNECTED, ConnectionState.STOPPED}
    ),
    ConnectionState.DISCONNECTED: frozenset({ConnectionState.CONNECTING, ConnectionState.STOPPED}),
    ConnectionState.STOPPED: frozenset(),
}


class GapKind(StrEnum):
    WEBSOCKET_GAP = "websocket_gap"  # sequence discontinuity inside a session
    RECONNECT_GAP = "reconnect_gap"  # the outage window between sessions
    STALE_FEED = "stale_feed"  # heartbeat watchdog fired


@dataclass(frozen=True, slots=True)
class GapRecord:
    """A permanently recorded absence.

    Observations inside a gap are **never fabricated or interpolated** (`06` §6). The
    window is stored so research can exclude or flag it — a gap that is not recorded is
    indistinguishable later from a quiet market.
    """

    kind: GapKind
    channel: str | None
    detected_at: datetime
    window_start: datetime | None
    window_end: datetime | None
    expected_sequence: int | None = None
    observed_sequence: int | None = None
    feed_session_id: str | None = None
    detail: str = ""

    @property
    def missing_count(self) -> int | None:
        if self.expected_sequence is None or self.observed_sequence is None:
            return None
        return max(self.observed_sequence - self.expected_sequence, 0)


@dataclass(frozen=True, slots=True)
class WatchdogVerdict:
    stale: bool
    silence: timedelta
    budget: timedelta


@dataclass
class FeedSession:
    """One WebSocket connection's lifetime.

    A **new session id on every connection** is what makes sequence numbers meaningful:
    they are only comparable within a session.
    """

    session_id: str
    ordinal: int
    opened_at: datetime
    #: Highest in-session sequence seen, per channel.
    last_sequence: dict[str, int] = field(default_factory=dict)
    closed_at: datetime | None = None
    last_message_at: datetime | None = None
    message_count: int = 0
    confidence: IdentityConfidence = IdentityConfidence.WEAK

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class SessionManager:
    """Owns session identity, sequence tracking, gap detection and the watchdog."""

    def __init__(
        self,
        clock: Clock,
        *,
        heartbeat_budget: timedelta = timedelta(seconds=10),
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock
        self._heartbeat_budget = heartbeat_budget
        self._state = ConnectionState.IDLE
        self._ordinals = count(0)
        self._sessions: list[FeedSession] = []
        self._current: FeedSession | None = None
        self._gaps: list[GapRecord] = []
        self._drained = 0
        self._session_id_factory = session_id_factory or self._default_session_id

    # ------------------------------------------------------------- state machine

    @property
    def state(self) -> ConnectionState:
        return self._state

    def transition(self, to: ConnectionState) -> None:
        """Move to *to*, rejecting any transition the table does not permit."""
        if to not in _PERMITTED[self._state]:
            raise ValueError(f"illegal websocket transition {self._state.value} -> {to.value}")
        self._state = to

    # ------------------------------------------------------------------ sessions

    @staticmethod
    def _default_session_id() -> str:
        import uuid

        return str(uuid.uuid4())

    @property
    def current(self) -> FeedSession | None:
        return self._current

    @property
    def sessions(self) -> tuple[FeedSession, ...]:
        return tuple(self._sessions)

    @property
    def gaps(self) -> tuple[GapRecord, ...]:
        return tuple(self._gaps)

    def drain_new_gaps(self) -> tuple[GapRecord, ...]:
        """Gaps detected since the last drain.

        The collector consumes gaps exactly once so a single discontinuity triggers a
        single recovery. `gaps` remains the full permanent record — draining marks
        gaps as *handled*, never as forgotten, because the window must stay queryable
        for research long after recovery completed.
        """
        new = self._gaps[self._drained :]
        self._drained = len(self._gaps)
        return tuple(new)

    def open_session(self) -> FeedSession:
        """Begin a new feed session, recording the outage window if one preceded it."""
        now = self._clock.now()
        previous = self._current
        if previous is not None and previous.is_open:
            self.close_session(reason="superseded by a new session")
            previous = self._current

        session = FeedSession(
            session_id=self._session_id_factory(),
            ordinal=next(self._ordinals),
            opened_at=now,
        )

        if previous is not None and previous.closed_at is not None:
            # The outage window is a permanent fact, not a transient condition.
            self._gaps.append(
                GapRecord(
                    kind=GapKind.RECONNECT_GAP,
                    channel=None,
                    detected_at=now,
                    window_start=previous.closed_at,
                    window_end=now,
                    feed_session_id=previous.session_id,
                    detail=(
                        "feed session "
                        f"{previous.ordinal} -> {session.ordinal}; sequence numbers are "
                        "not comparable across this boundary"
                    ),
                )
            )

        self._sessions.append(session)
        self._current = session
        return session

    def close_session(self, reason: str = "") -> None:
        if self._current is None or not self._current.is_open:
            return
        self._current.closed_at = self._clock.now()

    # -------------------------------------------------------------- observations

    def record_message(
        self,
        channel: str,
        sequence: int | None,
        confidence: IdentityConfidence,
        observed_at: datetime | None = None,
    ) -> GapRecord | None:
        """Record a received message, returning a gap if one was detected.

        Gap detection is performed **only** when the identity confidence supports it.
        Under WEAK confidence there is no sequence to be discontinuous, and claiming a
        gap — or claiming clean coverage — would be asserting more than the data shows.
        """
        if self._current is None:
            raise RuntimeError("no open feed session; call open_session() first")

        now = self._clock.now()
        session = self._current
        session.message_count += 1
        session.last_message_at = now
        session.confidence = confidence

        if not confidence.supports_sequence_gap_detection or sequence is None:
            return None

        last = session.last_sequence.get(channel)
        gap: GapRecord | None = None
        if last is not None and sequence > last + 1:
            gap = GapRecord(
                kind=GapKind.WEBSOCKET_GAP,
                channel=channel,
                detected_at=now,
                window_start=None,
                window_end=None,
                expected_sequence=last + 1,
                observed_sequence=sequence,
                feed_session_id=session.session_id,
                detail=f"{sequence - last - 1} message(s) missing on {channel}",
            )
            self._gaps.append(gap)

        # Out-of-order or duplicate arrivals do not lower the watermark: a late message
        # is placed by its observed_at, not by arrival, and the inbox absorbs duplicates.
        if last is None or sequence > last:
            session.last_sequence[channel] = sequence
        return gap

    # ------------------------------------------------------------------ watchdog

    def check_watchdog(self) -> WatchdogVerdict:
        """Detect a silent feed — a connection that is open but delivering nothing."""
        if self._current is None:
            return WatchdogVerdict(False, timedelta(0), self._heartbeat_budget)
        reference = self._current.last_message_at or self._current.opened_at
        silence = self._clock.now() - ensure_utc(reference)
        return WatchdogVerdict(
            stale=silence > self._heartbeat_budget,
            silence=silence,
            budget=self._heartbeat_budget,
        )

    def record_stale(self, detail: str = "") -> GapRecord:
        session = self._current
        now = self._clock.now()
        reference = session.last_message_at or session.opened_at if session else now
        gap = GapRecord(
            kind=GapKind.STALE_FEED,
            channel=None,
            detected_at=now,
            window_start=ensure_utc(reference),
            window_end=now,
            feed_session_id=session.session_id if session else None,
            detail=detail or "no message within heartbeat budget",
        )
        self._gaps.append(gap)
        return gap

    # -------------------------------------------------------------------- replay

    def session_ordinal(self, session_id: str) -> int | None:
        """Stable cross-session ordering key (`10-REPLAY.md` §3).

        Assigned once at session open and stored, never re-derived per run — deriving it
        at query time would make replay ordering depend on what else is in the database.
        """
        for s in self._sessions:
            if s.session_id == session_id:
                return s.ordinal
        return None
