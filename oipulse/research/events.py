"""Event definitions and deterministic detection — `09-RESEARCH.md` §3.

An event is *"condition X occurred at this market time, and here is what was knowable
then"*. The condition is an evaluable predicate over `MarketState` plus features, with
**exact feature versions pinned** — a feature bumping to v3 must not silently change
which events a historical study finds.

Detection reuses Phase 4 features and Phase 5 signals. It **creates no second analytics
implementation**: a threshold-crossing event reads a `MetricValue` that the Phase 4
engine produced, and a signal-transition event reads a `Signal` the Phase 5 evaluator
produced.

Every event carries four distinct timestamps, and the distinction is the point:

| | |
|---|---|
| `market_time` | when the condition was true in the market |
| `knowledge_horizon` | what was known when the condition was evaluated |
| `detected_at` | when detection ran |
| `available_at` | when the event itself became legitimately consumable |

`decision_time` is deliberately **not** a fifth field: it is a semantic role, an
argument to a query, never persisted (`05` §2).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from oipulse.analytics.values import MetricValue
from oipulse.research.access import FeatureAccessError, PointInTimeAccessor
from oipulse.signals.model import SignalStatus

__all__ = [
    "EVENT_DEFINITIONS",
    "DuplicateEventDefinition",
    "Event",
    "EventDefinition",
    "EventDefinitionRegistry",
    "EventKind",
    "UnknownEventDefinition",
    "detect_over",
    "research_digest",
    "signal_transition_event",
    "threshold_crossing_event",
]


def research_digest(payload: dict[str, Any]) -> str:
    """Stable digest. Sorted keys, compact separators, `Decimal` via `str`.

    Used for every content address in this layer, so two artifacts are comparable by
    hash without anybody agreeing on a serialisation convention first.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class EventKind(StrEnum):
    """The event types the specification names (`09` §3, `18-ROADMAP.md` Phase 6).

    Deliberately closed. Inventing further kinds beyond the authoritative list is
    exactly the feature sprawl the brief forbids.
    """

    #: A Phase 5 signal entered a given lifecycle state (`09` §5).
    SIGNAL_TRANSITION = "signal_transition"
    #: A Phase 4 feature crossed a declared threshold.
    FEATURE_THRESHOLD_CROSSING = "feature_threshold_crossing"


class DuplicateEventDefinition(ValueError):
    """`(event_id, version)` registered twice.

    Raised rather than overwritten: the second registration would change which events
    a stored historical study is understood to have found.
    """


class UnknownEventDefinition(KeyError):
    """No such `(event_id, version)`."""


@dataclass(frozen=True, slots=True)
class Event:
    """One detected occurrence.

    `evidence_refs` holds the identity tuples of the metric values or signals that
    caused detection, so an event resolves back through the Phase 5 evidence chain to
    `MarketState` and the raw observations. References, not copies.
    """

    event_id: str
    event_version: int
    kind: EventKind
    underlying_id: int
    expiry_id: int | None
    market_time: datetime
    knowledge_horizon: datetime
    detected_at: datetime
    available_at: datetime
    build_context_id: str
    #: What triggered it, as resolvable references.
    evidence_refs: tuple[str, ...] = ()
    #: The observed value that crossed, or the status entered. Never a rendered blob.
    trigger_value: str | None = None
    quality_status: str = "ok"

    def __post_init__(self) -> None:
        if self.available_at < self.market_time:
            raise ValueError(
                f"{self.event_id}: available_at precedes market_time; an event cannot "
                f"be consumable before it happened"
            )

    @property
    def occurrence_id(self) -> str:
        """Deterministic identity. Excludes `detected_at`: when detection ran is
        execution metadata, not part of what the event *is*."""
        return (
            "evt_"
            + research_digest(
                {
                    "event_id": self.event_id,
                    "event_version": self.event_version,
                    "kind": self.kind.value,
                    "underlying_id": self.underlying_id,
                    "expiry_id": self.expiry_id,
                    "market_time": self.market_time.isoformat(),
                    "knowledge_horizon": self.knowledge_horizon.isoformat(),
                    "build_context_id": self.build_context_id,
                    "trigger_value": self.trigger_value,
                }
            )[:32]
        )


#: A detector is pure: accessor plus evaluation instant in, events out.
DetectorFn = Callable[[PointInTimeAccessor, datetime, "EventDefinition"], tuple[Event, ...]]


@dataclass(frozen=True, slots=True)
class EventDefinition:
    """A versioned, content-addressed event condition."""

    event_id: str
    version: int
    kind: EventKind
    definition: str
    #: Exact `(identifier, version)` pairs the condition consumes.
    requires_features: tuple[tuple[str, int], ...] = ()
    requires_signal_types: tuple[tuple[str, int], ...] = ()
    #: Declared thresholds. Part of the content address, so an edit is visible.
    config: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    detector: DetectorFn | None = None
    #: States with UNRELIABLE quality are excluded by default (`09` §3).
    exclude_unreliable: bool = True

    @property
    def key(self) -> tuple[str, int]:
        return (self.event_id, self.version)

    @property
    def label(self) -> str:
        return f"{self.event_id}@v{self.version}"

    @property
    def content_digest(self) -> str:
        """Content address of the definition itself.

        A threshold change produces a new digest, so studies run before and after it
        are distinguishable rather than pooled — the "silent formula drift" bias in
        `09` §6, applied to event conditions.
        """
        return (
            "evd_"
            + research_digest(
                {
                    "event_id": self.event_id,
                    "version": self.version,
                    "kind": self.kind.value,
                    "requires_features": [f"{i}@v{v}" for i, v in sorted(self.requires_features)],
                    "requires_signal_types": [
                        f"{i}@v{v}" for i, v in sorted(self.requires_signal_types)
                    ],
                    "config": dict(sorted(self.config)),
                    "exclude_unreliable": self.exclude_unreliable,
                }
            )[:24]
        )

    def get(self, name: str, default: str) -> str:
        for key, value in self.config:
            if key == name:
                return value
        return default

    def decimal(self, name: str, default: str) -> Decimal:
        return Decimal(self.get(name, default))

    def detect(self, accessor: PointInTimeAccessor, at: datetime) -> tuple[Event, ...]:
        if self.detector is None:
            raise UnknownEventDefinition(f"{self.label} has no detector")
        return self.detector(accessor, at, self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "version": self.version,
            "kind": self.kind.value,
            "definition": self.definition,
            "requires_features": [f"{i}@v{v}" for i, v in self.requires_features],
            "requires_signal_types": [f"{i}@v{v}" for i, v in self.requires_signal_types],
            "config": dict(self.config),
            "exclude_unreliable": self.exclude_unreliable,
            "content_digest": self.content_digest,
        }


class EventDefinitionRegistry:
    """All declared event definitions. Versions coexist, as feature versions do."""

    __slots__ = ("_by_key",)

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], EventDefinition] = {}

    def register(self, definition: EventDefinition) -> EventDefinition:
        if definition.key in self._by_key:
            raise DuplicateEventDefinition(
                f"{definition.label} is already registered. Changing an event "
                f"condition's meaning requires a NEW version; overwriting would "
                f"change which events a stored study is understood to have found."
            )
        self._by_key[definition.key] = definition
        return definition

    def get(self, event_id: str, version: int) -> EventDefinition:
        try:
            return self._by_key[(event_id, version)]
        except KeyError as exc:
            raise UnknownEventDefinition(f"{event_id}@v{version} is not registered") from exc

    def all(self) -> tuple[EventDefinition, ...]:
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    def __len__(self) -> int:
        return len(self._by_key)


EVENT_DEFINITIONS = EventDefinitionRegistry()


# ----------------------------------------------------------------- detectors


def threshold_crossing_event(
    accessor: PointInTimeAccessor, at: datetime, definition: EventDefinition
) -> tuple[Event, ...]:
    """A pinned feature crossed its declared threshold, as of `at`.

    Reads a value the Phase 4 engine produced; no formula is recomputed here. The
    accessor refuses anything not available at `at`, so an event can never be detected
    from information that had not arrived.
    """
    if not definition.requires_features:
        return ()
    feature_id, version = definition.requires_features[0]
    threshold = definition.decimal("threshold", "0")
    direction = definition.get("direction", "above")

    try:
        value = accessor.get_feature(feature_id, version, at)
    except FeatureAccessError:
        # Not available at `at`. Not an error for a scan: the condition simply could
        # not be evaluated then, which is different from being false.
        return ()

    if definition.exclude_unreliable and value.quality_status.value == "unreliable":
        return ()
    if not isinstance(value.value, Decimal | int) or isinstance(value.value, bool):
        return ()

    observed = Decimal(value.value)
    crossed = observed >= threshold if direction == "above" else observed <= threshold
    if not crossed:
        return ()

    return (
        Event(
            event_id=definition.event_id,
            event_version=definition.version,
            kind=EventKind.FEATURE_THRESHOLD_CROSSING,
            underlying_id=_underlying_of(value),
            expiry_id=_expiry_of(value),
            market_time=value.observed_at,
            knowledge_horizon=value.knowledge_horizon,
            detected_at=at,
            # An event is consumable no earlier than the value that revealed it.
            available_at=max(value.available_at, value.observed_at),
            build_context_id=value.build_context_id,
            evidence_refs=(f"{value.feature_id}@v{value.feature_version}:{value.inputs_digest}",),
            trigger_value=str(observed),
            quality_status=value.quality_status.value,
        ),
    )


def signal_transition_event(
    accessor: PointInTimeAccessor, at: datetime, definition: EventDefinition
) -> tuple[Event, ...]:
    """A Phase 5 signal entered the declared lifecycle state (`09` §5)."""
    target = definition.get("status", SignalStatus.ACTIVE.value)
    wanted = {t for t, _ in definition.requires_signal_types}
    out: list[Event] = []
    for candidate in accessor.available_signals(at):
        if wanted and candidate.signal_type not in wanted:
            continue
        if candidate.status.value != target:
            continue
        if definition.exclude_unreliable and candidate.quality_status.value == "unreliable":
            continue
        out.append(
            Event(
                event_id=definition.event_id,
                event_version=definition.version,
                kind=EventKind.SIGNAL_TRANSITION,
                underlying_id=candidate.identity.underlying_id,
                expiry_id=candidate.identity.expiry_id,
                market_time=candidate.identity.market_time,
                knowledge_horizon=candidate.identity.knowledge_horizon,
                detected_at=at,
                available_at=max(candidate.available_at, candidate.identity.market_time),
                build_context_id=candidate.identity.build_context_id,
                evidence_refs=(f"signal:{candidate.signal_id}",),
                trigger_value=candidate.status.value,
                quality_status=candidate.quality_status.value,
            )
        )
    # Deterministic: detection order must never depend on collection iteration order.
    return tuple(sorted(out, key=lambda e: (e.market_time, e.occurrence_id)))


def _underlying_of(value: MetricValue) -> int:
    if value.scope.kind.value == "underlying":
        return int(value.scope.ref)
    return 0


def _expiry_of(value: MetricValue) -> int | None:
    if value.scope.kind.value == "expiry":
        return int(value.scope.ref)
    return None


def detect_over(
    accessor: PointInTimeAccessor,
    definition: EventDefinition,
    instants: Sequence[datetime],
) -> tuple[Event, ...]:
    """Scan a series of evaluation instants, deduplicating by occurrence identity.

    Deduplication is by `occurrence_id`, so one condition holding across several scan
    instants yields one event rather than one per scan — before any sampling policy is
    applied. Overlap handling is a separate, declared concern (`sampling.py`).
    """
    seen: dict[str, Event] = {}
    for instant in sorted(instants):
        for event in definition.detect(accessor, instant):
            seen.setdefault(event.occurrence_id, event)
    return tuple(sorted(seen.values(), key=lambda e: (e.market_time, e.occurrence_id)))
