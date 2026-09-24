"""Staleness budgets and quality escalation.

`docs/design/04-MARKETSTATE.md` §3. Each data category has a maximum acceptable age.
Exceeding it **degrades the state** rather than silently producing a stale-but-precise
value — the point being that a number and its trustworthiness must travel together.

The budgets below are the documented ones, reproduced exactly. They are configuration,
carried in `staleness_policy_version` and therefore part of `build_context_id`, so
states built under different budgets can never collide (`04` §1). They are **not**
invented here and must not be changed without a new policy version.

OI and greeks carry looser budgets than spot because Upstox updates them less
frequently: a budget has to reflect the feed's real cadence, not an aspiration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from oipulse.dataquality.issues import IssueSeverity

__all__ = [
    "ANCHOR_MAX_AGE_MULTIPLIER",
    "DEFAULT_STALENESS_POLICY",
    "STALENESS_POLICY_VERSION",
    "CategoryBudget",
    "DataCategory",
    "QualityStatus",
    "StalenessPolicy",
]

#: Bump whenever any budget below changes. Feeds `build_context_id`.
STALENESS_POLICY_VERSION = "1.0.0"

#: `ANCHOR_MAX_AGE` defaults to 2x the chain poll interval (`04` §4). Beyond it the
#: anchor is no longer trustworthy as a cross-sectional reference.
ANCHOR_MAX_AGE_MULTIPLIER = 2


class DataCategory(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"
    OPTION_QUOTE = "option_quote"
    OPTION_OI = "option_oi"
    GREEKS = "greeks"
    DEPTH = "depth"
    INDEX_OHLC = "index_ohlc"


class QualityStatus(StrEnum):
    """State-level quality.

    Ordered worst-last so `max` over a set of statuses escalates correctly.
    """

    OK = "ok"
    WARNING = "warning"
    DEGRADED = "degraded"
    UNRELIABLE = "unreliable"

    @property
    def rank(self) -> int:
        return _STATUS_RANK[self]

    @classmethod
    def worst(cls, statuses: list[QualityStatus] | tuple[QualityStatus, ...]) -> QualityStatus:
        return max(statuses, key=lambda s: s.rank) if statuses else cls.OK


_STATUS_RANK: dict[QualityStatus, int] = {
    QualityStatus.OK: 0,
    QualityStatus.WARNING: 1,
    QualityStatus.DEGRADED: 2,
    QualityStatus.UNRELIABLE: 3,
}


@dataclass(frozen=True, slots=True)
class CategoryBudget:
    """One row of the budget table.

    `drop_on_breach` exists for depth alone: the documented fallback is "drop depth from
    the state rather than serve stale depth", which is materially different from
    retaining a flagged last value. A dropped category is absent, and absence is not
    zero.
    """

    category: DataCategory
    max_age: timedelta
    on_breach: QualityStatus
    severity: IssueSeverity
    drop_on_breach: bool = False
    #: Spot has no fallback: a state without spot is not a market picture.
    required: bool = False


@dataclass(frozen=True, slots=True)
class StalenessPolicy:
    """The budget table plus the escalation rules, as one versioned object."""

    version: str
    budgets: tuple[CategoryBudget, ...]
    #: Coverage thresholds from the escalation table (`04` §3).
    coverage_ok: float = 0.98
    coverage_degraded: float = 0.80

    def budget_for(self, category: DataCategory) -> CategoryBudget:
        for budget in self.budgets:
            if budget.category is category:
                return budget
        raise KeyError(f"no staleness budget defined for {category.value}")

    def is_stale(self, category: DataCategory, age: timedelta) -> bool:
        return age > self.budget_for(category).max_age

    def as_configuration(self) -> dict[str, object]:
        """The assembly-affecting content, for `BuildContext.create`.

        Every value the builder consults appears here. One omitted is one whose change
        would not produce a new `build_context_id`, which is exactly how two states
        assembled under incomparable rules come to share an identity.
        """
        return {
            "version": self.version,
            "coverage_ok": self.coverage_ok,
            "coverage_degraded": self.coverage_degraded,
            "budgets": [
                {
                    "category": b.category.value,
                    "max_age_seconds": b.max_age.total_seconds(),
                    "on_breach": b.on_breach.value,
                    "severity": b.severity.value,
                    "drop_on_breach": b.drop_on_breach,
                    "required": b.required,
                }
                for b in sorted(self.budgets, key=lambda b: b.category.value)
            ],
        }

    def escalate(
        self,
        *,
        breached: frozenset[DataCategory],
        coverage_ratio: float,
        missing_subscribed_expiry: bool,
    ) -> QualityStatus:
        """The escalation table (`04` §3), applied literally.

        | All categories within budget, coverage >= 98%        | OK          |
        | Any non-spot category over budget, or coverage 80-98% | DEGRADED    |
        | Spot over budget, or coverage < 80%, or no chain data
          for a subscribed expiry                              | UNRELIABLE  |

        A dropped category (depth) is **not** counted as over budget: it is no longer
        part of the state, so there is no stale value in it to degrade the state's
        trustworthiness. The issue is still recorded, so the drop is visible.
        """
        if (
            DataCategory.SPOT in breached
            or coverage_ratio < self.coverage_degraded
            or missing_subscribed_expiry
        ):
            return QualityStatus.UNRELIABLE
        non_spot_breached = {c for c in breached if c is not DataCategory.SPOT}
        if non_spot_breached or coverage_ratio < self.coverage_ok:
            return QualityStatus.DEGRADED
        return QualityStatus.OK


#: The documented budgets from `04-MARKETSTATE.md` §3, reproduced exactly.
DEFAULT_STALENESS_POLICY = StalenessPolicy(
    version=STALENESS_POLICY_VERSION,
    budgets=(
        CategoryBudget(
            DataCategory.SPOT,
            timedelta(seconds=5),
            QualityStatus.UNRELIABLE,
            IssueSeverity.CRITICAL,
            required=True,
        ),
        CategoryBudget(
            DataCategory.FUTURES,
            timedelta(seconds=5),
            QualityStatus.DEGRADED,
            IssueSeverity.DEGRADED,
        ),
        CategoryBudget(
            DataCategory.OPTION_QUOTE,
            timedelta(seconds=30),
            QualityStatus.DEGRADED,
            IssueSeverity.DEGRADED,
        ),
        CategoryBudget(
            DataCategory.OPTION_OI,
            timedelta(seconds=60),
            QualityStatus.DEGRADED,
            IssueSeverity.DEGRADED,
        ),
        CategoryBudget(
            DataCategory.GREEKS,
            timedelta(seconds=60),
            QualityStatus.DEGRADED,
            IssueSeverity.DEGRADED,
        ),
        CategoryBudget(
            DataCategory.DEPTH,
            timedelta(seconds=10),
            QualityStatus.WARNING,
            IssueSeverity.WARNING,
            drop_on_breach=True,
        ),
        CategoryBudget(
            DataCategory.INDEX_OHLC,
            timedelta(seconds=60),
            QualityStatus.WARNING,
            IssueSeverity.WARNING,
        ),
    ),
)
