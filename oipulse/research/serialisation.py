"""Rendering research artifacts for the API. Pure, web-stack-free.

Kept out of `api/` for the same reason the MarketState envelope and the signal
envelope are: the shape is a pure function of the artifact, so the contract stays
testable on an interpreter with no web stack installed.

Every response carries **both** market time and knowledge time where they apply, and
the hindsight flag travels with any result produced under `market_truth_at` — a
hindsight number must never be renderable as though it were live-reproducible.
"""

from __future__ import annotations

from typing import Any

from oipulse.research.dataset import Dataset
from oipulse.research.result import StudyResult
from oipulse.research.signal_evaluation import EvidenceAttribution, SignalEvaluation
from oipulse.research.study import EventStudy

__all__ = [
    "attribution_to_dict",
    "dataset_to_dict",
    "result_to_dict",
    "signal_evaluation_to_dict",
    "study_to_dict",
]


def study_to_dict(study: EventStudy) -> dict[str, Any]:
    return study.as_dict()


def dataset_to_dict(dataset: Dataset) -> dict[str, Any]:
    return dataset.as_dict()


def result_to_dict(result: StudyResult) -> dict[str, Any]:
    """The full artifact.

    `meta` restates the honesty numbers `09` §3 requires alongside every result:
    sample counts, exclusion counts, the comparison count and the hindsight flag. A
    client cannot render a mean without also receiving what it rests on.
    """
    body = result.as_dict()
    return {
        "data": body,
        "meta": {
            "content_hash": result.content_hash,
            "status": result.status.value,
            "is_hindsight": result.is_hindsight,
            "query_mode": result.query_mode,
            "knowledge_time": result.knowledge_horizon.isoformat(),
            "raw_events": result.sample.raw_events,
            "effective_sample": result.sample.effective_sample,
            "clusters": result.sample.clusters,
            "excluded_quality": result.sample.excluded_quality,
            "excluded_incomplete_window": result.sample.excluded_incomplete_window,
            "comparisons": result.sample.comparisons,
        },
    }


def signal_evaluation_to_dict(evaluation: SignalEvaluation) -> dict[str, Any]:
    return evaluation.as_dict()


def attribution_to_dict(attribution: EvidenceAttribution) -> dict[str, Any]:
    return attribution.as_dict()
