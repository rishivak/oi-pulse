"""Phase 1 foundation tests.

Written with `unittest` (stdlib) rather than bare pytest functions so the suite runs
without any third-party package installed. pytest collects `TestCase` classes natively,
so this costs nothing once the dependency set is available.

Covers the Phase 1 test requirements from `docs/design/18-ROADMAP.md`:
import-linter contracts, clock-access AST scan, outbox idempotency, and config
validation refusing placeholders.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.core.clock import (
    Clock,
    FrozenClock,
    ReplayClock,
    SystemClock,
    ensure_utc,
    utc,
)
from oipulse.core.config import (
    find_placeholder,
    load_settings,
)
from oipulse.core.errors import (
    ConfigurationError,
    TemporalBoundError,
    UnboundedQueryError,
)
from oipulse.core.ids import new_correlation_id, new_event_id
from oipulse.core.money import to_decimal
from oipulse.core.timeauthority import Timeframe, bucket_end, bucket_start
from oipulse.core.timemode import (
    KnowledgeAt,
    MarketTruthAt,
    TradableInformationAt,
)
from oipulse.events.domain import (
    AggregateType,
    DomainEvent,
    SequenceGap,
    check_sequence,
)
from oipulse.events.inbox import InMemoryInbox
from oipulse.observability.correlation import (
    correlation_scope,
    current_correlation_id,
)
from oipulse.persistence.repository import (
    ResolvedBound,
    TemporalRepository,
    resolve_bound,
)

VALID_ENV = {
    "DATABASE_URL": "postgresql://localhost/oipulse",
    "SESSION_SECRET_KEY": "s" * 40,
    "TOKEN_ENCRYPTION_KEY": "t" * 44,
}


# ---------------------------------------------------------------- clock


class TestClock(unittest.TestCase):
    def test_all_clocks_satisfy_the_protocol(self):
        for clock in (SystemClock(), FrozenClock(utc(2026, 3, 3)), ReplayClock(utc(2026, 3, 3))):
            self.assertIsInstance(clock, Clock)

    def test_every_clock_returns_utc_aware(self):
        for clock in (SystemClock(), FrozenClock(utc(2026, 3, 3)), ReplayClock(utc(2026, 3, 3))):
            self.assertIsNotNone(clock.now().tzinfo, f"{type(clock).__name__} returned naive")

    def test_naive_datetime_is_rejected_not_assumed_utc(self):
        from datetime import datetime as _dt

        with self.assertRaises(ValueError):
            ensure_utc(_dt(2026, 3, 3, 11, 42))

    def test_frozen_clock_does_not_advance(self):
        c = FrozenClock(utc(2026, 3, 3, 11, 42))
        self.assertEqual(c.now(), c.now())

    def test_replay_clock_cannot_move_backwards(self):
        c = ReplayClock(utc(2026, 3, 3, 11, 42))
        c.advance_to(utc(2026, 3, 3, 11, 43))
        with self.assertRaises(ValueError):
            c.advance_to(utc(2026, 3, 3, 11, 40))

    def test_replay_clock_rejects_negative_advance(self):
        c = ReplayClock(utc(2026, 3, 3, 11, 42))
        with self.assertRaises(ValueError):
            c.advance_by(timedelta(seconds=-1))


# ------------------------------------------------------- time authority


class TestTimeAuthority(unittest.TestCase):
    """One flooring rule. The legacy codebase had two that disagreed at 1h."""

    SESSION_OPEN = utc(2026, 3, 3, 3, 45)  # 09:15 IST

    def test_buckets_anchor_to_session_open_not_wall_clock(self):
        # 06:12:41Z with a 03:45Z open. Wall-clock hourly flooring gives 06:00.
        # Session-anchored gives 05:45 — this is the distinction the legacy bug lost.
        got = bucket_start(utc(2026, 3, 3, 6, 12, 41), Timeframe.ONE_HOUR, self.SESSION_OPEN)
        self.assertEqual(got, utc(2026, 3, 3, 5, 45))
        self.assertNotEqual(got, utc(2026, 3, 3, 6, 0))

    def test_session_open_is_the_first_bucket_start(self):
        for tf in Timeframe:
            self.assertEqual(
                bucket_start(self.SESSION_OPEN, tf, self.SESSION_OPEN),
                self.SESSION_OPEN,
                f"{tf.value} did not anchor at the open",
            )

    def test_bucket_is_half_open_and_contiguous(self):
        at = utc(2026, 3, 3, 6, 12, 41)
        for tf in Timeframe:
            start = bucket_start(at, tf, self.SESSION_OPEN)
            end = bucket_end(at, tf, self.SESSION_OPEN)
            self.assertLessEqual(start, at)
            self.assertLess(at, end)
            self.assertEqual(end - start, timedelta(minutes=tf.minutes))
            # the next instant lands in the next bucket, with no gap
            self.assertEqual(bucket_start(end, tf, self.SESSION_OPEN), end)

    def test_flooring_is_idempotent(self):
        at = utc(2026, 3, 3, 6, 12, 41)
        for tf in Timeframe:
            once = bucket_start(at, tf, self.SESSION_OPEN)
            twice = bucket_start(once, tf, self.SESSION_OPEN)
            self.assertEqual(once, twice)


# ------------------------------------------------------------ time modes


class TestTemporalBounds(unittest.TestCase):
    def test_market_truth_requires_an_explicit_knowledge_horizon(self):
        # 05 §3: the single-argument form was removed deliberately.
        with self.assertRaises(TypeError):
            MarketTruthAt(utc(2026, 3, 3, 11, 40))  # type: ignore[call-arg]

    def test_knowledge_at_bounds_both_axes_to_the_same_instant(self):
        r = resolve_bound(KnowledgeAt(utc(2026, 3, 3, 11, 42)))
        self.assertEqual(r.observed_at_max, r.ingested_at_max)
        self.assertIsNone(r.available_at_max)
        self.assertEqual(r.mode, "knowledge_at")

    def test_market_truth_separates_the_two_axes(self):
        r = resolve_bound(MarketTruthAt(utc(2026, 3, 3, 11, 40), utc(2026, 3, 3, 13, 0)))
        self.assertEqual(r.observed_at_max, utc(2026, 3, 3, 11, 40))
        self.assertEqual(r.ingested_at_max, utc(2026, 3, 3, 13, 0))

    def test_tradable_information_adds_the_availability_filter(self):
        r = resolve_bound(TradableInformationAt(utc(2026, 3, 3, 11, 42)))
        self.assertTrue(r.filters_availability)

    def test_knowledge_horizon_before_valid_time_is_incoherent(self):
        with self.assertRaises(TemporalBoundError):
            resolve_bound(MarketTruthAt(utc(2026, 3, 3, 13, 0), utc(2026, 3, 3, 11, 40)))

    def test_unbounded_query_is_refused(self):
        with self.assertRaises(UnboundedQueryError):
            resolve_bound(None)


class _RawRepo(TemporalRepository):
    supports_availability = False

    def _fetch(self, bound: ResolvedBound, **criteria):
        return [bound.mode]


class _DerivedRepo(TemporalRepository):
    supports_availability = True

    def _fetch(self, bound: ResolvedBound, **criteria):
        return [bound.mode]


class TestTemporalRepository(unittest.TestCase):
    def test_raw_observations_reject_availability_semantics(self):
        # 12 §2: raw observations have no available_at, so the mode is not applicable.
        with self.assertRaises(TemporalBoundError):
            _RawRepo().fetch(TradableInformationAt(utc(2026, 3, 3, 11, 42)))

    def test_raw_observations_accept_knowledge_and_market_truth(self):
        repo = _RawRepo()
        self.assertEqual(repo.fetch(KnowledgeAt(utc(2026, 3, 3))), ["knowledge_at"])
        self.assertEqual(
            repo.fetch(MarketTruthAt(utc(2026, 3, 3), utc(2026, 3, 4))),
            ["market_truth_at"],
        )

    def test_derived_data_accepts_availability_semantics(self):
        self.assertEqual(
            _DerivedRepo().fetch(TradableInformationAt(utc(2026, 3, 3))),
            ["tradable_information_at"],
        )


# ----------------------------------------------------------------- config


class TestConfigValidation(unittest.TestCase):
    """18 Phase 1: 'config validation refuses placeholders'."""

    def test_valid_configuration_loads(self):
        s = load_settings(VALID_ENV)
        self.assertEqual(s.app_env, "development")
        self.assertFalse(s.live_trading_env_gates_open)

    def test_placeholder_secret_refuses_startup(self):
        for placeholder in (
            "REPLACE_WITH_FERNET_KEY",
            "CHANGE_ME_PLEASE_THIS_IS_LONG_ENOUGH_X",
            "your_secret_key_goes_here_padding_xxxxx",
            "<set-me-to-something-real-and-long-here>",
        ):
            with (
                self.subTest(placeholder=placeholder),
                self.assertRaises(ConfigurationError),
            ):
                load_settings({**VALID_ENV, "TOKEN_ENCRYPTION_KEY": placeholder})

    def test_placeholder_detection_is_case_insensitive(self):
        self.assertIsNotNone(find_placeholder("replace_with_key"))
        self.assertIsNotNone(find_placeholder("XxReplace_WITHxx"))
        self.assertIsNone(find_placeholder("a" * 40))

    def test_short_secret_refuses_startup(self):
        with self.assertRaises(ConfigurationError):
            load_settings({**VALID_ENV, "SESSION_SECRET_KEY": "short"})

    def test_missing_required_value_refuses_startup(self):
        env = {k: v for k, v in VALID_ENV.items() if k != "DATABASE_URL"}
        with self.assertRaises(ConfigurationError):
            load_settings(env)

    def test_all_problems_reported_together(self):
        # Reporting only the first means fix-restart-discover-the-next.
        warnings = load_settings({}, strict=False).warnings
        self.assertGreaterEqual(len(warnings), 3)

    def test_live_trading_needs_both_environment_gates(self):
        one = load_settings({**VALID_ENV, "LIVE_TRADING_ENABLED": "true"})
        self.assertFalse(one.live_trading_env_gates_open)
        self.assertTrue(any("remains disabled" in w for w in one.warnings))

        both = load_settings(
            {**VALID_ENV, "LIVE_TRADING_ENABLED": "true", "LIVE_TRADING_CONFIRMED": "true"}
        )
        self.assertTrue(both.live_trading_env_gates_open)
        self.assertTrue(any("third and final gate" in w for w in both.warnings))

    def test_live_trading_is_off_by_default(self):
        self.assertFalse(load_settings(VALID_ENV).live_trading_env_gates_open)

    def test_production_rejects_sqlite(self):
        with self.assertRaises(ConfigurationError):
            load_settings({**VALID_ENV, "APP_ENV": "production", "DATABASE_URL": "sqlite:///x.db"})


# ------------------------------------------------------------- money / ids


class TestPrimitives(unittest.TestCase):
    def test_float_is_rejected_for_monetary_values(self):
        with self.assertRaises(TypeError):
            to_decimal(1.5)

    def test_decimal_and_str_accepted_exactly(self):
        self.assertEqual(to_decimal("25000.50"), Decimal("25000.50"))
        self.assertEqual(to_decimal(25000), Decimal(25000))

    def test_ids_are_unique(self):
        self.assertNotEqual(new_event_id(), new_event_id())
        self.assertNotEqual(new_correlation_id(), new_correlation_id())


# ------------------------------------------------------------------ events


def _event(seq: int, aggregate_id: str = "order-1") -> DomainEvent:
    return DomainEvent(
        event_type="OrderSubmitted",
        aggregate_type=AggregateType.ORDER,
        aggregate_id=aggregate_id,
        aggregate_sequence=seq,
        occurred_at=utc(2026, 3, 3, 11, 42),
        payload={},
    )


class TestAggregateOrdering(unittest.TestCase):
    """03 §4: ordering rests on the sequence check, not on a Redis lock."""

    def test_sequence_must_start_at_one(self):
        with self.assertRaises(ValueError):
            _event(0)

    def test_next_in_sequence_is_accepted(self):
        check_sequence(None, _event(1))
        check_sequence(1, _event(2))

    def test_event_ahead_of_its_turn_is_deferred(self):
        with self.assertRaises(SequenceGap) as ctx:
            check_sequence(1, _event(3))
        self.assertEqual(ctx.exception.expected, 2)
        self.assertEqual(ctx.exception.got, 3)

    def test_redelivery_behind_the_watermark_is_not_an_error(self):
        # At-least-once delivery is normal; the inbox absorbs it idempotently.
        check_sequence(5, _event(3))

    def test_events_carry_ordering_identity(self):
        e = _event(1)
        self.assertEqual(e.aggregate_key, ("order", "order-1"))
        self.assertIsNotNone(e.event_id)


class TestTransactionalInbox(unittest.TestCase):
    """03 §4: exactly-once DATABASE APPLICATION per (subscriber, event_id).

    Explicitly not global exactly-once — external side effects are out of scope.
    """

    def setUp(self):
        self.inbox = InMemoryInbox()
        self.applied: list[str] = []

    def _mutate(self):
        self.applied.append("x")
        return "done"

    def test_first_delivery_applies(self):
        self.assertEqual(self.inbox.apply_once("portfolio", "e1", self._mutate), "done")
        self.assertEqual(len(self.applied), 1)

    def test_redelivery_is_a_noop(self):
        self.inbox.apply_once("portfolio", "e1", self._mutate)
        self.assertIsNone(self.inbox.apply_once("portfolio", "e1", self._mutate))
        self.assertIsNone(self.inbox.apply_once("portfolio", "e1", self._mutate))
        self.assertEqual(len(self.applied), 1, "mutation applied more than once")

    def test_each_subscriber_applies_independently(self):
        self.inbox.apply_once("portfolio", "e1", self._mutate)
        self.inbox.apply_once("risk", "e1", self._mutate)
        self.assertEqual(len(self.applied), 2)

    def test_failed_mutation_releases_the_claim_so_retry_works(self):
        # The claim must not survive a failed mutation, or the retry is suppressed
        # and the write is lost — the entire point of the same-transaction pattern.
        def boom():
            raise RuntimeError("crash between mutation and commit")

        with self.assertRaises(RuntimeError):
            self.inbox.apply_once("portfolio", "e1", boom)
        self.assertFalse(self.inbox.was_applied("portfolio", "e1"))

        self.assertEqual(self.inbox.apply_once("portfolio", "e1", self._mutate), "done")
        self.assertEqual(len(self.applied), 1)


# ----------------------------------------------------------- observability


class TestCorrelation(unittest.TestCase):
    def test_scope_allocates_and_restores(self):
        self.assertIsNone(current_correlation_id())
        with correlation_scope() as cid:
            self.assertEqual(current_correlation_id(), cid)
        self.assertIsNone(current_correlation_id())

    def test_nested_scopes_do_not_leak(self):
        with correlation_scope() as outer:
            with correlation_scope() as inner:
                self.assertNotEqual(outer, inner)
            self.assertEqual(current_correlation_id(), outer)


# ------------------------------------------------------------- CI guards


class TestGuardsAreWired(unittest.TestCase):
    """The Phase 1 gate: boundaries + clock enforced in CI.

    Runs the guards as subprocesses so the test asserts the exact thing CI asserts.
    """

    def _run(self, tool: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / tool)],
            cwd=REPO,
            capture_output=True,
            text=True,
        )

    def test_clock_access_guard_passes(self):
        r = self._run("check_clock_access.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_import_boundary_guard_passes(self):
        r = self._run("check_import_boundaries.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_temporal_repository_guard_passes(self):
        r = self._run("check_temporal_repository.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
