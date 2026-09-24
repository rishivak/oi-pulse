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
    "record_alert_delivery",
    "record_alert_suppressed",
    "record_alert_triggered",
    "record_feature_computed",
    "record_feature_skipped",
    "record_provenance_failure",
    "record_research_artifact",
    "record_research_failure",
    "record_research_run",
    "record_signal_created",
    "record_signal_evaluation",
    "record_signal_idempotent_repeat",
    "record_signal_skipped",
    "record_signal_transition",
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

# --- Phase 5 signals and alerts (`16-OBSERVABILITY.md` §"Signals and alerts").
SIGNALS_CREATED = "signals_created_total"
SIGNAL_LIFECYCLE_TRANSITIONS = "signal_lifecycle_transitions_total"
#: Evaluations that produced nothing, labelled by reason. Named separately from
#: `signals_created_total` so "the pipeline is quiet" and "the pipeline is blocked"
#: are distinguishable on a dashboard.
SIGNAL_EVALUATIONS_SKIPPED = "signal_evaluations_skipped_total"
SIGNAL_EVALUATION_DURATION = "signal_evaluation_duration_seconds"
#: `available_at - market_time`. Confirms a signal never precedes its information.
SIGNAL_AVAILABILITY_LAG = "signal_availability_lag_seconds"
#: Source events whose re-processing was absorbed by the identity key rather than
#: creating a second signal. A rising count is healthy; a zero count on a retrying
#: consumer means idempotency is not actually being exercised.
SIGNAL_IDEMPOTENT_REPEATS = "signal_idempotent_repeats_total"
ALERTS_TRIGGERED = "alerts_triggered_total"
ALERTS_SUPPRESSED = "alerts_suppressed_total"
ALERTS_DELIVERED = "alerts_delivered_total"
ALERTS_FAILED = "alerts_failed_total"
ALERT_DELIVERY_DURATION = "alert_delivery_duration_seconds"

# --- Phase 6 research (`16-OBSERVABILITY.md` conventions).
RESEARCH_RUNS = "research_runs_total"
RESEARCH_RUN_DURATION = "research_run_duration_seconds"
RESEARCH_FAILURES = "research_failures_total"
#: Raw detections, before any sampling policy is applied.
RESEARCH_EVENTS_DETECTED = "research_events_detected_total"
#: Events removed by clustering, separation or quality. Labelled by reason, so
#: "the study found nothing" and "the study excluded everything" stay distinguishable.
RESEARCH_EVENTS_EXCLUDED = "research_events_excluded_total"
#: Forward windows running past the end of the dataset. Never fabricated, so a rising
#: count means studies are being run too close to the present.
RESEARCH_INCOMPLETE_WINDOWS = "research_incomplete_windows_total"
RESEARCH_ARTIFACTS_CREATED = "research_artifacts_created_total"
RESEARCH_CONTENT_HASHES = "research_content_hashes_total"
#: A reference that failed to resolve. Should always be zero; non-zero means the
#: audit chain is broken and results are no longer explainable.
RESEARCH_PROVENANCE_FAILURES = "research_provenance_failures_total"

METRICS = MetricsRegistry()


def record_research_run(
    study_id: str,
    version: int,
    *,
    status: str,
    duration_seconds: float,
    raw_events: int,
    effective_sample: int,
    excluded_quality: int,
    incomplete_windows: int,
) -> None:
    """One study execution. Primitives only: `research/` may not import this module.

    Both event counts are recorded, never just one: reporting raw detections alone
    would restate the significance inflation that clustering exists to correct.
    """
    labels = {"study": study_id, "version": str(version)}
    METRICS.inc(RESEARCH_RUNS, {**labels, "status": status})
    METRICS.observe(RESEARCH_RUN_DURATION, duration_seconds, labels)
    METRICS.inc(RESEARCH_EVENTS_DETECTED, labels, raw_events)
    METRICS.inc(
        RESEARCH_EVENTS_EXCLUDED,
        {**labels, "reason": "clustering"},
        max(raw_events - effective_sample, 0),
    )
    if excluded_quality:
        METRICS.inc(RESEARCH_EVENTS_EXCLUDED, {**labels, "reason": "quality"}, excluded_quality)
    if incomplete_windows:
        METRICS.inc(RESEARCH_INCOMPLETE_WINDOWS, labels, incomplete_windows)


def record_research_failure(study_id: str, reason: str) -> None:
    METRICS.inc(RESEARCH_FAILURES, {"study": study_id, "reason": reason})


def record_research_artifact(kind: str, *, content_hash_computed: bool = True) -> None:
    """A dataset or result artifact was materialised."""
    METRICS.inc(RESEARCH_ARTIFACTS_CREATED, {"kind": kind})
    if content_hash_computed:
        METRICS.inc(RESEARCH_CONTENT_HASHES, {"kind": kind})


def record_provenance_failure(kind: str, reference: str) -> None:
    """A reference that would not resolve. Should never fire in a healthy system."""
    METRICS.inc(RESEARCH_PROVENANCE_FAILURES, {"kind": kind, "reference": reference[:64]})


def record_signal_created(
    signal_type: str, version: int, *, status: str, availability_lag_seconds: float
) -> None:
    """One signal produced. Primitives only: `signals/` may not import this module."""
    labels = {"type": signal_type, "version": str(version)}
    METRICS.inc(SIGNALS_CREATED, {**labels, "status": status})
    METRICS.observe(SIGNAL_AVAILABILITY_LAG, availability_lag_seconds, labels)


def record_signal_transition(signal_type: str, from_status: str, to_status: str) -> None:
    METRICS.inc(
        SIGNAL_LIFECYCLE_TRANSITIONS,
        {"type": signal_type, "from": from_status, "to": to_status},
    )


def record_signal_skipped(signal_type: str, version: int, reason: str) -> None:
    METRICS.inc(
        SIGNAL_EVALUATIONS_SKIPPED,
        {"type": signal_type, "version": str(version), "reason": reason},
    )


def record_signal_evaluation(duration_seconds: float, *, rules: int) -> None:
    METRICS.observe(SIGNAL_EVALUATION_DURATION, duration_seconds, {"rules": str(rules)})


def record_signal_idempotent_repeat(signal_type: str) -> None:
    """A repeated source event that updated one entity instead of creating a second."""
    METRICS.inc(SIGNAL_IDEMPOTENT_REPEATS, {"type": signal_type})


def record_alert_triggered(rule_id: str, channel: str, severity: str) -> None:
    METRICS.inc(ALERTS_TRIGGERED, {"rule": rule_id, "channel": channel, "severity": severity})


def record_alert_suppressed(rule_id: str, reason: str) -> None:
    """Suppression is recorded, not silent: "why did I not get an alert?" has an answer."""
    METRICS.inc(ALERTS_SUPPRESSED, {"rule": rule_id, "reason": reason})


def record_alert_delivery(
    channel: str, *, succeeded: bool, duration_seconds: float, attempts: int
) -> None:
    labels = {"channel": channel}
    METRICS.inc(ALERTS_DELIVERED if succeeded else ALERTS_FAILED, labels)
    METRICS.observe(
        ALERT_DELIVERY_DURATION, duration_seconds, {**labels, "attempts": str(attempts)}
    )


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
