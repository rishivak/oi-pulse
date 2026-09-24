"""Point-in-time correctness — `05-DATA_LIFECYCLE_PIT.md` §3, `04-MARKETSTATE.md` §1.

An observation participates only when `observed_at <= T` **and** `ingested_at <= K`.
`observed_at` alone never establishes participation; that substitution is the exact
look-ahead the bitemporal model exists to prevent, and it is invisible in results —
a backtest using it simply looks better than reality.

The worked example from the design and the brief:

    observation: observed_at = 11:40, ingested_at = 11:44
    MarketState(T=11:45, K=11:42)  -> must NOT participate
    MarketState(T=11:45, K=11:45)  -> may participate

`at(0)` here is 11:30 IST, so `at(10)` is 11:40 and `at(14)` is 11:44.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.marketdata.store.memory import InMemoryObservationStore
from tests.phase3._fixtures import (
    STRIKES,
    UNDERLYING,
    at,
    builder,
    greeks_obs,
    index_obs,
    leg_id,
    quote_obs,
)

CALL_A0 = leg_id(0, 0, True)


def _store_with_late_arrival() -> InMemoryObservationStore:
    """The 11:40/11:44 fixture: observed at 11:40, only known to us at 11:44."""
    store = InMemoryObservationStore()
    rows: list[object] = [index_obs(at(10), at(10))]
    for strike_index in range(len(STRIKES)):
        for is_call in (True, False):
            iid = leg_id(0, strike_index, is_call)
            # Every leg is observed at 11:40 but ingested at 11:44.
            rows.append(quote_obs(iid, at(10), at(14), ltp="120"))
            rows.append(greeks_obs(iid, at(10), at(14)))
    store.append(rows)  # type: ignore[arg-type]
    return store


class TestLateArrival(unittest.TestCase):
    """The canonical case. Nothing else in Phase 3 matters if this is wrong."""

    def setUp(self):
        self.store = _store_with_late_arrival()
        self.builder = builder(self.store)

    def test_a_late_observation_is_excluded_before_it_was_ingested(self):
        """T=11:45, K=11:42. observed_at <= T, but ingested_at > K."""
        state = self.builder.build(UNDERLYING, at(15), at(12))
        legs = state.expiries[0].legs if state.expiries else ()
        self.assertEqual(legs, (), "a not-yet-ingested observation must not participate")
        self.assertEqual(state.expiries[0].missing_leg_count, 6)

    def test_the_same_observation_is_included_once_ingested(self):
        """T=11:45, K=11:45. Both predicates satisfied."""
        state = self.builder.build(UNDERLYING, at(15), at(15))
        self.assertEqual(len(state.expiries[0].legs), 6)
        self.assertEqual(state.expiries[0].legs[0].ltp, Decimal("120"))

    def test_observed_at_alone_never_establishes_participation(self):
        """The two states differ only in K, and that difference must change the result."""
        excluded = self.builder.build(UNDERLYING, at(15), at(12))
        included = self.builder.build(UNDERLYING, at(15), at(15))
        self.assertNotEqual(excluded.content_digest(), included.content_digest())
        self.assertEqual(excluded.quality.coverage_ratio, 0.0)
        self.assertEqual(included.quality.coverage_ratio, 1.0)

    def test_spot_obeys_the_same_rule(self):
        """A spot known only later must not appear, and its absence is UNRELIABLE."""
        store = InMemoryObservationStore()
        store.append([index_obs(at(10), at(14))])  # type: ignore[arg-type]
        b = builder(store)
        self.assertIsNone(b.build(UNDERLYING, at(15), at(12)).spot.ltp)
        self.assertEqual(b.build(UNDERLYING, at(15), at(15)).spot.ltp, Decimal("25000"))


class TestFutureInformation(unittest.TestCase):
    """Nothing after `T` may ever appear, at any knowledge horizon."""

    def test_an_observation_after_market_time_never_participates(self):
        store = InMemoryObservationStore()
        store.append(  # type: ignore[arg-type]
            [
                index_obs(at(5), at(5), ltp="25000"),
                index_obs(at(20), at(20), ltp="25500"),
            ]
        )
        b = builder(store)
        # K far in the future does not license reading past T: market-truth widens the
        # knowledge axis, never the valid-time axis.
        state = b.build(UNDERLYING, at(10), at(60))
        self.assertEqual(state.spot.ltp, Decimal("25000"))

    def test_a_later_leg_price_does_not_leak_into_an_earlier_state(self):
        store = InMemoryObservationStore()
        store.append(  # type: ignore[arg-type]
            [
                index_obs(at(5), at(5)),
                quote_obs(CALL_A0, at(5), at(5), ltp="120"),
                quote_obs(CALL_A0, at(20), at(20), ltp="999"),
            ]
        )
        state = builder(store).build(UNDERLYING, at(10), at(60))
        leg = next(leg for leg in state.expiries[0].legs if int(leg.instrument_id) == CALL_A0)
        self.assertEqual(leg.ltp, Decimal("120"))


class TestCorrections(unittest.TestCase):
    """A correction is a new row, never a mutation (`05` §6).

    A query before the correction arrived must still return what we believed then.
    """

    def test_a_correction_is_invisible_before_its_ingestion(self):
        store = InMemoryObservationStore()
        original = quote_obs(CALL_A0, at(5), at(5), ltp="120")
        store.append([index_obs(at(5), at(5)), original])  # type: ignore[arg-type]
        corrected = quote_obs(CALL_A0, at(5), at(25), ltp="125")
        store.append([corrected])  # type: ignore[arg-type]

        b = builder(store)
        before = b.build(UNDERLYING, at(30), at(10))
        after = b.build(UNDERLYING, at(30), at(30))

        leg_before = next(
            leg for leg in before.expiries[0].legs if int(leg.instrument_id) == CALL_A0
        )
        leg_after = next(leg for leg in after.expiries[0].legs if int(leg.instrument_id) == CALL_A0)
        self.assertEqual(leg_before.ltp, Decimal("120"), "the original belief is preserved")
        self.assertEqual(leg_after.ltp, Decimal("125"), "the correction is visible after K")


class TestAgeIsRelativeToMarketTime(unittest.TestCase):
    """`age = T - observed_at`, never `now - observed_at`.

    A state reconstructed for last March must report the ages that held in March. Using
    wall-clock here would make every historical state instantly and absurdly stale, and
    would make the same reconstruction return different answers on different days.
    """

    def test_age_does_not_depend_on_the_wall_clock(self):
        store = _store_with_late_arrival()
        early_clock = builder(store, now_minute=20)
        late_clock = builder(store, now_minute=6000)
        a = early_clock.build(UNDERLYING, at(15), at(15))
        b = late_clock.build(UNDERLYING, at(15), at(15))
        self.assertEqual(a.spot.age, b.spot.age)
        self.assertEqual(a.content_digest(), b.content_digest())

    def test_age_is_measured_from_market_time(self):
        store = _store_with_late_arrival()
        state = builder(store).build(UNDERLYING, at(15), at(15))
        self.assertEqual(state.spot.age.total_seconds(), 300.0)


if __name__ == "__main__":
    unittest.main()
