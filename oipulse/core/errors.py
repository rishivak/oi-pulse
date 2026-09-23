"""Domain error hierarchy.

Errors named here are part of the architectural contract: several appear in
`docs/design/15-TESTING.md` §2 as the exception a *required failing test* asserts. They
are defined in Phase 1 so that later phases raise the agreed type rather than inventing
one, and so the tests that must fail can be written against a stable name.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "FeatureAccessError",
    "InsufficientHistoryError",
    "InvalidStateTransition",
    "OIPulseError",
    "QualityRequirementsUnmet",
    "TemporalBoundError",
    "UnboundedQueryError",
    "UnsafeRetryError",
]


class OIPulseError(Exception):
    """Base for every error the platform raises deliberately."""


class ConfigurationError(OIPulseError):
    """Invalid or missing configuration. Raised at startup, never later.

    `14-DEPLOYMENT.md` §3: the process refuses to start rather than failing midway.
    """


class TemporalBoundError(OIPulseError):
    """A temporal bound is incoherent — for example a knowledge horizon before its valid time."""


class UnboundedQueryError(OIPulseError):
    """A repository read was attempted without a temporal bound.

    `05-DATA_LIFECYCLE_PIT.md` §3: there is no unbounded query for callers to reach for.
    The type signature normally prevents this; the error covers dynamic call paths.
    """


class FeatureAccessError(OIPulseError):
    """A feature was requested before its `available_at`.

    The engine refuses rather than warning (`09-RESEARCH.md` §2). Asserted by
    `15-TESTING.md` §2.2 and §2.3.
    """


class QualityRequirementsUnmet(OIPulseError):
    """A feature declined to compute because its quality requirements were not met.

    Returning *unavailable* is correct; returning a number derived from missing inputs is
    not (`07-ANALYTICS.md` §2).
    """


class InsufficientHistoryError(OIPulseError):
    """A statistic needs more history than exists.

    Reported rather than computed from a short window — withholding a statistic beats
    fabricating one (`07-ANALYTICS.md` §4.2).
    """


class UnsafeRetryError(OIPulseError):
    """A retry was attempted while the prior outcome is unknown.

    `11-TRADING.md` §5: never resubmit from `UNKNOWN`. Resolution is by reconciliation
    against broker truth, never by inference. Asserted by `15-TESTING.md` §2.5.
    """


class InvalidStateTransition(OIPulseError):
    """A state machine was asked for a transition its table does not permit."""

    def __init__(self, machine: str, from_state: str, to_state: str) -> None:
        super().__init__(f"{machine}: transition {from_state} -> {to_state} is not permitted")
        self.machine = machine
        self.from_state = from_state
        self.to_state = to_state
