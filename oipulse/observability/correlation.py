"""Correlation context.

`docs/design/16-OBSERVABILITY.md` §2: a correlation id originates at ingestion (or an API
request, or a job run) and propagates through every derived operation via context-local
storage — **never threaded manually through function signatures**. Threading it by hand
guarantees it is dropped somewhere, and the one place it is dropped is the one place the
trail is needed.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from oipulse.core.ids import CausationId, CorrelationId, new_correlation_id

__all__ = [
    "bind_correlation",
    "correlation_scope",
    "current_causation_id",
    "current_correlation_id",
]

_correlation: contextvars.ContextVar[CorrelationId | None] = contextvars.ContextVar(
    "oipulse_correlation_id", default=None
)
_causation: contextvars.ContextVar[CausationId | None] = contextvars.ContextVar(
    "oipulse_causation_id", default=None
)


def current_correlation_id() -> CorrelationId | None:
    return _correlation.get()


def current_causation_id() -> CausationId | None:
    return _causation.get()


@contextmanager
def correlation_scope(
    correlation_id: CorrelationId | None = None,
    causation_id: CausationId | None = None,
) -> Iterator[CorrelationId]:
    """Establish a correlation scope, allocating an id when none is supplied.

    Restores the previous values on exit, so nested scopes compose and an inner scope
    cannot leak into its caller.
    """
    cid = correlation_id or new_correlation_id()
    ctoken = _correlation.set(cid)
    ztoken = _causation.set(causation_id)
    try:
        yield cid
    finally:
        _correlation.reset(ctoken)
        _causation.reset(ztoken)


def bind_correlation(correlation_id: CorrelationId) -> None:
    """Set the correlation id for the current context without a scope.

    For entry points that own the whole context — a worker task, a request handler.
    """
    _correlation.set(correlation_id)
