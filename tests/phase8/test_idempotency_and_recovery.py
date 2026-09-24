"""Idempotency, event ordering, reconstruction, recovery and ledger determinism.

Phase 8 brief §11, §12, §13, §16, §23. The claim under test is precise and is
**not** global exactly-once delivery (`03` §4):

> *exactly-once database application per `(subscriber, event_id)` transaction*

So the tests below assert that repeated delivery, retries, restarts and reordering
all converge on the same state — not that a message is delivered once.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from oipulse.backtest.intents import Side
from oipulse.events.domain import AggregateType, DomainEvent, SequenceGap
from oipulse.events.inbox import InMemoryInbox
from oipulse.trading.events import PAPER_TRADING_SUBSCRIBER, AggregateWatermarks
from oipulse.trading.ledger import InsufficientCash, PaperLedger, replay_fills
from oipulse.trading.orders import OrderState
from tests.phase8 import _fixtures as fx
from tests.phase8._fixtures import at


def _event(aggregate_id: str, sequence: int, event_type: str = "paper.test") -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        aggregate_type=AggregateType.ORDER,
        aggregate_id=aggregate_id,
        aggregate_sequence=sequence,
        occurred_at=at(1),
        payload={},
    )


class TestIntentIdempotency(unittest.TestCase):
    def test_the_intent_id_is_content_addressed(self) -> None:
        self.assertEqual(fx.intent().intent_id, fx.intent().intent_id)

    def test_a_different_quantity_is_a_different_intent(self) -> None:
        self.assertNotEqual(fx.intent(quantity=50).intent_id, fx.intent(quantity=51).intent_id)

    def test_a_different_knowledge_time_is_a_different_intent(self) -> None:
        """Same instruction on different knowledge is a different decision."""
        self.assertNotEqual(
            fx.intent(decision_minute=0, knowledge_minute=0).intent_id,
            fx.intent(decision_minute=0, knowledge_minute=2).intent_id,
        )

    def test_reason_and_evidence_do_not_change_identity(self) -> None:
        """Prose describes a decision; it does not change what it instructs."""
        import dataclasses

        base = fx.intent()
        reworded = dataclasses.replace(base, reason="different words", evidence_refs=("x",))
        self.assertEqual(base.intent_id, reworded.intent_id)

    def test_submitting_the_same_intent_twice_changes_nothing(self) -> None:
        rt = fx.runtime()
        rows = fx.observations()
        first = fx.submit(rt, rows, fx.intent())
        cash_after_first = rt.ledger.cash
        position_after_first = rt.ledger.position(fx.TARGET)

        second = fx.submit(rt, rows, fx.intent())
        self.assertTrue(second.duplicate)
        self.assertFalse(first.duplicate)
        self.assertEqual(rt.ledger.cash, cash_after_first)
        self.assertEqual(rt.ledger.position(fx.TARGET), position_after_first)
        self.assertEqual(rt.ledger.fills_applied, 1)
        self.assertEqual(len(rt.orders()), 1)
        self.assertEqual(rt.duplicate_intents_ignored, 1)

    def test_a_duplicate_submission_still_returns_the_original_result(self) -> None:
        """A caller retrying must get the outcome, not an empty acknowledgement."""
        rt = fx.runtime()
        rows = fx.observations()
        fx.submit(rt, rows, fx.intent())
        again = fx.submit(rt, rows, fx.intent())
        self.assertEqual(len(again.orders), 1)
        self.assertEqual(len(again.fills), 1)

    def test_a_client_supplied_key_is_the_dedup_key(self) -> None:
        base = fx.intent(client_order_intent_id="client-123")
        self.assertEqual(base.idempotency_key, "client-123")
        self.assertEqual(fx.intent().idempotency_key, fx.intent().intent_id)

    def test_two_different_intents_sharing_a_client_key_dedupe_to_one(self) -> None:
        """A client asserting "this is the same order" is taken at its word."""
        rt = fx.runtime()
        rows = fx.observations()
        fx.submit(rt, rows, fx.intent(quantity=50, client_order_intent_id="k"))
        second = fx.submit(rt, rows, fx.intent(quantity=10, client_order_intent_id="k"))
        self.assertTrue(second.duplicate)
        self.assertEqual(rt.ledger.position(fx.TARGET).quantity, 50)


class TestFillIdempotency(unittest.TestCase):
    def test_a_duplicate_fill_does_not_double_the_position(self) -> None:
        """Brief §11, stated as the headline requirement."""
        rt = fx.runtime()
        rows = fx.observations()
        result = fx.submit(rt, rows, fx.intent())
        fill = result.fills[0]

        self.assertFalse(rt.ledger.apply(fill), "the same fill must be refused")
        self.assertEqual(rt.ledger.position(fx.TARGET).quantity, 50)
        self.assertEqual(rt.ledger.duplicate_fills_ignored, 1)

    def test_duplicate_fills_do_not_double_fees_or_cash(self) -> None:
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent())
        cash, fees = rt.ledger.cash, rt.ledger.fees
        rt.ledger.apply(result.fills[0])
        self.assertEqual(rt.ledger.cash, cash)
        self.assertEqual(rt.ledger.fees, fees)

    def test_has_applied_reports_without_applying(self) -> None:
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent())
        self.assertTrue(rt.ledger.has_applied(result.fills[0]))
        self.assertEqual(rt.ledger.fills_applied, 1)


class TestEventOrderingAndInbox(unittest.TestCase):
    def test_an_event_ahead_of_its_turn_is_deferred_not_applied(self) -> None:
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        applied: list[int] = []

        with self.assertRaises(SequenceGap):
            marks.apply_ordered(_event("ord_1", 2), inbox, lambda: applied.append(2))
        self.assertEqual(applied, [], "nothing may be mutated when a gap is detected")

    def test_the_gap_closes_and_the_deferred_event_then_applies(self) -> None:
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        applied: list[int] = []

        marks.apply_ordered(_event("ord_1", 1), inbox, lambda: applied.append(1))
        marks.apply_ordered(_event("ord_1", 2), inbox, lambda: applied.append(2))
        self.assertEqual(applied, [1, 2])

    def test_a_redelivered_event_is_absorbed_not_reapplied(self) -> None:
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        applied: list[int] = []
        event = _event("ord_1", 1)

        marks.apply_ordered(event, inbox, lambda: applied.append(1))
        marks.apply_ordered(event, inbox, lambda: applied.append(1))
        self.assertEqual(applied, [1], "the inbox must absorb the redelivery")

    def test_an_event_behind_the_watermark_is_not_an_error(self) -> None:
        """Ordinary at-least-once redelivery, not a failure to alert on (`03` §4)."""
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        marks.apply_ordered(_event("ord_1", 1), inbox, lambda: None)
        marks.apply_ordered(_event("ord_1", 2), inbox, lambda: None)
        marks.apply_ordered(_event("ord_1", 1), inbox, lambda: None)  # must not raise

    def test_a_failed_mutation_releases_the_claim_so_the_retry_works(self) -> None:
        """The exact failure the transactional inbox exists to prevent.

        A claim that survived a failed mutation would suppress the retry and lose
        the write.
        """
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        attempts: list[int] = []

        def failing() -> None:
            attempts.append(1)
            raise RuntimeError("persistence failure")

        with self.assertRaises(RuntimeError):
            marks.apply_ordered(_event("ord_1", 1), inbox, failing)
        self.assertFalse(inbox.was_applied(PAPER_TRADING_SUBSCRIBER, _event("ord_1", 1).event_id))

        # The retry must now succeed rather than being silently skipped.
        marks.apply_ordered(_event("ord_1", 1), inbox, lambda: attempts.append(2))
        self.assertEqual(attempts, [1, 2])

    def test_a_failed_mutation_does_not_advance_the_watermark(self) -> None:
        """Otherwise the successor is deferred forever behind a rolled-back event."""
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        event = _event("ord_1", 1)

        def failing() -> None:
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            marks.apply_ordered(event, inbox, failing)
        self.assertIsNone(marks.last_applied(event))

    def test_different_aggregates_have_independent_sequences(self) -> None:
        marks = AggregateWatermarks()
        inbox = InMemoryInbox()
        marks.apply_ordered(_event("ord_1", 1), inbox, lambda: None)
        # ord_2's sequence 1 must not be considered a gap because ord_1 is at 1.
        marks.apply_ordered(_event("ord_2", 1), inbox, lambda: None)

    def test_the_runtime_emits_every_order_transition_in_sequence(self) -> None:
        """A skipped transition would open a gap that defers everything after it."""
        rt = fx.runtime()
        result = fx.submit(rt, fx.observations(), fx.intent())
        order = result.orders[0]
        self.assertEqual([e.sequence for e in order.events], [1, 2])
        self.assertEqual(
            [e.to_state for e in order.events], [OrderState.ACCEPTED, OrderState.FILLED]
        )


class TestReservation(unittest.TestCase):
    def test_reserving_reduces_available_cash_but_not_total(self) -> None:
        ledger = PaperLedger("acc", opening_cash=Decimal(1000))
        ledger.reserve("ord_1", Decimal(400))
        self.assertEqual(ledger.cash, Decimal(1000))
        self.assertEqual(ledger.available_cash, Decimal(600))

    def test_over_reserving_is_refused(self) -> None:
        ledger = PaperLedger("acc", opening_cash=Decimal(1000))
        with self.assertRaises(InsufficientCash):
            ledger.reserve("ord_1", Decimal(1001))

    def test_re_reserving_the_same_order_replaces_rather_than_adds(self) -> None:
        """A retried acceptance must not double-commit the same cash."""
        ledger = PaperLedger("acc", opening_cash=Decimal(1000))
        ledger.reserve("ord_1", Decimal(400))
        ledger.reserve("ord_1", Decimal(400))
        self.assertEqual(ledger.reserved_cash, Decimal(400))

    def test_releasing_twice_is_safe(self) -> None:
        """A cancel retried after a restart must not release cash twice."""
        ledger = PaperLedger("acc", opening_cash=Decimal(1000))
        ledger.reserve("ord_1", Decimal(400))
        self.assertEqual(ledger.release("ord_1"), Decimal(400))
        self.assertEqual(ledger.release("ord_1"), Decimal(0))
        self.assertEqual(ledger.reserved_cash, Decimal(0))


class TestLedgerDeterminism(unittest.TestCase):
    def test_the_same_fill_stream_produces_the_same_state(self) -> None:
        rt = fx.runtime()
        fx.submit(rt, fx.observations(), fx.intent())
        fills = rt.fills()

        first = replay_fills("acc", opening_cash=Decimal(500000), fills=fills)
        second = replay_fills("acc", opening_cash=Decimal(500000), fills=fills)
        marks = fx.marks(fx.observations(), 3)
        self.assertEqual(
            first.snapshot(as_of=at(3), marks=marks).as_dict(),
            second.snapshot(as_of=at(3), marks=marks).as_dict(),
        )

    def test_reconstruction_equals_the_live_fold(self) -> None:
        """Brief §12: event stream → state must equal the state built live."""
        rows = fx.observations()
        rt = fx.runtime()
        fx.submit(rt, rows, fx.intent())
        fx.submit(rt, rows, fx.intent(instrument_id=fx.OTHER, quantity=20))

        marks = fx.marks(rows, 3)
        live = rt.ledger.snapshot(as_of=at(3), marks=marks)
        rebuilt = rt.rebuild_ledger_from_fills(rt.fills()).snapshot(as_of=at(3), marks=marks)
        self.assertEqual(live.as_dict(), rebuilt.as_dict())

    def test_replaying_a_stream_with_duplicates_produces_the_same_state(self) -> None:
        """Recovery must be safe against at-least-once redelivery (brief §23)."""
        rows = fx.observations()
        rt = fx.runtime()
        fx.submit(rt, rows, fx.intent())
        fills = rt.fills()

        marks = fx.marks(rows, 3)
        clean = replay_fills("acc", opening_cash=Decimal(500000), fills=fills)
        doubled = replay_fills("acc", opening_cash=Decimal(500000), fills=[*fills, *fills, *fills])
        self.assertEqual(
            clean.snapshot(as_of=at(3), marks=marks).as_dict(),
            doubled.snapshot(as_of=at(3), marks=marks).as_dict(),
        )
        self.assertEqual(doubled.duplicate_fills_ignored, 2 * len(fills))

    def test_position_cash_and_pnl_reconcile(self) -> None:
        rows = fx.observations()
        rt = fx.runtime()
        fx.submit(rt, rows, fx.intent(quantity=50))
        snapshot = rt.ledger.snapshot(as_of=at(3), marks=fx.marks(rows, 3))

        self.assertEqual(snapshot.net_pnl, snapshot.gross_pnl - snapshot.fees)
        self.assertEqual(snapshot.equity, Decimal(500000) + snapshot.net_pnl)
        self.assertGreater(snapshot.fees, Decimal(0), "a filled order must have paid fees")

    def test_a_short_position_is_tracked_honestly(self) -> None:
        rt = fx.runtime()
        rows = fx.observations()
        fx.submit(rt, rows, fx.intent(side=Side.SELL, quantity=30))
        position = rt.ledger.position(fx.TARGET)
        assert position is not None
        self.assertEqual(position.quantity, -30)
        self.assertGreater(rt.ledger.cash, Decimal(500000), "a sale brings in premium")

    def test_an_unmarked_position_is_named_not_marked_at_cost(self) -> None:
        rt = fx.runtime()
        fx.submit(rt, fx.observations(), fx.intent())
        snapshot = rt.ledger.snapshot(as_of=at(3), marks={})
        self.assertEqual(snapshot.unrealized_pnl, Decimal(0))
        self.assertEqual(snapshot.inner.unmarked_instruments, (fx.TARGET,))


class TestRestartRecovery(unittest.TestCase):
    """Brief §23. A restart must not apply anything twice."""

    def test_a_restarted_runtime_rebuilt_from_fills_matches_the_original(self) -> None:
        rows = fx.observations()
        original = fx.runtime()
        fx.submit(original, rows, fx.intent())
        fx.submit(original, rows, fx.intent(instrument_id=fx.OTHER, quantity=20))
        marks = fx.marks(rows, 3)
        before = original.ledger.snapshot(as_of=at(3), marks=marks)

        # A fresh process: new runtime, same account, replaying the persisted fills.
        restarted = fx.runtime()
        rebuilt = restarted.rebuild_ledger_from_fills(original.fills())
        self.assertEqual(rebuilt.snapshot(as_of=at(3), marks=marks).as_dict(), before.as_dict())

    def test_resubmitting_after_a_restart_does_not_duplicate_the_order(self) -> None:
        """The order id is derived from the intent, so the second attempt collides.

        In-memory this shows up as an identical id; in PostgreSQL the UNIQUE
        constraint on `trade_orders.order_id` turns it into a conflict.
        """
        rows = fx.observations()
        first = fx.runtime()
        original = fx.submit(first, rows, fx.intent()).orders[0]

        restarted = fx.runtime()
        replayed = fx.submit(restarted, rows, fx.intent()).orders[0]
        self.assertEqual(original.order_id, replayed.order_id)

    def test_a_replayed_submission_produces_an_identical_fill(self) -> None:
        rows = fx.observations()
        first = fx.runtime()
        second = fx.runtime()
        a = fx.submit(first, rows, fx.intent()).fills[0]
        b = fx.submit(second, rows, fx.intent()).fills[0]
        self.assertEqual(a.as_dict(), b.as_dict())


if __name__ == "__main__":
    unittest.main()
