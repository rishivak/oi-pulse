"""The normative lifecycle transition table — `08-SIGNALS.md` §3.

> The diagram in the design is illustrative; **the table is authoritative.** Any
> transition not listed is invalid and raises.

Two rules the diagram left ambiguous are explicit here and are each pinned by a test:

* **`CONFIRMED -> INVALIDATED` is permitted.** Confirmation is not absorbing; treating
  it as such would hide exactly the cases most worth studying.
* **A signal never returns to `FORMING`, and never leaves a terminal state.** A
  recurrence is a *new* signal with its own id, so research counts two occurrences
  rather than one implausibly long-lived entity.

Nothing is overwritten. Every transition appends to history, because the sequence of
states a signal passed through is itself research material.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from oipulse.core.errors import OIPulseError
from oipulse.signals.model import Signal, SignalStatus

__all__ = [
    "PERMITTED_TRANSITIONS",
    "IllegalTransition",
    "TransitionTrigger",
    "apply_transition",
    "is_permitted",
]


class TransitionTrigger(StrEnum):
    """Why a transition occurred. Recorded so history is interpretable."""

    ENTRY_PARTIAL = "entry_conditions_partially_met"
    ENTRY_FULL = "all_entry_conditions_met"
    EVIDENCE_UPDATED = "evidence_updated"
    PERSISTENCE_MET = "persistence_criteria_met"
    INVALIDATION_TRUE = "invalidation_condition_true"
    EVIDENCE_DECAYED = "evidence_decayed_below_threshold"
    EXPIRED = "expires_at_reached"


class IllegalTransition(OIPulseError):
    """A transition the normative table does not list. Raised, never coerced."""


#: The authoritative table from `08` §3, transcribed exactly.
PERMITTED_TRANSITIONS: dict[SignalStatus | None, frozenset[SignalStatus]] = {
    None: frozenset({SignalStatus.FORMING}),
    SignalStatus.FORMING: frozenset(
        {SignalStatus.ACTIVE, SignalStatus.FADED, SignalStatus.EXPIRED}
    ),
    SignalStatus.ACTIVE: frozenset(
        {
            SignalStatus.ACTIVE,  # self-transition: evidence updated
            SignalStatus.CONFIRMED,
            SignalStatus.INVALIDATED,
            SignalStatus.FADED,
            SignalStatus.EXPIRED,
        }
    ),
    SignalStatus.CONFIRMED: frozenset(
        {
            SignalStatus.CONFIRMED,  # self-transition: evidence updated
            SignalStatus.INVALIDATED,
            SignalStatus.FADED,
            SignalStatus.EXPIRED,
        }
    ),
    # Terminal. Deliberately empty rather than absent, so a lookup returns "nothing
    # permitted" instead of raising a KeyError that a caller might catch and ignore.
    SignalStatus.INVALIDATED: frozenset(),
    SignalStatus.EXPIRED: frozenset(),
    SignalStatus.FADED: frozenset(),
}


def is_permitted(current: SignalStatus | None, target: SignalStatus) -> bool:
    return target in PERMITTED_TRANSITIONS.get(current, frozenset())


def apply_transition(
    signal: Signal,
    target: SignalStatus,
    trigger: TransitionTrigger,
    at: datetime,
    *,
    strength: Decimal | None = None,
) -> Signal:
    """Move a signal to `target`, appending to history. Returns a NEW signal.

    `at` is supplied, never read from a clock: a transition applied during replay must
    land at the replayed instant, not at the moment the replay happens to run.
    """
    if not is_permitted(signal.status, target):
        raise IllegalTransition(
            f"{signal.signal_type}: {signal.status.value} -> {target.value} is not in the "
            f"normative transition table (08-SIGNALS.md §3)"
            + (
                "; terminal states are never left, and a recurrence is a new signal"
                if signal.status.is_terminal
                else ""
            )
        )
    return replace(
        signal,
        status=target,
        strength=signal.strength if strength is None else strength,
        updated_at=at,
        history=(*signal.history, (f"{target.value}:{trigger.value}", at)),
    )
