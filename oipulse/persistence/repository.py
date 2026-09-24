"""Temporal repository base — the three query modes, and no fourth.

`docs/design/05-DATA_LIFECYCLE_PIT.md` §3: *"There is no unbounded query for callers to
reach for — a repository call without a mode is a compile-time/lint error."*

Two mechanisms enforce that here:

1. **Signature.** `TemporalRepository.fetch` takes a `TemporalBound`. There is no overload
   without one, so the ordinary path cannot express an unbounded read.
2. **Runtime guard.** `resolve_bound` rejects `None` and rejects an incoherent bound, for
   the dynamic call paths a signature cannot cover.

`tools/check_temporal_repository.py` adds the third: a static scan rejecting any public
read method on a repository subclass that does not accept a bound.

Phase 1 provides the base only. Concrete repositories arrive with the observation store
in Phase 2; this module deliberately contains no SQL and no driver import, so `core` and
the contract can be tested before a database exists.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from oipulse.core.errors import TemporalBoundError, UnboundedQueryError
from oipulse.core.timemode import (
    KnowledgeAt,
    MarketTruthAt,
    TemporalBound,
    TradableInformationAt,
)

__all__ = ["ResolvedBound", "TemporalRepository", "resolve_bound"]


@dataclass(frozen=True, slots=True)
class ResolvedBound:
    """A temporal bound reduced to the predicates a storage layer applies.

    Keeping this separate from `TemporalBound` means a storage adapter never re-derives
    the semantics — there is one translation from mode to predicate, so a new adapter
    cannot quietly implement `knowledge_at` as `observed_at <= t` and lose the guarantee.
    """

    observed_at_max: datetime
    ingested_at_max: datetime
    available_at_max: datetime | None
    mode: str

    @property
    def filters_availability(self) -> bool:
        return self.available_at_max is not None


def resolve_bound(bound: TemporalBound | None) -> ResolvedBound:
    """Translate a temporal bound into storage predicates.

    Raises `UnboundedQueryError` when no bound is supplied — the dynamic-path counterpart
    to the type signature.
    """
    if bound is None:
        raise UnboundedQueryError(
            "a temporal bound is required; use market_truth_at(), knowledge_at() or "
            "tradable_information_at()"
        )

    if isinstance(bound, MarketTruthAt):
        if bound.knowledge_as_of < bound.valid_time:
            # A knowledge horizon before the valid time is incoherent: it asks what we
            # knew about the future. 10-REPLAY.md §2 disallows K < T for the same reason.
            raise TemporalBoundError(
                f"knowledge_as_of ({bound.knowledge_as_of.isoformat()}) precedes "
                f"valid_time ({bound.valid_time.isoformat()})"
            )
        return ResolvedBound(
            observed_at_max=bound.valid_time,
            ingested_at_max=bound.knowledge_as_of,
            available_at_max=None,
            mode="market_truth_at",
        )

    if isinstance(bound, KnowledgeAt):
        return ResolvedBound(
            observed_at_max=bound.t,
            ingested_at_max=bound.t,
            available_at_max=None,
            mode="knowledge_at",
        )

    if isinstance(bound, TradableInformationAt):
        return ResolvedBound(
            observed_at_max=bound.t,
            ingested_at_max=bound.t,
            available_at_max=bound.t,
            mode="tradable_information_at",
        )

    raise UnboundedQueryError(f"unrecognised temporal bound: {type(bound).__name__!r}")


class TemporalRepository[T](abc.ABC):
    """Base for every repository that reads temporally-scoped data.

    Subclasses implement `_fetch`, which receives an already-resolved bound. They never
    see the raw mode, so they cannot re-interpret it.
    """

    #: Set by subclasses that read derived data carrying `available_at`.
    #: Raw observations have no availability, so `tradable_information_at` is not a valid
    #: mode for them (`12-API_SPEC.md` §2).
    supports_availability: bool = False

    def fetch(self, bound: TemporalBound, **criteria: object) -> Sequence[T]:
        resolved = resolve_bound(bound)
        if resolved.filters_availability and not self.supports_availability:
            raise TemporalBoundError(
                f"{type(self).__name__} holds raw observations, which have no "
                f"available_at; use knowledge_at() or market_truth_at() instead of "
                f"tradable_information_at()"
            )
        return self._fetch(resolved, **criteria)

    @abc.abstractmethod
    def _fetch(self, bound: ResolvedBound, **criteria: object) -> Sequence[T]:
        """Apply *bound* to storage. Implemented per store in Phase 2 and later."""
        raise NotImplementedError
