"""Account lifecycle, order model and every state transition.

Phase 8 brief §1, §2, §5, §7. `11-TRADING.md` §4:

> **Invalid transitions raise.** The machine is a declared table of permitted
> transitions, not scattered `if` statements.

Every permitted transition and a representative set of forbidden ones are exercised
below, including from each terminal state — a machine that is only tested on its
happy path is a machine whose forbidden edges are untested.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from oipulse.backtest.intents import OrderType, Side
from oipulse.trading.accounts import (
    AccountMode,
    AccountStatus,
    LiveExecutionUnavailable,
    PaperAccount,
    PaperAccountConfig,
    account_transitions,
    resolve_execution_mode,
)
from oipulse.trading.orders import (
    InvalidTransition,
    OrderState,
    PaperOrder,
    RejectReason,
    is_terminal,
    permitted_transitions,
)
from tests.phase8 import _fixtures as fx
from tests.phase8._fixtures import at

TERMINAL = (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED)


def _order(**kwargs: object) -> PaperOrder:
    defaults: dict[str, object] = {
        "order_id": "ord_test",
        "account_id": fx.ACCOUNT_ID,
        "intent_id": "int_test",
        "instrument_id": fx.TARGET,
        "side": Side.BUY,
        "quantity": 100,
        "order_type": OrderType.MARKET,
        "created_at": at(0),
    }
    defaults.update(kwargs)
    return PaperOrder(**defaults)  # type: ignore[arg-type]


class TestAccountLifecycle(unittest.TestCase):
    def test_a_new_account_starts_initialised_and_takes_no_intents(self) -> None:
        account = fx.account(status=AccountStatus.INITIALISED)
        self.assertIs(account.status, AccountStatus.INITIALISED)
        self.assertFalse(account.status.accepts_intents)

    def test_activation_records_the_opening_market_time(self) -> None:
        account = fx.account(status=AccountStatus.INITIALISED).with_status(
            AccountStatus.ACTIVE, at=at(1)
        )
        self.assertIs(account.status, AccountStatus.ACTIVE)
        self.assertEqual(account.opened_at, at(1))
        self.assertTrue(account.status.accepts_intents)

    def test_suspended_accounts_take_no_intents_but_can_resume(self) -> None:
        account = fx.account().with_status(AccountStatus.SUSPENDED, at=at(1))
        self.assertFalse(account.status.accepts_intents)
        self.assertTrue(account.with_status(AccountStatus.ACTIVE, at=at(2)).status.accepts_intents)

    def test_closed_is_terminal(self) -> None:
        closed = fx.account().with_status(AccountStatus.CLOSED, at=at(3))
        self.assertEqual(closed.closed_at, at(3))
        self.assertEqual(account_transitions(AccountStatus.CLOSED), frozenset())
        for target in AccountStatus:
            with self.subTest(target=target), self.assertRaises(ValueError):
                closed.with_status(target, at=at(4))

    def test_an_invalid_lifecycle_transition_raises_rather_than_being_corrected(self) -> None:
        initialised = fx.account(status=AccountStatus.INITIALISED)
        with self.assertRaises(ValueError) as caught:
            initialised.with_status(AccountStatus.SUSPENDED, at=at(1))
        self.assertIn("not a permitted transition", str(caught.exception))

    def test_the_account_holds_no_balance(self) -> None:
        """`11` §8: a position is a fold over fills, not a running total.

        An account object carrying a mutable balance is exactly such a total, so
        cash and positions live in the ledger and are absent here.
        """
        fields = set(fx.account().as_dict())
        for banned in ("cash", "balance", "positions", "realized_pnl", "equity"):
            with self.subTest(field=banned):
                self.assertNotIn(banned, fields)


class TestPaperOnlyBoundary(unittest.TestCase):
    """The mandatory architectural gate (brief §4)."""

    def test_a_live_account_cannot_be_constructed(self) -> None:
        with self.assertRaises(LiveExecutionUnavailable) as caught:
            PaperAccount(
                account_id="acc-live",
                owner="tester",
                mode=AccountMode.LIVE,
                config=fx.config(),
            )
        self.assertIn("no live broker adapter exists", str(caught.exception))

    def test_mode_resolution_refuses_anything_but_paper(self) -> None:
        """The single place a mode becomes an execution path.

        Constructed by bypassing `__post_init__` so the resolver itself is under
        test rather than the constructor that normally guards it — otherwise this
        would only be re-testing the previous assertion.
        """
        account = fx.account()
        object.__setattr__(account, "mode", AccountMode.LIVE)
        with self.assertRaises(LiveExecutionUnavailable):
            resolve_execution_mode(account)

    def test_paper_resolves_normally(self) -> None:
        self.assertIs(resolve_execution_mode(fx.account()), AccountMode.PAPER)

    def test_a_runtime_cannot_be_built_for_a_live_account(self) -> None:
        """Refused in the constructor, so a non-paper account cannot even be wired."""
        account = fx.account()
        object.__setattr__(account, "mode", AccountMode.LIVE)
        with self.assertRaises(LiveExecutionUnavailable):
            fx.runtime(acct=account)


class TestAccountConfig(unittest.TestCase):
    def test_configuration_is_content_addressed(self) -> None:
        self.assertEqual(fx.config().content_digest, fx.config().content_digest)

    def test_changing_the_starting_cash_changes_the_digest(self) -> None:
        self.assertNotEqual(
            fx.config(starting_cash=Decimal(100000)).content_digest,
            fx.config(starting_cash=Decimal(200000)).content_digest,
        )

    def test_changing_a_fill_assumption_changes_the_digest(self) -> None:
        """Two accounts under different execution assumptions are not comparable."""
        self.assertNotEqual(
            fx.config(model=fx.fill_model(slippage_parameter=Decimal("0.1"))).content_digest,
            fx.config(model=fx.fill_model(slippage_parameter=Decimal("0.9"))).content_digest,
        )

    def test_a_negative_starting_balance_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            PaperAccountConfig(
                starting_cash=Decimal(-1),
                currency="INR",
                fill_model=fx.fill_model(),
                cost_model=fx.config().cost_model,
            )

    def test_the_fill_and_cost_models_are_required_not_defaulted(self) -> None:
        """Brief §8: every execution assumption must be configuration-identifiable."""
        import inspect

        required = [
            name
            for name, parameter in inspect.signature(PaperAccountConfig).parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        for field in ("starting_cash", "currency", "fill_model", "cost_model"):
            self.assertIn(field, required)


class TestOrderStateMachine(unittest.TestCase):
    def test_every_permitted_transition_is_accepted(self) -> None:
        """Walks the declared table rather than a hand-written list.

        A transition added to the table but never exercised would otherwise be
        untested, and this test grows automatically with the machine.
        """
        for source, targets in (
            (OrderState.CREATED, permitted_transitions(OrderState.CREATED)),
            (OrderState.ACCEPTED, permitted_transitions(OrderState.ACCEPTED)),
            (OrderState.OPEN, permitted_transitions(OrderState.OPEN)),
            (
                OrderState.PARTIALLY_FILLED,
                permitted_transitions(OrderState.PARTIALLY_FILLED),
            ),
        ):
            for target in targets:
                with self.subTest(source=source, target=target):
                    order = _order(state=source)
                    moved = order.transition(target, at=at(1), trigger="test")
                    self.assertIs(moved.state, target)
                    self.assertEqual(moved.events[-1].to_state, target)
                    self.assertEqual(moved.events[-1].from_state, source)

    def test_every_forbidden_transition_raises(self) -> None:
        for source in OrderState:
            allowed = permitted_transitions(source)
            for target in OrderState:
                if target in allowed:
                    continue
                with (
                    self.subTest(source=source, target=target),
                    self.assertRaises(InvalidTransition),
                ):
                    _order(state=source).transition(target, at=at(1), trigger="test")

    def test_every_terminal_state_permits_nothing(self) -> None:
        for state in TERMINAL:
            with self.subTest(state=state):
                self.assertTrue(is_terminal(state))
                self.assertEqual(permitted_transitions(state), frozenset())

    def test_no_state_is_both_terminal_and_transitionable(self) -> None:
        for state in OrderState:
            with self.subTest(state=state):
                self.assertEqual(is_terminal(state), not permitted_transitions(state))

    def test_transitions_append_rather_than_replace(self) -> None:
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        order = order.transition(OrderState.OPEN, at=at(2), trigger="b")
        order = order.transition(OrderState.CANCELLED, at=at(3), trigger="c")
        self.assertEqual([e.sequence for e in order.events], [1, 2, 3])
        self.assertEqual(
            [e.to_state for e in order.events],
            [OrderState.ACCEPTED, OrderState.OPEN, OrderState.CANCELLED],
        )

    def test_the_original_order_is_not_mutated(self) -> None:
        order = _order()
        order.transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        self.assertIs(order.state, OrderState.CREATED)
        self.assertEqual(order.events, ())


class TestOrderFills(unittest.TestCase):
    def test_a_partial_fill_moves_to_partially_filled(self) -> None:
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        order = order.apply_fill(quantity=40, price=Decimal(100), at=at(1))
        self.assertIs(order.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 40)
        self.assertEqual(order.remaining_quantity, 60)

    def test_completing_the_quantity_moves_to_filled(self) -> None:
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        order = order.apply_fill(quantity=100, price=Decimal(100), at=at(1))
        self.assertIs(order.state, OrderState.FILLED)
        self.assertEqual(order.remaining_quantity, 0)

    def test_the_average_price_is_quantity_weighted(self) -> None:
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        order = order.apply_fill(quantity=40, price=Decimal(100), at=at(1))
        order = order.apply_fill(quantity=60, price=Decimal(110), at=at(2))
        self.assertEqual(order.average_fill_price, Decimal(106))

    def test_the_destination_is_computed_not_supplied(self) -> None:
        """A caller cannot mark an order FILLED while quantity is outstanding."""
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        partly = order.apply_fill(quantity=1, price=Decimal(100), at=at(1))
        self.assertIs(partly.state, OrderState.PARTIALLY_FILLED)

    def test_overfilling_is_refused(self) -> None:
        order = _order(quantity=10).transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        with self.assertRaises(ValueError) as caught:
            order.apply_fill(quantity=11, price=Decimal(100), at=at(1))
        self.assertIn("must not overfill", str(caught.exception))

    def test_a_zero_or_negative_fill_is_refused(self) -> None:
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        for quantity in (0, -5):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                order.apply_fill(quantity=quantity, price=Decimal(100), at=at(1))

    def test_filling_a_cancelled_order_raises(self) -> None:
        order = _order().transition(OrderState.ACCEPTED, at=at(1), trigger="a")
        cancelled = order.cancel(at=at(2))
        with self.assertRaises(InvalidTransition):
            cancelled.apply_fill(quantity=10, price=Decimal(100), at=at(3))


class TestOrderIdentity(unittest.TestCase):
    def test_the_order_id_is_deterministic(self) -> None:
        """Brief §19. A restart must recompute the same id, not mint a new one."""
        first = PaperOrder.derive_id("int_abc", 0, fx.ACCOUNT_ID)
        second = PaperOrder.derive_id("int_abc", 0, fx.ACCOUNT_ID)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("ord_"))

    def test_each_leg_gets_a_distinct_id(self) -> None:
        self.assertNotEqual(
            PaperOrder.derive_id("int_abc", 0, fx.ACCOUNT_ID),
            PaperOrder.derive_id("int_abc", 1, fx.ACCOUNT_ID),
        )

    def test_the_same_leg_in_a_different_account_is_a_different_order(self) -> None:
        self.assertNotEqual(
            PaperOrder.derive_id("int_abc", 0, "acc-a"),
            PaperOrder.derive_id("int_abc", 0, "acc-b"),
        )

    def test_no_wall_clock_or_random_component(self) -> None:
        """Two ids computed at different moments must be identical."""
        import time

        first = PaperOrder.derive_id("int_abc", 0, fx.ACCOUNT_ID)
        time.sleep(0.01)
        self.assertEqual(first, PaperOrder.derive_id("int_abc", 0, fx.ACCOUNT_ID))


class TestRejectionsCarryReasons(unittest.TestCase):
    def test_a_rejection_records_its_reason(self) -> None:
        order = _order().reject(RejectReason.INSUFFICIENT_CASH, at=at(1), detail="not enough cash")
        self.assertIs(order.state, OrderState.REJECTED)
        self.assertIs(order.reject_reason, RejectReason.INSUFFICIENT_CASH)
        self.assertIn("reason", dict(order.events[-1].payload))

    def test_the_envelope_always_states_paper_mode(self) -> None:
        self.assertEqual(_order().as_dict()["mode"], "PAPER")


if __name__ == "__main__":
    unittest.main()
