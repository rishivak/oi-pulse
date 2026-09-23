"""Central rate-limit / capacity governance.

`docs/design/06-UPSTOX_INTEGRATION.md` §4. Request budget is a managed resource because
the expiry dimension multiplies demand: adding an expiry to a universe must visibly cost
cadence rather than silently causing throttling.

Every caller acquires budget before issuing a request; exhaustion **queues rather than
fails**. A 429 honours `Retry-After` and feeds the delay back into the governor so the
whole process backs off, not just the unlucky caller.

Deliberately clock-injected: `time.monotonic()` would be a wall-clock read, which the
Phase 1 guard forbids outside `core.clock`, and which would make the governor
untestable without sleeping.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from oipulse.core.clock import Clock

__all__ = ["BudgetDecision", "EndpointClass", "RateLimitGovernor", "RateLimitPolicy"]


class EndpointClass(StrEnum):
    """Limits are per endpoint class, not global — discovery and quotes differ."""

    DISCOVERY = "discovery"
    OPTION_CHAIN = "option_chain"
    QUOTE = "quote"
    HISTORICAL = "historical"
    ORDER = "order"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Requests permitted per window for one endpoint class.

    Values are **configuration**, not architectural constants (constraint E). Defaults
    are conservative Phase-2 assumptions verified during the soak.
    """

    requests: int
    window: timedelta = timedelta(seconds=1)
    source: str = "phase2-assumption (unverified against live provider)"

    def __post_init__(self) -> None:
        if self.requests < 1:
            raise ValueError("requests must be >= 1")


DEFAULT_POLICIES: dict[EndpointClass, RateLimitPolicy] = {
    EndpointClass.DISCOVERY: RateLimitPolicy(requests=10, window=timedelta(seconds=1)),
    EndpointClass.OPTION_CHAIN: RateLimitPolicy(requests=10, window=timedelta(seconds=1)),
    EndpointClass.QUOTE: RateLimitPolicy(requests=25, window=timedelta(seconds=1)),
    EndpointClass.HISTORICAL: RateLimitPolicy(requests=5, window=timedelta(seconds=1)),
    EndpointClass.ORDER: RateLimitPolicy(requests=10, window=timedelta(seconds=1)),
}


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """Whether a request may proceed now, and if not, how long to wait."""

    granted: bool
    wait: timedelta = timedelta(0)
    remaining: int = 0
    reason: str = ""


@dataclass
class _WindowState:
    hits: deque[datetime] = field(default_factory=deque)
    backoff_until: datetime | None = None


class RateLimitGovernor:
    """Sliding-window budget per endpoint class.

    Single-process here. `06` §4 specifies the budget is shared across roles via Redis;
    that sharing is a storage detail behind this same interface, so the call sites do
    not change when it lands.
    """

    def __init__(
        self,
        clock: Clock,
        policies: dict[EndpointClass, RateLimitPolicy] | None = None,
    ) -> None:
        self._clock = clock
        self._policies = dict(policies or DEFAULT_POLICIES)
        self._state: dict[EndpointClass, _WindowState] = {}

    def policy(self, endpoint: EndpointClass) -> RateLimitPolicy:
        return self._policies[endpoint]

    def acquire(self, endpoint: EndpointClass) -> BudgetDecision:
        """Attempt to consume one unit of budget."""
        policy = self._policies[endpoint]
        state = self._state.setdefault(endpoint, _WindowState())
        now = self._clock.now()

        if state.backoff_until is not None and now < state.backoff_until:
            return BudgetDecision(
                granted=False,
                wait=state.backoff_until - now,
                remaining=0,
                reason="provider-directed backoff in effect",
            )

        cutoff = now - policy.window
        while state.hits and state.hits[0] <= cutoff:
            state.hits.popleft()

        if len(state.hits) >= policy.requests:
            oldest = state.hits[0]
            return BudgetDecision(
                granted=False,
                wait=(oldest + policy.window) - now,
                remaining=0,
                reason="window exhausted",
            )

        state.hits.append(now)
        return BudgetDecision(granted=True, remaining=policy.requests - len(state.hits))

    def penalise(self, endpoint: EndpointClass, retry_after: timedelta) -> None:
        """Record a provider 429.

        Applies to the whole endpoint class, not just the caller that hit it: the limit
        belongs to the account, so one caller backing off while others keep firing would
        not resolve it.
        """
        state = self._state.setdefault(endpoint, _WindowState())
        state.backoff_until = self._clock.now() + retry_after

    def remaining(self, endpoint: EndpointClass) -> int:
        policy = self._policies[endpoint]
        state = self._state.setdefault(endpoint, _WindowState())
        cutoff = self._clock.now() - policy.window
        live = sum(1 for h in state.hits if h > cutoff)
        return max(policy.requests - live, 0)

    def chain_poll_interval(self, chain_count: int, fraction: float = 0.5) -> timedelta:
        """Derive chain-poll cadence from budget rather than hardcoding it.

        `06` §4: cadence is `(underlyings x expiries) / budget`. Adding an expiry
        therefore visibly costs cadence instead of silently causing throttling.
        """
        if chain_count <= 0:
            return timedelta(0)
        policy = self._policies[EndpointClass.OPTION_CHAIN]
        per_second = (policy.requests * fraction) / policy.window.total_seconds()
        if per_second <= 0:
            raise ValueError("option-chain budget is zero; cannot derive a cadence")
        return timedelta(seconds=chain_count / per_second)
