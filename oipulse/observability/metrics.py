"""Phase 2 metrics.

`docs/design/16-OBSERVABILITY.md` §3. A stdlib registry so the ingestion path can be
instrumented without a client library; the names and label sets match the design exactly,
so swapping in a Prometheus client later changes no call site.

Ingestion latency (`ingested_at - observed_at`) is the single most important health
metric: it is the direct measure of the gap between market truth and knowledge, which is
the thing the whole bitemporal model exists to represent.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

__all__ = [
    "METRICS",
    "MetricsRegistry",
    "record_feature_computed",
    "record_feature_skipped",
]

#: (metric name, sorted label pairs). Named because it appears in four signatures and
#: a bare `tuple` there is an implicit Any under mypy --strict.
MetricKey = tuple[str, tuple[tuple[str, str], ...]]
Labels = dict[str, str] | None


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    samples: list[float] = field(default_factory=list)

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        # Bounded reservoir: enough for a percentile, not enough to leak.
        if len(self.samples) < 2048:
            self.samples.append(value)

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    def quantile(self, q: float) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        idx = min(int(q * len(ordered)), len(ordered) - 1)
        return ordered[idx]


class MetricsRegistry:
    """Counters and histograms, keyed by name plus a sorted label tuple."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[MetricKey, float] = defaultdict(float)
        self._gauges: dict[MetricKey, float] = {}
        self._histograms: dict[MetricKey, _Histogram] = defaultdict(_Histogram)

    @staticmethod
    def _key(name: str, labels: Labels) -> MetricKey:
        return (name, tuple(sorted((labels or {}).items())))

    def inc(self, name: str, labels: Labels = None, by: float = 1.0) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += by

    def set_gauge(self, name: str, value: float, labels: Labels = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, value: float, labels: Labels = None) -> None:
        with self._lock:
            self._histograms[self._key(name, labels)].observe(value)

    def counter(self, name: str, labels: Labels = None) -> float:
        return self._counters.get(self._key(name, labels), 0.0)

    def gauge(self, name: str, labels: Labels = None) -> float | None:
        return self._gauges.get(self._key(name, labels))

    def histogram(self, name: str, labels: Labels = None) -> _Histogram:
        return self._histograms[self._key(name, labels)]

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                "counters": {f"{n}{list(lbl)}": v for (n, lbl), v in self._counters.items()},
                "gauges": {f"{n}{list(lbl)}": v for (n, lbl), v in self._gauges.items()},
                "histograms": {
                    f"{n}{list(lbl)}": {
                        "count": h.count,
                        "mean": round(h.mean, 6),
                        "p50": round(h.quantile(0.50), 6),
                        "p99": round(h.quantile(0.99), 6),
                        "max": h.maximum,
                    }
                    for (n, lbl), h in self._histograms.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# Metric names, per 16 §3. Defined as constants so a typo is an import error.
INGESTION_LATENCY = "ingestion_latency_seconds"
OBSERVATIONS_INGESTED = "observations_ingested_total"
OBSERVATION_DUPLICATES = "observation_duplicates_total"
OBSERVATION_OUT_OF_ORDER = "observation_out_of_order_total"
WS_CONNECTION_STATE = "ws_connection_state"
WS_RECONNECTS = "ws_reconnects_total"
WS_GAP_SECONDS = "ws_gap_seconds"
REST_REQUEST_DURATION = "rest_request_duration_seconds"
REST_ERRORS = "rest_errors_total"
RATE_LIMIT_BUDGET_REMAINING = "rate_limit_budget_remaining"
RATE_LIMIT_EVENTS = "rate_limit_events_total"
SUBSCRIPTION_CAPACITY_USED = "subscription_capacity_used"
SUBSCRIPTION_CAPACITY_REMAINING = "subscription_capacity_remaining"
SUBSCRIPTION_REJECTIONS = "subscription_rejections_total"
SUBSCRIPTION_DEGRADATION_ACTIVE = "subscription_degradation_active"
OBSERVATION_IDENTITY_CONFIDENCE = "observation_identity_confidence"
CHAIN_COVERAGE_RATIO = "chain_coverage_ratio"
DQ_ISSUES = "data_quality_issues_total"

# --- Phase 4 analytics (`16-OBSERVABILITY.md` §3 and the feature-availability rows).
ANALYTICS_COMPUTE_DURATION = "analytics_compute_duration_seconds"
ANALYTICS_SKIPPED = "analytics_skipped_total"
FEATURE_UNAVAILABLE = "feature_unavailable_total"
#: `available_at - lookback_end`. Confirms features do not become available before
#: their window closes.
FEATURE_AVAILABILITY_LAG = "feature_availability_lag_seconds"
#: `available_at - last_input_available_at`. Confirms availability tracks **input
#: readiness** rather than market time (`07-ANALYTICS.md` §3).
FEATURE_INPUT_READINESS_LAG = "feature_input_readiness_lag_seconds"

METRICS = MetricsRegistry()


def record_feature_computed(
    feature_id: str,
    version: int,
    *,
    duration_seconds: float,
    availability_lag_seconds: float,
    input_readiness_lag_seconds: float | None = None,
) -> None:
    """Record one successful feature computation.

    Takes primitives, not an analytics object. `analytics/` may not import this module
    -- the registry below is process-global mutable state, which the purity contract
    forbids -- so the caller that owns the impure side of the pipeline translates an
    `ExecutionReport` into these calls. Keeping the argument types primitive is what
    keeps the dependency pointing one way.
    """
    labels = {"feature": feature_id, "version": str(version)}
    METRICS.observe(ANALYTICS_COMPUTE_DURATION, duration_seconds, labels)
    METRICS.observe(FEATURE_AVAILABILITY_LAG, availability_lag_seconds, labels)
    if input_readiness_lag_seconds is not None:
        METRICS.observe(FEATURE_INPUT_READINESS_LAG, input_readiness_lag_seconds, labels)


def record_feature_skipped(feature_id: str, version: int, reason: str) -> None:
    """Record a feature that did not compute, labelled by reason.

    Both counters are incremented: `analytics_skipped_total` answers "what is the
    pipeline not producing", `feature_unavailable_total` answers "how often is this
    feature absent". A quality rejection that incremented neither would make a feature
    silently vanish from dashboards.
    """
    labels = {"feature": feature_id, "version": str(version), "reason": reason}
    METRICS.inc(ANALYTICS_SKIPPED, labels)
    METRICS.inc(FEATURE_UNAVAILABLE, {"feature": feature_id, "version": str(version)})
