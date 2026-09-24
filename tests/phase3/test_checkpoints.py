"""Checkpoint selection, reuse and materialization — `04-MARKETSTATE.md` §5.

The rule under test is a prohibition, so most of these assert that something is *not*
reused:

> A checkpoint whose `knowledge_horizon` is later than the requested `K` must never be
> used. It may incorporate observations that had not yet arrived at `K`.

Nearest-match is the subtle form of look-ahead: it returns something plausible, and the
error surfaces only as a backtest that cannot be reproduced live.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketstate.builder import StateBuilder
from oipulse.marketstate.checkpoints import (
    Checkpoint,
    CheckpointTrigger,
    InMemoryCheckpointStore,
    StateService,
)
from oipulse.marketstate.context import BuildContext
from oipulse.marketstate.state import StateIdentity
from tests.phase3._fixtures import UNDERLYING, at, builder, populated_store


class _CountingBuilder(StateBuilder):
    """Counts builds, so "reused" can be proven rather than inferred from equality."""

    builds = 0

    def build(self, underlying_id, market_time, knowledge_horizon=None):  # type: ignore[override]
        type(self).builds += 1
        return super().build(underlying_id, market_time, knowledge_horizon)


class TestCheckpointSelection(unittest.TestCase):
    def setUp(self):
        _CountingBuilder.builds = 0
        self.store = populated_store(at(0))
        base = builder(self.store)
        self.builder = _CountingBuilder(
            self.store, base._universe, base._clock, build_context=base.build_context
        )
        self.checkpoints = InMemoryCheckpointStore()
        self.service = StateService(self.builder, self.checkpoints)

    def _write(self, market_time, knowledge, context=None):
        state = StateBuilder(
            self.store,
            self.builder._universe,
            self.builder._clock,
            build_context=context or self.builder.build_context,
        ).build(UNDERLYING, market_time, knowledge)
        self.checkpoints.put(
            Checkpoint(state, CheckpointTrigger.CADENCE, state.provenance.assembled_at)
        )
        return state

    def test_a_matching_checkpoint_is_reused(self):
        self._write(at(5), at(5))
        before = _CountingBuilder.builds
        state = self.service.get_state(UNDERLYING, at(5), at(5))
        self.assertEqual(_CountingBuilder.builds, before, "no rebuild should have happened")
        self.assertEqual(self.service.reuse_count, 1)
        self.assertEqual(state.identity.market_time, at(5))

    def test_a_later_k_checkpoint_never_satisfies_an_earlier_k_request(self):
        """The headline prohibition. A later-K state may contain data absent at K."""
        self._write(at(5), at(20))
        state = self.service.get_state(UNDERLYING, at(5), at(5))
        self.assertEqual(self.service.reuse_count, 0)
        self.assertEqual(self.service.reconstruction_count, 1)
        self.assertEqual(state.identity.knowledge_horizon, at(5))

    def test_an_earlier_k_checkpoint_never_satisfies_a_later_k_request(self):
        """Also not a substitute: it is missing data that had arrived by K."""
        self._write(at(5), at(5))
        self.service.get_state(UNDERLYING, at(5), at(20))
        self.assertEqual(self.service.reuse_count, 0)
        self.assertEqual(self.service.reconstruction_count, 1)

    def test_a_different_market_time_is_rejected(self):
        self._write(at(5), at(5))
        self.service.get_state(UNDERLYING, at(6), at(6))
        self.assertEqual(self.service.reuse_count, 0)

    def test_a_different_build_context_is_rejected(self):
        """Selection is on the full tuple; a context mismatch means reconstruct."""
        other = BuildContext.create(
            staleness_policy_version="9.9.9", configuration={"different": True}
        )
        self._write(at(5), at(5), context=other)
        self.service.get_state(UNDERLYING, at(5), at(5))
        self.assertEqual(self.service.reuse_count, 0)

    def test_selection_is_exact_match_not_nearest(self):
        """Several near misses stored; none may be chosen."""
        for k in (at(4), at(6), at(7)):
            self._write(at(5), k)
        self.service.get_state(UNDERLYING, at(5), at(5))
        self.assertEqual(self.service.reuse_count, 0, "nearest-match is look-ahead in disguise")


class TestCheckpointStore(unittest.TestCase):
    def setUp(self):
        self.store = populated_store(at(0))
        self.builder = builder(self.store)
        self.checkpoints = InMemoryCheckpointStore()

    def _cp(self, market_time, knowledge=None, trigger=CheckpointTrigger.CADENCE):
        state = self.builder.build(UNDERLYING, market_time, knowledge)
        return Checkpoint(state, trigger, state.provenance.assembled_at)

    def test_the_identity_tuple_is_the_uniqueness_key(self):
        """Mirrors `UNIQUE (underlying_id, observed_at, knowledge_horizon,
        build_context_id)` so a test passing here is testing the database's real rule."""
        self.checkpoints.put(self._cp(at(5), at(5)))
        self.checkpoints.put(self._cp(at(5), at(20)))
        self.assertEqual(self.checkpoints.count, 2, "different K is a different row")

    def test_a_byte_identical_successor_is_deduplicated(self):
        """§5: during a quiet period this collapses redundant rows losing nothing,
        because reconstruction would produce the same state anyway."""
        first = self._cp(at(5), at(5))
        self.assertTrue(self.checkpoints.put(first))
        self.assertFalse(self.checkpoints.put(self._cp(at(5), at(5))))
        self.assertEqual(self.checkpoints.count, 1)
        self.assertEqual(self.checkpoints.deduplicated, 1)

    def test_every_documented_trigger_exists(self):
        self.assertEqual(
            {t.value for t in CheckpointTrigger},
            {"cadence", "chain_snapshot", "session_boundary", "quality_transition", "manual"},
        )

    def test_lookup_uses_the_full_tuple(self):
        self.checkpoints.put(self._cp(at(5), at(5)))
        wrong_context = StateIdentity(
            underlying_id=UNDERLYING,
            market_time=at(5),
            knowledge_horizon=at(5),
            build_context_id="bc_not_this_one",
        )
        self.assertIsNone(self.checkpoints.get(wrong_context))


class TestReconstructionEqualsMaterialization(unittest.TestCase):
    """§5: one function serves live assembly, reconstruction, replay and backtest.

    A separate historical path is the classic backtest/live mismatch: the two drift,
    and the drift is invisible until a strategy that backtested well loses money.
    """

    def test_a_checkpointed_state_equals_a_fresh_reconstruction(self):
        store = populated_store(at(0))
        b = builder(store)
        checkpoints = InMemoryCheckpointStore()
        service = StateService(b, checkpoints)

        materialized = service.checkpoint(UNDERLYING, at(5), at(5)).state
        reconstructed = StateBuilder(
            store, b._universe, b._clock, build_context=b.build_context
        ).build(UNDERLYING, at(5), at(5))

        self.assertEqual(materialized.content_digest(), reconstructed.content_digest())
        self.assertEqual(materialized.as_comparable(), reconstructed.as_comparable())

    def test_reuse_returns_the_same_content_as_reconstruction(self):
        store = populated_store(at(0))
        b = builder(store)
        service = StateService(b, InMemoryCheckpointStore())
        service.checkpoint(UNDERLYING, at(5), at(5))

        reused = service.get_state(UNDERLYING, at(5), at(5))
        fresh = StateService(b, None).get_state(UNDERLYING, at(5), at(5))
        self.assertEqual(reused.content_digest(), fresh.content_digest())

    def test_caching_a_reconstruction_is_off_by_default(self):
        """Growing state_checkpoints from read traffic is a surprising cost for a GET."""
        store = populated_store(at(0))
        checkpoints = InMemoryCheckpointStore()
        StateService(builder(store), checkpoints).get_state(UNDERLYING, at(5), at(5))
        self.assertEqual(checkpoints.count, 0)

        opt_in = InMemoryCheckpointStore()
        StateService(builder(store), opt_in, cache_reconstructions=True).get_state(
            UNDERLYING, at(5), at(5)
        )
        self.assertEqual(opt_in.count, 1)


if __name__ == "__main__":
    unittest.main()
