"""Property and invariant tests — mandatory test 6 (`07-ANALYTICS.md` §7).

The design names three invariants explicitly: *PCR >= 0; exposure aggregation across
strikes equals the total; migration magnitude is bounded by the strike range.* Those
are here, plus the ones that are genuinely true of these formulas.

**Only invariants that actually hold are asserted.** A property test for something
merely usually true is worse than none: it fails on legitimate data and gets weakened
until it asserts nothing.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import oipulse.analytics  # noqa: F401
from oipulse.analytics.domains import gamma as gx
from oipulse.analytics.domains import greeks as gk
from oipulse.analytics.domains import positioning as pos
from oipulse.analytics.migration import (
    MigrationTracker,
    OIMigrationObservation,
)
from oipulse.analytics.values import MetricValue, Unavailable
from tests.phase4._fixtures import at, ctx, expiry_slice, leg, state

STRIKES = ("24800", "24900", "25000", "25100", "25200")


def book(call_oi: list[int], put_oi: list[int], gamma_v: str = "0.001") -> tuple:
    legs = []
    for i, strike in enumerate(STRIKES):
        legs.append(leg(strike, "call", oi=call_oi[i], gamma=gamma_v, instrument_id=700 + i * 2))
        legs.append(leg(strike, "put", oi=put_oi[i], gamma=gamma_v, instrument_id=701 + i * 2))
    return (expiry_slice(tuple(legs)),)


class TestPcrDomain(unittest.TestCase):
    """PCR >= 0 — named in `07` §7. Open interest cannot be negative, so neither can
    the ratio of two OI sums."""

    def test_pcr_is_never_negative_across_a_grid_of_books(self):
        for calls, puts in product([1, 500, 10_000], repeat=2):
            expiries = book([calls] * 5, [puts] * 5)
            outcome = pos.pcr(ctx(state(expiries=expiries), expiry_id=10))
            with self.subTest(calls=calls, puts=puts):
                self.assertIsInstance(outcome, MetricValue)
                assert isinstance(outcome, MetricValue)
                self.assertGreaterEqual(outcome.value, 0)

    def test_pcr_is_the_reciprocal_when_the_sides_swap(self):
        """A genuine symmetry: swapping call and put OI inverts the ratio."""
        a = pos.pcr(ctx(state(expiries=book([1000] * 5, [2000] * 5)), expiry_id=10))
        b = pos.pcr(ctx(state(expiries=book([2000] * 5, [1000] * 5)), expiry_id=10))
        assert isinstance(a, MetricValue) and isinstance(b, MetricValue)
        self.assertAlmostEqual(float(a.value) * float(b.value), 1.0, places=9)


class TestConcentrationBounds(unittest.TestCase):
    """A Herfindahl index over n buckets lies in [1/n, 1]."""

    def test_concentration_is_bounded(self):
        for spread in ([1000] * 5, [5000, 1, 1, 1, 1], [1, 1, 5000, 1, 1]):
            expiries = book(spread, spread)
            outcome = pos.oi_concentration(ctx(state(expiries=expiries), expiry_id=10))
            assert isinstance(outcome, MetricValue)
            with self.subTest(spread=spread):
                self.assertGreaterEqual(outcome.value, Decimal(1) / Decimal(len(STRIKES)))
                self.assertLessEqual(outcome.value, Decimal(1))


class TestExposureAggregation(unittest.TestCase):
    """Exposure aggregation across strikes equals the total — named in `07` §7."""

    def test_the_expiry_total_equals_the_sum_of_per_strike_contributions(self):
        expiries = book([1000, 2000, 3000, 2000, 1000], [1500] * 5)
        scoped = ctx(state(expiries=expiries), expiry_id=10, lot_size=75)
        total = gk.gamma_exposure(scoped)
        assert isinstance(total, MetricValue)

        by_hand = sum(
            (legv.gamma or Decimal(0)) * Decimal(legv.oi or 0) * Decimal(75)
            for legv in expiries[0].legs
        )
        self.assertEqual(total.value, by_hand)

    def test_gex_total_equals_the_sum_of_gex_by_strike(self):
        expiries = book([1000, 2000, 3000, 2000, 1000], [1500] * 5)
        scoped = ctx(state(expiries=expiries), expiry_id=10, lot_size=75)
        profile = gx.gex_by_strike(scoped)
        total = gx.gex_total(scoped)
        assert isinstance(profile, MetricValue) and isinstance(total, MetricValue)
        assert isinstance(profile.value, tuple)
        self.assertEqual(sum(amount for _, amount in profile.value), total.value)

    def test_gex_profile_ends_at_the_expiry_total(self):
        """The last cumulative point is by construction the sum of every strike."""
        expiries = book([1000, 2000, 3000, 2000, 1000], [1500] * 5)
        scoped = ctx(state(expiries=expiries), expiry_id=10, lot_size=75)
        profile = gx.gex_profile(scoped)
        by_expiry = gx.gex_by_expiry(scoped)
        assert isinstance(profile, MetricValue) and isinstance(by_expiry, MetricValue)
        assert isinstance(profile.value, tuple)
        self.assertEqual(profile.value[-1][1], by_expiry.value)

    def test_exposure_scales_linearly_with_lot_size(self):
        """A unit-consistency property: doubling the lot doubles the exposure."""
        expiries = book([1000] * 5, [1000] * 5)
        one = gk.gamma_exposure(ctx(state(expiries=expiries), expiry_id=10, lot_size=50))
        two = gk.gamma_exposure(ctx(state(expiries=expiries), expiry_id=10, lot_size=100))
        assert isinstance(one, MetricValue) and isinstance(two, MetricValue)
        self.assertEqual(two.value, one.value * 2)


class TestGexConventionSymmetry(unittest.TestCase):
    """The two registered conventions differ exactly by the sign on puts."""

    def test_a_call_only_book_is_identical_under_both_conventions(self):
        legs = tuple(leg(s, "call", oi=1000, instrument_id=800 + i) for i, s in enumerate(STRIKES))
        s = state(expiries=(expiry_slice(legs),))
        short = gx.gex_by_expiry(
            ctx(s, expiry_id=10, lot_size=75, convention="dealer_short_calls_long_puts")
        )
        long_all = gx.gex_by_expiry(ctx(s, expiry_id=10, lot_size=75, convention="dealer_long_all"))
        assert isinstance(short, MetricValue) and isinstance(long_all, MetricValue)
        self.assertEqual(short.value, long_all.value)

    def test_a_put_only_book_is_negated_between_the_conventions(self):
        legs = tuple(leg(s, "put", oi=1000, instrument_id=810 + i) for i, s in enumerate(STRIKES))
        s = state(expiries=(expiry_slice(legs),))
        short = gx.gex_by_expiry(
            ctx(s, expiry_id=10, lot_size=75, convention="dealer_short_calls_long_puts")
        )
        long_all = gx.gex_by_expiry(ctx(s, expiry_id=10, lot_size=75, convention="dealer_long_all"))
        assert isinstance(short, MetricValue) and isinstance(long_all, MetricValue)
        self.assertEqual(short.value, -long_all.value)


class TestMigrationBounds(unittest.TestCase):
    """Migration magnitude is bounded by the strike range — named in `07` §7."""

    def test_displacement_never_exceeds_the_chain_width(self):
        low, high = Decimal(STRIKES[0]), Decimal(STRIKES[-1])
        for shift in ([1200, 900, 1000, 1100, 800], [500, 500, 500, 2000, 2500]):
            then = state(market_time=at(-20), expiries=book([1000] * 5, [1000] * 5))
            now = state(market_time=at(0), expiries=book([1000] * 5, shift))
            outcome = pos.put_oi_migration(ctx(now, history=(then,), expiry_id=10))
            if isinstance(outcome, Unavailable):
                continue
            with self.subTest(shift=shift):
                self.assertLessEqual(abs(outcome.value), high - low)

    def test_the_tracked_entity_magnitude_is_bounded_by_the_range(self):
        from oipulse.analytics.migration import MigrationStatus

        tracker = MigrationTracker(threshold=Decimal("50"), confirm_after=3)
        base = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)
        tracker.observe(
            OIMigrationObservation(
                base, 10, "put", Decimal("24800"), Decimal("25200"), Decimal("120")
            )
        )
        entity = tracker.live()[0]
        self.assertTrue(entity.spans(Decimal("24800"), Decimal("25200")))
        self.assertIs(entity.status, MigrationStatus.FORMING)


class TestMigrationLifecycle(unittest.TestCase):
    """`07` §5: the worked case is ONE entity, not three observations."""

    def setUp(self):
        self.base = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)
        self.tracker = MigrationTracker(threshold=Decimal("50"), confirm_after=3)

    def _observe(self, minutes: int, destination: str, magnitude: str):
        return self.tracker.observe(
            OIMigrationObservation(
                self.base + timedelta(minutes=minutes),
                10,
                "put",
                Decimal("25000"),
                Decimal(destination),
                Decimal(magnitude),
            )
        )

    def test_the_worked_case_is_a_single_advancing_entity(self):
        """25,000 PE -> 25,200 PE -> 25,300 PE is one migration with a growing duration."""
        self._observe(0, "25200", "120")
        self._observe(5, "25200", "150")
        self._observe(10, "25300", "180")

        self.assertEqual(len(self.tracker.live()), 1, "three observations, one entity")
        entity = self.tracker.live()[0]
        self.assertEqual(entity.origin_strike, Decimal("25000"), "the origin is never rewritten")
        self.assertEqual(entity.destination_strike, Decimal("25300"))
        self.assertEqual(entity.windows, 3)
        self.assertEqual(entity.duration, timedelta(minutes=10))

    def test_it_confirms_only_after_the_declared_number_of_windows(self):
        from oipulse.analytics.migration import MigrationStatus

        self.assertIs(self._observe(0, "25200", "120").status, MigrationStatus.FORMING)
        self.assertIs(self._observe(5, "25200", "150").status, MigrationStatus.FORMING)
        self.assertIs(self._observe(10, "25300", "180").status, MigrationStatus.CONFIRMED)

    def test_decay_fades_the_entity_rather_than_deleting_it(self):
        """A migration that stopped is a fact research needs."""
        from oipulse.analytics.migration import MigrationStatus

        self._observe(0, "25200", "120")
        faded = self._observe(5, "25200", "10")
        self.assertIs(faded.status, MigrationStatus.FADED)
        self.assertEqual(len(self.tracker.live()), 0)
        self.assertEqual(len(self.tracker.faded()), 1)
        self.assertEqual(self.tracker.faded()[0].duration, timedelta(minutes=5))

    def test_the_tracker_is_deterministic_over_a_replayed_sequence(self):
        """Replay compatibility: the same observations always yield the same entities."""
        sequence = [(0, "25200", "120"), (5, "25200", "150"), (10, "25300", "180")]
        first = MigrationTracker(threshold=Decimal("50"), confirm_after=3)
        second = MigrationTracker(threshold=Decimal("50"), confirm_after=3)
        for tracker in (first, second):
            for minutes, destination, magnitude in sequence:
                tracker.observe(
                    OIMigrationObservation(
                        self.base + timedelta(minutes=minutes),
                        10,
                        "put",
                        Decimal("25000"),
                        Decimal(destination),
                        Decimal(magnitude),
                    )
                )
        self.assertEqual(first.all(), second.all())

    def test_opposite_directions_are_separate_entities(self):
        """An up-migration and a down-migration are different facts about the book."""
        self._observe(0, "25200", "120")
        self.tracker.observe(
            OIMigrationObservation(
                self.base + timedelta(minutes=5),
                10,
                "put",
                Decimal("25000"),
                Decimal("24800"),
                Decimal("-120"),
            )
        )
        self.assertEqual(len(self.tracker.live()), 2)


if __name__ == "__main__":
    unittest.main()
