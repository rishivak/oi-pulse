"""Ingestor runtime and readiness probes — P1.

External verification found `--role ingestor` exiting 4 ("no runtime yet") and readiness
probing nothing at all. Both are now implemented, and these tests cover the parts that
can be executed offline: configuration refusal, universe parsing, preflight gating,
capacity refusal, anchoring, graceful shutdown, and what readiness does and does not
include.

NOT COVERED HERE, and stated rather than implied: the live loop against
api.upstox.com, a real PostgreSQL write, and a real Redis PING. The sandbox has none of
those. Probes are exercised against injected failures and against the genuine
"dependency absent" path, which is the state of this environment.

SYNTHETIC fixtures throughout.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from oipulse.core.clock import FrozenClock
from oipulse.core.config import Settings
from oipulse.core.errors import ConfigurationError
from oipulse.instruments.universe import DataMode
from oipulse.marketdata.lifecycle import SessionManager
from oipulse.marketdata.providers.upstox.provider import (
    ExpiryBinding,
    InstrumentResolver,
    UpstoxMarketDataProvider,
)
from oipulse.marketdata.providers.upstox.schemas import parse_chain_response
from oipulse.marketdata.runtime import (
    UNIVERSE_ENV_VAR,
    IngestorRuntime,
    IngestorSpec,
    PreflightFailed,
    build_spec_from_env,
    default_session_predicate,
    parse_universe,
)
from oipulse.marketdata.store.memory import AsyncSinkAdapter, InMemoryObservationStore
from oipulse.observability.readiness import (
    ROLE_REQUIREMENTS,
    ProbeResult,
    check_runtime_dependencies,
)
from tests.fixtures.synthetic import SYNTHETIC_CHAIN_RESPONSE

_SETTINGS = Settings(
    app_env="development",
    role="ingestor",
    log_level="INFO",
    instance_id="test-1",
    database_url="postgresql+asyncpg://u:p@localhost/db",
    redis_url="redis://localhost:6379/0",
    session_secret_key="x" * 48,
    token_encryption_key="y" * 48,
)

_UNIVERSE = {
    "mode": "greeks",
    "instruments": [{"vendor_key": "NSE_FO|1", "instrument_id": 1}],
    "underlying_ids": [100],
    "expiries": [
        {
            "expiry_id": 10,
            "underlying_id": 100,
            "underlying_vendor_key": "NSE_INDEX|Nifty 50",
            "expiry": "2026-03-05",
        }
    ],
}


class _FixtureRest:
    """SYNTHETIC REST transport. Not recorded Upstox data."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, date]] = []
        self.fail = fail

    async def get_option_chain(self, instrument_key, expiry):
        self.calls.append((instrument_key, expiry))
        if self.fail:
            raise RuntimeError("synthetic transport failure")
        return SYNTHETIC_CHAIN_RESPONSE

    async def get_historical_oi(self, instrument_key, expiry, on):
        raise AssertionError("not used by the ingestor runtime")


def _chain_mappings(clock: FrozenClock) -> tuple[tuple[str, int], ...]:
    """Vendor key -> instrument id for every leg of the SYNTHETIC chain.

    A real shard's universe lists these; the fixture derives them so anchoring resolves
    the same way it would in deployment.
    """
    rows, _ = parse_chain_response(SYNTHETIC_CHAIN_RESPONSE)
    out: list[tuple[str, int]] = []
    for i, row in enumerate(rows):
        if row.call_options:
            out.append((row.call_options.instrument_key, i * 2))
        if row.put_options:
            out.append((row.put_options.instrument_key, i * 2 + 1))
    return tuple(out)


def _runtime(rest: _FixtureRest | None = None) -> tuple[IngestorRuntime, InMemoryObservationStore]:
    clock = FrozenClock(datetime(2026, 3, 2, 6, 0, tzinfo=UTC))
    sessions = SessionManager(clock, session_id_factory=lambda: "s1")
    resolver = InstrumentResolver()
    provider = UpstoxMarketDataProvider(rest or _FixtureRest(), clock, resolver, sessions)
    store = InMemoryObservationStore()
    mappings = _chain_mappings(clock)
    spec = IngestorSpec(
        vendor_keys=tuple(key for key, _ in mappings),
        mode=DataMode.GREEKS,
        underlying_ids=(100,),
        expiry_bindings=(
            ExpiryBinding(
                expiry_id=10,
                underlying_id=100,
                underlying_vendor_key="NSE_INDEX|Nifty 50",
                expiry=date(2026, 3, 5),
            ),
        ),
        instrument_mappings=mappings,
    )
    runtime = IngestorRuntime(
        _SETTINGS, spec, clock, sessions, provider, AsyncSinkAdapter(store), resolver=resolver
    )
    runtime.bind()
    return runtime, store


class TestUniverseParsing(unittest.TestCase):
    """The shard's universe is configuration. A bad one must stop the process."""

    def test_a_valid_universe_parses(self):
        spec = parse_universe(__import__("json").dumps(_UNIVERSE))
        self.assertEqual(spec.vendor_keys, ("NSE_FO|1",))
        self.assertEqual(spec.mode, DataMode.GREEKS)
        self.assertEqual(spec.expiry_ids, (10,))
        self.assertEqual(spec.instrument_mappings, (("NSE_FO|1", 1),))

    def test_missing_configuration_refuses_rather_than_defaulting(self):
        """Guessing a universe would collect something nobody asked for."""
        import os

        previous = os.environ.pop(UNIVERSE_ENV_VAR, None)
        try:
            with self.assertRaises(ConfigurationError) as ctx:
                build_spec_from_env()
            self.assertIn(UNIVERSE_ENV_VAR, str(ctx.exception))
        finally:
            if previous is not None:
                os.environ[UNIVERSE_ENV_VAR] = previous

    def test_malformed_json_is_rejected_with_the_variable_named(self):
        with self.assertRaises(ConfigurationError) as ctx:
            parse_universe("{not json")
        self.assertIn(UNIVERSE_ENV_VAR, str(ctx.exception))

    def test_an_unknown_data_mode_is_rejected(self):
        bad = {**_UNIVERSE, "mode": "telepathy"}
        with self.assertRaises(ConfigurationError) as ctx:
            parse_universe(__import__("json").dumps(bad))
        self.assertIn("mode", str(ctx.exception))

    def test_an_empty_instrument_list_is_rejected(self):
        bad = {**_UNIVERSE, "instruments": []}
        with self.assertRaises(ConfigurationError):
            parse_universe(__import__("json").dumps(bad))

    def test_a_partial_expiry_binding_is_rejected(self):
        """A partially parsed universe collects a different set than was asked for."""
        bad = {**_UNIVERSE, "expiries": [{"expiry_id": 10}]}
        with self.assertRaises(ConfigurationError):
            parse_universe(__import__("json").dumps(bad))


class TestPreflight(unittest.TestCase):
    """A process that cannot reach its durable store must refuse to start."""

    def test_preflight_fails_when_a_dependency_is_unreachable(self):
        runtime, _ = _runtime()
        with self.assertRaises(PreflightFailed) as ctx:
            asyncio.run(runtime.preflight())
        message = str(ctx.exception)
        # No PostgreSQL, Redis or driver exists in this sandbox, so every probe fails.
        self.assertIn("postgres", message)
        self.assertIn("redis", message)

    def test_preflight_reports_every_failure_not_just_the_first(self):
        """An operator fixing a deployment should see the whole list in one restart."""
        runtime, _ = _runtime()
        with self.assertRaises(PreflightFailed) as ctx:
            asyncio.run(runtime.preflight())
        self.assertGreaterEqual(str(ctx.exception).count(":"), 2)

    def test_the_secret_bearing_url_is_never_in_the_failure_message(self):
        """`database_url` carries the password."""
        runtime, _ = _runtime()
        with self.assertRaises(PreflightFailed) as ctx:
            asyncio.run(runtime.preflight())
        self.assertNotIn("p@localhost", str(ctx.exception))


class TestAnchoring(unittest.TestCase):
    """REST before stream, so the first states are snapshot-anchored."""

    def test_anchor_fetches_and_persists_every_bound_expiry(self):
        rest = _FixtureRest()
        runtime, store = _runtime(rest)
        persisted = asyncio.run(runtime.anchor())

        self.assertEqual(rest.calls, [("NSE_INDEX|Nifty 50", date(2026, 3, 5))])
        self.assertEqual(persisted, store.count())
        self.assertGreater(persisted, 0)

    def test_a_failed_anchor_degrades_rather_than_refusing_the_session(self):
        """Stream-only is worse than anchored, and far better than not collecting."""
        runtime, store = _runtime(_FixtureRest(fail=True))
        self.assertEqual(asyncio.run(runtime.anchor()), 0)
        self.assertEqual(store.count(), 0)


class TestShutdown(unittest.TestCase):
    """A killed ingestor that never closes its session leaves a hole that looks like
    a provider outage, and the distinction cannot be recovered afterwards."""

    def test_shutdown_stops_the_collector_and_closes_the_session(self):
        runtime, _ = _runtime()
        runtime._sessions.open_session()
        self.assertIsNotNone(runtime._sessions.current)

        runtime.shutdown("signal_SIGTERM")
        current = runtime._sessions.current
        self.assertTrue(current is None or not current.is_open)

    def test_shutdown_is_idempotent(self):
        """SIGTERM followed by SIGINT during a slow drain must not double-close."""
        runtime, _ = _runtime()
        runtime._sessions.open_session()
        runtime.shutdown("first")
        runtime.shutdown("second")


class TestSessionPredicate(unittest.TestCase):
    """The trading calendar is injected, never hardcoded."""

    def test_the_window_is_closed_outside_market_hours(self):
        # 03:00 UTC = 08:30 IST, before the open.
        self.assertFalse(default_session_predicate(datetime(2026, 3, 2, 3, 0, tzinfo=UTC)))

    def test_the_window_is_open_during_market_hours(self):
        # 06:00 UTC = 11:30 IST on a Monday.
        self.assertTrue(default_session_predicate(datetime(2026, 3, 2, 6, 0, tzinfo=UTC)))

    def test_the_window_is_closed_at_the_weekend(self):
        # 2026-03-07 is a Saturday.
        self.assertFalse(default_session_predicate(datetime(2026, 3, 7, 6, 0, tzinfo=UTC)))

    def test_the_predicate_is_replaceable(self):
        """A deployment with a real trading calendar injects its own."""
        spec = IngestorSpec(
            vendor_keys=("k",), mode=DataMode.GREEKS, session_predicate=lambda _at: False
        )
        self.assertFalse(spec.session_predicate(datetime(2026, 3, 2, 6, 0, tzinfo=UTC)))


class TestReadinessProbes(unittest.TestCase):
    """Readiness covers this process's dependencies — and deliberately not the provider."""

    def test_every_role_declares_its_local_requirements(self):
        for role in ("api", "ingestor", "processor", "jobs", "trader", "all"):
            with self.subTest(role=role):
                self.assertIn(role, ROLE_REQUIREMENTS)
                self.assertTrue(ROLE_REQUIREMENTS[role])

    def test_a_missing_package_fails_the_runtime_probe_by_name(self):
        result = check_runtime_dependencies("ingestor")
        self.assertIsInstance(result, ProbeResult)
        # The sandbox has no sqlalchemy/asyncpg/redis, so this is the genuine failure.
        self.assertFalse(result.ok)
        self.assertIn("sqlalchemy", result.detail)

    def test_an_unknown_role_is_a_failure_not_a_silent_pass(self):
        """A typo in --role must not produce a process that reports itself ready."""
        result = check_runtime_dependencies("ingester")
        self.assertFalse(result.ok)
        self.assertIn("unknown role", result.detail)

    def test_provider_availability_is_not_a_readiness_dependency(self):
        """An Upstox outage — or a closed market — must not withdraw the API from
        rotation. The approved design does not make feed health a readiness gate."""
        from oipulse.observability.readiness import register_dependency_probes

        class _Registry:
            """Satisfies `ProbeRegistry` structurally. FastAPI is not installed here,
            so `api.health.ReadinessRegistry` cannot be imported; the protocol is the
            contract either way."""

            def __init__(self):
                self.names: list[str] = []

            def register(self, name, probe):
                self.names.append(name)

        registry = _Registry()
        registered = register_dependency_probes(registry, _SETTINGS, "ingestor")

        self.assertEqual(set(registered), {"postgres", "redis", "runtime_dependencies"})
        self.assertEqual(set(registry.names), set(registered))
        self.assertNotIn(
            "provider",
            " ".join(registry.names),
            "feed health must not gate readiness; it is a data-quality concern",
        )


class TestEntrypointDispatch(unittest.TestCase):
    """`--role ingestor` must no longer exit 4."""

    def test_ingestor_is_not_listed_as_an_unimplemented_role(self):
        from oipulse.run import _PHASE_OF_ROLE

        self.assertNotIn("ingestor", _PHASE_OF_ROLE)

    def test_the_entrypoint_dispatches_the_ingestor_role(self):
        import oipulse.run as run_module

        self.assertTrue(hasattr(run_module, "run_ingestor_role"))

    def test_a_missing_universe_exits_as_a_configuration_error(self):
        """Exit 2 is the configuration status, distinct from 4 ("not implemented")."""
        import os

        import oipulse.run as run_module

        previous = os.environ.pop(UNIVERSE_ENV_VAR, None)
        try:
            self.assertEqual(run_module.run_ingestor_role(_SETTINGS), 2)
        finally:
            if previous is not None:
                os.environ[UNIVERSE_ENV_VAR] = previous


if __name__ == "__main__":
    unittest.main()
