"""Transactional inbox — atomic, idempotent database application.

`docs/design/03-EVENT_MODEL.md` §4. A consumption marker written *after* a business
mutation is not enough: a crash in between leaves the mutation applied and the marker
absent, so redelivery applies it twice. For positions, portfolio, risk and orders that is
unacceptable.

The marker and the mutation therefore commit **together**:

    BEGIN
      INSERT INTO sys_event_inbox (subscriber, event_id) ON CONFLICT DO NOTHING
      IF inserted = 0: COMMIT and return          -- already applied, no-op
      ... perform the business mutation ...
    COMMIT

**What this guarantees, stated precisely:** *exactly-once database application per
`(subscriber, event_id)` transaction*. It is **not** global exactly-once processing.
Postgres can make the marker and the mutation atomic; it cannot make an arbitrary
external side effect — an HTTP call, a Telegram message, a broker order — happen exactly
once. Consumers with external side effects must additionally be idempotent at the
boundary, and `11-TRADING.md` §5 addresses the broker case.

Phase 1 defines the protocol and an in-memory implementation for testing the semantics.
The Postgres implementation lands with the storage layer; the contract is identical, so
the tests written against this protocol exercise both.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from oipulse.core.ids import EventId

__all__ = ["AlreadyApplied", "InMemoryInbox", "InboxGuard"]

R = TypeVar("R")


class AlreadyApplied(Exception):
    """Signals a no-op redelivery. Not an error condition."""


class InboxGuard(Protocol):
    """Claims `(subscriber, event_id)` inside the caller's transaction."""

    def claim(self, subscriber: str, event_id: EventId) -> bool:
        """Return True if this consumer should apply the event.

        False means it has already been applied and the caller must not mutate.
        The claim must be written in the **same transaction** as the mutation.
        """
        ...


class InMemoryInbox:
    """Reference implementation with transaction semantics, for tests.

    `apply_once` models the commit boundary: if the mutation raises, the claim is rolled
    back with it, so a crashed consumer can retry. That rollback is the entire point of
    the pattern — a claim that survived a failed mutation would suppress the retry and
    lose the write.
    """

    __slots__ = ("_applied",)

    def __init__(self) -> None:
        self._applied: set[tuple[str, str]] = set()

    def claim(self, subscriber: str, event_id: EventId) -> bool:
        key = (subscriber, str(event_id))
        if key in self._applied:
            return False
        self._applied.add(key)
        return True

    def _release(self, subscriber: str, event_id: EventId) -> None:
        self._applied.discard((subscriber, str(event_id)))

    def apply_once(
        self,
        subscriber: str,
        event_id: EventId,
        mutation: Callable[[], R],
    ) -> R | None:
        """Run *mutation* at most once per `(subscriber, event_id)`.

        Returns the mutation's result, or None if it had already been applied.
        On failure the claim is released so the event can be redelivered.
        """
        if not self.claim(subscriber, event_id):
            return None
        try:
            return mutation()
        except BaseException:
            # Same-transaction rollback: the marker does not survive a failed mutation.
            self._release(subscriber, event_id)
            raise

    def was_applied(self, subscriber: str, event_id: EventId) -> bool:
        return (subscriber, str(event_id)) in self._applied
