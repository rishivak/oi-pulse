"""Ingestor runtime — the `--role ingestor` process.

`docs/design/14-DEPLOYMENT.md` §1 and §2 and `06-UPSTOX_INTEGRATION.md`. The role's only job
is *getting bytes durably stored, fast*: it must never block on analytics, and it
implements nothing from Phase 3 — no MarketState, no features, no signals.

The sequence is fixed and each step exists to make a specific failure loud and early:

1. **Preflight.** PostgreSQL, Redis and the role's local packages are probed before
   anything is constructed. A process that cannot reach its durable store must refuse to
   start, not discover it on the first write during market hours.
2. **Credentials.** Absent or placeholder credentials stop startup here. The alternative
   is a process that runs, connects to nothing and reports no error.
3. **Plan capacity.** `SubscriptionPlanner` turns a runtime WebSocket rejection into a
   deterministic planning decision. `UNSATISFIABLE` refuses to start; `DEGRADED` starts
   and records a data-quality issue, because absent data must be attributable to a
   capacity decision rather than mistaken for a quiet market.
4. **Anchor with REST, then stream.** A chain snapshot first, so the earliest states are
   snapshot-anchored rather than stream-only.
5. **Collect during the market window**, recovering out of band on every gap.
6. **Shut down gracefully** on SIGTERM/SIGINT: stop consuming, close the feed session so
   the outage window is recorded, and let in-flight writes finish. A killed ingestor that
   never closes its session leaves a gap that looks like a provider outage.

**What this runtime deliberately does not decide.** The trading calendar is injected as
a predicate, never hardcoded. The legacy collector carried a hardcoded holiday list whose
own comment called the entries "indicative", and it collected junk on unlisted holidays.
`default_session_predicate` below is a weekday-and-window default and says plainly that
it knows nothing about holidays.

NOT EXECUTABLE IN THE DEVELOPMENT SANDBOX: this path needs network access to
api.upstox.com, credentials, PostgreSQL and Redis, none of which exist here. The wiring
and the refusal conditions are unit-tested offline; the live loop is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time

from oipulse.core.clock import Clock, SystemClock
from oipulse.core.config import Settings
from oipulse.core.errors import ConfigurationError
from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.instruments.universe import DataMode
from oipulse.marketdata.collector import CanonicalCollector, CollectorPlan, ObservationSink
from oipulse.marketdata.lifecycle import ConnectionState, SessionManager
from oipulse.marketdata.providers.upstox.provider import (
    ExpiryBinding,
    InstrumentResolver,
    UpstoxMarketDataProvider,
)
from oipulse.marketdata.providers.upstox.v3 import (
    ProtoDecoderUnavailable,
    ProtoFrameDecoder,
    RestAuthorizer,
    UpstoxV3FeedClient,
)
from oipulse.marketdata.ratelimit import RateLimitGovernor
from oipulse.marketdata.subscription import SubscriptionPlanner, SubscriptionRequest
from oipulse.observability.logging import get_logger
from oipulse.observability.readiness import DependencyProbes

log = get_logger(__name__)

__all__ = [
    "UNIVERSE_ENV_VAR",
    "IngestorRuntime",
    "IngestorSpec",
    "PreflightFailed",
    "build_spec_from_env",
    "build_v3_feed_client",
    "default_session_predicate",
    "load_proto_decoder",
    "parse_universe",
    "run_ingestor",
]

#: The shard's universe, as JSON. Configuration, not a default: see `parse_universe`.
UNIVERSE_ENV_VAR = "OIPULSE_INGESTOR_UNIVERSE"

#: NSE regular equity-derivatives session, in IST. Used only by the default predicate.
_SESSION_OPEN = time(9, 15)
_SESSION_CLOSE = time(15, 30)
_IST_OFFSET_MINUTES = 330


class PreflightFailed(ConfigurationError):
    """A dependency the ingestor needs is unavailable. The process refuses to start."""


def default_session_predicate(at: datetime) -> bool:
    """Weekday 09:15-15:30 IST.

    **This predicate knows nothing about holidays**, and that limitation is the point of
    naming it `default`: a deployment with a trading calendar injects its own. On a
    holiday this returns True and the feed simply delivers nothing, which is recorded as
    staleness -- visibly wrong, rather than the legacy behaviour of writing junk rows
    against a hardcoded and admittedly indicative holiday list.
    """
    minutes = at.hour * 60 + at.minute + _IST_OFFSET_MINUTES
    ist_minutes = minutes % (24 * 60)
    day_rollover = minutes // (24 * 60)
    weekday = (at.weekday() + day_rollover) % 7
    if weekday >= 5:
        return False
    open_at = _SESSION_OPEN.hour * 60 + _SESSION_OPEN.minute
    close_at = _SESSION_CLOSE.hour * 60 + _SESSION_CLOSE.minute
    return open_at <= ist_minutes < close_at


@dataclass(frozen=True, slots=True)
class IngestorSpec:
    """What this ingestor shard collects.

    A shard, not the whole market: `14` §2 shards the ingestor by instrument-key range,
    each shard owning its own WebSocket subscription set.
    """

    vendor_keys: tuple[str, ...]
    mode: DataMode
    underlying_ids: tuple[int, ...] = ()
    expiry_bindings: tuple[ExpiryBinding, ...] = ()
    subscription: SubscriptionRequest | None = None
    session_predicate: Callable[[datetime], bool] = default_session_predicate
    #: Registered vendor-key -> instrument-id mappings, as of the runtime's start.
    instrument_mappings: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def expiry_ids(self) -> tuple[int, ...]:
        return tuple(int(b.expiry_id) for b in self.expiry_bindings)


class IngestorRuntime:
    """Owns one ingestor process's lifecycle.

    Constructed with its collaborators rather than building them from the environment,
    so every refusal condition is testable without a database, a broker or a network.
    `run_ingestor` below is the composition root that supplies the real ones.
    """

    def __init__(
        self,
        settings: Settings,
        spec: IngestorSpec,
        clock: Clock,
        sessions: SessionManager,
        provider: UpstoxMarketDataProvider,
        store: ObservationSink,
        resolver: InstrumentResolver | None = None,
        planner: SubscriptionPlanner | None = None,
        governor: RateLimitGovernor | None = None,
        probes: DependencyProbes | None = None,
    ) -> None:
        self._settings = settings
        self._spec = spec
        self._clock = clock
        self._sessions = sessions
        self._provider = provider
        self._resolver = resolver or InstrumentResolver()
        self._governor = governor or RateLimitGovernor(clock)
        self._probes = probes or DependencyProbes()
        self._collector = CanonicalCollector(
            clock,
            planner or SubscriptionPlanner(),
            self._governor,
            sessions,
            store,
            provider,
        )
        self._stopping = False

    @property
    def collector(self) -> CanonicalCollector:
        return self._collector

    def bind(self) -> None:
        """Register vendor-key and expiry bindings before any fetch.

        Separate from `run` so anchoring and recovery resolve identically whether the
        process started them or a test did. Without the bindings, chain legs resolve to
        no instrument and are silently dropped -- a chain that looks like a thin market
        rather than a wiring mistake.
        """
        now = self._clock.now()
        for vendor_key, instrument_id in self._spec.instrument_mappings:
            self._resolver.register(vendor_key, InstrumentId(instrument_id), now)
        for binding in self._spec.expiry_bindings:
            self._provider.register_expiry(binding)

    # --------------------------------------------------------------- preflight

    async def preflight(self) -> None:
        """Probe every dependency before constructing anything that uses it.

        **Aggregating, not fail-fast.** All three probes always run and `PreflightFailed`
        lists every failure, so an operator fixing a deployment sees the whole list in
        one restart rather than discovering the next broken dependency on the next try.
        The probes are injectable (`DependencyProbes`) so this contract can be asserted
        without depending on what happens to be running on the machine.
        """
        results = await self._probes.evaluate(
            database_url=self._settings.database_url,
            redis_url=self._settings.redis_url,
            role="ingestor",
        )
        failed = [r for r in results if not r.ok]
        if failed:
            detail = "; ".join(f"{r.name}: {r.detail}" for r in failed)
            log.error("ingestor_preflight_failed", extra={"detail": detail})
            raise PreflightFailed(f"ingestor cannot start: {detail}")
        log.info("ingestor_preflight_ok", extra={"checks": [r.name for r in results]})

    def plan(self) -> CollectorPlan:
        """Capacity decision, made before a single subscription is sent.

        The request is built from the shard's **real** instrument ids. An earlier
        version used `range(len(vendor_keys))`, which not only failed the `InstrumentId`
        contract but planned capacity against fabricated ids 0..n-1: the protected-set
        logic in `SubscriptionPlanner`, which must never drop spot or front-expiry ATM,
        compares against real ids and would have matched none of them.
        """
        request = self._spec.subscription
        if request is None:
            if not self._spec.instrument_mappings:
                raise ConfigurationError(
                    "cannot plan capacity: the universe maps no vendor keys to "
                    "instrument ids. Supply instrument_mappings or an explicit "
                    "SubscriptionRequest."
                )
            instrument_ids = tuple(
                InstrumentId(instrument_id)
                for _vendor_key, instrument_id in self._spec.instrument_mappings
            )
            request = SubscriptionRequest({self._spec.mode: instrument_ids})
        return self._collector.plan(
            request,
            self._spec.vendor_keys,
            self._spec.mode,
            chain_count=len(self._spec.expiry_bindings),
            underlying_ids=self._spec.underlying_ids,
            expiry_ids=self._spec.expiry_ids,
        )

    # -------------------------------------------------------------- anchoring

    async def anchor(self) -> int:
        """Fetch one REST chain per bound expiry before streaming begins.

        Without it the first observations are stream-only, and a cross-sectional read
        taken in that window has no consistency set behind it (`04` §4).
        """
        persisted = 0
        for binding in self._spec.expiry_bindings:
            try:
                snapshot = await self._provider.fetch_option_chain(
                    binding.underlying_vendor_key,
                    binding.underlying_id,
                    binding.expiry_id,
                    binding.expiry,
                    expected_leg_count=binding.expected_leg_count,
                )
            except Exception as exc:
                # Anchoring is best-effort: failing to anchor degrades the first states
                # to stream-only, which is worse than anchored but far better than
                # refusing to collect the session at all.
                log.warning(
                    "anchor_fetch_failed",
                    extra={"expiry_id": int(binding.expiry_id), "error": str(exc)[:200]},
                )
                continue
            persisted += await self._collector.persist(list(snapshot.legs))
        return persisted

    # ------------------------------------------------------------------- run

    async def run(self) -> None:
        """Preflight, plan, anchor, then collect until stopped."""
        await self.preflight()

        self.bind()
        plan = self.plan()

        for state in (
            ConnectionState.CONNECTING,
            ConnectionState.AUTHENTICATING,
            ConnectionState.SUBSCRIBING,
            ConnectionState.STREAMING,
        ):
            self._sessions.transition(state)
        self._sessions.open_session()

        await self.anchor()
        log.info(
            "ingestor_started",
            extra={
                "instruments": len(self._spec.vendor_keys),
                "expiries": len(self._spec.expiry_bindings),
                "verdict": plan.capacity.verdict.value,
            },
        )
        try:
            await self._collector.run(plan, self._spec.session_predicate)
        finally:
            self.shutdown("runtime_exit")

    def shutdown(self, reason: str) -> None:
        """Stop consuming and close the feed session.

        Closing the session is what records the outage window. A process killed without
        it leaves a hole indistinguishable from a provider outage, and the distinction
        cannot be recovered afterwards.
        """
        if self._stopping:
            return
        self._stopping = True
        self._collector.stop()
        with contextlib.suppress(Exception):
            self._sessions.close_session(reason)
        log.info("ingestor_stopped", extra={"reason": reason})


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, runtime: IngestorRuntime) -> None:
    """SIGTERM and SIGINT stop the runtime gracefully.

    SIGTERM is what an orchestrator sends during a rolling deploy (`14` §7), and that
    deploy's brief WS gap is supposed to be *recorded* as a data-quality issue, which
    only happens if the session is closed on the way out.
    """

    def _handler(received: signal.Signals) -> Callable[[], None]:
        """Bind the signal by argument rather than by default-argument capture.

        The previous `lambda s=sig:` relied on a default argument to capture the loop
        variable, which is both easy to misread and untypeable -- the default makes the
        lambda's signature depend on the captured value.
        """

        def handle() -> None:
            runtime.shutdown(f"signal_{received.name}")

        return handle

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, _handler(sig))


def load_proto_decoder() -> ProtoFrameDecoder | None:
    """Return the Upstox V3 Protobuf decoder, or None if none is available.

    The single injection point for the provider-owned `.proto`. It returns None today:
    the official V3 definition is not bundled, the development environment cannot reach
    `api.upstox.com` to obtain it, and no Protobuf runtime is installable here.

    Returning None rather than a guessed decoder is the whole point. Protobuf field
    numbers written from memory do not fail loudly -- a wrong number decodes to a
    plausible value for the wrong field, which then lands in durable market data and
    is indistinguishable from a real observation.
    """
    return None


def build_v3_feed_client(
    rest: RestAuthorizer, clock: Clock, sessions: SessionManager
) -> UpstoxV3FeedClient:
    """Construct the V3 feed client, or refuse with an actionable message."""
    decoder = load_proto_decoder()
    if decoder is None:
        raise ProtoDecoderUnavailable(
            "the ingestor cannot start: the Upstox V3 market-data feed carries binary "
            "Protobuf and no decoder is available.\n"
            "Supply the official V3 .proto definition and return a decoder from "
            "oipulse.marketdata.runtime.load_proto_decoder().\n"
            "There is deliberately no JSON fallback -- Upstox V2 market data is "
            "discontinued, and parsing a V3 frame as JSON yields no observations "
            "rather than an error."
        )
    return UpstoxV3FeedClient(rest, clock, sessions, decoder)


def parse_universe(document: str) -> IngestorSpec:
    """Build an `IngestorSpec` from the shard's JSON universe description.

    Shape::

        {
          "mode": "greeks",
          "instruments": [{"vendor_key": "NSE_FO|12345", "instrument_id": 1}],
          "underlying_ids": [100],
          "expiries": [
            {"expiry_id": 10, "underlying_id": 100,
             "underlying_vendor_key": "NSE_INDEX|Nifty 50",
             "expiry": "2026-03-05", "expected_leg_count": 120}
          ]
        }

    Every field is validated here and a bad document stops the process, because a
    partially parsed universe collects a *different* set of instruments than the one
    the operator asked for -- and nothing downstream can tell the difference.
    """
    try:
        raw = json.loads(document)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{UNIVERSE_ENV_VAR} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{UNIVERSE_ENV_VAR} must be a JSON object")

    mode_name = str(raw.get("mode", "")).strip().lower()
    try:
        mode = DataMode(mode_name)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in DataMode)
        raise ConfigurationError(
            f"{UNIVERSE_ENV_VAR}.mode must be one of {allowed}; got {mode_name!r}"
        ) from exc

    instruments = raw.get("instruments") or []
    if not isinstance(instruments, list) or not instruments:
        raise ConfigurationError(f"{UNIVERSE_ENV_VAR}.instruments must be a non-empty list")

    mappings: list[tuple[str, int]] = []
    for index, entry in enumerate(instruments):
        if not isinstance(entry, dict) or "vendor_key" not in entry:
            raise ConfigurationError(
                f"{UNIVERSE_ENV_VAR}.instruments[{index}] needs vendor_key and instrument_id"
            )
        mappings.append((str(entry["vendor_key"]), int(entry["instrument_id"])))

    bindings: list[ExpiryBinding] = []
    for index, entry in enumerate(raw.get("expiries") or []):
        if not isinstance(entry, dict):
            raise ConfigurationError(f"{UNIVERSE_ENV_VAR}.expiries[{index}] must be an object")
        try:
            bindings.append(
                ExpiryBinding(
                    expiry_id=ExpiryId(int(entry["expiry_id"])),
                    underlying_id=InstrumentId(int(entry["underlying_id"])),
                    underlying_vendor_key=str(entry["underlying_vendor_key"]),
                    expiry=date.fromisoformat(str(entry["expiry"])),
                    expected_leg_count=(
                        int(entry["expected_leg_count"])
                        if entry.get("expected_leg_count") is not None
                        else None
                    ),
                )
            )
        except (KeyError, ValueError) as exc:
            raise ConfigurationError(
                f"{UNIVERSE_ENV_VAR}.expiries[{index}] is invalid: {exc}"
            ) from exc

    return IngestorSpec(
        vendor_keys=tuple(key for key, _ in mappings),
        mode=mode,
        underlying_ids=tuple(int(u) for u in raw.get("underlying_ids") or ()),
        expiry_bindings=tuple(bindings),
        instrument_mappings=tuple(mappings),
    )


def build_spec_from_env() -> IngestorSpec:
    """Read the shard's universe from the environment, or refuse to start.

    There is deliberately no default. Guessing a universe would start a process that
    collects something nobody asked for and bills rate-limit budget against it.
    """
    document = os.environ.get(UNIVERSE_ENV_VAR, "").strip()
    if not document:
        raise ConfigurationError(
            f"{UNIVERSE_ENV_VAR} is required for the ingestor role and was empty. "
            f"It describes the instruments, expiries and data mode this shard collects; "
            f"see docs/design/14-DEPLOYMENT.md."
        )
    return parse_universe(document)


def run_ingestor(settings: Settings, spec: IngestorSpec | None = None) -> int:
    """Composition root for `python -m oipulse.run --role ingestor`.

    Returns a process exit code rather than raising, so the entrypoint can report a
    configuration or preflight failure as a distinct status instead of a traceback.
    """
    if spec is None:
        # The universe a shard collects is environment configuration, not a default.
        # Guessing one would start a process that collects something nobody asked for.
        log.error("ingestor_spec_missing")
        print(
            "the ingestor needs an instrument universe to collect.\n"
            "Phase 2 supplies it through the composition root; no universe is configured "
            "in this environment, so there is nothing to subscribe to.",
            file=sys.stderr,
        )
        return 5

    clock = SystemClock()
    sessions = SessionManager(clock)

    try:
        from oipulse.marketdata.providers.upstox.rest import UpstoxRestClient
        from oipulse.marketdata.store.postgres import PostgresObservationRepository
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        print(
            f"cannot start the ingestor role: {exc.name} is not installed.\n"
            f"Install the runtime dependencies with:  pip install -e .",
            file=sys.stderr,
        )
        return 3

    access_token = _access_token(settings)
    if not access_token:
        log.error("ingestor_credentials_missing")
        print(
            "UPSTOX_ACCESS_TOKEN is required for the ingestor role and was empty.\n"
            "The token is never exposed to the frontend and is read only by this process.",
            file=sys.stderr,
        )
        return 2

    governor = RateLimitGovernor(clock)
    resolver = InstrumentResolver()
    rest = UpstoxRestClient(access_token, clock, governor)

    # The live feed is Upstox **V3**, which carries binary Protobuf. The V2 JSON client
    # is deliberately not wired here: V2 market data is discontinued, and falling back
    # to it would parse binary frames as JSON and silently produce no observations.
    try:
        feed = build_v3_feed_client(rest, clock, sessions)
    except ProtoDecoderUnavailable as exc:
        log.error("ingestor_v3_decoder_unavailable")
        print(str(exc), file=sys.stderr)
        return 7

    provider = UpstoxMarketDataProvider(rest, clock, resolver, sessions, feed)

    async def main() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                runtime = IngestorRuntime(
                    settings,
                    spec,
                    clock,
                    sessions,
                    provider,
                    PostgresObservationRepository(connection),
                    resolver=resolver,
                    governor=governor,
                )
                _install_signal_handlers(asyncio.get_running_loop(), runtime)
                await runtime.run()
        finally:
            await engine.dispose()

    try:
        asyncio.run(main())
    except PreflightFailed as exc:
        print(str(exc), file=sys.stderr)
        return 6
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return 0
    return 0


def _access_token(settings: Settings) -> str:
    """The broker token, read in-process only.

    CLAUDE.md rule 18: broker credentials and access tokens are never exposed to the
    frontend. It is read here, passed to the transports, and never logged -- the error
    path below reports only that it is missing, never its value.
    """
    return os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
