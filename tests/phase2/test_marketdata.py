"""Phase 2 canonical market-data tests.

Driven by **synthetic** fixtures (`tests/fixtures/synthetic/`). These verify *our*
normalization, identity, idempotency and lifecycle logic. They verify **nothing** about
Upstox's actual behaviour — see that directory's README and the external verification
checklist.

`unittest` so the suite runs with no third-party package installed; pytest collects
`TestCase` natively.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.core.clock import FrozenClock, utc
from oipulse.core.errors import TemporalBoundError
from oipulse.core.timemode import KnowledgeAt, MarketTruthAt, TradableInformationAt
from oipulse.dataquality.issues import IssueSeverity, IssueType
from oipulse.instruments.models import (
    Exchange,
    Expiry,
    ExpiryType,
    Instrument,
    InstrumentType,
    InstrumentVersion,
    OptionType,
    VendorMapping,
)
from oipulse.instruments.universe import (
    AtmRange,
    DataMode,
    FrontN,
    InstrumentUniverse,
    MonthlyN,
    WeeklyExpiries,
)
from oipulse.marketdata.identity import (
    IdentityConfidence,
    IdentityTier,
    resolve_identity,
)
from oipulse.marketdata.lifecycle import (
    ConnectionState,
    GapKind,
    SessionManager,
)
from oipulse.marketdata.observations import HistoricalDailyOI, ObservationKind
from oipulse.marketdata.providers.upstox.normalize import (
    TIMESTAMP_FALLBACK_KEY,
    normalize_chain_row,
    normalize_historical_oi,
    normalize_ws_tick,
)
from oipulse.marketdata.providers.upstox.provider import (
    InstrumentResolver,
)
from oipulse.marketdata.providers.upstox.schemas import parse_chain_response
from oipulse.marketdata.providers.upstox.ws import extract_identity_hints
from oipulse.marketdata.ratelimit import (
    EndpointClass,
    RateLimitGovernor,
)
from oipulse.marketdata.recovery import (
    CoherenceMode,
    RecoveryTrigger,
    assess_divergence,
    coherence_mode_for,
    plan_recovery,
)
from oipulse.marketdata.store.memory import InMemoryObservationStore
from oipulse.marketdata.subscription import (
    CapacityVerdict,
    DegradationStep,
    SubscriptionBudget,
    SubscriptionPlanner,
    SubscriptionRequest,
)
from tests.fixtures.synthetic import (
    SYNTHETIC_CHAIN_MULTI_EXPIRY,
    SYNTHETIC_CHAIN_RESPONSE,
    SYNTHETIC_CHAIN_WITH_MALFORMED_ROW,
    SYNTHETIC_HISTORICAL_OI,
    SYNTHETIC_WS_TICK_NO_IDENTITY,
    SYNTHETIC_WS_TICK_WITH_IDENTITY,
)

SESSION_OPEN = time(3, 45)  # 09:15 IST
SESSION_CLOSE = time(10, 0)  # 15:30 IST


def _clock(h: int = 6, m: int = 0, s: int = 0) -> FrozenClock:
    return FrozenClock(utc(2026, 3, 3, h, m, s))


# ------------------------------------------------- canonical instrument identity


class TestInstrumentIdentity(unittest.TestCase):
    def test_identity_is_separate_from_metadata_version(self):
        """A lot-size revision creates a new version; identity is untouched."""
        march = InstrumentVersion(
            1, utc(2026, 1, 1), utc(2026, 6, 1), "NIFTY", Exchange.NFO, 50, Decimal("0.05")
        )
        july = InstrumentVersion(
            1, utc(2026, 6, 1), None, "NIFTY", Exchange.NFO, 75, Decimal("0.05")
        )
        self.assertTrue(march.covers(utc(2026, 3, 3)))
        self.assertFalse(july.covers(utc(2026, 3, 3)))
        self.assertEqual(march.lot_size, 50)
        self.assertEqual(july.lot_size, 75)

    def test_march_exposure_uses_march_lot_size(self):
        versions = [
            InstrumentVersion(
                1, utc(2026, 1, 1), utc(2026, 6, 1), "N", Exchange.NFO, 50, Decimal("0.05")
            ),
            InstrumentVersion(1, utc(2026, 6, 1), None, "N", Exchange.NFO, 75, Decimal("0.05")),
        ]
        at_march = next(v for v in versions if v.covers(utc(2026, 3, 3)))
        self.assertEqual(at_march.lot_size, 50, "history must not be rewritten by a revision")

    def test_vendor_key_is_external_and_temporal(self):
        m = VendorMapping(1, "upstox", "NSE_INDEX|Nifty 50", utc(2026, 1, 1), utc(2026, 6, 1))
        self.assertTrue(m.covers(utc(2026, 3, 3)))
        self.assertFalse(m.covers(utc(2026, 7, 3)))

    def test_reissued_vendor_key_resolves_to_the_right_instrument(self):
        r = InstrumentResolver()
        r.register("CE", 101, utc(2026, 1, 1))
        r.register("CE", 999, utc(2026, 6, 1))  # vendor reissues
        self.assertEqual(r.resolve("CE", utc(2026, 3, 3)), 101)
        self.assertEqual(r.resolve("CE", utc(2026, 7, 3)), 999)

    def test_option_requires_its_defining_attributes(self):
        with self.assertRaises(ValueError):
            Instrument(id=1, instrument_type=InstrumentType.OPTION)

    def test_negative_or_zero_strike_rejected(self):
        with self.assertRaises(ValueError):
            Instrument(
                id=1,
                instrument_type=InstrumentType.OPTION,
                underlying_id=2,
                expiry_id=3,
                strike=Decimal("0"),
                option_type=OptionType.CALL,
            )

    def test_overlapping_versions_are_invalid_by_construction(self):
        with self.assertRaises(ValueError):
            InstrumentVersion(
                1, utc(2026, 6, 1), utc(2026, 1, 1), "N", Exchange.NFO, 50, Decimal("0.05")
            )


# ----------------------------------------------------------------- multi-expiry


class TestMultiExpiry(unittest.TestCase):
    def setUp(self):
        self.expiries = [
            Expiry(1, 100, date(2026, 3, 5), ExpiryType.WEEKLY),
            Expiry(2, 100, date(2026, 3, 12), ExpiryType.WEEKLY),
            Expiry(3, 100, date(2026, 3, 19), ExpiryType.WEEKLY),
            Expiry(4, 100, date(2026, 3, 26), ExpiryType.MONTHLY),
            Expiry(5, 100, date(2026, 2, 26), ExpiryType.MONTHLY),  # expired
        ]
        self.as_of = date(2026, 3, 3)

    def test_front_n_selects_more_than_the_front_expiry(self):
        got = FrontN(3).select(self.expiries, self.as_of)
        self.assertEqual([e.expiry_date.day for e in got], [5, 12, 19])

    def test_expired_expiries_excluded(self):
        got = FrontN(10).select(self.expiries, self.as_of)
        self.assertNotIn(date(2026, 2, 26), [e.expiry_date for e in got])

    def test_monthly_selector_enables_term_structure(self):
        got = MonthlyN(2).select(self.expiries, self.as_of)
        self.assertEqual([e.expiry_date for e in got], [date(2026, 3, 26)])

    def test_weekly_selector(self):
        self.assertEqual(len(WeeklyExpiries().select(self.expiries, self.as_of)), 3)

    def test_days_to_expiry_is_derived_not_stored(self):
        self.assertEqual(self.expiries[0].days_to_expiry(self.as_of), 2)

    def test_universe_is_system_level_not_user_scoped(self):
        u = InstrumentUniverse(
            name="nifty-core",
            underlying_ids=(100,),
            expiry_selector=FrontN(3),
            strike_selector=AtmRange(10),
        )
        self.assertFalse(hasattr(u, "user_id"))

    def test_multi_expiry_chains_normalize_independently(self):
        clock = _clock()
        total = 0
        for body in SYNTHETIC_CHAIN_MULTI_EXPIRY.values():
            rows, rejected = parse_chain_response(body)
            self.assertEqual(rejected, [])
            for i, row in enumerate(rows):
                total += len(
                    normalize_chain_row(
                        row, call_instrument_id=i * 2, put_instrument_id=i * 2 + 1, clock=clock
                    )
                )
        self.assertEqual(total, 2 * 3 * 4, "2 expiries x 3 strikes x (CE,PE) x (quote,greeks)")


# ----------------------------------------------------------------- normalization


class TestNormalization(unittest.TestCase):
    def setUp(self):
        self.clock = _clock()
        self.rows, _ = parse_chain_response(SYNTHETIC_CHAIN_RESPONSE)

    def test_paired_row_becomes_per_leg_observations(self):
        obs = normalize_chain_row(
            self.rows[0], call_instrument_id=101, put_instrument_id=102, clock=self.clock
        )
        self.assertEqual(len(obs), 4)
        self.assertEqual({o.instrument_id for o in obs}, {101, 102})

    def test_all_greeks_are_persisted_not_just_iv(self):
        obs = normalize_chain_row(
            self.rows[0], call_instrument_id=101, put_instrument_id=None, clock=self.clock
        )
        g = next(o for o in obs if o.kind is ObservationKind.GREEKS)
        for field_name in ("iv", "delta", "gamma", "theta", "vega"):
            self.assertIsNotNone(getattr(g, field_name), f"{field_name} was discarded")

    def test_quotes_capture_bid_ask_and_provider_prev_oi(self):
        obs = normalize_chain_row(
            self.rows[0], call_instrument_id=101, put_instrument_id=None, clock=self.clock
        )
        q = next(o for o in obs if o.kind is ObservationKind.QUOTE)
        self.assertIsNotNone(q.bid)
        self.assertIsNotNone(q.ask)
        self.assertIsNotNone(q.provider_prev_oi)

    def test_unknown_vendor_fields_preserved_in_raw_extra(self):
        body = {
            "data": [
                {
                    "expiry": "2026-03-05",
                    "strike_price": 25000,
                    "call_options": {
                        "instrument_key": "CE",
                        "market_data": {"ltp": 1.0, "brand_new_field": "keep"},
                    },
                }
            ]
        }
        rows, _ = parse_chain_response(body)
        obs = normalize_chain_row(
            rows[0], call_instrument_id=1, put_instrument_id=None, clock=self.clock
        )
        self.assertEqual(obs[0].raw_extra.get("brand_new_field"), "keep")

    def test_values_are_decimal_not_float(self):
        obs = normalize_chain_row(
            self.rows[0], call_instrument_id=101, put_instrument_id=None, clock=self.clock
        )
        q = next(o for o in obs if o.kind is ObservationKind.QUOTE)
        self.assertIsInstance(q.ltp, Decimal)

    def test_malformed_rows_are_reported_not_silently_skipped(self):
        rows, rejected = parse_chain_response(SYNTHETIC_CHAIN_WITH_MALFORMED_ROW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rejected), 3)

    def test_negative_oi_is_rejected(self):
        body = {
            "data": [
                {
                    "expiry": "2026-03-05",
                    "strike_price": 25000,
                    "call_options": {"instrument_key": "CE", "market_data": {"oi": -5}},
                }
            ]
        }
        rows, _ = parse_chain_response(body)
        with self.assertRaises(ValueError):
            normalize_chain_row(
                rows[0], call_instrument_id=1, put_instrument_id=None, clock=self.clock
            )


# ------------------------------------------------------------ timestamp semantics


class TestTimestampSemantics(unittest.TestCase):
    """Requirement A: observed_at is never a substitute for ingestion time."""

    def test_venue_and_receipt_times_are_distinct(self):
        clock = FrozenClock(utc(2026, 3, 3, 11, 44))
        rows, _ = parse_chain_response(SYNTHETIC_CHAIN_RESPONSE)
        obs = normalize_chain_row(
            rows[0],
            call_instrument_id=101,
            put_instrument_id=None,
            clock=clock,
            venue_timestamp=utc(2026, 3, 3, 11, 40),
        )
        o = obs[0]
        self.assertEqual(o.observed_at, utc(2026, 3, 3, 11, 40))
        self.assertEqual(o.ingested_at, utc(2026, 3, 3, 11, 44))
        self.assertEqual(o.ingestion_lag_seconds, 240.0)

    def test_the_canonical_1140_1144_fixture(self):
        """05 §3: the two modes must diverge for a late-arriving observation."""
        clock = FrozenClock(utc(2026, 3, 3, 11, 44))
        rows, _ = parse_chain_response(SYNTHETIC_CHAIN_RESPONSE)
        obs = normalize_chain_row(
            rows[0],
            call_instrument_id=101,
            put_instrument_id=None,
            clock=clock,
            venue_timestamp=utc(2026, 3, 3, 11, 40),
        )
        store = InMemoryObservationStore()
        store.append(obs)
        at = utc(2026, 3, 3, 11, 42)

        self.assertTrue(store.fetch(MarketTruthAt(at, utc(2026, 3, 3, 13, 0)), instrument_id=101))
        self.assertFalse(store.fetch(KnowledgeAt(at), instrument_id=101))
        self.assertTrue(store.fetch(KnowledgeAt(utc(2026, 3, 3, 11, 45)), instrument_id=101))

    def test_missing_venue_timestamp_is_recorded_not_hidden(self):
        """Assumption A-3: the substitution must be visible on the row."""
        clock = _clock()
        rows, _ = parse_chain_response(SYNTHETIC_CHAIN_RESPONSE)
        obs = normalize_chain_row(
            rows[0], call_instrument_id=101, put_instrument_id=None, clock=clock
        )
        self.assertIn(TIMESTAMP_FALLBACK_KEY, obs[0].raw_extra)

    def test_naive_datetime_rejected(self):
        from datetime import datetime as _dt

        from oipulse.core.clock import ensure_utc

        with self.assertRaises(ValueError):
            ensure_utc(_dt(2026, 3, 3))


# ------------------------------------------------- historical daily OI granularity


class TestHistoricalDailyOI(unittest.TestCase):
    """Requirement B: daily OI must stay distinct from intraday observations."""

    def _build(self, ingested_h: int = 2) -> HistoricalDailyOI:
        return normalize_historical_oi(
            SYNTHETIC_HISTORICAL_OI["data"][0],
            instrument_id=101,
            trade_date=date(2026, 1, 15),
            session_open=SESSION_OPEN,
            session_close=SESSION_CLOSE,
            clock=FrozenClock(utc(2026, 3, 1, ingested_h, 0)),
        )

    def test_carries_date_granularity_and_an_interval(self):
        o = self._build()
        self.assertEqual(o.kind, ObservationKind.HISTORICAL_DAILY_OI)
        self.assertEqual(o.observation_date, date(2026, 1, 15))
        self.assertIsNotNone(o.valid_from)
        self.assertIsNotNone(o.valid_to)

    def test_cannot_be_constructed_without_an_interval(self):
        from oipulse.marketdata.identity import resolve_identity as ri

        with self.assertRaises(ValueError):
            HistoricalDailyOI(
                instrument_id=1,
                observed_at=utc(2026, 1, 15, 10),
                ingested_at=utc(2026, 3, 1),
                source="rest_hist_oi",
                identity=ri(
                    instrument_id=1, observed_at=utc(2026, 1, 15, 10), source="x", payload={}
                ),
                observation_date=date(2026, 1, 15),
            )

    def test_interval_covers_the_session_not_an_instant(self):
        o = self._build()
        self.assertTrue(o.covers_instant(utc(2026, 1, 15, 6, 0)))
        self.assertFalse(o.covers_instant(utc(2026, 1, 16, 6, 0)))

    def test_backfill_is_invisible_to_earlier_knowledge_queries(self):
        """ingested_at is the backfill run time, so it cannot contaminate the past."""
        o = self._build()
        store = InMemoryObservationStore()
        store.append([o])
        self.assertFalse(store.fetch(KnowledgeAt(utc(2026, 1, 15, 11, 0)), instrument_id=101))
        self.assertTrue(store.fetch(KnowledgeAt(utc(2026, 3, 1, 3, 0)), instrument_id=101))

    def test_distinct_source_keeps_it_separable_forever(self):
        self.assertEqual(self._build().source.value, "rest_hist_oi")


# ------------------------------------------------ provider / event identity (A-1)


class TestObservationIdentity(unittest.TestCase):
    def test_provider_event_id_is_the_strongest_tier(self):
        i = resolve_identity(
            instrument_id=1,
            observed_at=utc(2026, 3, 3),
            source="ws",
            payload={"ltp": 1},
            provider_event_id="evt-1",
        )
        self.assertEqual(i.tier, IdentityTier.PROVIDER_EVENT_ID)
        self.assertEqual(i.confidence, IdentityConfidence.STRONG)

    def test_feed_sequence_is_the_second_tier(self):
        i = resolve_identity(
            instrument_id=1,
            observed_at=utc(2026, 3, 3),
            source="ws",
            payload={"ltp": 1},
            feed_session_id="s1",
            channel="c",
            channel_sequence=7,
        )
        self.assertEqual(i.tier, IdentityTier.FEED_SEQUENCE)
        self.assertEqual(i.confidence, IdentityConfidence.STRONG)

    def test_content_hash_fallback_is_weak(self):
        """A-1: when the provider supplies neither, we say so rather than assume."""
        i = resolve_identity(
            instrument_id=1, observed_at=utc(2026, 3, 3), source="ws", payload={"ltp": 1}
        )
        self.assertEqual(i.tier, IdentityTier.CONTENT_HASH)
        self.assertEqual(i.confidence, IdentityConfidence.WEAK)
        self.assertFalse(i.confidence.supports_sequence_gap_detection)

    def test_feed_sequence_identity_is_session_scoped(self):
        """Constraint C: a sequence means nothing outside its session."""
        i = resolve_identity(
            instrument_id=1,
            observed_at=utc(2026, 3, 3),
            source="ws",
            payload={"ltp": 1},
            feed_session_id="s1",
            channel="c",
            channel_sequence=7,
        )
        self.assertTrue(i.is_session_scoped)
        self.assertIn("s1", i.dedup_key)

    def test_distinct_events_at_one_timestamp_have_distinct_identities(self):
        a = resolve_identity(
            instrument_id=1, observed_at=utc(2026, 3, 3), source="ws", payload={"ltp": 1}
        )
        b = resolve_identity(
            instrument_id=1, observed_at=utc(2026, 3, 3), source="ws", payload={"ltp": 2}
        )
        self.assertNotEqual(a.dedup_key, b.dedup_key)

    def test_identical_content_yields_identical_identity(self):
        kw = {
            "instrument_id": 1,
            "observed_at": utc(2026, 3, 3),
            "source": "ws",
            "payload": {"ltp": 1},
        }
        self.assertEqual(resolve_identity(**kw).dedup_key, resolve_identity(**kw).dedup_key)

    def test_ws_hint_extraction_tolerates_absence(self):
        self.assertEqual(extract_identity_hints(SYNTHETIC_WS_TICK_NO_IDENTITY), (None, None, None))
        eid, seq, _ = extract_identity_hints(SYNTHETIC_WS_TICK_WITH_IDENTITY)
        self.assertEqual((eid, seq), ("evt-000001", 1))


# ------------------------------------------------------ ingestion / idempotency


class TestIngestionIdempotency(unittest.TestCase):
    """Requirements G and H: replay and restart must not duplicate."""

    def _chain_observations(self, clock):
        rows, _ = parse_chain_response(SYNTHETIC_CHAIN_RESPONSE)
        out = []
        for i, row in enumerate(rows):
            out.extend(
                normalize_chain_row(
                    row, call_instrument_id=i * 2, put_instrument_id=i * 2 + 1, clock=clock
                )
            )
        return out

    def test_replaying_a_batch_inserts_nothing_new(self):
        clock = _clock()
        obs = self._chain_observations(clock)
        store = InMemoryObservationStore()
        first = store.append(obs)
        second = store.append(obs)
        third = store.append(obs)
        self.assertEqual(first.duplicates, 0)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(third.inserted, 0)
        self.assertEqual(store.count(), first.inserted)

    def test_restart_does_not_duplicate(self):
        """A fresh collector re-fetching the same instant writes nothing new."""
        clock = _clock()
        store = InMemoryObservationStore()
        store.append(self._chain_observations(clock))
        before = store.count()
        store.append(self._chain_observations(clock))  # simulated restart
        self.assertEqual(store.count(), before)

    def test_reconnect_overlap_does_not_duplicate(self):
        """REST recovery and the resumed WS stream may cover the same instant."""
        clock = _clock()
        store = InMemoryObservationStore()
        ws = normalize_ws_tick(
            SYNTHETIC_WS_TICK_WITH_IDENTITY,
            instrument_id=101,
            clock=clock,
            feed_session_id="s1",
            channel="option_chain",
            provider_event_id="evt-000001",
            channel_sequence=1,
        )
        store.append(ws)
        store.append(ws)  # overlapping recovery delivers it again
        self.assertEqual(store.count(), len(ws))

    def test_genuinely_different_ticks_are_both_kept(self):
        clock = _clock()
        store = InMemoryObservationStore()
        a = normalize_ws_tick(
            SYNTHETIC_WS_TICK_WITH_IDENTITY,
            instrument_id=101,
            clock=clock,
            feed_session_id="s1",
            channel="c",
            provider_event_id="evt-1",
            channel_sequence=1,
        )
        b = normalize_ws_tick(
            SYNTHETIC_WS_TICK_WITH_IDENTITY,
            instrument_id=101,
            clock=clock,
            feed_session_id="s1",
            channel="c",
            provider_event_id="evt-2",
            channel_sequence=2,
        )
        store.append(a)
        store.append(b)
        self.assertEqual(store.count(), len(a) + len(b))

    def test_raw_store_refuses_availability_semantics(self):
        with self.assertRaises(TemporalBoundError):
            InMemoryObservationStore().fetch(TradableInformationAt(utc(2026, 3, 3)))


# ------------------------------------------------ websocket lifecycle and gaps


class TestWebSocketLifecycle(unittest.TestCase):
    def setUp(self):
        self.clock = _clock()
        counter = iter(f"sess-{i}" for i in range(1, 50))
        self.mgr = SessionManager(self.clock, session_id_factory=lambda: next(counter))

    def _to_streaming(self):
        for s in (
            ConnectionState.CONNECTING,
            ConnectionState.AUTHENTICATING,
            ConnectionState.SUBSCRIBING,
            ConnectionState.STREAMING,
        ):
            self.mgr.transition(s)

    def test_lifecycle_order_is_enforced(self):
        self._to_streaming()
        self.assertEqual(self.mgr.state, ConnectionState.STREAMING)

    def test_illegal_transition_raises(self):
        self._to_streaming()
        with self.assertRaises(ValueError):
            self.mgr.transition(ConnectionState.IDLE)

    def test_in_session_sequence_gap_detected(self):
        self._to_streaming()
        self.mgr.open_session()
        for seq in (1, 2, 3):
            self.assertIsNone(self.mgr.record_message("c", seq, IdentityConfidence.STRONG))
        gap = self.mgr.record_message("c", 7, IdentityConfidence.STRONG)
        self.assertIsNotNone(gap)
        self.assertEqual(gap.kind, GapKind.WEBSOCKET_GAP)
        self.assertEqual(gap.missing_count, 3)

    def test_sequence_reset_across_reconnect_is_not_a_gap(self):
        """Constraint C: channel_sequence is only meaningful within a session."""
        self._to_streaming()
        self.mgr.open_session()
        self.mgr.record_message("c", 500, IdentityConfidence.STRONG)
        self.mgr.transition(ConnectionState.DISCONNECTED)
        self.mgr.close_session()
        self.mgr.transition(ConnectionState.CONNECTING)
        self.mgr.open_session()
        self.assertIsNone(self.mgr.record_message("c", 1, IdentityConfidence.STRONG))

    def test_reconnect_records_the_outage_window(self):
        self._to_streaming()
        self.mgr.open_session()
        self.clock.set(utc(2026, 3, 3, 6, 0, 30))
        self.mgr.transition(ConnectionState.DISCONNECTED)
        self.mgr.close_session()
        self.clock.set(utc(2026, 3, 3, 6, 0, 35))
        self.mgr.transition(ConnectionState.CONNECTING)
        self.mgr.open_session()
        gap = next(g for g in self.mgr.gaps if g.kind is GapKind.RECONNECT_GAP)
        self.assertEqual((gap.window_end - gap.window_start), timedelta(seconds=5))

    def test_weak_identity_claims_no_sequence_gap(self):
        """Constraint D: no guarantee beyond what the identity demonstrates."""
        self._to_streaming()
        self.mgr.open_session()
        self.mgr.record_message("c", 1, IdentityConfidence.WEAK)
        self.assertIsNone(self.mgr.record_message("c", 900, IdentityConfidence.WEAK))
        self.assertEqual([g for g in self.mgr.gaps if g.kind is GapKind.WEBSOCKET_GAP], [])

    def test_session_ordinals_are_monotonic_and_stored(self):
        self._to_streaming()
        a = self.mgr.open_session()
        self.mgr.transition(ConnectionState.DISCONNECTED)
        self.mgr.close_session()
        self.mgr.transition(ConnectionState.CONNECTING)
        b = self.mgr.open_session()
        self.assertLess(a.ordinal, b.ordinal)
        self.assertEqual(self.mgr.session_ordinal(a.session_id), a.ordinal)

    def test_watchdog_detects_a_silent_feed(self):
        self._to_streaming()
        self.mgr.open_session()
        self.mgr.record_message("c", 1, IdentityConfidence.STRONG)
        self.clock.set(utc(2026, 3, 3, 6, 1, 0))
        verdict = self.mgr.check_watchdog()
        self.assertTrue(verdict.stale)
        self.assertEqual(self.mgr.record_stale().kind, GapKind.STALE_FEED)

    def test_duplicate_or_late_sequence_does_not_lower_the_watermark(self):
        self._to_streaming()
        self.mgr.open_session()
        self.mgr.record_message("c", 5, IdentityConfidence.STRONG)
        self.mgr.record_message("c", 3, IdentityConfidence.STRONG)  # late
        self.assertIsNone(self.mgr.record_message("c", 6, IdentityConfidence.STRONG))


# ------------------------------------------------------- recovery and coherence


class TestRecoveryAndCoherence(unittest.TestCase):
    def test_coherence_modes(self):
        now = utc(2026, 3, 3, 6, 5)
        budget = timedelta(minutes=2)
        self.assertEqual(
            coherence_mode_for(
                now=now, anchor_observed_at=utc(2026, 3, 3, 6, 4, 30), anchor_max_age=budget
            ),
            CoherenceMode.SNAPSHOT_ANCHORED,
        )
        self.assertEqual(
            coherence_mode_for(
                now=now, anchor_observed_at=utc(2026, 3, 3, 6, 0), anchor_max_age=budget
            ),
            CoherenceMode.SNAPSHOT_STALE,
        )
        self.assertEqual(
            coherence_mode_for(now=now, anchor_observed_at=None, anchor_max_age=budget),
            CoherenceMode.STREAM_ONLY,
        )
        self.assertEqual(
            coherence_mode_for(
                now=now, anchor_observed_at=now, anchor_max_age=budget, recovering=True
            ),
            CoherenceMode.RECOVERING,
        )

    def test_divergence_detected_beyond_tolerance(self):
        report = assess_divergence(
            {101: {"ltp": Decimal("120.50")}, 102: {"ltp": Decimal("98.20")}},
            {101: {"ltp": Decimal("120.55")}, 102: {"ltp": Decimal("91.00")}},
        )
        self.assertEqual(report.diverged, 1)
        self.assertEqual(report.instrument_ids, (102,))
        self.assertTrue(report.material)

    def test_no_divergence_within_tolerance(self):
        report = assess_divergence({101: {"ltp": Decimal("100")}}, {101: {"ltp": Decimal("100.2")}})
        self.assertFalse(report.material)

    def test_gap_triggers_out_of_band_recovery_and_records_the_hole(self):
        clock = _clock()
        mgr = SessionManager(clock, session_id_factory=lambda: "s1")
        for s in (
            ConnectionState.CONNECTING,
            ConnectionState.AUTHENTICATING,
            ConnectionState.SUBSCRIBING,
            ConnectionState.STREAMING,
        ):
            mgr.transition(s)
        mgr.open_session()
        mgr.record_message("c", 1, IdentityConfidence.STRONG)
        gap = mgr.record_message("c", 5, IdentityConfidence.STRONG)

        plan = plan_recovery(gap, clock=clock, underlying_ids=(1,), expiry_ids=(10, 11))
        self.assertEqual(plan.trigger, RecoveryTrigger.WEBSOCKET_GAP)
        self.assertTrue(plan.out_of_band)
        issue = plan.issues[0]
        self.assertEqual(issue.type, IssueType.WEBSOCKET_GAP)
        self.assertEqual(issue.severity, IssueSeverity.DEGRADED)
        self.assertTrue(issue.blocks_analytics)
        self.assertEqual(issue.context["missing_count"], 3)


# --------------------------------------------------------- subscription planner


class TestSubscriptionPlanner(unittest.TestCase):
    """Requirement E: capacity is deterministic before subscribing."""

    def _request(self, full=0, greeks=0, ltpc=0, protected=0):
        by_mode, i = {}, 0
        for mode, n in ((DataMode.FULL, full), (DataMode.GREEKS, greeks), (DataMode.LTPC, ltpc)):
            if n:
                by_mode[mode] = tuple(range(i, i + n))
                i += n
        return SubscriptionRequest(by_mode=by_mode, protected=frozenset(range(protected)))

    def test_small_universe_accepted(self):
        result = SubscriptionPlanner().plan(self._request(greeks=500))
        self.assertEqual(result.verdict, CapacityVerdict.ACCEPTED)
        self.assertEqual(result.planned_total, 500)
        self.assertTrue(result.ok_to_subscribe)

    def test_oversized_universe_degrades_and_records_why(self):
        result = SubscriptionPlanner().plan(self._request(greeks=3000, protected=5))
        self.assertEqual(result.verdict, CapacityVerdict.DEGRADED)
        self.assertLess(result.planned_total, result.requested_total)
        self.assertTrue(result.degradations, "degradation must be recorded, never silent")
        self.assertTrue(result.dropped_count)

    def test_depth_is_shed_before_coverage(self):
        result = SubscriptionPlanner().plan(self._request(full=2000, greeks=100, protected=3))
        self.assertEqual(result.verdict, CapacityVerdict.DEGRADED)
        self.assertIn(DegradationStep.DROP_DEPTH_FAR_STRIKES, result.degradations)

    def test_impossible_universe_refuses_rather_than_truncating(self):
        result = SubscriptionPlanner().plan(self._request(greeks=50000, protected=50000))
        self.assertEqual(result.verdict, CapacityVerdict.UNSATISFIABLE)
        self.assertFalse(result.ok_to_subscribe)
        self.assertEqual(result.allocations, ())

    def test_protected_instruments_are_never_dropped(self):
        request = self._request(greeks=4000, protected=10)
        result = SubscriptionPlanner().plan(request)
        self.assertFalse(set(result.dropped_instruments) & request.protected)

    def test_budget_is_configuration_not_a_constant(self):
        tiny = SubscriptionPlanner(
            SubscriptionBudget(
                max_connections=1, max_subscriptions_ltpc_greeks=100, max_subscriptions_full=50
            )
        )
        self.assertEqual(tiny.plan(self._request(greeks=50)).verdict, CapacityVerdict.ACCEPTED)
        self.assertNotEqual(
            tiny.plan(self._request(greeks=5000, protected=2)).verdict, CapacityVerdict.ACCEPTED
        )

    def test_budget_declares_its_provenance(self):
        self.assertIn("assumption", SubscriptionBudget().source.lower())

    def test_allocation_never_mixes_full_with_the_shared_pool(self):
        result = SubscriptionPlanner().plan(self._request(full=100, greeks=100))
        for allocation in result.allocations:
            self.assertIsInstance(allocation.mode, DataMode)
            self.assertTrue(allocation.instrument_ids)

    def test_reserve_is_held_back(self):
        budget = SubscriptionBudget(reserve_fraction=0.10, max_subscriptions_ltpc_greeks=1000)
        self.assertEqual(budget.usable(DataMode.GREEKS), 900)

    def test_result_reports_the_numbers_it_used(self):
        result = SubscriptionPlanner().plan(self._request(greeks=500))
        self.assertEqual(result.budget.max_subscriptions_ltpc_greeks, 2000)
        self.assertIn("accepted", result.summary().lower())


# ------------------------------------------------------ rate limit / capacity


class TestRateLimitGovernance(unittest.TestCase):
    def setUp(self):
        self.clock = _clock()
        self.gov = RateLimitGovernor(self.clock)

    def test_budget_exhausts_then_recovers(self):
        policy = self.gov.policy(EndpointClass.OPTION_CHAIN)
        for _ in range(policy.requests):
            self.assertTrue(self.gov.acquire(EndpointClass.OPTION_CHAIN).granted)
        blocked = self.gov.acquire(EndpointClass.OPTION_CHAIN)
        self.assertFalse(blocked.granted)
        self.assertGreater(blocked.wait, timedelta(0))
        self.clock.set(utc(2026, 3, 3, 6, 0, 2))
        self.assertTrue(self.gov.acquire(EndpointClass.OPTION_CHAIN).granted)

    def test_429_penalises_the_whole_endpoint_class(self):
        self.gov.penalise(EndpointClass.OPTION_CHAIN, timedelta(seconds=5))
        decision = self.gov.acquire(EndpointClass.OPTION_CHAIN)
        self.assertFalse(decision.granted)
        self.assertIn("backoff", decision.reason)

    def test_endpoint_classes_have_independent_budgets(self):
        for _ in range(self.gov.policy(EndpointClass.OPTION_CHAIN).requests):
            self.gov.acquire(EndpointClass.OPTION_CHAIN)
        self.assertTrue(self.gov.acquire(EndpointClass.QUOTE).granted)

    def test_cadence_derives_from_budget_so_expiries_cost_visibly(self):
        few = self.gov.chain_poll_interval(3)
        many = self.gov.chain_poll_interval(12)
        self.assertGreater(many, few)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ------------------------------------------------- collector recovery wiring


class TestCollectorRecovery(unittest.TestCase):
    """Detecting a gap and not acting on it leaves the state degraded silently."""

    def setUp(self):
        from oipulse.marketdata.collector import CanonicalCollector

        self.clock = _clock()
        self.sessions = SessionManager(self.clock, session_id_factory=lambda: "s1")
        self.store = InMemoryObservationStore()
        self.collector = CanonicalCollector(
            self.clock,
            SubscriptionPlanner(),
            RateLimitGovernor(self.clock),
            self.sessions,
            self.store,
        )
        for state in (
            ConnectionState.CONNECTING,
            ConnectionState.AUTHENTICATING,
            ConnectionState.SUBSCRIBING,
            ConnectionState.STREAMING,
        ):
            self.sessions.transition(state)
        self.sessions.open_session()

    def test_gaps_drain_exactly_once(self):
        """One discontinuity must trigger one recovery, not one per subsequent frame."""
        self.sessions.record_message("c", 1, IdentityConfidence.STRONG)
        self.sessions.record_message("c", 5, IdentityConfidence.STRONG)
        first = self.sessions.drain_new_gaps()
        second = self.sessions.drain_new_gaps()
        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())

    def test_drained_gaps_remain_in_the_permanent_record(self):
        """Draining marks a gap handled, never forgotten: research must still see it."""
        self.sessions.record_message("c", 1, IdentityConfidence.STRONG)
        self.sessions.record_message("c", 5, IdentityConfidence.STRONG)
        self.sessions.drain_new_gaps()
        self.assertEqual(len([g for g in self.sessions.gaps if g.kind is GapKind.WEBSOCKET_GAP]), 1)

    def test_recovery_records_a_quality_issue(self):
        import asyncio

        self.sessions.record_message("c", 1, IdentityConfidence.STRONG)
        self.sessions.record_message("c", 5, IdentityConfidence.STRONG)
        gap = self.sessions.drain_new_gaps()[0]
        plan = asyncio.run(self.collector.recover_after_gap(gap, (1,), (10,)))

        self.assertEqual(plan.trigger, RecoveryTrigger.WEBSOCKET_GAP)
        self.assertTrue(plan.out_of_band)
        issues = self.collector.drain_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].type, IssueType.WEBSOCKET_GAP)
        self.assertEqual(self.collector.drain_issues(), (), "issues drain once")

    def test_plan_carries_the_scope_recovery_needs(self):
        plan = self.collector.plan(
            SubscriptionRequest({DataMode.GREEKS: (1, 2, 3)}),
            ["k1", "k2", "k3"],
            DataMode.GREEKS,
            chain_count=2,
            underlying_ids=(100,),
            expiry_ids=(10, 11),
        )
        self.assertEqual(plan.underlying_ids, (100,))
        self.assertEqual(plan.expiry_ids, (10, 11))
