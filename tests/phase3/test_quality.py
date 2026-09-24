"""Staleness, coverage, coherence and determinism — `04-MARKETSTATE.md` §3, §4.

The recurring principle: a number and its trustworthiness travel together, and missing
data stays missing. A stale-but-precise-looking value and a fabricated zero are the two
ways a state can lie while appearing healthy.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketdata.store.memory import InMemoryObservationStore
from oipulse.marketstate.builder import DEFAULT_ANCHOR_MAX_AGE, StateBuilder
from oipulse.marketstate.staleness import (
    DEFAULT_STALENESS_POLICY,
    DataCategory,
    QualityStatus,
)
from oipulse.marketstate.state import CoherenceMode
from tests.phase3._fixtures import (
    EXPIRY_A,
    STRIKES,
    UNDERLYING,
    at,
    builder,
    greeks_obs,
    index_obs,
    leg_id,
    populated_store,
    quote_obs,
)

CALL_A0 = leg_id(0, 0, True)


class TestDocumentedBudgets(unittest.TestCase):
    """The budgets are the documented ones. None was invented here."""

    def test_every_documented_budget_matches_the_design(self):
        expected = {
            DataCategory.SPOT: 5,
            DataCategory.FUTURES: 5,
            DataCategory.OPTION_QUOTE: 30,
            DataCategory.OPTION_OI: 60,
            DataCategory.GREEKS: 60,
            DataCategory.DEPTH: 10,
            DataCategory.INDEX_OHLC: 60,
        }
        for category, seconds in expected.items():
            with self.subTest(category=category.value):
                budget = DEFAULT_STALENESS_POLICY.budget_for(category)
                self.assertEqual(budget.max_age, timedelta(seconds=seconds))

    def test_spot_is_required_and_breaches_to_unreliable(self):
        budget = DEFAULT_STALENESS_POLICY.budget_for(DataCategory.SPOT)
        self.assertTrue(budget.required)
        self.assertIs(budget.on_breach, QualityStatus.UNRELIABLE)

    def test_depth_is_dropped_rather_than_served_stale(self):
        self.assertTrue(DEFAULT_STALENESS_POLICY.budget_for(DataCategory.DEPTH).drop_on_breach)

    def test_oi_and_greeks_are_looser_than_spot(self):
        """They must reflect the feed's real cadence, not an aspiration."""
        spot = DEFAULT_STALENESS_POLICY.budget_for(DataCategory.SPOT).max_age
        for category in (DataCategory.OPTION_OI, DataCategory.GREEKS):
            with self.subTest(category=category.value):
                self.assertGreater(DEFAULT_STALENESS_POLICY.budget_for(category).max_age, spot)

    def test_the_coverage_thresholds_match_the_escalation_table(self):
        self.assertEqual(DEFAULT_STALENESS_POLICY.coverage_ok, 0.98)
        self.assertEqual(DEFAULT_STALENESS_POLICY.coverage_degraded, 0.80)


class TestEscalation(unittest.TestCase):
    """The escalation table, applied literally."""

    def _escalate(self, **kwargs):
        defaults = {
            "breached": frozenset(),
            "coverage_ratio": 1.0,
            "missing_subscribed_expiry": False,
        }
        return DEFAULT_STALENESS_POLICY.escalate(**{**defaults, **kwargs})

    def test_everything_within_budget_is_ok(self):
        self.assertIs(self._escalate(), QualityStatus.OK)

    def test_a_non_spot_breach_degrades(self):
        self.assertIs(
            self._escalate(breached=frozenset({DataCategory.GREEKS})), QualityStatus.DEGRADED
        )

    def test_coverage_between_80_and_98_degrades(self):
        self.assertIs(self._escalate(coverage_ratio=0.9), QualityStatus.DEGRADED)

    def test_a_spot_breach_is_unreliable(self):
        self.assertIs(
            self._escalate(breached=frozenset({DataCategory.SPOT})), QualityStatus.UNRELIABLE
        )

    def test_coverage_below_80_is_unreliable(self):
        self.assertIs(self._escalate(coverage_ratio=0.5), QualityStatus.UNRELIABLE)

    def test_a_missing_subscribed_expiry_is_unreliable(self):
        self.assertIs(self._escalate(missing_subscribed_expiry=True), QualityStatus.UNRELIABLE)

    def test_an_unreliable_state_is_still_built(self):
        """Suppressing it would hide the outage. Only signal evaluation is skipped."""
        store = InMemoryObservationStore()
        store.append([index_obs(at(0), at(0))])  # type: ignore[arg-type]
        state = builder(store, subscribed=True).build(UNDERLYING, at(30), at(30))
        self.assertIs(state.quality.status, QualityStatus.UNRELIABLE)
        self.assertFalse(state.quality.is_usable_for_signals)
        self.assertIsNotNone(state.identity, "the state exists and is a valid record")


class TestStalenessInAssembledStates(unittest.TestCase):
    def test_a_stale_spot_flags_the_leg_and_escalates(self):
        store = populated_store(at(0))
        state = builder(store).build(UNDERLYING, at(1), at(1))  # 60s after observation
        self.assertTrue(state.spot.stale)
        self.assertIs(state.quality.status, QualityStatus.UNRELIABLE)

    def test_a_fresh_state_is_ok(self):
        store = populated_store(at(0))
        state = builder(store).build(UNDERLYING, at(0), at(0))
        self.assertFalse(state.spot.stale)
        self.assertIs(state.quality.status, QualityStatus.OK)

    def test_quote_and_greeks_staleness_are_judged_separately(self):
        """A quote 45s old is stale; greeks 45s old are not. One budget for both would
        flag greeks that are perfectly current for what they are."""
        store = InMemoryObservationStore()
        store.append(  # type: ignore[arg-type]
            [
                index_obs(at(0, 45), at(0, 45)),
                quote_obs(CALL_A0, at(0), at(0)),
                greeks_obs(CALL_A0, at(0), at(0)),
            ]
        )
        state = builder(store).build(UNDERLYING, at(0, 45), at(0, 45))
        leg = state.expiries[0].legs[0]
        self.assertTrue(leg.quote_stale, "45s > 30s quote budget")
        self.assertFalse(leg.greeks_stale, "45s < 60s greeks budget")
        self.assertFalse(leg.oi_stale, "45s < 60s OI budget")

    def test_a_stale_component_raises_an_issue_naming_the_budget(self):
        store = populated_store(at(0))
        state = builder(store).build(UNDERLYING, at(1), at(1))
        details = " ".join(i.detail for i in state.quality.issues)
        self.assertIn("spot", details)
        self.assertIn("budget", details)


class TestCoverageAndMissingData(unittest.TestCase):
    def test_a_missing_leg_is_counted_not_omitted_silently(self):
        store = InMemoryObservationStore()
        rows: list[object] = [index_obs(at(0), at(0))]
        # Only one of six legs observed.
        rows.append(quote_obs(CALL_A0, at(0), at(0)))
        store.append(rows)  # type: ignore[arg-type]
        state = builder(store).build(UNDERLYING, at(0), at(0))
        expiry = state.expiries[0]
        self.assertEqual(len(expiry.legs), 1)
        self.assertEqual(expiry.missing_leg_count, 5)
        self.assertAlmostEqual(expiry.coverage_ratio, 1 / 6)

    def test_absence_is_never_rendered_as_zero(self):
        """A strike with no OI observation and a strike with zero OI are different."""
        store = InMemoryObservationStore()
        store.append(  # type: ignore[arg-type]
            [index_obs(at(0), at(0)), greeks_obs(CALL_A0, at(0), at(0))]
        )
        state = builder(store).build(UNDERLYING, at(0), at(0))
        leg = state.expiries[0].legs[0]
        self.assertIsNone(leg.oi, "no quote observation means no OI, not zero OI")
        self.assertIsNone(leg.ltp)
        self.assertIsNotNone(leg.iv, "the greeks observation is present")

    def test_an_aggregate_over_no_observations_is_none_not_zero(self):
        store = InMemoryObservationStore()
        store.append(  # type: ignore[arg-type]
            [index_obs(at(0), at(0)), greeks_obs(CALL_A0, at(0), at(0))]
        )
        state = builder(store).build(UNDERLYING, at(0), at(0))
        aggregates = state.expiries[0].aggregates
        self.assertIsNone(aggregates.total_call_oi)
        self.assertIsNone(aggregates.total_put_oi)
        self.assertIsNone(aggregates.pcr, "an undefined ratio is not a value")

    def test_surfaces_distinguish_absent_from_observed(self):
        store = populated_store(at(0))
        state = builder(store).build(UNDERLYING, at(0), at(0))
        surface = state.expiries[0].surfaces.oi_by_strike
        self.assertEqual(set(surface), set(STRIKES))
        for call, put in surface.values():
            self.assertIsNotNone(call)
            self.assertIsNotNone(put)


class TestCoherence(unittest.TestCase):
    """REST snapshot = cross-sectional anchor; WS = incremental (`04` §4)."""

    class _Anchor:
        def __init__(self, expiry_id, observed_at, reference="chain-1"):
            self._expiry_id = expiry_id
            self._observed_at = observed_at
            self._reference = reference

        @property
        def reference(self):
            return self._reference

        @property
        def expiry_id(self):
            return self._expiry_id

        @property
        def observed_at(self):
            return self._observed_at

        @property
        def legs(self):
            return ()

    class _Anchors:
        def __init__(self, anchor=None):
            self._anchor = anchor

        def latest_anchor(self, underlying_id, expiry_id, bound):
            return self._anchor

    def _build(self, anchors=None, market_time=None):
        store = populated_store(at(0))
        base = builder(store)
        b = StateBuilder(
            store,
            base._universe,
            base._clock,
            anchors=anchors,
            build_context=base.build_context,
        )
        return b.build(UNDERLYING, market_time or at(0), market_time or at(0))

    def test_no_anchor_is_stream_only(self):
        """An arbitrary collection of WS messages is not a synchronized snapshot."""
        state = self._build()
        self.assertIs(state.coherence.mode, CoherenceMode.STREAM_ONLY)
        self.assertFalse(state.coherence.mode.is_cross_sectional)

    def test_a_recent_anchor_is_snapshot_anchored(self):
        anchors = self._Anchors(self._Anchor(EXPIRY_A, at(0)))
        state = self._build(anchors=anchors)
        self.assertIs(state.coherence.mode, CoherenceMode.SNAPSHOT_ANCHORED)
        self.assertTrue(state.coherence.mode.is_cross_sectional)
        self.assertEqual(state.coherence.chain_snapshot_ref, "chain-1")

    def test_an_anchor_beyond_the_budget_is_snapshot_stale(self):
        """Beyond ANCHOR_MAX_AGE the anchor is no longer a trustworthy reference."""
        old = at(0)
        market_time = old + DEFAULT_ANCHOR_MAX_AGE + timedelta(seconds=1)
        anchors = self._Anchors(self._Anchor(EXPIRY_A, old))
        state = self._build(anchors=anchors, market_time=market_time)
        self.assertIs(state.coherence.mode, CoherenceMode.SNAPSHOT_STALE)
        self.assertFalse(state.coherence.mode.is_cross_sectional)

    def test_a_gap_makes_the_state_recovering(self):
        """RECOVERING outranks anchor age: no consistency claim is defensible."""
        from oipulse.dataquality.issues import IssueSeverity, IssueType, QualityIssue

        store = populated_store(at(0))
        base = builder(store)
        b = StateBuilder(store, base._universe, base._clock)
        mode = b._coherence_mode(
            at(0),
            at(0),
            True,
            [
                QualityIssue(
                    type=IssueType.WEBSOCKET_GAP,
                    severity=IssueSeverity.DEGRADED,
                    detected_at=at(0),
                )
            ],
        )
        self.assertIs(mode, CoherenceMode.RECOVERING)

    def test_all_four_modes_exist(self):
        self.assertEqual(
            {m.value for m in CoherenceMode},
            {"snapshot_anchored", "stream_only", "snapshot_stale", "recovering"},
        )


class TestDeterminism(unittest.TestCase):
    """same observations + same K + same BuildContext = same MarketState."""

    def test_identical_inputs_produce_an_identical_state(self):
        a = builder(populated_store(at(0))).build(UNDERLYING, at(0), at(0))
        b = builder(populated_store(at(0))).build(UNDERLYING, at(0), at(0))
        self.assertEqual(a.content_digest(), b.content_digest())

    def test_input_ordering_does_not_change_the_state(self):
        """Iteration order must never leak into the result."""
        forward = InMemoryObservationStore()
        rows: list[object] = [index_obs(at(0), at(0))]
        for strike_index in range(len(STRIKES)):
            for is_call in (True, False):
                iid = leg_id(0, strike_index, is_call)
                rows.append(quote_obs(iid, at(0), at(0)))
                rows.append(greeks_obs(iid, at(0), at(0)))
        forward.append(rows)  # type: ignore[arg-type]

        reverse = InMemoryObservationStore()
        reverse.append(list(reversed(rows)))  # type: ignore[arg-type]

        a = builder(forward).build(UNDERLYING, at(0), at(0))
        b = builder(reverse).build(UNDERLYING, at(0), at(0))
        self.assertEqual(a.content_digest(), b.content_digest())

    def test_the_digest_excludes_assembly_wall_clock(self):
        """Two builds at different moments are the same state."""
        store = populated_store(at(0))
        a = builder(store, now_minute=1).build(UNDERLYING, at(0), at(0))
        b = builder(store, now_minute=9999).build(UNDERLYING, at(0), at(0))
        self.assertNotEqual(a.provenance.assembled_at, b.provenance.assembled_at)
        self.assertEqual(a.content_digest(), b.content_digest())

    def test_a_different_knowledge_horizon_changes_the_digest(self):
        store = InMemoryObservationStore()
        store.append(  # type: ignore[arg-type]
            [index_obs(at(0), at(0)), quote_obs(CALL_A0, at(0), at(20))]
        )
        b = builder(store)
        early = b.build(UNDERLYING, at(30), at(10))
        late = b.build(UNDERLYING, at(30), at(30))
        self.assertNotEqual(early.content_digest(), late.content_digest())

    def test_decimals_survive_the_digest_without_float_drift(self):
        store = populated_store(at(0))
        state = builder(store).build(UNDERLYING, at(0), at(0))
        comparable = state.as_comparable()
        self.assertIsInstance(comparable["spot"]["ltp"], str)
        self.assertEqual(comparable["spot"]["ltp"], str(Decimal("25000")))


class TestImmutability(unittest.TestCase):
    """A consumer who can mutate a state can make two holders disagree, silently."""

    def setUp(self):
        self.state = builder(populated_store(at(0))).build(UNDERLYING, at(0), at(0))

    def test_the_state_cannot_be_reassigned(self):
        with self.assertRaises(AttributeError):
            self.state.session_phase = "open"  # type: ignore[misc]

    def test_nested_values_cannot_be_reassigned(self):
        with self.assertRaises(AttributeError):
            self.state.spot.ltp = Decimal("1")  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            self.state.expiries[0].legs[0].oi = 0  # type: ignore[misc]

    def test_collections_are_not_mutable(self):
        self.assertIsInstance(self.state.expiries, tuple)
        self.assertIsInstance(self.state.expiries[0].legs, tuple)
        with self.assertRaises(TypeError):
            self.state.expiries[0].surfaces.oi_by_strike[Decimal("1")] = (1, 1)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
