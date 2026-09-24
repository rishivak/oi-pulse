"""The canonical collector — wires plan, subscribe, ingest, recover, persist.

`docs/design/06-UPSTOX_INTEGRATION.md` and `18-ROADMAP.md` Phase 2. This is the component
the operational requirement refers to: *"as soon as the canonical collector is capable of
producing valid durable observations, enable continuous collection during the intended
NSE market window."*

NOT RUNNABLE IN THE DEVELOPMENT SANDBOX: continuous collection needs network access to
api.upstox.com, credentials, and a PostgreSQL to write to. None is available here. The
control flow below is what the external environment runs; the decision logic it depends
on (planning, identity, gap detection, recovery, idempotency) is verified offline.

Ordering of operations is deliberate:

1. **Plan capacity first.** An `UNSATISFIABLE` universe refuses to start rather than
   failing at subscribe time.
2. **Anchor with REST before streaming.** A chain snapshot gives a cross-sectional
   baseline, so the first states are `SNAPSHOT_ANCHORED` rather than `STREAM_ONLY`.
3. **Stream, detecting gaps at whatever confidence the identity supports.**
4. **Recover out of band** — never queued behind routine polling while degraded.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from oipulse.core.clock import Clock
from oipulse.core.errors import OIPulseError
from oipulse.dataquality.issues import IssueSeverity, IssueType, QualityIssue
from oipulse.instruments.universe import DataMode
from oipulse.marketdata.lifecycle import GapRecord, SessionManager
from oipulse.marketdata.observations import MarketObservation
from oipulse.marketdata.ratelimit import RateLimitGovernor
from oipulse.marketdata.recovery import RecoveryPlan, plan_recovery
from oipulse.marketdata.store.memory import WriteResult
from oipulse.marketdata.subscription import (
    CapacityResult,
    CapacityVerdict,
    SubscriptionPlanner,
    SubscriptionRequest,
)
from oipulse.observability.logging import get_logger
from oipulse.observability.metrics import (
    DQ_ISSUES,
    INGESTION_LATENCY,
    METRICS,
    OBSERVATION_DUPLICATES,
    OBSERVATION_IDENTITY_CONFIDENCE,
    OBSERVATIONS_INGESTED,
    SUBSCRIPTION_CAPACITY_REMAINING,
    SUBSCRIPTION_CAPACITY_USED,
    SUBSCRIPTION_DEGRADATION_ACTIVE,
    SUBSCRIPTION_REJECTIONS,
)

log = get_logger(__name__)

__all__ = [
    "CanonicalCollector",
    "CollectorPlan",
    "ObservationSink",
    "RecoveredChain",
    "StreamingProvider",
    "UnsatisfiableUniverse",
]


class ObservationSink(Protocol):
    """The durable write contract the collector depends on.

    Async because the durable implementation is `PostgresObservationStore`, which writes
    over an async driver. Declaring this structurally rather than typing the constructor
    parameter `object` matters: the previous `# type: ignore[attr-defined]` at the call
    site suppressed precisely the error that would have caught `persist()` calling an
    async `append()` without awaiting it, which silently wrote nothing against the real
    store while the synchronous in-memory twin kept the tests green.
    """

    async def append(self, observations: Iterable[MarketObservation]) -> WriteResult: ...


class RecoveredChain(Protocol):
    """Minimal shape of an out-of-band recovery fetch: the legs to re-persist."""

    @property
    def legs(self) -> Sequence[MarketObservation]: ...


class StreamingProvider(Protocol):
    """The provider surface the collector needs, and no more."""

    def stream(self, vendor_keys: Sequence[str], mode: str) -> AsyncIterator[MarketObservation]: ...

    async def fetch_recovery_chain(self, expiry_id: int) -> RecoveredChain: ...


class UnsatisfiableUniverse(OIPulseError):
    """The universe cannot fit under any allowed degradation. Refuse to start."""


@dataclass(frozen=True, slots=True)
class CollectorPlan:
    capacity: CapacityResult
    vendor_keys: tuple[str, ...]
    mode: DataMode
    chain_poll_interval: timedelta
    underlying_ids: tuple[int, ...] = ()
    expiry_ids: tuple[int, ...] = ()
    issues: tuple[QualityIssue, ...] = field(default_factory=tuple)


class CanonicalCollector:
    """Owns one universe's collection lifecycle."""

    def __init__(
        self,
        clock: Clock,
        planner: SubscriptionPlanner,
        governor: RateLimitGovernor,
        sessions: SessionManager,
        store: ObservationSink,
        provider: StreamingProvider | None = None,
    ) -> None:
        self._clock = clock
        self._planner = planner
        self._governor = governor
        self._sessions = sessions
        self._store = store
        self._provider = provider
        self._running = False
        self._issues: list[QualityIssue] = []

    # -------------------------------------------------------------------- plan

    def plan(
        self,
        request: SubscriptionRequest,
        vendor_keys: Sequence[str],
        mode: DataMode,
        chain_count: int,
        underlying_ids: tuple[int, ...] = (),
        expiry_ids: tuple[int, ...] = (),
    ) -> CollectorPlan:
        """Decide capacity **before** subscribing. Refuse if impossible."""
        capacity = self._planner.plan(request)

        for data_mode, used in capacity.used_by_mode.items():
            METRICS.set_gauge(SUBSCRIPTION_CAPACITY_USED, used, {"mode": data_mode.value})
            METRICS.set_gauge(
                SUBSCRIPTION_CAPACITY_REMAINING,
                max(self._planner.budget.usable(data_mode) - used, 0),
                {"mode": data_mode.value},
            )

        issues: list[QualityIssue] = []
        if capacity.verdict is CapacityVerdict.UNSATISFIABLE:
            METRICS.inc(SUBSCRIPTION_REJECTIONS, {"verdict": capacity.verdict.value})
            log.error("subscription_unsatisfiable", extra={"reason": capacity.reason})
            raise UnsatisfiableUniverse(capacity.reason)

        if capacity.verdict is CapacityVerdict.DEGRADED:
            METRICS.inc(SUBSCRIPTION_REJECTIONS, {"verdict": capacity.verdict.value})
            METRICS.set_gauge(SUBSCRIPTION_DEGRADATION_ACTIVE, 1.0)
            # Recorded as a data-quality fact: absent data must be attributable to a
            # capacity decision rather than a venue outage, and that distinction is
            # invisible after the fact unless captured now.
            issues.append(
                QualityIssue(
                    type=IssueType.SUBSCRIPTION_DEGRADED,
                    severity=IssueSeverity.DEGRADED,
                    detected_at=self._clock.now(),
                    detail=capacity.summary(),
                    context={
                        "degradations": [d.value for d in capacity.degradations],
                        "dropped_count": capacity.dropped_count,
                        "budget_source": capacity.budget.source,
                    },
                )
            )
            log.warning("subscription_degraded", extra={"summary": capacity.summary()})
        else:
            METRICS.set_gauge(SUBSCRIPTION_DEGRADATION_ACTIVE, 0.0)
            log.info("subscription_accepted", extra={"summary": capacity.summary()})

        return CollectorPlan(
            capacity=capacity,
            vendor_keys=tuple(vendor_keys),
            mode=mode,
            # Cadence derives from budget, so adding an expiry visibly costs cadence.
            chain_poll_interval=self._governor.chain_poll_interval(chain_count),
            underlying_ids=underlying_ids,
            expiry_ids=expiry_ids,
            issues=tuple(issues),
        )

    # ----------------------------------------------------------------- ingest

    async def persist(self, observations: Sequence[MarketObservation]) -> int:
        """Write a batch idempotently and emit the ingestion metrics."""
        if not observations:
            return 0
        result = await self._store.append(observations)

        for obs in observations:
            METRICS.observe(
                INGESTION_LATENCY, obs.ingestion_lag_seconds, {"source": obs.source.value}
            )
            METRICS.inc(
                OBSERVATION_IDENTITY_CONFIDENCE,
                {"confidence": obs.identity.confidence.value},
            )
        METRICS.inc(
            OBSERVATIONS_INGESTED, {"source": observations[0].source.value}, result.inserted
        )
        if result.duplicates:
            METRICS.inc(OBSERVATION_DUPLICATES, by=result.duplicates)
        return result.inserted

    async def recover_after_gap(
        self,
        gap: GapRecord,
        underlying_ids: tuple[int, ...],
        expiry_ids: tuple[int, ...],
    ) -> RecoveryPlan:
        """Out-of-band REST re-fetch. The gap window is recorded, never interpolated.

        Two things happen and both matter: the chain is re-fetched *outside* the normal
        poll cadence, so recovery is not queued behind routine polling while the state
        is degraded; and the gap window is recorded permanently, because observations
        inside it are absent, not zero.
        """
        plan = plan_recovery(
            gap, clock=self._clock, underlying_ids=underlying_ids, expiry_ids=expiry_ids
        )
        log.warning(
            "recovery_triggered", extra={"trigger": plan.trigger.value, "detail": plan.detail}
        )
        for issue in plan.issues:
            METRICS.inc(DQ_ISSUES, {"type": issue.type.value, "severity": issue.severity.value})
        self._issues.extend(plan.issues)

        if self._provider is not None and plan.out_of_band:
            for expiry_id in plan.expiry_ids:
                try:
                    snapshot = await self._provider.fetch_recovery_chain(expiry_id)
                except Exception as exc:
                    log.error("recovery_fetch_failed", extra={"error": str(exc)[:200]})
                    continue
                # Idempotent: the recovery fetch and the resumed stream may cover the
                # same instant, and the identity key absorbs the overlap.
                await self.persist(list(snapshot.legs))
        return plan

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        """Quality issues raised so far. Persisted by the caller into `dq_issues`."""
        return tuple(self._issues)

    def drain_issues(self) -> tuple[QualityIssue, ...]:
        issues = tuple(self._issues)
        self._issues.clear()
        return issues

    # ------------------------------------------------------------------- loop

    async def run(self, plan: CollectorPlan, session_is_open: Callable[[datetime], bool]) -> None:
        """Continuous collection during the market window.

        Requires a live provider, credentials and a database. `session_is_open` is a
        predicate rather than a hardcoded calendar: the legacy hardcoded holiday list,
        with entries its own comment marked "indicative", collected junk on unlisted
        holidays.
        """
        if self._provider is None:
            raise RuntimeError("no provider configured; cannot collect")

        self._running = True
        while self._running:
            if not session_is_open(self._clock.now()):
                await asyncio.sleep(30)
                continue
            try:
                async for observation in self._provider.stream(plan.vendor_keys, plan.mode.value):
                    await self.persist([observation])

                    # Any gap detected while consuming that frame triggers out-of-band
                    # recovery immediately. Detecting a gap and not acting on it leaves
                    # the state degraded for a whole poll interval.
                    for gap in self._sessions.drain_new_gaps():
                        await self.recover_after_gap(gap, plan.underlying_ids, plan.expiry_ids)
            except Exception as exc:
                log.error("collector_stream_error", extra={"error": str(exc)[:200]})
                await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False
