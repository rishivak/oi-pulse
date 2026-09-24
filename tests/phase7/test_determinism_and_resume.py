"""Replay determinism, knowledge-aware checkpoint selection, resume and corrections.

Brief §6, §16 and §17. The claim under test is the one everything else depends on:

> same observations + same K + same BuildContext = same MarketState

and its replay-level consequence, that a run's output is a function of its inputs and
not of when, where or in how many pieces it was executed.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from oipulse.core.ids import InstrumentId
from oipulse.marketstate.checkpoints import InMemoryCheckpointStore, StateService
from oipulse.marketstate.state import StateIdentity
from oipulse.replay.context import KnowledgeMode
from oipulse.replay.engine import ReplayEngine, ResumePoint
from tests.phase3._fixtures import UNDERLYING, at, builder
from tests.phase7 import _fixtures as fx


def _digests(results: tuple[object, ...]) -> list[str]:
    return [r.state.content_digest() for r in results]  # type: ignore[attr-defined]


class TestReplayDeterminism(unittest.TestCase):
    def test_two_identical_runs_produce_identical_states(self) -> None:
        rows = fx.observations()
        first = fx.engine(rows).run(fx.timeline(rows))
        second = fx.engine(rows).run(fx.timeline(rows))
        self.assertEqual(_digests(first), _digests(second))

    def test_shuffling_the_input_does_not_change_the_output(self) -> None:
        """Deterministic against input order, not merely repeatable.

        Re-running the same list proves little; a store returning rows in a different
        order is the realistic case, and it must produce the same replay.
        """
        rows = fx.observations()
        shuffled = list(reversed(rows))
        self.assertEqual(
            _digests(fx.engine(rows).run(fx.timeline(rows))),
            _digests(fx.engine(shuffled).run(fx.timeline(shuffled))),
        )

    def test_the_run_id_and_speed_are_excluded_from_replay_identity(self) -> None:
        """Two runs differing only in label or playback rate are the same replay."""
        from oipulse.replay.context import ReplaySpeed

        a = fx.context(run_id="run-a")
        b = fx.context(run_id="run-b")
        self.assertEqual(a.content_digest, b.content_digest)

        import dataclasses

        fast = dataclasses.replace(a, speed=ReplaySpeed.MAX)
        slow = dataclasses.replace(a, speed=ReplaySpeed.X1)
        self.assertEqual(fast.content_digest, slow.content_digest)

    def test_a_different_build_context_is_a_different_replay(self) -> None:
        self.assertNotEqual(
            fx.context(build_context_id="bc-1").content_digest,
            fx.context(build_context_id="bc-2").content_digest,
        )


class TestKnowledgeAwareCheckpointSelection(unittest.TestCase):
    """`10` §4: a checkpoint may be reused only on an exact identity match.

    A checkpoint built live at `K = 11:50` must never satisfy a replay step at
    `K = 11:43`; it may incorporate observations that had not arrived by 11:43, which
    is look-ahead arriving through a cache.
    """

    def test_a_checkpoint_at_a_later_knowledge_horizon_is_not_reused(self) -> None:
        rows = fx.observations()
        store = fx.store_with(rows)
        service = StateService(builder(store), InMemoryCheckpointStore())
        # Build and cache a state at a *later* knowledge horizon than the replay will
        # ask for.
        service.get_state(UNDERLYING, at(2), at(5))

        checkpoints = InMemoryCheckpointStore()
        engine = ReplayEngine(StateService(builder(store), checkpoints), checkpoints=checkpoints)
        engine.run(fx.timeline(rows))
        self.assertEqual(
            engine.progress.checkpoint_hits,
            0,
            "no lockstep step may be served by a checkpoint built at a later K",
        )

    def test_hits_and_misses_are_counted_and_sum_to_the_state_count(self) -> None:
        rows = fx.observations()
        engine = fx.engine(rows)
        engine.run(fx.timeline(rows))
        progress = engine.progress
        self.assertEqual(
            progress.checkpoint_hits + progress.checkpoint_misses, progress.states_built
        )
        self.assertEqual(progress.steps_completed, len(fx.timeline(rows).steps))

    def test_an_exact_identity_match_is_required_on_all_four_parts(self) -> None:
        """Change any one of the four and the checkpoint must not match."""
        base = StateIdentity(
            underlying_id=InstrumentId(UNDERLYING),
            market_time=at(2),
            knowledge_horizon=at(2),
            build_context_id=fx.BUILD_CONTEXT,
        )
        import dataclasses

        variants = [
            dataclasses.replace(base, market_time=at(3)),
            dataclasses.replace(base, knowledge_horizon=at(3)),
            dataclasses.replace(base, build_context_id="other"),
            dataclasses.replace(base, underlying_id=InstrumentId(999)),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(base.as_key(), variant.as_key())


class TestResume(unittest.TestCase):
    def test_a_resumed_run_matches_the_tail_of_an_uninterrupted_one(self) -> None:
        """Brief §17: resume must be identical, not merely similar."""
        rows = fx.observations()
        tl = fx.timeline(rows)

        whole = _digests(fx.engine(rows).run(tl))
        first_half = fx.engine(rows).run(tl)[:3]
        resumed = fx.engine(rows).run(tl, from_market_time=tl.steps[3].market_time)

        self.assertEqual(_digests(tuple(first_half)) + _digests(resumed), whole)

    def test_resuming_under_a_different_build_context_is_refused(self) -> None:
        point = ResumePoint(
            run_id="run-test",
            step_index=3,
            market_time=at(3),
            knowledge_horizon=at(3),
            build_context_id="bc-other",
        )
        with self.assertRaises(ValueError) as caught:
            point.validate_for(fx.context())
        self.assertIn("build context", str(caught.exception))

    def test_resuming_at_a_later_knowledge_horizon_is_refused(self) -> None:
        """Otherwise resume imports look-ahead through the checkpoint."""
        point = ResumePoint(
            run_id="run-test",
            step_index=3,
            market_time=at(3),
            knowledge_horizon=at(5),
            build_context_id=fx.BUILD_CONTEXT,
        )
        with self.assertRaises(ValueError) as caught:
            point.validate_for(fx.context())
        self.assertIn("look-ahead", str(caught.exception))

    def test_a_matching_resume_point_is_accepted(self) -> None:
        point = ResumePoint(
            run_id="run-test",
            step_index=3,
            market_time=at(3),
            knowledge_horizon=at(3),
            build_context_id=fx.BUILD_CONTEXT,
        )
        point.validate_for(fx.context())  # must not raise


class TestCorrectionsAndBitemporality(unittest.TestCase):
    def test_a_late_correction_is_invisible_to_a_replay_before_it_arrived(self) -> None:
        """A correction ingested at minute 5 must not alter the minute-2 state.

        This is the bitemporal rule stated as a replay property: the past is replayed
        as it was *known*, not as it was later understood.
        """
        rows = fx.observations()
        corrected = list(rows)
        from tests.phase3._fixtures import quote_obs

        corrected.append(quote_obs(fx.TARGET, at(2), at(5), ltp="999", oi=1, provider_prev_oi=1))

        clean = fx.engine(rows).run(fx.timeline(rows))
        with_correction = fx.engine(corrected).run(fx.timeline(corrected))

        by_time = {s.step.market_time: s.state.content_digest() for s in clean}
        early = next(s for s in with_correction if s.step.market_time == at(2))
        self.assertEqual(
            by_time[at(2)],
            early.state.content_digest(),
            "a correction ingested at minute 5 changed the minute-2 replay state",
        )

    def test_the_same_correction_is_visible_once_knowledge_reaches_it(self) -> None:
        """The other half: a correction must not be invisible forever.

        Without this, the previous test would also pass against an implementation
        that simply ignored the row.
        """
        rows = fx.observations()
        from tests.phase3._fixtures import quote_obs

        corrected = [*rows, quote_obs(fx.TARGET, at(2), at(5), ltp="999")]

        service = StateService(builder(fx.store_with(corrected)))
        before = service.get_state(UNDERLYING, at(2), at(4))
        after = service.get_state(UNDERLYING, at(2), at(5))
        self.assertNotEqual(
            before.content_digest(),
            after.content_digest(),
            "the correction must become visible at the K it was ingested by",
        )

    def test_market_truth_mode_pins_one_horizon_for_every_step(self) -> None:
        ctx = fx.context(
            knowledge_mode=KnowledgeMode.MARKET_TRUTH, pinned=at(5) + timedelta(hours=1)
        )
        tl = fx.timeline(fx.observations(), ctx)
        self.assertTrue(ctx.is_hindsight)
        self.assertEqual({s.knowledge_horizon for s in tl.steps}, {at(5) + timedelta(hours=1)})


if __name__ == "__main__":
    unittest.main()
