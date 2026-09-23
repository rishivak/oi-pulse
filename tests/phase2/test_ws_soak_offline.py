"""Offline WebSocket soak — session, reconnect, resume, overlapping REST recovery.

**What this proves and what it does not.**

It drives the real `SessionManager`, the real normalizer, the real identity resolver and
the real observation store through the full lifecycle the Phase 2 brief asks for:

    one continuous session → reconnect → resumed subscription → overlapping REST recovery

using **synthetic** frames. It therefore verifies *our* handling of those transitions.

It proves **nothing** about Upstox's actual identity or ordering semantics. Assumptions
A-1 (provider event id / channel sequence) and A-3 (venue timestamps) remain open and can
only be closed by the external soak against the live feed. This harness is what that
external run should be compared against: same assertions, real frames.

The harness deliberately runs the scenario **twice** — once with identity hints present
and once without — because the degraded path is the one that will actually be in force
if A-1 resolves negatively, and it must be correct rather than merely tolerated.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.core.clock import FrozenClock, utc
from oipulse.marketdata.identity import IdentityConfidence
from oipulse.marketdata.lifecycle import ConnectionState, GapKind, SessionManager
from oipulse.marketdata.providers.upstox.normalize import (
    normalize_chain_row,
    normalize_ws_tick,
)
from oipulse.marketdata.providers.upstox.schemas import parse_chain_response
from oipulse.marketdata.recovery import RecoveryTrigger, plan_recovery
from oipulse.marketdata.store.memory import InMemoryObservationStore
from tests.fixtures.synthetic import synthetic_chain

INSTRUMENT_ID = 101
CHANNEL = "option_chain"


@dataclass
class SoakReport:
    """Everything the external run should capture for comparison."""

    sessions: int
    frames: int
    observations_written: int
    duplicates_suppressed: int
    websocket_gaps: int
    reconnect_gaps: int
    confidence: str
    recovery_triggers: list[str]

    def render(self) -> str:
        return (
            f"sessions={self.sessions} frames={self.frames} "
            f"written={self.observations_written} dup_suppressed={self.duplicates_suppressed} "
            f"ws_gaps={self.websocket_gaps} reconnect_gaps={self.reconnect_gaps} "
            f"confidence={self.confidence} recovery={','.join(self.recovery_triggers) or '-'}"
        )


def _tick(seq: int, ltp: float, with_identity: bool) -> dict:
    """A SYNTHETIC frame. Identity hints present or absent, per the A-1 branch."""
    frame: dict = {
        "instrument_key": "NSE_FO|CE25000|2026-03-05",
        "channel": CHANNEL,
        "market_data": {"ltp": ltp, "oi": 450000 + seq, "volume": 1000 + seq},
        "option_greeks": {"iv": 14.8, "delta": 0.52, "gamma": 0.0008, "theta": -9.1, "vega": 11.2},
    }
    if with_identity:
        frame["event_id"] = f"evt-{seq:06d}"
        frame["sequence"] = seq
    return frame


def run_soak(*, with_identity: bool) -> SoakReport:
    """Drive the full lifecycle and return what happened."""
    clock = FrozenClock(utc(2026, 3, 3, 6, 0, 0))
    ids = iter(f"sess-{i}" for i in range(1, 20))
    sessions = SessionManager(
        clock, heartbeat_budget=timedelta(seconds=10), session_id_factory=lambda: next(ids)
    )
    store = InMemoryObservationStore()

    frames = 0
    written = 0
    duplicates = 0
    recovery: list[str] = []

    def consume(seq: int, ltp: float) -> None:
        nonlocal frames, written, duplicates
        frames += 1
        payload = _tick(seq, ltp, with_identity)
        session = sessions.current
        assert session is not None
        observations = normalize_ws_tick(
            payload,
            instrument_id=INSTRUMENT_ID,
            clock=clock,
            feed_session_id=session.session_id,
            channel=CHANNEL,
            provider_event_id=payload.get("event_id"),
            channel_sequence=payload.get("sequence"),
            received_seq=frames,
        )
        for obs in observations:
            sessions.record_message(CHANNEL, payload.get("sequence"), obs.identity.confidence)
        result = store.append(observations)
        written += result.inserted
        duplicates += result.duplicates

    # ---- phase 1: one continuous session -----------------------------------
    for state in (
        ConnectionState.CONNECTING,
        ConnectionState.AUTHENTICATING,
        ConnectionState.SUBSCRIBING,
        ConnectionState.STREAMING,
    ):
        sessions.transition(state)
    sessions.open_session()

    for seq in range(1, 11):
        clock.set(utc(2026, 3, 3, 6, 0, seq))
        consume(seq, 120.0 + seq * 0.1)

    # ---- phase 2: a gap inside the session ---------------------------------
    clock.set(utc(2026, 3, 3, 6, 0, 15))
    consume(15, 121.5)  # 11..14 never arrived

    for gap in sessions.gaps:
        if gap.kind is GapKind.WEBSOCKET_GAP:
            plan = plan_recovery(gap, clock=clock, underlying_ids=(1,), expiry_ids=(10,))
            recovery.append(plan.trigger.value)

    # ---- phase 3: disconnect and reconnect ---------------------------------
    clock.set(utc(2026, 3, 3, 6, 0, 20))
    sessions.transition(ConnectionState.DISCONNECTED)
    sessions.close_session()
    clock.set(utc(2026, 3, 3, 6, 0, 26))
    sessions.transition(ConnectionState.CONNECTING)
    sessions.transition(ConnectionState.AUTHENTICATING)
    sessions.transition(ConnectionState.SUBSCRIBING)
    sessions.transition(ConnectionState.STREAMING)
    sessions.open_session()

    # ---- phase 4: resumed subscription; provider sequence restarts ----------
    for seq in range(1, 6):
        clock.set(utc(2026, 3, 3, 6, 0, 26 + seq))
        consume(seq, 122.0 + seq * 0.1)

    # ---- phase 5: overlapping REST recovery --------------------------------
    # The recovery fetch covers instants the resumed stream also delivered. This is the
    # moment idempotency has to hold: two independent paths, same market instant.
    rows, _ = parse_chain_response(synthetic_chain("2026-03-05", [25000]))
    recovered = normalize_chain_row(
        rows[0], call_instrument_id=INSTRUMENT_ID, put_instrument_id=None, clock=clock
    )
    first = store.append(recovered)
    second = store.append(recovered)  # the same recovery replayed
    written += first.inserted + second.inserted
    duplicates += first.duplicates + second.duplicates

    for gap in sessions.gaps:
        if gap.kind is GapKind.RECONNECT_GAP:
            plan = plan_recovery(gap, clock=clock, underlying_ids=(1,), expiry_ids=(10,))
            recovery.append(plan.trigger.value)

    current = sessions.current
    return SoakReport(
        sessions=len(sessions.sessions),
        frames=frames,
        observations_written=written,
        duplicates_suppressed=duplicates,
        websocket_gaps=sum(1 for g in sessions.gaps if g.kind is GapKind.WEBSOCKET_GAP),
        reconnect_gaps=sum(1 for g in sessions.gaps if g.kind is GapKind.RECONNECT_GAP),
        confidence=(current.confidence.value if current else "unknown"),
        recovery_triggers=recovery,
    )


class TestWebSocketSoakOffline(unittest.TestCase):
    def test_soak_with_provider_identity(self):
        """The A-1-positive branch: provider supplies event ids and sequences."""
        r = run_soak(with_identity=True)
        self.assertEqual(r.sessions, 2, "one reconnect means two sessions")
        self.assertEqual(r.reconnect_gaps, 1, "the outage window must be recorded")
        self.assertEqual(r.websocket_gaps, 1, "the in-session discontinuity must be caught")
        self.assertEqual(r.confidence, IdentityConfidence.STRONG.value)
        self.assertIn(RecoveryTrigger.WEBSOCKET_GAP.value, r.recovery_triggers)
        self.assertIn(RecoveryTrigger.RECONNECT.value, r.recovery_triggers)
        self.assertGreater(r.duplicates_suppressed, 0, "overlapping recovery must dedup")

    def test_soak_without_provider_identity(self):
        """The A-1-negative branch, which is the one in force until the soak says otherwise.

        Everything still works: sessions, reconnect recording, dedup and recovery. The
        single difference is that **no sequence gap is claimed**, because a content hash
        cannot demonstrate one. Claiming clean coverage here would be the lie.
        """
        r = run_soak(with_identity=False)
        self.assertEqual(r.sessions, 2)
        self.assertEqual(r.reconnect_gaps, 1, "reconnect gaps do not depend on sequences")
        self.assertEqual(r.websocket_gaps, 0, "no sequence -> no gap claim (constraint D)")
        self.assertEqual(r.confidence, IdentityConfidence.WEAK.value)
        self.assertNotIn(RecoveryTrigger.WEBSOCKET_GAP.value, r.recovery_triggers)
        self.assertIn(RecoveryTrigger.RECONNECT.value, r.recovery_triggers)

    def test_sequence_restart_after_reconnect_is_not_a_gap(self):
        """Constraint C, end to end: the resumed stream restarts at 1 and that is fine."""
        r = run_soak(with_identity=True)
        self.assertEqual(r.websocket_gaps, 1, "only the in-session gap, not the restart")

    def test_event_id_scope_is_an_open_provider_question(self):
        """Pins a real ambiguity the offline soak exposed, for the external run to settle.

        The synthetic feed restarts its event ids at `evt-000001` in the second session.
        Tier-1 identity is `provider_event_id` alone, so those repeats deduplicate
        against session 1 — 12 observations suppressed in the with-identity branch.

        Whether that is correct depends on a fact we do not have: **is an Upstox event id
        globally unique, or scoped to a feed session?**

        * Globally unique  -> tier 1 as-is is right, and the synthetic fixture is simply
          unrealistic.
        * Session-scoped   -> tier 1 must include `feed_session_id`, or a reconnect will
          silently discard live data as "duplicates".

        The second failure mode is silent and destructive, so this is recorded as an
        explicit assumption rather than resolved by guessing. The external soak must
        capture two sessions' raw frames and compare their event ids.
        """
        strong = run_soak(with_identity=True)
        weak = run_soak(with_identity=False)
        # Current behaviour, pinned so a change to identity scoping is visible in CI.
        self.assertGreater(
            strong.duplicates_suppressed,
            weak.duplicates_suppressed,
            "tier-1 identity currently dedups across sessions; see docstring",
        )

    def test_soak_is_deterministic(self):
        self.assertEqual(
            run_soak(with_identity=True).render(), run_soak(with_identity=True).render()
        )


if __name__ == "__main__":
    for flag in (True, False):
        print(f"identity_hints={flag}: {run_soak(with_identity=flag).render()}")
    unittest.main(verbosity=2)
