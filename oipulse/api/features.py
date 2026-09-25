"""`/features` — the registry, served.

`docs/design/12-API_SPEC.md` §161:

| GET | `/features` | the registry — every feature, all versions, definitions, units, conventions, availability delay |
| GET | `/features/{id}/versions/{v}` | full definition |
| GET | `/features/{id}/values` | time series for a scope |

Serving the registry is what makes conventions never implicit: a user hovering GEX sees
the exact dealer convention in force, rather than having to read the source.

**Only the Phase 4 surface is implemented here.** `/features/{id}/values` requires a
`metric_values` reader, which needs a database this phase does not wire; it answers 503
naming the missing dependency rather than returning an empty series, because an empty
series is indistinguishable from "this feature produced nothing".

Availability filtering applies on this endpoint family: results are filtered to
`available_at <= decision_time`, and `decision_time` is a **request parameter**,
defaulting to `knowledge_time` — never a stored field (`12` §2, `05` §2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from oipulse.analytics.registry import REGISTRY, UnknownFeature

router = APIRouter(prefix="/features", tags=["features"])

__all__ = ["router"]


@router.get("")
async def list_features(
    scope: str | None = Query(None, description="filter by scope kind"),
) -> dict[str, Any]:
    """Every registered feature, every version.

    All versions are listed, not just the latest: v1 and v2 coexist and are
    independently referenceable, so a March research result stays interpretable after
    a v3 lands in June.
    """
    specs = REGISTRY.all()
    if scope is not None:
        specs = tuple(s for s in specs if s.scope.value == scope)
    return {
        "data": [s.as_dict() for s in specs],
        "meta": {
            "count": len(specs),
            "identifiers": len(REGISTRY.identifiers()),
            "scopes": sorted({s.scope.value for s in REGISTRY.all()}),
        },
    }


@router.get("/{identifier}/versions/{version}")
async def feature_definition(identifier: str, version: int) -> dict[str, Any]:
    """The full declared definition of one feature version."""
    try:
        spec = REGISTRY.get(identifier, version)
    except UnknownFeature as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {
        "data": spec.as_dict(),
        "meta": {"available_versions": list(REGISTRY.versions_of(identifier))},
    }


@router.get("/{identifier}/values")
async def feature_values(
    request: Request,
    identifier: str,
    scope_kind: str = Query(..., description="underlying | expiry | strike | contract"),
    scope_ref: str = Query(..., description="the scope's identifier"),
    market_time: datetime | None = Query(
        None, description="T — omit for the latest (live) reading"
    ),
    knowledge_time: datetime | None = Query(None, description="K, defaults to market_time"),
    decision_time: datetime | None = Query(
        None,
        description=(
            "results are filtered to available_at <= decision_time. "
            "Defaults to knowledge_time. A request parameter, never a stored field."
        ),
    ),
    version: int | None = Query(None, description="defaults to the latest registered"),
) -> dict[str, Any]:
    """Time series for one feature and scope.

    Requires a `metric_values` reader on `app.state.metric_reader`. Phase 4 declares
    the schema and the endpoint contract; wiring the durable reader needs a database
    to test against and is not done here.
    """
    try:
        spec = REGISTRY.get(identifier, version) if version else REGISTRY.latest(identifier)
    except UnknownFeature as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    from datetime import timezone

    if market_time is None:
        market_time = datetime.now(timezone.utc)

    if knowledge_time is not None and knowledge_time < market_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"knowledge_time {knowledge_time.isoformat()} precedes market_time "
                f"{market_time.isoformat()}; knowledge_time < market_time is rejected"
            ),
        )

    reader = getattr(request.app.state, "metric_reader", None)
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{spec.label} is registered, but no metric_values reader is configured "
                f"on this process. An empty series is not returned: it would be "
                f"indistinguishable from the feature having produced nothing."
            ),
        )

    effective_k = knowledge_time or market_time
    effective_decision = decision_time or effective_k
    rows = reader.series(
        feature_id=spec.identifier,
        feature_version=spec.version,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        market_time=market_time,
        knowledge_horizon=effective_k,
        decision_time=effective_decision,
    )
    return {
        "data": rows,
        "meta": {
            "market_time": market_time.isoformat(),
            "knowledge_time": effective_k.isoformat(),
            "decision_time": effective_decision.isoformat(),
            "semantics": "tradable_information_at",
            "feature": spec.as_dict(),
        },
    }
