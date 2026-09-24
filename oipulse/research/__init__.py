"""Layer 6 — Research and event studies.

`docs/design/09-RESEARCH.md`. Research is a first-class domain, not a reporting
afterthought, and its defining constraint is that **results must be point-in-time
correct — a research result that cannot be reproduced live is worse than no result.**

Three properties carry the layer:

* **Look-ahead is refused, not warned about.** `FeatureAccessError` is raised when a
  study requests a value whose `available_at` is later than the decision point. There
  is no path by which a study receives an unavailable feature and ignores a warning;
  researcher discipline is not a control.
* **Outcome data lives in a separate namespace from decision data.** Forward windows
  measure what happened and are by definition future-looking relative to the decision;
  they are legitimate for measurement and never reachable from a decision context.
* **Everything is content-addressed.** A study re-run over the same immutable inputs
  produces an identical content hash. Wall-clock execution metadata is recorded but is
  deliberately excluded from semantic identity.

This layer creates **no second analytics implementation**. It consumes the Phase 4
feature registry and Phase 5 signal definitions, pinned to exact versions.
"""

from oipulse.research.access import FeatureAccessError, PointInTimeAccessor
from oipulse.research.dataset import Dataset, build_dataset
from oipulse.research.engine import EventStudyEngine
from oipulse.research.result import StudyResult
from oipulse.research.study import EventStudy

__all__ = [
    "Dataset",
    "EventStudy",
    "EventStudyEngine",
    "FeatureAccessError",
    "PointInTimeAccessor",
    "StudyResult",
    "build_dataset",
]
