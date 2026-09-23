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

**Constraint D.** Whether Upstox supplies a usable event id or channel sequence is
**unverified** (assumption A-1). Rather than assume, every observation records the
`IdentityConfidence` actually achieved, and downstream gap detection claims no more than
that confidence supports.
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
    "content_hash",
    "resolve_identity",
]


class IdentityTier(StrEnum):
    PROVIDER_EVENT_ID = "provider_event_id"
    FEED_SEQUENCE = "feed_sequence"
    CONTENT_HASH = "content_hash"


class IdentityConfidence(StrEnum):
    """How trustworthy dedup and ordering are for a given row.

    Stored on **every** observation so a consumer always knows what the identity
    actually supports, instead of inheriting an assumption made at design time.
    """

    STRONG = "strong"  # provider-assigned id or in-session sequence
    WEAK = "weak"  # content hash only; dedup works, sequence gaps undetectable

    @property
    def supports_sequence_gap_detection(self) -> bool:
        """Only a STRONG identity can prove a message is missing.

        Under WEAK identity the system must **not** raise `WEBSOCKET_GAP` from sequence
        analysis — there is no sequence. Staleness and coverage remain available, and
        the data-quality surface says so rather than implying clean coverage
        (`06` §6).
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
        """The tuple a storage layer must make unique for this tier."""
        if self.tier is IdentityTier.PROVIDER_EVENT_ID:
            return ("provider_event_id", str(self.provider_event_id))
        if self.tier is IdentityTier.FEED_SEQUENCE:
            return (
                "feed_sequence",
                str(self.feed_session_id),
                str(self.channel),
                str(self.channel_sequence),
            )
        return ("content_hash", str(self.content_digest))

    @property
    def is_session_scoped(self) -> bool:
        """True when ordering information is valid only inside one feed session."""
        return self.tier is IdentityTier.FEED_SEQUENCE


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

    # Neither available: dedup still works, but nothing can prove a message is missing.
    return ObservationIdentity(
        tier=IdentityTier.CONTENT_HASH,
        confidence=IdentityConfidence.WEAK,
        feed_session_id=feed_session_id,
        channel=channel,
        content_digest=digest,
        received_seq=received_seq,
    )
