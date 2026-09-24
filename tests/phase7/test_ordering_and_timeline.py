"""Replay clock, cross-session ordering, timeline construction and PIT reconstruction.

Brief §1 to §5. The properties under test here are the foundation everything else in
Phase 7 rests on: if the order is not total and deterministic, no downstream
determinism claim means anything.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from oipulse.core.clock import ReplayClock
from oipulse.replay.context import KnowledgeMode, StepMode
from oipulse.replay.ordering import MISSING_SEQUENCE, SessionOrdinals, order_key, replay_order
from oipulse.replay.timeline import build_timeline, lockstep_horizons
from tests.phase3._fixtures import at
from tests.phase7 import _fixtures as fx


def _observation_in(session_id: str, *, sequence: int) -> object:
    """A quote whose identity carries a specific feed session and channel sequence.

    Constructed rather than fixture-derived because Upstox V3 supplies neither a
    provider event id nor a channel sequence (AD-30); the ordering rule still has to
    be correct for a provider that does, and this is the only way to exercise it.
    """
    from dataclasses import replace

    from tests.phase3._fixtures import quote_obs

    base = quote_obs(fx.TARGET, at(1))
    identity = replace(base.identity, feed_session_id=session_id, channel_sequence=sequence)
    return replace(base, identity=identity)


class TestReplayClock(unittest.TestCase):
    def test_the_clock_only_moves_forward(self) -> None:
        """A backwards step would let a later decision observe an earlier state."""
        clock = ReplayClock(at(0))
        clock.advance_to(at(2))
        self.assertEqual(clock.now(), at(2))
        with self.assertRaises(ValueError):
            clock.advance_to(at(1))

    def test_advancing_to_the_same_instant_is_allowed(self) -> None:
        """Several underlyings share one step; each may ask for the same instant."""
        clock = ReplayClock(at(0))
        clock.advance_to(at(1))
        clock.advance_to(at(1))
        self.assertEqual(clock.now(), at(1))


class TestCrossSessionOrdering(unittest.TestCase):
    """`10` §B4: `(observed_at, channel_sequence, id)` is **not** a total order.

    `channel_sequence` resets per feed session, so two rows from different sessions
    can carry the same sequence. The key has to interpose a session ordinal.
    """

    ORDINALS = SessionOrdinals.of({"session-a": 0, "session-b": 1})

    def test_ordering_is_independent_of_input_order(self) -> None:
        rows = fx.observations()
        forward = replay_order(rows)  # type: ignore[arg-type]
        backward = replay_order(list(reversed(rows)))  # type: ignore[arg-type]
        self.assertEqual(
            [o.identity.dedup_key for o in forward],
            [o.identity.dedup_key for o in backward],
            "a database returning rows in a different order must not change a replay",
        )

    def test_a_colliding_channel_sequence_is_split_by_the_session_ordinal(self) -> None:
        """The reason `(observed_at, channel_sequence, id)` is not a total order.

        `channel_sequence` resets per feed session, so a reconnect can re-emit
        sequence 7. Without the session ordinal the two rows tie, and their relative
        order falls to whatever the id tiebreaker happens to be -- which is not the
        chronological order. The ids here are chosen to disagree deliberately.
        """
        earlier = _observation_in("session-a", sequence=7)
        later = _observation_in("session-b", sequence=7)
        key_earlier = order_key(earlier, self.ORDINALS, 99)
        key_later = order_key(later, self.ORDINALS, 1)
        self.assertLess(
            key_earlier,
            key_later,
            "the session ordinal must decide, even when the id tiebreaker disagrees",
        )
        self.assertNotEqual(key_earlier[1], key_later[1])

    def test_an_observation_with_no_session_sorts_before_streamed_rows(self) -> None:
        """A REST snapshot is the anchor streamed updates are merged onto (`04` §4)."""
        self.assertLess(self.ORDINALS.ordinal(None), self.ORDINALS.ordinal("session-a"))

    def test_an_unknown_session_sorts_last_deterministically(self) -> None:
        """Unplaceable, but never dropped and never randomly ordered."""
        unknown = self.ORDINALS.ordinal("session-never-seen")
        self.assertEqual(unknown, 2)
        self.assertGreater(unknown, self.ORDINALS.ordinal("session-b"))

    def test_a_missing_channel_sequence_sorts_before_a_real_zero(self) -> None:
        """`MISSING_SEQUENCE` is -1, not 0.

        A provider could legitimately emit sequence 0, and collapsing "absent" onto
        it would make a real first message indistinguishable from no sequence at all.
        """
        self.assertEqual(MISSING_SEQUENCE, -1)
        self.assertLess(MISSING_SEQUENCE, 0)

    def test_the_order_key_is_a_stable_total_order(self) -> None:
        rows = replay_order(fx.observations())  # type: ignore[arg-type]
        ordinals = SessionOrdinals.of({"synthetic-session-1": 0})
        keys = [order_key(o, ordinals, i) for i, o in enumerate(rows)]
        self.assertEqual(keys, sorted(keys), "the emitted order must match the key order")
        self.assertEqual(len(set(keys)), len(keys), "the key must be total, not merely a sort")


class TestTimeline(unittest.TestCase):
    def test_event_mode_produces_one_step_per_distinct_market_time(self) -> None:
        tl = fx.timeline(fx.observations(minutes=4), fx.context(minutes=4))
        self.assertEqual(len(tl.steps), 4)
        self.assertEqual([s.index for s in tl.steps], [0, 1, 2, 3])

    def test_fixed_interval_mode_is_independent_of_observation_times(self) -> None:
        ctx = fx.context(
            minutes=6, step_mode=StepMode.FIXED_INTERVAL, interval=timedelta(minutes=1)
        )
        tl = build_timeline(ctx, fx.observations())  # type: ignore[arg-type]
        self.assertEqual([s.market_time for s in tl.steps], [at(i) for i in range(6)])

    def test_lockstep_is_the_default_and_every_step_has_k_equal_t(self) -> None:
        """Hindsight is never entered by default."""
        tl = fx.timeline(fx.observations())
        self.assertTrue(lockstep_horizons(tl))
        self.assertFalse(tl.context.is_hindsight)

    def test_visibility_requires_both_axes_not_either(self) -> None:
        """The property a one-axis filter would silently break.

        With a four-minute ingest lag, an observation observed at minute 1 was not
        known until minute 5. At a lockstep step of minute 2 it must be invisible,
        even though its market timestamp is in the past.
        """
        rows = fx.observations(minutes=6, ingest_lag=timedelta(minutes=4))
        tl = fx.timeline(rows)
        step = tl.steps[2]
        visible = tl.visible_observations(step)
        self.assertTrue(
            all(o.ingested_at <= step.knowledge_horizon for o in visible),
            "an observation ingested after K must not be visible at K",
        )
        observed_only = [o for o in tl.observations if o.observed_at <= step.market_time]
        self.assertLess(
            len(visible),
            len(observed_only),
            "with a real ingest lag, the two-axis filter must be strictly stronger "
            "than observed_at alone -- otherwise this test proves nothing",
        )

    def test_steps_from_returns_the_same_objects_not_recomputed_ones(self) -> None:
        """Resume must not drift from an uninterrupted run."""
        tl = fx.timeline(fx.observations())
        resumed = tl.steps_from(tl.steps[3].market_time)
        self.assertEqual(len(resumed), 3)
        for original, again in zip(tl.steps[3:], resumed, strict=True):
            self.assertIs(original, again)


class TestPointInTimeReconstruction(unittest.TestCase):
    def test_each_step_reconstructs_the_state_at_its_own_two_times(self) -> None:
        rows = fx.observations()
        tl = fx.timeline(rows)
        results = fx.engine(rows).run(tl)
        self.assertEqual(len(results), len(tl.steps))
        for stepped in results:
            self.assertEqual(stepped.state.market_time, stepped.step.market_time)
            self.assertEqual(stepped.state.knowledge_horizon, stepped.step.knowledge_horizon)

    def test_a_later_step_never_influences_an_earlier_one(self) -> None:
        """Truncating the future must not change any state before it.

        This is look-ahead stated as a property rather than as an intention: if a
        state at minute 2 depended on anything after minute 2, removing minutes 3-5
        would change it.
        """
        full = fx.observations(minutes=6)
        truncated = fx.observations(minutes=3)

        long_run = fx.engine(full).run(fx.timeline(full, fx.context(minutes=6)))
        short_run = fx.engine(truncated).run(fx.timeline(truncated, fx.context(minutes=3)))

        by_time = {s.step.market_time: s.state.content_digest() for s in long_run}
        for stepped in short_run:
            self.assertEqual(
                by_time[stepped.step.market_time],
                stepped.state.content_digest(),
                f"the state at {stepped.step.market_time} changed when later "
                f"observations were added -- that is look-ahead",
            )

    def test_market_truth_mode_requires_an_explicit_pinned_horizon(self) -> None:
        with self.assertRaises(ValueError):
            fx.context(knowledge_mode=KnowledgeMode.MARKET_TRUTH, pinned=None)


if __name__ == "__main__":
    unittest.main()
