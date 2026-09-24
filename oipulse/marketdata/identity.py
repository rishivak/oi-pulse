"""Observation identity and identity confidence.

`docs/design/03-EVENT_MODEL.md` §2 and `06-UPSTOX_INTEGRATION.md` §6.

A **timestamp is not an identity.** A WebSocket feed may legitimately deliver several
distinct events for one instrument at the same timestamp resolution; keying on
`(instrument_id, observed_at, source)` would collapse them and destroy real information
that cannot be recovered.

Identity resolves in priority order:

    1. provider_event_id
    2. (feed_session_id, channel, channel_sequence)
    3. (instrument_id, observed_at, source, content_hash)

**Constraint C.** `channel_sequence` is meaningful *only within its feed session*. It
resets on reconnect, so it is never a global total order. Tier 2 therefore always
includes `feed_session_id`, and cross-session ordering falls back to `observed_at` plus a
stored session ordinal (`10-REPLAY.md` §3).

**Constraint D — now settled for Upstox V3.** External verification of the live V3
market-data feed observed, across two distinct feed sessions:

    provider_event_id : absent
    channel_sequence  : absent

So for Upstox V3 the resolution above always lands on tier 3. That is not a degraded
outcome to be worked around; it is the provider's actual behaviour, and the model
records it rather than papering over it.

**Four things are kept distinct, and conflating any two of them is the failure mode
this module exists to prevent:**

===========================  ==========================================================
provider identity            An id the provider itself assigns to an event. Upstox V3
                             supplies none. Never synthesized.
OI Pulse-derived identity    `content_digest` — our own deterministic hash of the
                             decoded payload. Deduplicates correctly. It is **not** a
                             provider event id and is never labelled as one.
local receive ordering       `received_seq`, a local monotonic arrival counter.
                             Diagnostics only. Arrival order is not market order and no
                             processing logic depends on it.
provider ordering            A sequence the provider supplies. Upstox V3 supplies none,
                             so **no provider ordering exists for this feed** and none
                             is inferred from the three above.
===========================  ==========================================================

Consequence for gap detection: **upstream provider-sequence gaps are not detectable on
Upstox V3**, because there is no sequence to be discontinuous. Missing data is detected
instead through connectivity — disconnect, reconnect, and the heartbeat budget — and
repaired by REST recovery. See `IdentityConfidence.supports_sequence_gap_detection` and
`marketdata/lifecycle.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "IdentityConfidence",
    "IdentityTier",
    "ObservationIdentity",
    "OrderingAuthority",
    "content_hash",
    "resolve_identity",
]


class OrderingAuthority(StrEnum):
    """What, if anything, is entitled to order two observations relative to each other.

    Separated from identity because the two are routinely conflated and the cost is
    silent: a local arrival counter used as an ordering authority produces a total order
    that looks authoritative and is not, since arrival order is not market order.
    """

    #: The provider supplies an in-session sequence. Not the case for Upstox V3.
    PROVIDER_SEQUENCE = "provider_sequence"
    #: Only `observed_at` plus the stored session ordinal (`10-REPLAY.md` §3).
    OBSERVED_TIME = "observed_time"

    @property
    def supports_gap_detection(self) -> bool:
        """Only a provider sequence can prove a message is *missing*."""
        return self is OrderingAuthority.PROVIDER_SEQUENCE


class IdentityTier(StrEnum):
    """How an observation's identity was established.

    The stored values are unchanged: `identity_tier` is a persisted column and renaming
    the values would rewrite history for no semantic gain. What the members mean is
    documented here instead.
    """

    #: Provider-assigned event id. **Upstox V3 never produces this tier.**
    PROVIDER_EVENT_ID = "provider_event_id"
    #: Provider-supplied in-session sequence. **Upstox V3 never produces this tier.**
    FEED_SEQUENCE = "feed_sequence"
    #: OI Pulse-derived digest of the decoded payload. The only tier Upstox V3 produces.
    #: Derived by us, never a provider event id.
    CONTENT_HASH = "content_hash"

    @property
    def is_provider_supplied(self) -> bool:
        """True when the provider, not OI Pulse, established this identity."""
        return self is not IdentityTier.CONTENT_HASH


class IdentityConfidence(StrEnum):
    """How trustworthy dedup and ordering are for a given row.

    Stored on **every** observation so a consumer always knows what the identity
    actually supports, instead of inheriting an assumption made at design time.
    """

    STRONG = "strong"  # provider-assigned id or in-session sequence
    WEAK = "weak"  # OI Pulse-derived digest only; dedup works, sequence gaps undetectable

    @property
    def supports_sequence_gap_detection(self) -> bool:
        """Only a STRONG identity can prove a message is missing.

        Under WEAK identity the system must **not** raise `WEBSOCKET_GAP` from sequence
        analysis — there is no sequence. Staleness and coverage remain available, and
        the data-quality surface says so rather than implying clean coverage
        (`06` §6).

        **This is the normal state for Upstox V3**, which supplies neither an event id
        nor a channel sequence. Missing data on that feed is found through connectivity
        and the heartbeat budget, not through provider ordering, and the difference is
        deliberate: an undetectable gap that is reported as "no gap" is a false
        guarantee, which is worse than an acknowledged blind spot.
        """
        return self is IdentityConfidence.STRONG


def content_hash(payload: dict[str, Any]) -> str:
    """Stable digest of a normalized payload.

    Sorted keys and a compact separator so the same logical content always hashes the
    same regardless of provider field ordering. Last-resort identity only.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservationIdentity:
    """The resolved identity of one observation, plus how it was obtained."""

    tier: IdentityTier
    confidence: IdentityConfidence
    provider_event_id: str | None = None
    feed_session_id: str | None = None
    channel: str | None = None
    channel_sequence: int | None = None
    content_digest: str | None = None
    #: Local monotonic arrival counter. Diagnostics only — no processing logic depends
    #: on arrival order, because arrival order is not market order.
    received_seq: int | None = None

    @property
    def dedup_key(self) -> tuple[str, ...]:
        """The tuple a storage layer must make unique for this tier.

        **Tier 1 is scoped by feed session (A-13).** Whether an Upstox event id is
        globally unique or restarts per session is unverified. The two mistakes are not
        symmetric:

        * Scoping when ids are global: at worst a genuine cross-session repeat of the
          same id is stored twice. Visible, and recoverable by reconciliation.
        * Not scoping when ids are session-scoped: the second session's events collide
          with the first's and are **silently discarded as duplicates** — live market
          data lost, with no error and no gap recorded.

        The second is silent and destructive, so the identity is scoped until the soak
        settles A-13. `provider_event_id` is still preserved in full on the row; only
        the uniqueness key includes the session.
        """
        if self.tier is IdentityTier.PROVIDER_EVENT_ID:
            return (
                "provider_event_id",
                str(self.feed_session_id),  # None for REST, which has no session
                str(self.provider_event_id),
            )
        if self.tier is IdentityTier.FEED_SEQUENCE:
            return (
                "feed_sequence",
                str(self.feed_session_id),
                str(self.channel),
                str(self.channel_sequence),
            )
        return ("content_hash", str(self.content_digest))

    @property
    def is_provider_supplied(self) -> bool:
        """Did the provider establish this identity, or did OI Pulse derive it?

        Callers and operators must be able to answer this without reading the tier
        table. For Upstox V3 the answer is always False.
        """
        return self.tier.is_provider_supplied

    @property
    def ordering_authority(self) -> OrderingAuthority:
        """What may order this observation against another.

        A provider sequence only counts when the provider actually supplied one. With
        Upstox V3 this is always `OBSERVED_TIME`: `received_seq` is deliberately not
        considered, because promoting a local arrival counter to an ordering authority
        would manufacture a guarantee the feed does not give.
        """
        if self.tier is IdentityTier.FEED_SEQUENCE and self.channel_sequence is not None:
            return OrderingAuthority.PROVIDER_SEQUENCE
        return OrderingAuthority.OBSERVED_TIME

    @property
    def is_session_scoped(self) -> bool:
        """True when this identity is only meaningful inside one feed session.

        Both live tiers are: sequences reset on reconnect (constraint C), and event-id
        scope is unverified (A-13), so both are keyed with the session.
        """
        return (
            self.tier in (IdentityTier.FEED_SEQUENCE, IdentityTier.PROVIDER_EVENT_ID)
            and self.feed_session_id is not None
        )


def resolve_identity(
    *,
    instrument_id: int,
    observed_at: datetime,
    source: str,
    payload: dict[str, Any],
    provider_event_id: str | None = None,
    feed_session_id: str | None = None,
    channel: str | None = None,
    channel_sequence: int | None = None,
    received_seq: int | None = None,
) -> ObservationIdentity:
    """Resolve the strongest identity the provider actually supplied.

    Degrades explicitly rather than assuming. A content hash always accompanies the
    stronger tiers too, so a later migration to a different tier can verify it is
    talking about the same rows.
    """
    digest = content_hash(
        {
            "instrument_id": instrument_id,
            "observed_at": observed_at.isoformat(),
            "source": source,
            **payload,
        }
    )

    if provider_event_id:
        return ObservationIdentity(
            tier=IdentityTier.PROVIDER_EVENT_ID,
            confidence=IdentityConfidence.STRONG,
            provider_event_id=provider_event_id,
            feed_session_id=feed_session_id,
            channel=channel,
            channel_sequence=channel_sequence,
            content_digest=digest,
            received_seq=received_seq,
        )

    if feed_session_id and channel_sequence is not None:
        return ObservationIdentity(
            tier=IdentityTier.FEED_SEQUENCE,
            confidence=IdentityConfidence.STRONG,
            feed_session_id=feed_session_id,
            channel=channel,
            channel_sequence=channel_sequence,
            content_digest=digest,
            received_seq=received_seq,
        )

    # Neither available. This is the **expected** path for Upstox V3, not a fallback
    # for a malformed frame: dedup still works off our own digest, and nothing can
    # prove a message is missing, which is stated rather than hidden.
    return ObservationIdentity(
        tier=IdentityTier.CONTENT_HASH,
        confidence=IdentityConfidence.WEAK,
        feed_session_id=feed_session_id,
        channel=channel,
        content_digest=digest,
        received_seq=received_seq,
    )
