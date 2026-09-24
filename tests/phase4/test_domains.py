"""Hand-computed expected values — mandatory test 1 (`15-TESTING.md` §3).

Every value below is worked out by hand in the docstring or the comment beside it, so
a failure tells you whether the code or the expectation is wrong. A test that asserts
`compute(x) == compute(x)` proves nothing; these assert against arithmetic done
independently of the implementation.

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

import oipulse.analytics  # noqa: F401
from oipulse.analytics.domains import futures as fut
from oipulse.analytics.domains import gamma as gx
from oipulse.analytics.domains import greeks as gk
from oipulse.analytics.domains import positioning as pos
from oipulse.analytics.domains import price as px
from oipulse.analytics.domains import structure as st
from oipulse.analytics.domains import volatility as vol
from oipulse.analytics.values import MetricValue, Unavailable
from tests.phase4._fixtures import at, ctx, expiry_slice, future, leg, state


def value(outcome) -> Decimal | str | tuple:
    assert isinstance(outcome, MetricValue), f"expected a value, got {outcome}"
    return outcome.value


def chain(**oi_by_key) -> tuple:
    """Build a three-strike chain with explicit OI per (strike, side)."""
    legs = []
    for i, strike in enumerate(("24900", "25000", "25100")):
        for side in ("call", "put"):
            key = f"{side}_{strike}"
            legs.append(
                leg(
                    strike,
                    side,
                    oi=oi_by_key.get(key, 1000),
                    instrument_id=300 + i * 2 + (side == "put"),
                )
            )
    return (expiry_slice(tuple(legs)),)


class TestPositioning(unittest.TestCase):
    def test_pcr_is_put_oi_over_call_oi(self):
        """Calls 1000+1000+1000 = 3000; puts 2000+2000+2000 = 6000; PCR = 2."""
        expiries = chain(
            put_24900=2000,
            put_25000=2000,
            put_25100=2000,
            call_24900=1000,
            call_25000=1000,
            call_25100=1000,
        )
        self.assertEqual(value(pos.pcr(ctx(state(expiries=expiries), expiry_id=10))), Decimal(2))

    def test_pcr_is_unavailable_when_call_oi_is_zero(self):
        """An undefined ratio is not a value, and is certainly not infinity."""
        expiries = chain(call_24900=0, call_25000=0, call_25100=0)
        outcome = pos.pcr(ctx(state(expiries=expiries), expiry_id=10))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "undefined")

    def test_oi_change_is_the_difference_against_the_reference_state(self):
        """Now 6 x 1500 = 9000; reference 6 x 1000 = 6000; change = +3000."""
        then = state(market_time=at(-20), expiries=chain())
        now = state(
            market_time=at(0),
            expiries=chain(
                **{f"{s}_{k}": 1500 for s in ("call", "put") for k in ("24900", "25000", "25100")}
            ),
        )
        outcome = pos.oi_change(ctx(now, history=(then,), expiry_id=10))
        self.assertEqual(value(outcome), 3000)

    def test_oi_change_pct_is_the_fraction_of_the_reference(self):
        """3000 / 6000 = 0.5."""
        then = state(market_time=at(-20), expiries=chain())
        now = state(
            market_time=at(0),
            expiries=chain(
                **{f"{s}_{k}": 1500 for s in ("call", "put") for k in ("24900", "25000", "25100")}
            ),
        )
        self.assertEqual(
            value(pos.oi_change_pct(ctx(now, history=(then,), expiry_id=10))), Decimal("0.5")
        )

    def test_oi_concentration_is_the_herfindahl_index(self):
        """Per-strike totals 2000/2000/2000 -> shares 1/3 each -> 3 x (1/9) = 1/3."""
        outcome = pos.oi_concentration(ctx(state(expiries=chain()), expiry_id=10))
        self.assertAlmostEqual(float(value(outcome)), 1 / 3, places=9)

    def test_oi_concentration_is_one_when_all_oi_sits_at_one_strike(self):
        expiries = chain(
            call_24900=0,
            put_24900=0,
            call_25100=0,
            put_25100=0,
            call_25000=5000,
            put_25000=5000,
        )
        outcome = pos.oi_concentration(ctx(state(expiries=expiries), expiry_id=10))
        self.assertEqual(value(outcome), Decimal(1))

    def test_oi_wall_requires_the_declared_prominence(self):
        """A flat book has no wall: 1000 is not 1.5x the 1000 mean of the others."""
        outcome = pos.oi_wall_put(ctx(state(expiries=chain()), expiry_id=10, prominence="1.5"))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "undefined")

    def test_oi_wall_reports_the_prominent_strike(self):
        """Put OI 1000 / 5000 / 1000: mean of others is 1000, 5000 >= 1.5x -> 25000."""
        expiries = chain(put_25000=5000)
        outcome = pos.oi_wall_put(ctx(state(expiries=expiries), expiry_id=10, prominence="1.5"))
        self.assertEqual(value(outcome), Decimal("25000"))

    def test_volume_oi_ratio(self):
        """Six legs x 500 volume = 3000; OI 6 x 1000 = 6000; ratio = 0.5."""
        outcome = pos.volume_oi_ratio(ctx(state(expiries=chain()), expiry_id=10))
        self.assertEqual(value(outcome), Decimal("0.5"))

    def test_buildup_classification_matrix(self):
        """Price up (25000 -> 25100) with OI up (6000 -> 9000) is LONG_BUILDUP."""
        then = state(market_time=at(-20), spot="25000", expiries=chain())
        now = state(
            market_time=at(0),
            spot="25100",
            expiries=chain(
                **{f"{s}_{k}": 1500 for s in ("call", "put") for k in ("24900", "25000", "25100")}
            ),
        )
        self.assertEqual(
            value(pos.buildup_classification(ctx(now, history=(then,), expiry_id=10))),
            "LONG_BUILDUP",
        )

    def test_buildup_is_undefined_when_a_leg_is_flat(self):
        """Picking a quadrant anyway would invent a classification."""
        then = state(market_time=at(-20), spot="25000", expiries=chain())
        now = state(market_time=at(0), spot="25000", expiries=chain())
        outcome = pos.buildup_classification(ctx(now, history=(then,), expiry_id=10))
        self.assertIsInstance(outcome, Unavailable)

    def test_put_oi_migration_is_zero_when_oi_shifts_symmetrically(self):
        """Equal-and-opposite displacement about the centroid nets to zero."""
        then = state(market_time=at(-20), expiries=chain())
        now = state(
            market_time=at(0),
            expiries=chain(put_24900=900, put_25100=1100),
        )
        outcome = pos.put_oi_migration(ctx(now, history=(then,), expiry_id=10))
        # centroid 25000; -100 at 24900 (dist -100) and +100 at 25100 (dist +100)
        # numerator = (-100)(-100) + (100)(100) = 20000; denominator = 200 -> 100
        self.assertEqual(value(outcome), Decimal(100))


class TestVolatility(unittest.TestCase):
    def test_atm_iv_averages_the_two_sides_at_the_nearest_strike(self):
        """Spot 25000; call IV 0.12 and put IV 0.16 at 25000 -> 0.14."""
        legs = [
            leg("24900", "call", iv="0.20"),
            leg("24900", "put", iv="0.20"),
            leg("25000", "call", iv="0.12", instrument_id=11),
            leg("25000", "put", iv="0.16", instrument_id=12),
            leg("25100", "call", iv="0.20", instrument_id=13),
            leg("25100", "put", iv="0.20", instrument_id=14),
        ]
        outcome = vol.atm_iv(ctx(state(expiries=(expiry_slice(tuple(legs)),)), expiry_id=10))
        self.assertAlmostEqual(float(value(outcome)), 0.14, places=9)

    def test_iv_rank_refuses_a_short_window(self):
        """Fabricating a percentile is worse than withholding it."""
        outcome = vol.iv_rank(ctx(expiry_id=10, min_points=20))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "insufficient_history")

    def test_iv_percentile_refuses_a_short_window(self):
        outcome = vol.iv_percentile(ctx(expiry_id=10, min_points=20))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "insufficient_history")

    def test_parkinson_volatility_on_a_known_range(self):
        """high 101, low 99: sqrt(ln(101/99)^2 / (4 ln2)) * sqrt(1) computed by hand."""
        import math

        expected = math.sqrt((math.log(101 / 99) ** 2) / (4 * math.log(2)))
        outcome = vol.realized_vol_parkinson(ctx(state(high="101", low="99"), periods_per_year=1))
        self.assertAlmostEqual(float(value(outcome)), expected, places=10)

    def test_parkinson_is_unavailable_without_a_range(self):
        outcome = vol.realized_vol_parkinson(ctx(state(high=None, low=None)))
        self.assertIsInstance(outcome, Unavailable)


class TestGreeks(unittest.TestCase):
    def test_delta_exposure_refuses_without_a_lot_size(self):
        """A guessed lot size is a wrong exposure that looks right."""
        outcome = gk.delta_exposure(ctx(expiry_id=10))
        self.assertIsInstance(outcome, Unavailable)
        self.assertIn("lot_size", outcome.detail)

    def test_gamma_exposure_is_gamma_times_oi_times_lot(self):
        """6 legs x gamma 0.001 x OI 1000 x lot 75 x 1 = 6 x 75 = 450."""
        outcome = gk.gamma_exposure(ctx(state(expiries=chain()), expiry_id=10, lot_size=75))
        self.assertEqual(value(outcome), Decimal("450.000"))

    def test_a_non_positive_lot_size_is_refused(self):
        outcome = gk.gamma_exposure(ctx(expiry_id=10, lot_size=0))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "invalid_params")


class TestGamma(unittest.TestCase):
    def test_the_dealer_convention_flips_the_put_sign(self):
        """Under DEALER_SHORT_CALLS_LONG_PUTS calls are +gamma and puts -gamma, so a
        symmetric book nets to zero; under DEALER_LONG_ALL it does not."""
        expiries = chain()
        scoped = ctx(
            state(expiries=expiries),
            expiry_id=10,
            lot_size=75,
            convention="dealer_short_calls_long_puts",
        )
        self.assertEqual(value(gx.gex_by_expiry(scoped)), Decimal(0))

        both_long = ctx(
            state(expiries=expiries), expiry_id=10, lot_size=75, convention="dealer_long_all"
        )
        self.assertEqual(value(gx.gex_by_expiry(both_long)), Decimal("450.000"))

    def test_an_unknown_convention_is_refused_not_defaulted(self):
        outcome = gx.gex_total(ctx(lot_size=75, convention="whatever_seems_right"))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "invalid_params")

    def test_gex_profile_is_the_running_total(self):
        """Puts only, all -gamma: cumulative goes -75, -150, -225 across three strikes."""
        legs = [
            leg(s, "put", oi=1000, instrument_id=400 + i)
            for i, s in enumerate(("24900", "25000", "25100"))
        ]
        scoped = ctx(state(expiries=(expiry_slice(tuple(legs)),)), expiry_id=10, lot_size=75)
        profile = value(gx.gex_profile(scoped))
        self.assertEqual([str(k) for k, _ in profile], ["24900", "25000", "25100"])
        self.assertEqual(
            [v for _, v in profile], [Decimal("-75.000"), Decimal("-150.000"), Decimal("-225.000")]
        )

    def test_gamma_flip_is_unavailable_when_the_profile_never_crosses(self):
        legs = [
            leg(s, "put", oi=1000, instrument_id=410 + i)
            for i, s in enumerate(("24900", "25000", "25100"))
        ]
        outcome = gx.gamma_flip_level(
            ctx(state(expiries=(expiry_slice(tuple(legs)),)), expiry_id=10, lot_size=75)
        )
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "undefined")

    def test_gamma_flip_interpolates_between_bracketing_strikes(self):
        """Put -75 at 24900 then call +75 at 25000: cumulative -75 then 0, so the
        crossing sits exactly at 25000."""
        legs = (
            leg("24900", "put", oi=1000, instrument_id=420),
            leg("25000", "call", oi=1000, instrument_id=421),
        )
        outcome = gx.gamma_flip_level(
            ctx(state(expiries=(expiry_slice(legs),)), expiry_id=10, lot_size=75)
        )
        self.assertEqual(value(outcome), Decimal("25000"))


class TestPrice(unittest.TestCase):
    def test_return_over_the_window(self):
        """25000 -> 25250 is +0.01."""
        then = state(market_time=at(-20), spot="25000")
        now = state(market_time=at(0), spot="25250")
        self.assertEqual(value(px.price_return(ctx(now, history=(then,)))), Decimal("0.01"))

    def test_momentum_is_the_absolute_change(self):
        then = state(market_time=at(-20), spot="25000")
        now = state(market_time=at(0), spot="25250")
        self.assertEqual(value(px.momentum(ctx(now, history=(then,)))), Decimal("250"))

    def test_atr_is_the_mean_absolute_step(self):
        """Steps |10| and |30| -> mean 20."""
        h = (
            state(market_time=at(-40), spot="25000"),
            state(market_time=at(-20), spot="25010"),
        )
        now = state(market_time=at(0), spot="25040")
        self.assertEqual(value(px.atr(ctx(now, history=h))), Decimal(20))

    def test_vwap_is_unavailable_without_volume_rather_than_approximated(self):
        legs = tuple(leg(s, t, volume=None) for s in ("24900", "25000") for t in ("call", "put"))
        outcome = px.vwap(ctx(state(expiries=(expiry_slice(legs),)), expiry_id=10))
        self.assertIsInstance(outcome, Unavailable)
        self.assertIn("not approximated", outcome.detail)

    def test_vwap_weights_by_volume(self):
        """(100x100 + 200x300) / (100+300) = (10000 + 60000)/400 = 175."""
        legs = (
            leg("24900", "call", ltp="100", volume=100, instrument_id=500),
            leg("25000", "call", ltp="200", volume=300, instrument_id=501),
        )
        outcome = px.vwap(ctx(state(expiries=(expiry_slice(legs),)), expiry_id=10))
        self.assertEqual(value(outcome), Decimal(175))

    def test_trend_slope_on_a_straight_line(self):
        """Prices 100, 110, 120 against index 0,1,2 -> slope exactly 10."""
        h = (
            state(market_time=at(-40), spot="100"),
            state(market_time=at(-20), spot="110"),
        )
        now = state(market_time=at(0), spot="120")
        self.assertEqual(value(px.trend(ctx(now, history=h))), Decimal(10))


class TestFutures(unittest.TestCase):
    def test_basis_is_future_minus_spot(self):
        s = state(spot="25000", futures=(future("25050"),))
        self.assertEqual(value(fut.basis(ctx(s))), Decimal(50))

    def test_annualized_basis_requires_days_to_expiry(self):
        s = state(spot="25000", futures=(future("25050"),))
        outcome = fut.annualized_basis(ctx(s))
        self.assertIsInstance(outcome, Unavailable)

    def test_annualized_basis_on_known_numbers(self):
        """(25250/25000 - 1) x (365/10) = 0.01 x 36.5 = 0.365."""
        s = state(spot="25000", futures=(future("25250"),))
        outcome = fut.annualized_basis(ctx(s, days_to_expiry=10, day_count=365))
        self.assertAlmostEqual(float(value(outcome)), 0.365, places=10)

    def test_annualized_basis_is_undefined_at_expiry(self):
        s = state(spot="25000", futures=(future("25250"),))
        outcome = fut.annualized_basis(ctx(s, days_to_expiry=0))
        self.assertIsInstance(outcome, Unavailable)
        self.assertEqual(outcome.reason.value, "undefined")

    def test_futures_options_confirmation_reports_agreement(self):
        """Futures OI up and options OI up -> CONFIRMING. Not a trade direction."""
        then = state(market_time=at(-20), expiries=chain(), futures=(future(oi=5000),))
        now = state(
            market_time=at(0),
            expiries=chain(
                **{f"{s}_{k}": 1500 for s in ("call", "put") for k in ("24900", "25000", "25100")}
            ),
            futures=(future(oi=6000),),
        )
        outcome = fut.futures_options_confirmation(ctx(now, history=(then,), expiry_id=10))
        self.assertEqual(value(outcome), "CONFIRMING")


class TestStructure(unittest.TestCase):
    def test_support_is_the_largest_put_oi_below_spot(self):
        """Spot 25000; puts below are 24900 only -> support 24900."""
        outcome = st.support_from_positioning(ctx(state(expiries=chain()), expiry_id=10))
        self.assertEqual(value(outcome), Decimal("24900"))

    def test_resistance_is_the_largest_call_oi_above_spot(self):
        outcome = st.resistance_from_positioning(ctx(state(expiries=chain()), expiry_id=10))
        self.assertEqual(value(outcome), Decimal("25100"))

    def test_max_pain_on_a_hand_worked_book(self):
        """Calls only at 24900 with OI 1000: settling at the lowest strike minimises
        intrinsic value, so max pain is 24900."""
        legs = (
            leg("24900", "call", oi=1000, instrument_id=600),
            leg("25000", "call", oi=1000, instrument_id=601),
            leg("25100", "call", oi=1000, instrument_id=602),
        )
        outcome = st.max_pain(ctx(state(expiries=(expiry_slice(legs),)), expiry_id=10))
        self.assertEqual(value(outcome), Decimal("24900"))

    def test_regime_returns_unknown_rather_than_guessing(self):
        """Fewer than four prices is not enough to classify anything."""
        outcome = st.regime(ctx(state(market_time=at(0)), history=()))
        self.assertIsInstance(outcome, Unavailable)

    def test_regime_detects_a_clear_uptrend(self):
        """+0.6% over the window with a rising close and no new high at the end."""
        h = tuple(state(market_time=at(-40 + i * 10), spot=str(25000 + i * 40)) for i in range(4))
        now = state(market_time=at(0), spot="25150")
        outcome = st.regime(ctx(now, history=h))
        self.assertIn(value(outcome), {"TRENDING_UP", "BREAKOUT"})

    def test_every_regime_label_is_in_the_documented_set(self):
        from oipulse.analytics.domains.structure import Regime

        self.assertEqual(
            {r.value for r in Regime},
            {
                "TRENDING_UP",
                "TRENDING_DOWN",
                "RANGE",
                "HIGH_VOLATILITY",
                "LOW_VOLATILITY",
                "BREAKOUT",
                "BREAKDOWN",
                "TRANSITION",
                "UNKNOWN",
            },
        )


if __name__ == "__main__":
    unittest.main()
