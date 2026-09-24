"""MarketState identity — `04-MARKETSTATE.md` §1.

Identity is `(underlying_id, market_time, knowledge_horizon, build_context_id)`. Every
element is load-bearing, and the tests below fail if any one of them is dropped from
the key. The two that get dropped in practice are `knowledge_horizon` (mistaken for
metadata) and `build_context_id` (mistaken for a version tag), so each has its own test
asserting a *collision does not happen*.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketstate.builder import StateBuilder
from oipulse.marketstate.context import BuildContext
from oipulse.marketstate.staleness import DEFAULT_STALENESS_POLICY, StalenessPolicy
from oipulse.marketstate.state import StateIdentity
from tests.phase3._fixtures import UNDERLYING, at, builder, populated_store


class TestBuildContext(unittest.TestCase):
    """Immutable, deterministic, content-addressable (`04` §1)."""

    def _ctx(self, **overrides: object) -> BuildContext:
        base: dict[str, object] = {
            "staleness_policy_version": "1.0.0",
            "configuration": {"spot_budget_s": 5, "anchor_max_age_s": 30},
        }
        base.update(overrides)
        return BuildContext.create(**base)  # type: ignore[arg-type]

    def test_same_configuration_yields_the_same_identity(self):
        self.assertEqual(self._ctx().id, self._ctx().id)

    def test_the_identity_is_stable_across_dict_ordering(self):
        """Two processes building the same config differently must still agree."""
        a = BuildContext.create(staleness_policy_version="1.0.0", configuration={"a": 1, "b": 2})
        b = BuildContext.create(staleness_policy_version="1.0.0", configuration={"b": 2, "a": 1})
        self.assertEqual(a.id, b.id)

    def test_a_materially_different_configuration_yields_a_different_identity(self):
        changed = self._ctx(configuration={"spot_budget_s": 30, "anchor_max_age_s": 30})
        self.assertNotEqual(self._ctx().id, changed.id)

    def test_each_version_field_participates_in_the_identity(self):
        """Dropping any one from the derivation would let incomparable states collide."""
        base = self._ctx()
        for field, value in (
            ("builder_version", "9.9.9"),
            ("staleness_policy_version", "9.9.9"),
            ("feature_set_version", "9.9.9"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(base.id, self._ctx(**{field: value}).id)

    def test_a_context_is_immutable(self):
        ctx = self._ctx()
        with self.assertRaises(AttributeError):
            ctx.builder_version = "2.0.0"  # type: ignore[misc]

    def test_a_changed_staleness_budget_changes_the_build_context(self):
        """§3: budgets are configuration carried in the context, so states built under
        different budgets can never collide."""
        strict = StateBuilder(
            populated_store(at(0)),
            builder(populated_store(at(0)))._universe,
            builder(populated_store(at(0)))._clock,
        )
        loosened = StalenessPolicy(
            version="1.0.1",
            budgets=tuple(
                b
                if b.category.value != "spot"
                else type(b)(
                    b.category,
                    timedelta(seconds=120),
                    b.on_breach,
                    b.severity,
                    b.drop_on_breach,
                    b.required,
                )
                for b in DEFAULT_STALENESS_POLICY.budgets
            ),
        )
        relaxed = StateBuilder(
            populated_store(at(0)),
            builder(populated_store(at(0)))._universe,
            builder(populated_store(at(0)))._clock,
            policy=loosened,
        )
        self.assertNotEqual(strict.build_context.id, relaxed.build_context.id)


class TestStateIdentity(unittest.TestCase):
    """A state must not collide merely because underlying and market_time match."""

    def setUp(self):
        self.store = populated_store(at(0))
        self.builder = builder(self.store)

    def test_different_knowledge_horizons_are_distinguishable(self):
        """`MarketState(NIFTY, T, K=T)` and `(NIFTY, T, K=T+8m)` are different states."""
        early = self.builder.build(UNDERLYING, at(5), at(5))
        late = self.builder.build(UNDERLYING, at(5), at(13))
        self.assertNotEqual(early.identity.as_key(), late.identity.as_key())
        self.assertEqual(early.identity.underlying_id, late.identity.underlying_id)
        self.assertEqual(early.identity.market_time, late.identity.market_time)

    def test_different_market_times_are_distinguishable(self):
        a = self.builder.build(UNDERLYING, at(5))
        b = self.builder.build(UNDERLYING, at(6))
        self.assertNotEqual(a.identity.as_key(), b.identity.as_key())

    def test_different_build_contexts_are_distinguishable(self):
        other = StateBuilder(
            self.store,
            self.builder._universe,
            self.builder._clock,
            build_context=BuildContext.create(
                staleness_policy_version="2.0.0", configuration={"different": True}
            ),
        )
        a = self.builder.build(UNDERLYING, at(5))
        b = other.build(UNDERLYING, at(5))
        self.assertEqual(a.identity.market_time, b.identity.market_time)
        self.assertNotEqual(a.identity.build_context_id, b.identity.build_context_id)
        self.assertNotEqual(a.identity.as_key(), b.identity.as_key())

    def test_the_identity_key_carries_exactly_four_elements(self):
        """Not three, and not five: `decision_time` is a query parameter, never
        identity and never a stored field (`05` §2)."""
        key = self.builder.build(UNDERLYING, at(5)).identity.as_key()
        self.assertEqual(len(key), 4)

    def test_identity_does_not_carry_builder_version_separately(self):
        """AD-21: `build_context_id` subsumes it. Two sources for one fact can disagree."""
        fields = set(StateIdentity.__slots__)
        self.assertNotIn("builder_version", fields)
        self.assertNotIn("staleness_policy_version", fields)
        self.assertEqual(
            fields,
            {"underlying_id", "market_time", "knowledge_horizon", "build_context_id"},
        )

    def test_the_builder_accepts_a_knowledge_horizon_before_market_time(self):
        """`K < T` is a legitimate bitemporal query, and the layer split is deliberate.

        `12-API_SPEC.md` §2 rejects it on `/market/state`, where it is almost always
        two timestamps entered the wrong way round. The builder must still honour it:
        `MarketState(T=11:45, K=11:42)` is precisely the query that demonstrates a
        late-arriving observation being excluded, which the Phase 3 brief requires.

        The bound resolves to `knowledge_at(K)` because the knowledge constraint
        dominates -- an observation cannot be ingested before it was observed, so
        `ingested_at <= K` already implies `observed_at <= K < T`.
        """
        from oipulse.core.timemode import KnowledgeAt

        state = self.builder.build(UNDERLYING, at(10), at(5))
        self.assertEqual(state.identity.market_time, at(10))
        self.assertEqual(state.identity.knowledge_horizon, at(5))

        bound = StateBuilder.bound_for(at(10), at(5))
        self.assertIsInstance(bound, KnowledgeAt)
        self.assertEqual(bound.t, at(5))

    def test_the_endpoint_rejects_what_the_builder_permits(self):
        """The guard lives at the HTTP edge, and is asserted to still be there."""
        source = (REPO / "oipulse/api/market_state.py").read_text(encoding="utf-8")
        self.assertIn("knowledge_time < market_time is rejected", source)
        self.assertIn("HTTP_422_UNPROCESSABLE_ENTITY", source)


if __name__ == "__main__":
    unittest.main()
