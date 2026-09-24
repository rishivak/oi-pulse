"""`/research` — `12-API_SPEC.md` §176.

> `/research/studies` CRUD · `POST /research/studies/{id}/run` · `/results` ·
> `/research/datasets` · `/research/signal-evaluations`.
>
> Results include sample count, exclusion counts, comparison count and the full
> assumption set.

Two behaviours are specified and enforced here rather than left to a client:

* **A historical point-in-time query never silently returns latest knowledge.**
  `knowledge_time` is required on any query that reads history; omitting it is a 422,
  not a default to "now". Defaulting would turn a PIT question into a hindsight answer
  with no indication that it had happened.
* **A missing artifact is a 404, and an unconfigured process is a 503.** Neither
  returns an empty list, which is indistinguishable from "the study found nothing" —
  a completely different and far more interesting result.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response, status

from oipulse.research.serialisation import (
    dataset_to_dict,
    result_to_dict,
    signal_evaluation_to_dict,
    study_to_dict,
)

router = APIRouter(prefix="/research", tags=["research"])

__all__ = ["router"]


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "research_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "no research store is configured on this process. An empty list is not "
                "returned: it would be indistinguishable from a study having found "
                "nothing."
            ),
        )
    return store


@router.get("/studies")
async def list_studies(request: Request) -> dict[str, Any]:
    store = _store(request)
    studies = store.list_studies()
    return {"data": [study_to_dict(s) for s in studies], "meta": {"count": len(studies)}}


@router.get("/studies/{study_id}/versions/{version}")
async def get_study(request: Request, study_id: str, version: int) -> dict[str, Any]:
    store = _store(request)
    study = store.get_study(study_id, version)
    if study is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no study {study_id}@v{version}"
        )
    return {"data": study_to_dict(study)}


@router.post("/studies", status_code=status.HTTP_201_CREATED)
async def create_study(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    store = _store(request)
    try:
        study = store.create_study(body)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"data": study_to_dict(study)}


@router.delete(
    "/studies/{study_id}/versions/{version}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_study(request: Request, study_id: str, version: int) -> Response:
    store = _store(request)
    if not store.delete_study(study_id, version):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no study {study_id}@v{version}"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/studies/{study_id}/run")
async def run_study(
    request: Request,
    study_id: str,
    version: int = Query(..., description="exact study version; never defaulted"),
    knowledge_time: datetime = Query(
        ...,
        description=("K — required. A historical query is never answered with latest knowledge."),
    ),
) -> dict[str, Any]:
    """Execute a study at an explicit knowledge horizon.

    `knowledge_time` is **required**, not defaulted: silently answering a historical
    question with today's knowledge is the single most damaging thing this endpoint
    could do, and it would be invisible in the response.
    """
    store = _store(request)
    study = store.get_study(study_id, version)
    if study is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no study {study_id}@v{version}"
        )
    try:
        result = store.run_study(study, knowledge_time)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no source data covering the requested period: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return result_to_dict(result)


@router.get("/results/{content_hash}")
async def get_result(request: Request, content_hash: str) -> dict[str, Any]:
    """Retrieve an artifact by its content hash — the reproducibility identity."""
    store = _store(request)
    result = store.get_result(content_hash)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no research result with content hash {content_hash}",
        )
    return result_to_dict(result)


@router.get("/results")
async def list_results(
    request: Request,
    study_id: str | None = Query(None),
    dataset_content_hash: str | None = Query(None),
) -> dict[str, Any]:
    store = _store(request)
    results = store.list_results(study_id=study_id, dataset_content_hash=dataset_content_hash)
    return {
        "data": [result_to_dict(r)["data"] for r in results],
        "meta": {"count": len(results)},
    }


@router.get("/datasets/{content_hash}")
async def get_dataset(request: Request, content_hash: str) -> dict[str, Any]:
    store = _store(request)
    dataset = store.get_dataset(content_hash)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no dataset with content hash {content_hash}",
        )
    return {"data": dataset_to_dict(dataset)}


@router.get("/datasets")
async def list_datasets(request: Request) -> dict[str, Any]:
    store = _store(request)
    datasets = store.list_datasets()
    return {
        "data": [dataset_to_dict(d) for d in datasets],
        "meta": {"count": len(datasets)},
    }


@router.get("/signal-evaluations")
async def list_signal_evaluations(
    request: Request,
    result_content_hash: str | None = Query(None),
    signal_type: str | None = Query(None),
) -> dict[str, Any]:
    """Forward behaviour by lifecycle transition (`09` §5).

    Evaluations below their minimum sample are returned with `reportable: false` and
    no distribution: withholding the number while reporting the count is the honest
    form, and dropping the row entirely would hide that the question was asked.
    """
    store = _store(request)
    evaluations = store.list_signal_evaluations(
        result_content_hash=result_content_hash, signal_type=signal_type
    )
    return {
        "data": [signal_evaluation_to_dict(e) for e in evaluations],
        "meta": {
            "count": len(evaluations),
            "reportable": sum(1 for e in evaluations if e.is_reportable),
        },
    }
