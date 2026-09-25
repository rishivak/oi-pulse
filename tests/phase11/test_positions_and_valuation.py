"""Position identity, the fold, contract economics and point-in-time valuation.

Phase 11 brief §5, §6, §7, §9, §10, §21, §23. `11-TRADING.md` §8:

> Positions are derived from **fill events**, not maintained as a running total that
> can drift. A position is a fold over its fills.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from oipulse.backtest.intents import Side
from oipulse.marketstate.staleness import QualityStatus
from oipulse.trading.portfolio import (
    UNITS_PER_QUANTITY,
    CostBasisMethod,
    MultiplierSource,
    PositionKey,
    PositionStatus,
    UnvaluedReason,
    ValuationRefused,
    resolve_economics,
    value_positions,
)
from tests.phase11 import _fixtures as fx
from tests.phase11._fixtures import at


class TestPositionIdentity(unittest.TestCase):
    """Brief §5: account, portfolio, instrument — and deliberately nothing else."""

    def test_the_key_is_account_portfolio_instrument(self) -> None:
        key = fx.key()
        self.assertEqual(key.account_id, fx.ACCOUNT_ID)
        self.assertEqual(key.portfolio_id, fx.PORTFOLIO_ID)
        self.assertEqual(key.instrument_id, fx.TARGET)

    def test_the_position_id_is_deterministic(self) -> None:
        self.assertEqual(fx.key().position_id, fx.key().position_id)

    def test_each_dimension_changes_the_identity(self) -> None:
        base = fx.key()
        variants = [
            PositionKey("other", fx.PORTFOLIO_ID, fx.TARGET),
            PositionKey(fx.ACCOUNT_ID, "other", fx.TARGET),
            PositionKey(fx.ACCOUNT_ID, fx.PORTFOLIO_ID, fx.OTHER),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(base.position_id, variant.position_id)

    def test_side_is_not_part_of_the_identity(self) -> None:
        """A position is signed; long and short in one instrument are one position.

        Keying on side would let an account hold +10 and -10 at once and be flat in
        neither.
        """
        buys = fx.book([fx.fill(side=Side.BUY, quantity=30, price="100")])
        both = fx.book(
            [
                fx.fill(side=Side.BUY, quantity=30, price="100"),
                fx.fill(side=Side.SELL, quantity=10, price="110", tag="s"),
            ]
        )
        self.assertEqual(len(buys.positions()), 1)
        self.assertEqual(len(both.positions()), 1)
        self.assertEqual(both.positions()[0].quantity, 20)

    def test_an_account_and_portfolio_are_required(self) -> None:
        for kwargs in ({"account_id": ""}, {"portfolio_id": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PositionKey(
                    account_id=str(kwargs.get("account_id", "a")),
                    portfolio_id=str(kwargs.get("portfolio_id", "p")),
                    instrument_id=1,
                )

    def test_order_fill_position_and_portfolio_are_not_conflated(self) -> None:
        """Brief §5. Four identities, four distinct values."""
        from oipulse.backtest.ledger import fill_key

        one = fx.fill()
        book = fx.book([one])
        position = book.positions()[0]
        identities = {
            "fill": fill_key(one),
            "intent": one.intent_id,
            "position": position.key.position_id,
            "portfolio": position.key.portfolio_id,
        }
        self.assertEqual(len(set(identities.values())), len(identities))


class TestPositionFold(unittest.TestCase):
    """Brief §6: deterministic accounting from canonical fills."""

    def test_a_fill_creates_a_position(self) -> None:
        position = fx.book([fx.fill(quantity=50, price="100")]).positions()[0]
        self.assertEqual(position.quantity, 50)
        self.assertEqual(position.average_price, Decimal(100))
        self.assertIs(position.status, PositionStatus.OPEN)

    def test_a_duplicate_fill_does_not_double_count(self) -> None:
        one = fx.fill()
        book = fx.book([one])
        self.assertFalse(
            book.apply(one, account_id=fx.ACCOUNT_ID, portfolio_id=fx.PORTFOLIO_ID, at=at(1))
        )
        self.assertEqual(book.positions()[0].quantity, 50)
        self.assertEqual(book.duplicate_fills_ignored, 1)

    def test_the_cost_basis_is_weighted_average(self) -> None:
        """Declared, not inferred: brief §7 forbids a silent choice."""
        book = fx.book(
            [
                fx.fill(quantity=50, price="100", tag="a"),
                fx.fill(quantity=50, price="120", tag="b"),
            ]
        )
        position = book.positions()[0]
        self.assertEqual(position.average_price, Decimal(110))
        self.assertEqual(book.cost_basis_method, CostBasisMethod.WEIGHTED_AVERAGE)
        self.assertEqual(position.as_dict()["cost_basis_method"], "WEIGHTED_AVERAGE")

    def test_reducing_realises_pnl_against_the_basis(self) -> None:
        book = fx.book(
            [
                fx.fill(quantity=100, price="110", tag="a"),
                fx.fill(side=Side.SELL, quantity=40, price="130", tag="b"),
            ]
        )
        self.assertEqual(book.realized_pnl(fx.ACCOUNT_ID, fx.PORTFOLIO_ID), Decimal(800))
        position = book.positions()[0]
        self.assertEqual(position.quantity, 60)
        self.assertEqual(position.average_price, Decimal(110), "the basis is untouched")

    def test_a_closed_position_is_retained_not_deleted(self) -> None:
        """Its realised P&L is real and belongs in the day's attribution."""
        book = fx.book(
            [
                fx.fill(quantity=50, price="100", tag="a"),
                fx.fill(side=Side.SELL, quantity=50, price="110", tag="b"),
            ]
        )
        self.assertEqual(len(book.positions()), 0, "closed positions are hidden by default")
        closed = book.positions(include_closed=True)
        self.assertEqual(len(closed), 1)
        self.assertIs(closed[0].status, PositionStatus.CLOSED)

    def test_reordered_fills_produce_an_identical_book(self) -> None:
        """Brief §23. Average-cost arithmetic is order-sensitive, so the order is
        imposed by `apply_all` rather than left to the caller."""
        fills = [
            fx.fill(quantity=50, price="100", tag="a"),
            fx.fill(quantity=30, price="120", tag="b"),
            fx.fill(side=Side.SELL, quantity=20, price="130", tag="c"),
        ]
        forward = fx.book.__wrapped__ if hasattr(fx.book, "__wrapped__") else None
        del forward

        from oipulse.trading.portfolio import PositionBook

        a, b = PositionBook(), PositionBook()
        a.apply_all(fills, account_id="acc", portfolio_id="pf", at=at(1))
        b.apply_all(list(reversed(fills)), account_id="acc", portfolio_id="pf", at=at(1))
        self.assertEqual([p.as_dict() for p in a.positions()], [p.as_dict() for p in b.positions()])

    def test_positions_iterate_in_a_deterministic_order(self) -> None:
        book = fx.book(
            [
                fx.fill(instrument_id=fx.OTHER, quantity=10, price="100", tag="o"),
                fx.fill(instrument_id=fx.TARGET, quantity=10, price="100", tag="t"),
            ]
        )
        ids = [p.key.instrument_id for p in book.positions()]
        self.assertEqual(ids, sorted(ids))

    def test_fees_are_accumulated_per_position(self) -> None:
        book = fx.book([fx.fill(fees="12.50")])
        self.assertEqual(book.positions()[0].fees, Decimal("12.50"))

    def test_the_fold_records_its_own_provenance(self) -> None:
        """`11` §8 forbids a running total whose derivation cannot be checked."""
        position = fx.book([fx.fill()]).positions()[0]
        self.assertEqual(position.fills_applied, 1)
        self.assertTrue(position.last_fill_key)
        self.assertEqual(position.last_fill_at, at(1))


class TestContractEconomics(unittest.TestCase):
    """Brief §21 and `07` §4.3."""

    def test_the_version_valid_at_the_time_is_used(self) -> None:
        """A lot-size revision must not rewrite historical exposure."""
        before = fx.economics(minute=1)
        after = fx.economics(minute=3)
        self.assertEqual(before.lot_size, fx.LOT_SIZE_BEFORE)
        self.assertEqual(after.lot_size, fx.LOT_SIZE_AFTER)

    def test_unresolvable_metadata_returns_none_not_a_default(self) -> None:
        """Brief §21: do not silently assume economic parameters."""
        self.assertIsNone(resolve_economics(fx.TARGET, fx.instrument_versions(), at=at(-5)))
        self.assertIsNone(resolve_economics(999999, fx.instrument_versions(), at=at(1)))

    def test_overlapping_versions_raise_rather_than_picking_one(self) -> None:
        """Choosing silently would make the valuation depend on iteration order."""
        overlapping = [*fx.instrument_versions(), *fx.instrument_versions()]
        with self.assertRaises(ValueError) as caught:
            resolve_economics(fx.TARGET, overlapping, at=at(1))
        self.assertIn("overlapping", str(caught.exception))

    def test_an_absent_multiplier_is_a_declared_default_not_a_silent_one(self) -> None:
        self.assertIs(fx.economics().multiplier_source, MultiplierSource.DECLARED_DEFAULT)
        self.assertEqual(fx.economics().multiplier, Decimal(1))

    def test_a_declared_multiplier_is_read_from_the_instrument(self) -> None:
        with_multiplier = fx.economics(multiplier="10")
        self.assertIs(with_multiplier.multiplier_source, MultiplierSource.INSTRUMENT_METADATA)
        self.assertEqual(with_multiplier.multiplier, Decimal(10))

    def test_lot_size_is_not_applied_twice(self) -> None:
        """The most dangerous arithmetic error available here.

        Quantity is in **units**: 50 units of a lot-50 instrument is one lot, and
        its notional is price x 50. Multiplying by lot_size again would inflate
        every options position fifty-fold.
        """
        economics = fx.economics()
        self.assertEqual(UNITS_PER_QUANTITY, 1)
        self.assertEqual(economics.notional(Decimal(100), 50), Decimal(5000))
        self.assertEqual(economics.lots(50), Decimal(1))

    def test_a_declared_multiplier_does_scale_the_notional(self) -> None:
        self.assertEqual(fx.economics(multiplier="10").notional(Decimal(100), 50), Decimal(50000))

    def test_whole_lot_validation(self) -> None:
        economics = fx.economics()
        self.assertTrue(economics.is_whole_lots(100))
        self.assertFalse(economics.is_whole_lots(75))

    def test_a_non_positive_multiplier_is_refused(self) -> None:
        """It would silently zero or invert every valuation of the instrument."""
        with self.assertRaises(ValueError):
            resolve_economics(fx.TARGET, fx.instrument_versions(multiplier="0"), at=at(1))


class TestValuation(unittest.TestCase):
    """Brief §9 and §10."""

    def test_a_position_is_valued_from_the_canonical_state(self) -> None:
        result = fx.valued()
        self.assertTrue(result.is_complete)
        # Minute 3 ATM call is 115; 50 units at 115 = 5750... the fixture's target
        # is strike index 1, so 100 + 3*5 + 1 = 116.
        self.assertEqual(result.total_market_value, Decimal(5800))

    def test_unrealized_pnl_is_mark_less_basis(self) -> None:
        result = fx.valued(fills=[fx.fill(quantity=50, price="100")])
        self.assertEqual(result.total_unrealized_pnl, Decimal(800))

    def test_a_position_with_no_price_is_unvalued_and_named(self) -> None:
        """Never treated as zero: a total omitting it reads as a smaller book."""
        from oipulse.trading.portfolio import PositionBook

        book = PositionBook()
        book.apply(
            fx.fill(instrument_id=999999),
            account_id=fx.ACCOUNT_ID,
            portfolio_id=fx.PORTFOLIO_ID,
            at=at(1),
        )
        positions = book.positions(economics={999999: fx.economics()})
        result = value_positions(positions, fx.state_at(fx.observations(), 3))
        self.assertFalse(result.is_complete)
        self.assertEqual(len(result.unvalued), 1)
        self.assertIs(result.unvalued[0].unvalued_reason, UnvaluedReason.NO_PRICE_IN_STATE)
        self.assertEqual(result.total_market_value, Decimal(0))

    def test_a_position_with_no_economics_is_unvalued(self) -> None:
        positions = fx.book().positions(economics={})
        result = value_positions(positions, fx.state_at(fx.observations(), 3))
        self.assertIs(result.unvalued[0].unvalued_reason, UnvaluedReason.NO_CONTRACT_ECONOMICS)

    def test_an_unreliable_state_refuses_the_whole_valuation(self) -> None:
        """A refusal is not a portfolio worth nothing."""
        import dataclasses

        state = fx.state_at(fx.observations(), 3)
        degraded = dataclasses.replace(
            state,
            quality=dataclasses.replace(state.quality, status=QualityStatus.UNRELIABLE),
        )
        positions = fx.book().positions(economics={fx.TARGET: fx.economics()})
        with self.assertRaises(ValuationRefused):
            value_positions(positions, degraded)

    def test_an_unreliable_state_can_be_valued_when_explicitly_allowed(self) -> None:
        """The refusal is a declared policy, so the test above tests the policy."""
        import dataclasses

        state = fx.state_at(fx.observations(), 3)
        degraded = dataclasses.replace(
            state,
            quality=dataclasses.replace(state.quality, status=QualityStatus.UNRELIABLE),
        )
        positions = fx.book().positions(economics={fx.TARGET: fx.economics()})
        result = value_positions(positions, degraded, allow_unreliable_state=True)
        self.assertTrue(result.is_complete)

    def test_the_mark_source_is_recorded(self) -> None:
        self.assertIsNotNone(fx.valued().valued[0].mark_source)

    def test_both_times_travel_with_the_valuation(self) -> None:
        result = fx.valued()
        self.assertEqual(result.market_time, at(3))
        self.assertEqual(result.knowledge_time, at(3))


class TestPointInTime(unittest.TestCase):
    """Brief §10. A valuation at T on knowledge to K is not latest-known."""

    def test_a_later_price_does_not_affect_an_earlier_valuation(self) -> None:
        """The property a leaked future price would break."""
        early = fx.valued(valuation_minute=1)
        late = fx.valued(valuation_minute=3)
        self.assertLess(early.total_market_value, late.total_market_value)

        # Adding observations after minute 1 must not change the minute-1 value.
        extended = fx.observations(minutes=6)
        again = fx.valued(rows=extended, valuation_minute=1)
        self.assertEqual(early.total_market_value, again.total_market_value)

    def test_a_late_ingested_price_is_invisible_at_an_earlier_horizon(self) -> None:
        """The headline point-in-time rule, on an unambiguous case.

        A quote observed at minute 1 but **ingested** at minute 3 is not knowledge
        we held at minute 1. At K=1 the position is therefore unvalued; at K=3 it
        is valued. Using an instrument with no competing observation makes this
        test about the horizon rather than about which of two rows wins.
        """
        from oipulse.trading.portfolio import PositionBook
        from tests.phase3._fixtures import quote_obs

        late_instrument = fx.OTHER
        # Only one observation for this instrument, and it arrived late.
        rows = [
            row
            for row in fx.observations()
            if getattr(row, "instrument_id", None) != late_instrument
        ]
        rows.append(quote_obs(late_instrument, at(1), at(3), ltp="250"))

        book = PositionBook()
        book.apply(
            fx.fill(instrument_id=late_instrument, quantity=10, price="200"),
            account_id=fx.ACCOUNT_ID,
            portfolio_id=fx.PORTFOLIO_ID,
            at=at(1),
        )
        positions = book.positions(economics={late_instrument: fx.economics()})

        at_k1 = value_positions(positions, fx.state_at(rows, 1, knowledge_minute=1))
        self.assertFalse(at_k1.is_complete, "a price ingested later was not known yet")
        self.assertIs(at_k1.unvalued[0].unvalued_reason, UnvaluedReason.NO_PRICE_IN_STATE)

        at_k3 = value_positions(positions, fx.state_at(rows, 1, knowledge_minute=3))
        self.assertTrue(at_k3.is_complete, "and is known by K=3")
        self.assertEqual(at_k3.valued[0].mark, Decimal(250))

    def test_the_same_market_time_at_different_k_is_a_different_valuation(self) -> None:
        """Brief §10: valuation-at-T is not synonymous with latest-known.

        Distinguishable even when the numbers happen to agree, because the
        knowledge horizon is part of the artifact rather than metadata about it.
        """
        rows = fx.observations()
        a = value_positions(
            fx.book().positions(economics={fx.TARGET: fx.economics()}),
            fx.state_at(rows, 1, knowledge_minute=1),
        )
        b = value_positions(
            fx.book().positions(economics={fx.TARGET: fx.economics()}),
            fx.state_at(rows, 1, knowledge_minute=3),
        )
        self.assertEqual(a.market_time, b.market_time)
        self.assertNotEqual(a.knowledge_time, b.knowledge_time)
        self.assertNotEqual(a.as_dict(), b.as_dict())

    def test_valuation_has_no_parameter_for_a_later_price(self) -> None:
        """Structural: there is no way to pass one in."""
        import inspect

        parameters = set(inspect.signature(value_positions).parameters)
        self.assertEqual(
            parameters,
            {"positions", "state", "allow_unreliable_state", "allow_stale_quotes"},
        )


class TestDeterminism(unittest.TestCase):
    """Brief §23."""

    def test_identical_inputs_produce_an_identical_valuation(self) -> None:
        self.assertEqual(fx.valued().as_dict(), fx.valued().as_dict())

    def test_the_valuation_does_not_depend_on_when_it_ran(self) -> None:
        import time as wallclock

        first = fx.valued().as_dict()
        wallclock.sleep(0.01)
        self.assertEqual(first, fx.valued().as_dict())

    def test_reordered_positions_produce_an_identical_result(self) -> None:
        positions = fx.book(
            [
                fx.fill(instrument_id=fx.TARGET, tag="a"),
                fx.fill(instrument_id=fx.OTHER, tag="b"),
            ]
        ).positions(
            economics={fx.TARGET: fx.economics(), fx.OTHER: fx.economics(instrument_id=fx.OTHER)}
        )
        state = fx.state_at(fx.observations(), 3)
        forward = value_positions(list(positions), state)
        backward = value_positions(list(reversed(positions)), state)
        self.assertEqual(forward.as_dict(), backward.as_dict())


if __name__ == "__main__":
    unittest.main()
