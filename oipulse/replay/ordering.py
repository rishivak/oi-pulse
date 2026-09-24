"""Deterministic cross-session observation ordering — `10-REPLAY.md` §3.

> `(observed_at, channel_sequence, id)` is **not** a total order. `channel_sequence` is
> scoped to a feed session and resets on reconnect, so two observations from different
> sessions are not comparable by it — and a replay spanning a reconnect would order
> them arbitrarily.

The replay ordering key is therefore explicitly cross-session:

```
1. observed_at            market time
2. feed_session_ordinal   sessions ordered by their first ingested_at, assigned once
                          and stored — not re-derived per run
3. channel_sequence       within a session, where the provider supplies it
4. id                     stable tiebreaker; guarantees totality
```

Step 2 is what makes the key total across reconnects. Step 4 guarantees a deterministic
result **even when the provider supplies no sequence at all** — which is the verified
state of Upstox V3 (AD-30): `provider_event_id` and `channel_sequence` are both absent.
Ordering then degrades to `(observed_at, session, id)`, still deterministic, merely less
faithful to true arrival order.

> Determinism never depends on an unverified provider guarantee.

That is the load-bearing sentence. Nothing here synthesizes a sequence, and the absent
components sort as a declared sentinel rather than being invented.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from oipulse.marketdata.observations import MarketObservation

__all__ = [
    "MISSING_SEQUENCE",
    "ObservationOrderKey",
    "SessionOrdinals",
    "order_key",
    "replay_order",
]

#: Sorts before any real sequence. Chosen rather than 0 because a provider *could*
#: legitimately emit sequence 0, and conflating "no sequence" with "sequence zero"
#: would make two genuinely different orderings compare equal.
MISSING_SEQUENCE = -1


@dataclass(frozen=True, slots=True)
class SessionOrdinals:
    """Feed-session ordinals, assigned once and supplied — never re-derived per run.

    Re-deriving would make the order depend on which sessions happened to be loaded,
    so a replay over a narrower window could order the same two observations
    differently from a replay over a wider one. `10` §3 says "assigned once and
    stored" for exactly that reason.
    """

    by_session_id: tuple[tuple[str, int], ...] = ()

    @staticmethod
    def of(mapping: dict[str, int]) -> SessionOrdinals:
        return SessionOrdinals(tuple(sorted(mapping.items())))

    def ordinal(self, session_id: str | None) -> int:
        """The stored ordinal, or a sentinel for an observation with no session.

        REST rows carry no feed session. They sort before every streamed row at the
        same market time, which is the honest placement: a REST snapshot is the
        cross-sectional anchor that streamed updates are merged onto (`04` §4).
        """
        if session_id is None:
            return MISSING_SEQUENCE
        for candidate, ordinal in self.by_session_id:
            if candidate == session_id:
                return ordinal
        # An unknown session cannot be placed against the stored ones. It sorts last
        # at its market time and does so deterministically, rather than being dropped.
        return len(self.by_session_id)


#: `(observed_at, feed_session_ordinal, channel_sequence, id)`.
ObservationOrderKey = tuple[datetime, int, int, int]


def order_key(
    observation: MarketObservation,
    ordinals: SessionOrdinals,
    identity: int,
) -> ObservationOrderKey:
    """The four-part key from `10` §3.

    `identity` is the stable row id supplied by the caller. It is a **parameter**
    rather than something read off the observation, because the in-memory store and
    PostgreSQL assign ids differently and the tiebreaker has to be the same one the
    original run used.
    """
    sequence = observation.identity.channel_sequence
    return (
        observation.observed_at,
        ordinals.ordinal(observation.identity.feed_session_id),
        MISSING_SEQUENCE if sequence is None else sequence,
        identity,
    )


def replay_order(
    observations: Sequence[MarketObservation],
    ordinals: SessionOrdinals | None = None,
    *,
    identity_of: Callable[[MarketObservation], int] | None = None,
) -> tuple[MarketObservation, ...]:
    """Total, deterministic ordering of observations for replay.

    The result is independent of the input order, so a database returning rows in a
    different order cannot change a replay. A test asserts exactly that by feeding the
    reversed sequence.

    `identity_of` defaults to the observation's content digest reduced to an integer:
    stable across processes and derived from the row's own content, so two runs agree
    without a shared database. A caller holding real row ids should supply them.
    """
    resolved = ordinals or SessionOrdinals()
    identify = identity_of or _digest_identity
    return tuple(sorted(observations, key=lambda obs: order_key(obs, resolved, identify(obs))))


def _digest_identity(observation: MarketObservation) -> int:
    """A stable integer tiebreaker derived from the observation's own identity.

    Uses the resolved dedup key rather than `hash()`: Python's string hashing is
    randomised per process, so a `hash()`-based tiebreaker would order two runs
    differently on the same data — precisely the hidden non-determinism `10` §3's
    replay-versus-live comparison exists to catch.
    """
    import hashlib

    digest = hashlib.sha256("|".join(observation.identity.dedup_key).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)
