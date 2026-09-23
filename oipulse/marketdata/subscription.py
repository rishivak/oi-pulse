"""SubscriptionPlanner — capacity is decided *before* subscribing.

`docs/design/06-UPSTOX_INTEGRATION.md` §5 and `19-DECISIONS.md` AD-25.

    requested universe → required data modes → instrument count
    → connection/subscription budget → allocation → CapacityResult

Discovering that a universe is too large via a runtime WebSocket failure is
unacceptable; a capacity problem must be a deterministic planning result, not an outage.

**Constraint E.** Vendor limits are **configuration**, never architectural constants.
`SubscriptionBudget` defaults are Phase-2-confirmed *assumptions* (A-5) carrying the
values indicated by review; they are overridden from config and verified during the soak.
Nothing in this module hardcodes a number as truth.

`DEGRADED` is never silent: the reduction is recorded so a later researcher can see that
depth was missing for a period because of a capacity decision, not because the venue
stopped sending it — a distinction that is invisible after the fact unless captured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from oipulse.core.ids import InstrumentId
from oipulse.instruments.universe import DataMode

__all__ = [
    "CapacityResult",
    "CapacityVerdict",
    "ConnectionAllocation",
    "DegradationStep",
    "SubscriptionBudget",
    "SubscriptionPlanner",
    "SubscriptionRequest",
]


class CapacityVerdict(StrEnum):
    ACCEPTED = "accepted"
    DEGRADED = "degraded"
    UNSATISFIABLE = "unsatisfiable"


class DegradationStep(StrEnum):
    """The ladder, applied in order. Each step is recorded when taken (`06` §5).

    Spot and front-expiry ATM coverage are never degraded — losing them makes
    `MarketState` `UNRELIABLE` by definition, so shedding them to fit would defeat the
    purpose of fitting.
    """

    DROP_DEPTH_FAR_STRIKES = "drop_depth_far_strikes"
    NARROW_STRIKE_BAND = "narrow_strike_band"
    DROP_BACK_EXPIRIES = "drop_back_expiries"
    DROP_NON_PRIMARY_UNDERLYINGS = "drop_non_primary_underlyings"


@dataclass(frozen=True, slots=True)
class SubscriptionBudget:
    """Provider connection and subscription limits.

    **Phase-2-confirmed assumptions, not architectural constants** (A-5). Review
    indicates roughly 2 WebSocket connections per user with combined subscription limits
    around 2,000 for LTPC/Greeks and 1,500 for Full mode. These are defaults for
    configuration and are verified by the soak; the planner reports the numbers it used
    in every result so a plan is auditable against the limits in force at the time.
    """

    max_connections: int = 2
    max_subscriptions_ltpc_greeks: int = 2000
    max_subscriptions_full: int = 1500
    #: Held back so a mid-session universe change does not immediately exhaust capacity.
    reserve_fraction: float = 0.05
    source: str = "phase2-assumption:A-5 (unverified against live provider)"

    def __post_init__(self) -> None:
        if self.max_connections < 1:
            raise ValueError("max_connections must be >= 1")
        if not 0.0 <= self.reserve_fraction < 1.0:
            raise ValueError("reserve_fraction must be in [0, 1)")

    def usable(self, mode: DataMode) -> int:
        raw = (
            self.max_subscriptions_full
            if mode is DataMode.FULL
            else self.max_subscriptions_ltpc_greeks
        )
        return int(raw * (1.0 - self.reserve_fraction))


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    """What the universe resolved to: instruments grouped by required data mode."""

    by_mode: dict[DataMode, tuple[InstrumentId, ...]]
    #: Instruments that must never be dropped — spot and front-expiry ATM.
    protected: frozenset[InstrumentId] = frozenset()

    @property
    def total_instruments(self) -> int:
        return sum(len(v) for v in self.by_mode.values())

    def count(self, mode: DataMode) -> int:
        return len(self.by_mode.get(mode, ()))


@dataclass(frozen=True, slots=True)
class ConnectionAllocation:
    connection_index: int
    mode: DataMode
    instrument_ids: tuple[InstrumentId, ...]

    @property
    def size(self) -> int:
        return len(self.instrument_ids)


@dataclass(frozen=True, slots=True)
class CapacityResult:
    """The planning decision, with the arithmetic that produced it."""

    verdict: CapacityVerdict
    allocations: tuple[ConnectionAllocation, ...]
    budget: SubscriptionBudget
    requested_total: int
    planned_total: int
    degradations: tuple[DegradationStep, ...] = ()
    dropped_instruments: tuple[InstrumentId, ...] = ()
    reason: str = ""
    #: Capacity used per mode, for the `subscription_capacity_used` metric (`16` §3).
    used_by_mode: dict[DataMode, int] = field(default_factory=dict)

    @property
    def ok_to_subscribe(self) -> bool:
        return self.verdict is not CapacityVerdict.UNSATISFIABLE

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_instruments)

    def summary(self) -> str:
        base = (
            f"{self.verdict.value.upper()}: {self.planned_total}/{self.requested_total} "
            f"instruments across {len(self.allocations)} connection(s)"
        )
        if self.degradations:
            base += f" after {', '.join(d.value for d in self.degradations)}"
        return base


class SubscriptionPlanner:
    """Turns a resolved universe into a deterministic capacity decision."""

    def __init__(self, budget: SubscriptionBudget | None = None) -> None:
        self._budget = budget or SubscriptionBudget()

    @property
    def budget(self) -> SubscriptionBudget:
        return self._budget

    def plan(self, request: SubscriptionRequest) -> CapacityResult:
        """Plan without degradation if possible, else degrade, else refuse."""
        fits, used = self._fits(request)
        if fits:
            return CapacityResult(
                verdict=CapacityVerdict.ACCEPTED,
                allocations=self._allocate(request),
                budget=self._budget,
                requested_total=request.total_instruments,
                planned_total=request.total_instruments,
                used_by_mode=used,
                reason="universe fits within budget",
            )
        return self._degrade(request)

    # ---------------------------------------------------------------- internals

    def _fits(self, request: SubscriptionRequest) -> tuple[bool, dict[DataMode, int]]:
        """Capacity is per mode-class *and* per connection count.

        FULL draws on a separate, smaller pool; LTPC and GREEKS share one.
        """
        full = request.count(DataMode.FULL)
        shared = request.count(DataMode.LTPC) + request.count(DataMode.GREEKS)
        used = {DataMode.FULL: full, DataMode.GREEKS: shared}

        if full > self._budget.usable(DataMode.FULL):
            return False, used
        if shared > self._budget.usable(DataMode.GREEKS):
            return False, used

        per_conn = max(self._budget.usable(DataMode.FULL), self._budget.usable(DataMode.GREEKS))
        needed = self._connections_needed(full, shared, per_conn)
        return needed <= self._budget.max_connections, used

    @staticmethod
    def _connections_needed(full: int, shared: int, per_conn: int) -> int:
        if per_conn <= 0:
            return 1 << 30
        return (
            -(-full // per_conn) + -(-shared // per_conn)
            if (full and shared)
            else (-(-(full + shared) // per_conn) or 1)
        )

    def _allocate(self, request: SubscriptionRequest) -> tuple[ConnectionAllocation, ...]:
        """Assign instruments to connections, never mixing FULL with the shared pool.

        Keeping modes on separate connections means a FULL-pool exhaustion cannot take
        LTPC coverage down with it.
        """
        allocations: list[ConnectionAllocation] = []
        index = 0
        for mode in (DataMode.FULL, DataMode.GREEKS, DataMode.LTPC):
            ids = request.by_mode.get(mode, ())
            if not ids:
                continue
            per_conn = self._budget.usable(mode)
            for start in range(0, len(ids), max(per_conn, 1)):
                allocations.append(
                    ConnectionAllocation(index, mode, tuple(ids[start : start + per_conn]))
                )
                index += 1
        return tuple(allocations)

    def _degrade(self, request: SubscriptionRequest) -> CapacityResult:
        """Apply the ladder in order, recording every step taken."""
        steps: list[DegradationStep] = []
        dropped: list[InstrumentId] = []
        by_mode = {m: list(ids) for m, ids in request.by_mode.items()}

        # Step 1: FULL -> GREEKS for unprotected instruments (drops depth, keeps greeks).
        full = by_mode.get(DataMode.FULL, [])
        if full:
            demote = [i for i in full if i not in request.protected]
            if demote:
                steps.append(DegradationStep.DROP_DEPTH_FAR_STRIKES)
                by_mode[DataMode.FULL] = [i for i in full if i in request.protected]
                by_mode.setdefault(DataMode.GREEKS, []).extend(demote)
                candidate = self._request_from(by_mode, request.protected)
                fits, used = self._fits(candidate)
                if fits:
                    return self._degraded_result(request, candidate, steps, dropped, used)

        # Steps 2-4: shed unprotected instruments from the tail. The universe resolver
        # orders by distance from ATM and by expiry proximity, so the tail is the least
        # informative subscription, not an arbitrary one.
        for step in (
            DegradationStep.NARROW_STRIKE_BAND,
            DegradationStep.DROP_BACK_EXPIRIES,
            DegradationStep.DROP_NON_PRIMARY_UNDERLYINGS,
        ):
            shed = self._shed_tail(by_mode, request.protected, fraction=0.25)
            if not shed:
                break
            steps.append(step)
            dropped.extend(shed)
            candidate = self._request_from(by_mode, request.protected)
            fits, used = self._fits(candidate)
            if fits:
                return self._degraded_result(request, candidate, steps, dropped, used)

        # Protected set alone still does not fit: refuse rather than silently truncate.
        candidate = self._request_from(by_mode, request.protected)
        _, used = self._fits(candidate)
        return CapacityResult(
            verdict=CapacityVerdict.UNSATISFIABLE,
            allocations=(),
            budget=self._budget,
            requested_total=request.total_instruments,
            planned_total=0,
            degradations=tuple(steps),
            dropped_instruments=tuple(dropped),
            used_by_mode=used,
            reason=(
                "universe cannot fit under any allowed degradation; protected spot and "
                "front-expiry ATM coverage must not be shed. Narrow the universe "
                "explicitly."
            ),
        )

    def _degraded_result(
        self,
        original: SubscriptionRequest,
        candidate: SubscriptionRequest,
        steps: list[DegradationStep],
        dropped: list[InstrumentId],
        used: dict[DataMode, int],
    ) -> CapacityResult:
        return CapacityResult(
            verdict=CapacityVerdict.DEGRADED,
            allocations=self._allocate(candidate),
            budget=self._budget,
            requested_total=original.total_instruments,
            planned_total=candidate.total_instruments,
            degradations=tuple(steps),
            dropped_instruments=tuple(dropped),
            used_by_mode=used,
            reason=(
                "universe fits only after degradation; the reduction is recorded as a "
                "data-quality fact so absent data is attributable to a capacity "
                "decision rather than a venue outage"
            ),
        )

    @staticmethod
    def _request_from(
        by_mode: dict[DataMode, list[InstrumentId]],
        protected: frozenset[InstrumentId],
    ) -> SubscriptionRequest:
        return SubscriptionRequest(
            by_mode={m: tuple(ids) for m, ids in by_mode.items() if ids},
            protected=protected,
        )

    @staticmethod
    def _shed_tail(
        by_mode: dict[DataMode, list[InstrumentId]],
        protected: frozenset[InstrumentId],
        fraction: float,
    ) -> list[InstrumentId]:
        """Remove a slice of the least-informative unprotected instruments."""
        shed: list[InstrumentId] = []
        for mode, ids in by_mode.items():
            unprotected = [i for i in ids if i not in protected]
            if not unprotected:
                continue
            n = max(1, int(len(unprotected) * fraction))
            victims = set(unprotected[-n:])
            by_mode[mode] = [i for i in ids if i not in victims]
            shed.extend(sorted(victims))
        return shed
