"""The live-execution capability gate — the structural half of the safety barrier.

Phase 10 brief §4:

> Enforce `LIVE_EXECUTION_ENABLED = false` ... but do NOT rely solely on a
> configuration flag. There must be structural protection such that Phase 10 cannot
> submit a real order in the current deployment.

So there are two layers, and the second is the one that matters.

**The flag.** `LIVE_EXECUTION_ENABLED` is a module constant, not a setting read from
the environment. `11-TRADING.md` §9 requires live trading to be feature-flagged off;
making it a constant means flipping it is a code change that appears in a diff and
trips a guard, rather than an environment variable somebody exports.

**The absence.** Even with the flag set to True, no order could be submitted, because
`UpstoxBrokerAdapter` contains no HTTP client, no credential access and no
request-encoding code. `06-UPSTOX_INTEGRATION.md` established the market-data client
only; the order-placement wire format was never established, and Phase 10 does not
invent one (brief §28). The adapter's submitting methods raise unconditionally.

That asymmetry is deliberate. A flag alone can be flipped by accident or by a config
merge. An implementation that does not exist cannot be enabled by anything, and
`tools/check_live_execution_barrier.py` verifies mechanically that it still does not
exist.

### Why the capability is a value rather than a boolean

An adapter declares what it *can* do, and the caller asks for what it *needs*. A
boolean `is_live` invites `if not is_live: ...` branches scattered through call
sites, each of which is a place to get the polarity backwards. A capability request
has one enforcement point, here.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "LIVE_EXECUTION_ENABLED",
    "ExecutionCapability",
    "LiveExecutionDisabled",
    "require_capability",
]

#: `11-TRADING.md` §9 and the Phase 10 brief §4. A constant, not configuration:
#: enabling live execution must be a reviewable code change, and even then the
#: submitting implementation does not exist.
LIVE_EXECUTION_ENABLED = False


class ExecutionCapability(StrEnum):
    """What an adapter is able to do.

    `SIMULATE` covers everything the paper venue does. `LIVE_SUBMIT` is declared so
    the boundary is expressible; nothing in this deployment grants it.
    """

    #: Fills are produced by a model, against stored or live market data.
    SIMULATE = "SIMULATE"
    #: Read-only provider queries used by reconciliation. Distinct from submission
    #: because reading broker truth is safe and is the whole point of `11` §6.
    QUERY_PROVIDER = "QUERY_PROVIDER"
    #: Place, modify or cancel a real order at a real venue. Never granted here.
    LIVE_SUBMIT = "LIVE_SUBMIT"


class LiveExecutionDisabled(RuntimeError):
    """Raised whenever anything asks for a live-submission capability.

    Not a warning and not a no-op. A caller that believed it had submitted an order
    and had not would be in a far worse position than one that saw an exception: the
    first silently holds a position it does not have, the second stops.
    """


def require_capability(
    granted: frozenset[ExecutionCapability], needed: ExecutionCapability, *, who: str
) -> None:
    """Assert an adapter may do what is being asked of it.

    The single enforcement point. Every submitting call goes through here, so "can
    this reach a real venue?" is answered in one function rather than at every call
    site — and for `LIVE_SUBMIT` it is answered by raising.
    """
    if needed is ExecutionCapability.LIVE_SUBMIT:
        raise LiveExecutionDisabled(
            f"{who} requested LIVE_SUBMIT. Live execution is disabled "
            f"(LIVE_EXECUTION_ENABLED={LIVE_EXECUTION_ENABLED}) and, independently of "
            f"that flag, no adapter in this codebase can place a real order: the "
            f"Upstox order wire format was never established and is not invented "
            f"here. Enabling live trading requires implementing that adapter, the "
            f"three documented gates in 11-TRADING.md §9, and a clean reconciliation "
            f"run."
        )
    if needed not in granted:
        raise LiveExecutionDisabled(
            f"{who} requested {needed.value} but is granted only {sorted(c.value for c in granted)}"
        )
