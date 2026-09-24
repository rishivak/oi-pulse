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
    "record_backtest_run",
    "record_feature_computed",
    "record_feature_skipped",
    "record_live_execution_refusal",
    "record_paper_account",
    "record_paper_execution_latency",
    "record_paper_fill",
    "record_paper_intent",
    "record_paper_ledger_failure",
    "record_paper_order",
    "record_paper_sequence_gap",
    "record_provenance_failure",
    "record_replay_reconstruction",
    "record_replay_resume_rejection",
    "record_replay_run",
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


# --- Phase 7 replay and backtesting (`16-OBSERVABILITY.md` conventions,
# `10-REPLAY.md` §4 and §6).
REPLAY_RUNS = "replay_runs_total"
REPLAY_RUN_DURATION = "replay_run_duration_seconds"
REPLAY_STEPS = "replay_steps_total"
REPLAY_STATES_BUILT = "replay_states_built_total"
#: Checkpoint reuse vs reconstruction. `16` calls for both: under market-truth the
#: hit count should be zero, and a surprising number of hits would mean the
#: exact-identity rule had been weakened somewhere.
REPLAY_CHECKPOINT_HITS = "replay_checkpoint_hits_total"
REPLAY_CHECKPOINT_MISSES = "replay_checkpoint_misses_total"
REPLAY_RECONSTRUCTION_DURATION = "replay_reconstruction_duration_seconds"
#: Resume attempts refused because the build context or knowledge horizon differed.
#: Should be rare; a rising count means runs are being resumed across a config change.
REPLAY_RESUME_REJECTIONS = "replay_resume_rejections_total"

BACKTEST_RUNS = "backtest_runs_total"
BACKTEST_RUN_DURATION = "backtest_run_duration_seconds"
BACKTEST_INTENTS = "backtest_intents_total"
BACKTEST_FILLS = "backtest_fills_total"
#: Labelled by reason, so "the strategy traded little" and "the strategy's orders
#: could not be filled" stay distinguishable — they imply opposite conclusions.
BACKTEST_REJECTIONS = "backtest_rejections_total"
BACKTEST_PARTIAL_FILLS = "backtest_partial_fills_total"
#: Fills priced against an assumed spread rather than an observed quote (`10` §6).
#: Non-zero means the run is not comparable with a full-fidelity one.
BACKTEST_ASSUMPTION_BASED_FILLS = "backtest_assumption_based_fills_total"
#: Runs completed with no risk engine attached. While Phase 9 is pending this equals
#: the run count; once it lands, a non-zero value is a misconfiguration.
BACKTEST_UNRISKED_RUNS = "backtest_unrisked_runs_total"
BACKTEST_LEDGER_DUPLICATES = "backtest_ledger_duplicate_fills_total"


def record_replay_run(
    run_id: str,
    *,
    status: str,
    duration_seconds: float,
    steps: int,
    states_built: int,
    checkpoint_hits: int,
    checkpoint_misses: int,
) -> None:
    """One replay run. Primitives only: `replay/` may not import this module.

    Hits and misses are both recorded rather than a ratio, so a dashboard can show
    the absolute reconstruction load as well as the reuse rate — a 90% reuse rate
    over ten steps and over ten thousand are very different operational facts.
    """
    labels = {"run": run_id}
    METRICS.inc(REPLAY_RUNS, {**labels, "status": status})
    METRICS.observe(REPLAY_RUN_DURATION, duration_seconds, labels)
    METRICS.inc(REPLAY_STEPS, labels, steps)
    METRICS.inc(REPLAY_STATES_BUILT, labels, states_built)
    METRICS.inc(REPLAY_CHECKPOINT_HITS, labels, checkpoint_hits)
    METRICS.inc(REPLAY_CHECKPOINT_MISSES, labels, checkpoint_misses)


def record_replay_reconstruction(run_id: str, duration_seconds: float) -> None:
    """One state reconstruction. The cost `16` asks to be visible per step."""
    METRICS.observe(REPLAY_RECONSTRUCTION_DURATION, duration_seconds, {"run": run_id})


def record_replay_resume_rejection(run_id: str, reason: str) -> None:
    METRICS.inc(REPLAY_RESUME_REJECTIONS, {"run": run_id, "reason": reason})


def record_backtest_run(
    run_id: str,
    strategy_id: str,
    *,
    status: str,
    duration_seconds: float,
    intents: int,
    fills: int,
    partial_fills: int,
    assumption_based_fills: int,
    rejection_reasons: dict[str, int],
    risk_evaluated: bool,
    duplicate_fills: int = 0,
) -> None:
    """One backtest run, with the flags that qualify its numbers.

    `assumption_based_fills` and `risk_evaluated` are emitted as metrics, not only
    stored on the artifact: an operator watching a dashboard must be able to see that
    a run's results rest on assumed spreads or on no risk constraint at all, without
    opening the result.
    """
    labels = {"run": run_id, "strategy": strategy_id}
    METRICS.inc(BACKTEST_RUNS, {**labels, "status": status})
    METRICS.observe(BACKTEST_RUN_DURATION, duration_seconds, labels)
    METRICS.inc(BACKTEST_INTENTS, labels, intents)
    METRICS.inc(BACKTEST_FILLS, labels, fills)
    if partial_fills:
        METRICS.inc(BACKTEST_PARTIAL_FILLS, labels, partial_fills)
    if assumption_based_fills:
        METRICS.inc(BACKTEST_ASSUMPTION_BASED_FILLS, labels, assumption_based_fills)
    for reason, count in sorted(rejection_reasons.items()):
        METRICS.inc(BACKTEST_REJECTIONS, {**labels, "reason": reason}, count)
    if not risk_evaluated:
        METRICS.inc(BACKTEST_UNRISKED_RUNS, labels)
    if duplicate_fills:
        METRICS.inc(BACKTEST_LEDGER_DUPLICATES, labels, duplicate_fills)


# --- Phase 8 paper trading (`16-OBSERVABILITY.md` conventions, `11-TRADING.md`).
#
# Every name is prefixed `paper_`. Phase 8 brief §21: "Do not create misleading
# metrics for broker execution." A metric called `orders_submitted_total` on a
# dashboard would read as broker traffic; `paper_orders_submitted_total` cannot.
PAPER_ACCOUNTS = "paper_accounts_total"
PAPER_INTENTS = "paper_intents_total"
#: Intents refused before an order existed, labelled by reason.
PAPER_INTENTS_REJECTED = "paper_intents_rejected_total"
PAPER_ORDERS = "paper_orders_total"
PAPER_ORDER_REJECTIONS = "paper_order_rejections_total"
PAPER_FILLS = "paper_fills_total"
PAPER_PARTIAL_FILLS = "paper_partial_fills_total"
PAPER_CANCELLATIONS = "paper_cancellations_total"
PAPER_EXPIRATIONS = "paper_expirations_total"
#: Redeliveries absorbed by the inbox or the ledger's fill key. A healthy non-zero
#: value; a *rising* one means something upstream is retrying harder than expected.
PAPER_DUPLICATE_EVENTS = "paper_duplicate_events_total"
#: Events deferred because their predecessor had not been applied. Should be rare and
#: self-clearing; a sustained value means an aggregate's sequence is stuck.
PAPER_SEQUENCE_GAPS = "paper_sequence_gaps_total"
#: Wall-clock cost of processing one intent end to end. A *system* latency, explicitly
#: not a market-time quantity -- it must never be mistaken for execution delay, which
#: is modelled by the fill model's declared latency.
PAPER_EXECUTION_LATENCY = "paper_execution_latency_seconds"
PAPER_LEDGER_FAILURES = "paper_ledger_update_failures_total"
#: Accounts running with no risk engine. While Phase 9 is pending this equals the
#: account count; once it lands, a non-zero value is a misconfiguration.
PAPER_UNRISKED_ACCOUNTS = "paper_unrisked_accounts_total"
#: Attempts to reach a live execution path. Must always be zero: no live adapter
#: exists, so a non-zero value means something tried and was refused.
PAPER_LIVE_EXECUTION_REFUSALS = "paper_live_execution_refusals_total"


def record_paper_account(account_id: str, *, status: str, risk_evaluated: bool) -> None:
    """One account lifecycle transition."""
    METRICS.inc(PAPER_ACCOUNTS, {"account": account_id, "status": status})
    if not risk_evaluated:
        METRICS.inc(PAPER_UNRISKED_ACCOUNTS, {"account": account_id})


def record_paper_intent(
    account_id: str,
    *,
    accepted: bool,
    reject_reason: str | None = None,
    duplicate: bool = False,
) -> None:
    """One intent submission, including the ones that went nowhere.

    A rejected intent is counted under its reason rather than lumped in with
    successes: "the strategy traded little" and "the strategy's intents were all
    refused for insufficient cash" imply opposite conclusions.
    """
    labels = {"account": account_id}
    if duplicate:
        METRICS.inc(PAPER_DUPLICATE_EVENTS, {**labels, "kind": "intent"})
        return
    METRICS.inc(PAPER_INTENTS, {**labels, "accepted": str(accepted).lower()})
    if not accepted:
        METRICS.inc(PAPER_INTENTS_REJECTED, {**labels, "reason": reject_reason or "unspecified"})


def record_paper_order(account_id: str, *, state: str, reject_reason: str | None = None) -> None:
    """One order reaching a state. Labelled by state, so the mix is visible."""
    labels = {"account": account_id}
    METRICS.inc(PAPER_ORDERS, {**labels, "state": state})
    if reject_reason is not None:
        METRICS.inc(PAPER_ORDER_REJECTIONS, {**labels, "reason": reject_reason})
    if state == "CANCELLED":
        METRICS.inc(PAPER_CANCELLATIONS, labels)
    elif state == "EXPIRED":
        METRICS.inc(PAPER_EXPIRATIONS, labels)


def record_paper_fill(
    account_id: str, *, partial: bool, assumption_based: bool, duplicate: bool = False
) -> None:
    labels = {"account": account_id}
    if duplicate:
        METRICS.inc(PAPER_DUPLICATE_EVENTS, {**labels, "kind": "fill"})
        return
    METRICS.inc(PAPER_FILLS, {**labels, "assumption_based": str(assumption_based).lower()})
    if partial:
        METRICS.inc(PAPER_PARTIAL_FILLS, labels)


def record_paper_sequence_gap(aggregate_type: str, aggregate_id: str) -> None:
    METRICS.inc(
        PAPER_SEQUENCE_GAPS, {"aggregate_type": aggregate_type, "aggregate": aggregate_id[:64]}
    )


def record_paper_execution_latency(account_id: str, seconds: float) -> None:
    """System processing time, **not** modelled execution delay.

    The two are different quantities and conflating them would make a slow process
    look like a slow market. Modelled delay lives in the fill model's declared
    latency and never appears here.
    """
    METRICS.observe(PAPER_EXECUTION_LATENCY, seconds, {"account": account_id})


def record_paper_ledger_failure(account_id: str, reason: str) -> None:
    METRICS.inc(PAPER_LEDGER_FAILURES, {"account": account_id, "reason": reason})


def record_live_execution_refusal(requested_mode: str) -> None:
    """Something asked for live execution and was refused. Must always be zero."""
    METRICS.inc(PAPER_LIVE_EXECUTION_REFUSALS, {"requested_mode": requested_mode})
