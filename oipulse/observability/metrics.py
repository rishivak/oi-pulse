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
    "record_attribution_run",
    "record_backtest_run",
    "record_capability_refusal",
    "record_feature_computed",
    "record_feature_skipped",
    "record_oms_duplicate_attempt",
    "record_oms_order_state",
    "record_oms_submission",
    "record_oms_unresolved",
    "record_paper_account",
    "record_paper_execution_latency",
    "record_paper_fill",
    "record_paper_intent",
    "record_paper_ledger_failure",
    "record_paper_order",
    "record_paper_sequence_gap",
    "record_portfolio_snapshot",
    "record_portfolio_valuation",
    "record_position_reconciliation",
    "record_position_update",
    "record_provenance_failure",
    "record_reconciliation_run",
    "record_replay_reconstruction",
    "record_replay_resume_rejection",
    "record_replay_run",
    "record_research_artifact",
    "record_research_failure",
    "record_research_run",
    "record_risk_contention",
    "record_risk_duplicate",
    "record_risk_evaluation",
    "record_risk_expired_approval",
    "record_risk_failure",
    "record_signal_created",
    "record_signal_evaluation",
    "record_signal_idempotent_repeat",
    "record_signal_skipped",
    "record_signal_transition",
    "record_time_to_resolve",
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


# --- Phase 9 risk (`16-OBSERVABILITY.md` conventions, `11-TRADING.md` §3).
#
# Every name is prefixed `risk_`. Brief §27: metrics must represent actual
# execution paths -- so there is no metric here for a broker, an order or a fill,
# because the risk layer touches none of them.
RISK_EVALUATIONS = "risk_evaluations_total"
RISK_APPROVALS = "risk_approvals_total"
RISK_REJECTIONS = "risk_rejections_total"
#: Approvals that had lapsed by the time execution was attempted. `11` §3 requires
#: refusal rather than silent re-approval, so a rising count means intents are
#: sitting too long between evaluation and submission.
RISK_EXPIRED_APPROVALS = "risk_expired_approvals_total"
#: Intents approved for less than they asked. Only non-zero when resizing is on.
RISK_RESIZED_INTENTS = "risk_resized_intents_total"
RISK_EVALUATION_DURATION = "risk_evaluation_duration_seconds"
#: Labelled by limit id, so an operator sees *which* limit is binding rather than
#: only that something is.
RISK_LIMIT_BREACHES = "risk_limit_breaches_total"
#: Configured limits that could not be checked. Each one is a fail-closed refusal
#: and a data problem; a sustained count means risk is blind, not that it is safe.
RISK_LIMITS_NOT_EVALUABLE = "risk_limits_not_evaluable_total"
#: Evaluations that could not run at all. Distinct from a rejection: a rejection is
#: a decision, this is the absence of one.
RISK_EVALUATION_FAILURES = "risk_evaluation_failures_total"
#: Refusals because the state carried knowledge from after the evaluation horizon.
#: Should always be zero; non-zero means something assembled state incorrectly.
RISK_HORIZON_VIOLATIONS = "risk_horizon_violations_total"
#: Intents refused because the kill switch was engaged.
RISK_KILL_SWITCH_BLOCKS = "risk_kill_switch_blocks_total"
#: Redelivered evaluations absorbed rather than re-applied.
RISK_DUPLICATE_EVALUATIONS = "risk_duplicate_evaluations_total"
#: Concurrent evaluations that had to retry because the state moved underneath them.
RISK_CONTENTION_RETRIES = "risk_contention_retries_total"
#: Accounts evaluating with no configured policy. Every such account is unable to
#: trade, so this is a configuration gap rather than an exposure.
RISK_UNPOLICED_ACCOUNTS = "risk_unpoliced_accounts_total"


def record_risk_evaluation(
    account_id: str,
    *,
    policy_label: str,
    verdict: str,
    evaluated: bool,
    duration_seconds: float,
    requested_quantity: int,
    approved_quantity: int,
    breached_limits: tuple[str, ...] = (),
    unevaluable_limits: tuple[str, ...] = (),
) -> None:
    """One risk evaluation, with the limits that actually bound it.

    Breaches are labelled by `limit_id` rather than counted in aggregate: an
    operator needs to know *which* limit is binding, and a single number cannot
    distinguish an account pressing against its position cap from one whose market
    data has gone stale.
    """
    labels = {"account": account_id, "policy": policy_label}
    METRICS.inc(RISK_EVALUATIONS, {**labels, "verdict": verdict})
    METRICS.observe(RISK_EVALUATION_DURATION, duration_seconds, labels)

    if verdict == "REJECTED":
        METRICS.inc(RISK_REJECTIONS, labels)
    else:
        METRICS.inc(RISK_APPROVALS, {**labels, "verdict": verdict})
    if verdict == "MODIFIED" and approved_quantity < requested_quantity:
        METRICS.inc(RISK_RESIZED_INTENTS, labels)
    if not evaluated:
        METRICS.inc(RISK_UNPOLICED_ACCOUNTS, {"account": account_id})

    for limit_id in breached_limits:
        METRICS.inc(RISK_LIMIT_BREACHES, {**labels, "limit": limit_id})
        if limit_id == "kill_switch":
            METRICS.inc(RISK_KILL_SWITCH_BLOCKS, labels)
        elif limit_id == "knowledge_horizon":
            METRICS.inc(RISK_HORIZON_VIOLATIONS, labels)
    for limit_id in unevaluable_limits:
        METRICS.inc(RISK_LIMITS_NOT_EVALUABLE, {**labels, "limit": limit_id})


def record_risk_expired_approval(account_id: str, intent_id: str) -> None:
    """An approval that had lapsed before execution was attempted."""
    METRICS.inc(RISK_EXPIRED_APPROVALS, {"account": account_id, "intent": intent_id[:64]})


def record_risk_failure(account_id: str, reason: str) -> None:
    """An evaluation that could not run. Never counted as a rejection."""
    METRICS.inc(RISK_EVALUATION_FAILURES, {"account": account_id, "reason": reason})


def record_risk_duplicate(account_id: str, kind: str) -> None:
    METRICS.inc(RISK_DUPLICATE_EVALUATIONS, {"account": account_id, "kind": kind})


def record_risk_contention(account_id: str, resource: str) -> None:
    """Two intents competing for the same headroom; one had to re-evaluate."""
    METRICS.inc(RISK_CONTENTION_RETRIES, {"account": account_id, "resource": resource})


# --- Phase 10 OMS and reconciliation (`16-OBSERVABILITY.md`, `11-TRADING.md` §6).
#
# Brief §26: "Do not expose misleading 'live trading successful' metrics while live
# execution is disabled." There is therefore no `orders_placed_total` and no
# `live_orders_total` here. Every name says `oms_` or `reconciliation_`, and the
# submission counter is labelled by outcome so an acknowledgement from the *paper*
# venue can never be read as a live fill.
OMS_ORDERS = "oms_orders_total"
OMS_SUBMIT_ATTEMPTS = "oms_submit_attempts_total"
#: Submissions the authorization gate refused, labelled by refusal reason.
OMS_SUBMISSIONS_BLOCKED = "oms_submissions_blocked_total"
OMS_PROVIDER_ACKS = "oms_provider_acknowledgements_total"
OMS_PROVIDER_REJECTS = "oms_provider_rejects_total"
#: Submissions that got no answer. `11` §5's ambiguous outcome. Never zero in a
#: healthy system with a real network, and every one must reconcile.
OMS_AMBIGUOUS_SUBMISSIONS = "oms_ambiguous_submissions_total"
OMS_FILLS = "oms_fills_total"
OMS_CANCELLATIONS = "oms_cancellations_total"
#: Orders currently in UNKNOWN or PENDING_RECONCILIATION. A gauge, not a counter:
#: what matters is how many are outstanding right now, because each one blocks its
#: instrument for its strategy.
OMS_UNRESOLVED_ORDERS = "oms_unresolved_orders"
#: Round-trip to the venue, in seconds. A *system* latency: it is not market time
#: and must never be read as one.
OMS_PROVIDER_LATENCY = "oms_provider_latency_seconds"
#: Attempts that were already sent and were refused locally. The local half of
#: submission idempotency doing its job.
OMS_DUPLICATE_ATTEMPTS = "oms_duplicate_attempts_total"

RECONCILIATION_RUNS = "reconciliation_runs_total"
RECONCILIATION_DURATION = "reconciliation_duration_seconds"
#: Labelled by kind and resolution, so "the provider was ahead and we caught up"
#: and "there is an order at the broker we cannot explain" are not one number.
RECONCILIATION_DISCREPANCIES = "reconciliation_discrepancies_total"
RECONCILIATION_FILLS_INSERTED = "reconciliation_fills_inserted_total"
#: Discrepancies no automatic action is authorised for. `11` §6 requires alerting
#: on these; a rising count means a human is needed, not that the system is coping.
RECONCILIATION_NEEDS_ATTENTION = "reconciliation_needs_attention_total"
#: Provider observations already applied. Duplicates absorbed, not re-applied.
RECONCILIATION_DUPLICATE_OBSERVATIONS = "reconciliation_duplicate_observations_total"
#: How long an order stayed unresolved before reconciliation settled it.
RECONCILIATION_TIME_TO_RESOLVE = "reconciliation_time_to_resolve_seconds"
#: Whether the most recent run was clean. `18` Phase 10: trader is not ready until
#: it is, so this is the metric an operator gates a deployment on.
RECONCILIATION_LAST_RUN_CLEAN = "reconciliation_last_run_clean"
#: Attempts to reach a live execution path. Must always be zero: no adapter can
#: submit, so a non-zero value means something tried and the barrier held.
LIVE_EXECUTION_REFUSALS = "live_execution_refusals_total"


def record_oms_submission(
    *,
    venue: str,
    outcome: str,
    refusal: str | None = None,
    latency_seconds: float | None = None,
) -> None:
    """One submission attempt, labelled by what actually happened.

    `outcome` carries the adapter's answer -- acknowledged, rejected, ambiguous,
    not authorized, capability denied -- and `venue` says which venue answered. A
    dashboard therefore cannot show an acknowledgement without showing that it came
    from `PAPER`, which is what keeps brief §26's prohibition honest.
    """
    labels = {"venue": venue}
    METRICS.inc(OMS_SUBMIT_ATTEMPTS, {**labels, "outcome": outcome})
    if outcome == "ACKNOWLEDGED":
        METRICS.inc(OMS_PROVIDER_ACKS, labels)
    elif outcome == "REJECTED":
        METRICS.inc(OMS_PROVIDER_REJECTS, labels)
    elif outcome == "AMBIGUOUS":
        METRICS.inc(OMS_AMBIGUOUS_SUBMISSIONS, labels)
    elif outcome in ("NOT_AUTHORIZED", "CAPABILITY_DENIED"):
        METRICS.inc(OMS_SUBMISSIONS_BLOCKED, {**labels, "reason": refusal or outcome})
    if latency_seconds is not None:
        METRICS.observe(OMS_PROVIDER_LATENCY, latency_seconds, labels)


def record_oms_order_state(venue: str, state: str) -> None:
    METRICS.inc(OMS_ORDERS, {"venue": venue, "state": state})
    if state == "CANCELLED":
        METRICS.inc(OMS_CANCELLATIONS, {"venue": venue})


def record_oms_unresolved(venue: str, count: int) -> None:
    """A gauge: how many orders are unresolved **now**."""
    METRICS.set_gauge(OMS_UNRESOLVED_ORDERS, float(count), {"venue": venue})


def record_oms_duplicate_attempt(venue: str) -> None:
    METRICS.inc(OMS_DUPLICATE_ATTEMPTS, {"venue": venue})


def record_reconciliation_run(
    *,
    trigger: str,
    duration_seconds: float,
    is_clean: bool,
    discrepancies: dict[tuple[str, str], int],
    fills_inserted: int,
    needs_attention: int,
    duplicates_absorbed: int = 0,
) -> None:
    """One reconciliation pass, with its discrepancies broken out.

    `discrepancies` is keyed by `(kind, resolution)` rather than by kind alone: a
    `PROVIDER_AHEAD` that was applied is routine, and a `PROVIDER_AHEAD` that could
    not be resolved is not, and one label cannot tell them apart.
    """
    labels = {"trigger": trigger}
    METRICS.inc(RECONCILIATION_RUNS, {**labels, "clean": str(is_clean).lower()})
    METRICS.observe(RECONCILIATION_DURATION, duration_seconds, labels)
    METRICS.set_gauge(RECONCILIATION_LAST_RUN_CLEAN, 1.0 if is_clean else 0.0, {})
    for (kind, resolution), count in sorted(discrepancies.items()):
        METRICS.inc(
            RECONCILIATION_DISCREPANCIES,
            {**labels, "kind": kind, "resolution": resolution},
            count,
        )
    if fills_inserted:
        METRICS.inc(RECONCILIATION_FILLS_INSERTED, labels, fills_inserted)
    if needs_attention:
        METRICS.inc(RECONCILIATION_NEEDS_ATTENTION, labels, needs_attention)
    if duplicates_absorbed:
        METRICS.inc(RECONCILIATION_DUPLICATE_OBSERVATIONS, labels, duplicates_absorbed)


def record_time_to_resolve(seconds: float) -> None:
    """How long an order stayed unresolved. Market-time duration, not wall clock."""
    METRICS.observe(RECONCILIATION_TIME_TO_RESOLVE, seconds, {})


def record_capability_refusal(who: str, capability: str) -> None:
    """An adapter was asked for a capability it does not hold. The barrier held.

    Distinct from `record_paper_account`'s mode refusal, which is about an
    *account* claiming to be live. This is about an *adapter* being asked to
    submit. Both must always be zero; conflating them would hide which barrier
    was tested.
    """
    METRICS.inc(LIVE_EXECUTION_REFUSALS, {"who": who, "capability": capability})


# --- Phase 11 portfolio and attribution (`16-OBSERVABILITY.md`, `11-TRADING.md` §8).
#
# Brief §27: names and units must be meaningful. Every amount metric is in account
# currency and says so in its name; every ratio is a fraction in [0, 1] and says
# that too. A bare `residual_total` would be ambiguous between the two.
PORTFOLIO_VALUATIONS = "portfolio_valuations_total"
PORTFOLIO_VALUATION_DURATION = "portfolio_valuation_duration_seconds"
#: Positions that could not be priced, labelled by reason. Each one silently
#: understates exposure if a reader takes the total at face value.
PORTFOLIO_UNVALUED_POSITIONS = "portfolio_unvalued_positions_total"
#: Valuations refused outright because the state was unreliable. Distinct from a
#: partial valuation: a refusal produced no number at all.
PORTFOLIO_VALUATIONS_REFUSED = "portfolio_valuations_refused_total"
PORTFOLIO_POSITION_UPDATES = "portfolio_position_updates_total"
PORTFOLIO_DUPLICATE_FILLS = "portfolio_duplicate_fills_total"
PORTFOLIO_SNAPSHOTS = "portfolio_snapshots_total"
#: Snapshots whose valuation was partial. A gauge of how often the book cannot be
#: fully priced, which is an operational fact rather than an error.
PORTFOLIO_INCOMPLETE_SNAPSHOTS = "portfolio_incomplete_snapshots_total"

ATTRIBUTION_RUNS = "attribution_runs_total"
ATTRIBUTION_DURATION = "attribution_duration_seconds"
#: Absolute unexplained amount, in account currency. `18` Phase 11 requires the
#: residual to be prominent, and a histogram of it is how an operator notices the
#: decomposition degrading before anyone reads a report.
ATTRIBUTION_RESIDUAL_ABS = "attribution_residual_abs_currency"
#: Residual as a fraction of |total P&L|, in [0, 1]. The number that says how much
#: the model actually explained; the absolute figure alone cannot.
ATTRIBUTION_RESIDUAL_FRACTION = "attribution_residual_fraction_ratio"
#: Components that could not be computed, labelled by component. Usually the
#: residual's cause, and labelling them is what makes the cause findable.
ATTRIBUTION_UNCOMPUTED_COMPONENTS = "attribution_uncomputed_components_total"
#: Slices with no identifiable owner. Brief §15: represented, never assigned.
ATTRIBUTION_UNATTRIBUTED_SLICES = "attribution_unattributed_slices_total"

POSITION_RECONCILIATION_RUNS = "position_reconciliation_runs_total"
#: Labelled by kind and resolution: a corrected quantity mismatch and an
#: unexplainable broker position are not one number.
POSITION_RECONCILIATION_MISMATCHES = "position_reconciliation_mismatches_total"
POSITION_RECONCILIATION_CORRECTIONS = "position_reconciliation_corrections_total"
POSITION_RECONCILIATION_NEEDS_ATTENTION = "position_reconciliation_needs_attention_total"
#: Instrument versions that could not be resolved at the valuation time. Each one
#: makes a position unvaluable, so this should stay at zero.
PORTFOLIO_ECONOMICS_UNRESOLVED = "portfolio_economics_unresolved_total"


def record_portfolio_valuation(
    account_id: str,
    *,
    duration_seconds: float,
    positions_valued: int,
    unvalued_by_reason: dict[str, int] | None = None,
    refused: bool = False,
) -> None:
    """One valuation pass, with what it could not price.

    `unvalued_by_reason` is labelled rather than summed: a missing price and
    missing contract economics are different problems with different fixes, and
    one counter could not tell an operator which they have.
    """
    labels = {"account": account_id}
    if refused:
        METRICS.inc(PORTFOLIO_VALUATIONS_REFUSED, labels)
        return
    METRICS.inc(PORTFOLIO_VALUATIONS, labels)
    METRICS.observe(PORTFOLIO_VALUATION_DURATION, duration_seconds, labels)
    for reason, count in sorted((unvalued_by_reason or {}).items()):
        METRICS.inc(PORTFOLIO_UNVALUED_POSITIONS, {**labels, "reason": reason}, count)
        if reason == "NO_CONTRACT_ECONOMICS":
            METRICS.inc(PORTFOLIO_ECONOMICS_UNRESOLVED, labels, count)


def record_portfolio_snapshot(account_id: str, *, is_complete: bool) -> None:
    METRICS.inc(PORTFOLIO_SNAPSHOTS, {"account": account_id})
    if not is_complete:
        METRICS.inc(PORTFOLIO_INCOMPLETE_SNAPSHOTS, {"account": account_id})


def record_position_update(account_id: str, *, applied: int, duplicates: int) -> None:
    labels = {"account": account_id}
    if applied:
        METRICS.inc(PORTFOLIO_POSITION_UPDATES, labels, applied)
    if duplicates:
        METRICS.inc(PORTFOLIO_DUPLICATE_FILLS, labels, duplicates)


def record_attribution_run(
    account_id: str,
    *,
    bucket: str,
    duration_seconds: float,
    residual_abs: float,
    residual_fraction: float | None,
    uncomputed_components: tuple[str, ...] = (),
    unattributed_slices: int = 0,
) -> None:
    """One attribution pass. The residual is recorded both ways, deliberately.

    Absolute and fractional are different questions. A residual of 500 rupees is
    trivial on a 5,000,000 move and alarming on a 600 one; only the fraction
    distinguishes them, and only the absolute figure sizes the exposure.
    """
    labels = {"account": account_id, "bucket": bucket}
    METRICS.inc(ATTRIBUTION_RUNS, labels)
    METRICS.observe(ATTRIBUTION_DURATION, duration_seconds, labels)
    METRICS.observe(ATTRIBUTION_RESIDUAL_ABS, residual_abs, labels)
    if residual_fraction is not None:
        METRICS.observe(ATTRIBUTION_RESIDUAL_FRACTION, residual_fraction, labels)
    for component in uncomputed_components:
        METRICS.inc(ATTRIBUTION_UNCOMPUTED_COMPONENTS, {**labels, "component": component})
    if unattributed_slices:
        METRICS.inc(ATTRIBUTION_UNATTRIBUTED_SLICES, labels, unattributed_slices)


def record_position_reconciliation(
    account_id: str,
    *,
    is_clean: bool,
    mismatches: dict[tuple[str, str], int] | None = None,
    corrections: int = 0,
    needs_attention: int = 0,
) -> None:
    labels = {"account": account_id}
    METRICS.inc(POSITION_RECONCILIATION_RUNS, {**labels, "clean": str(is_clean).lower()})
    for (kind, resolution), count in sorted((mismatches or {}).items()):
        METRICS.inc(
            POSITION_RECONCILIATION_MISMATCHES,
            {**labels, "kind": kind, "resolution": resolution},
            count,
        )
    if corrections:
        METRICS.inc(POSITION_RECONCILIATION_CORRECTIONS, labels, corrections)
    if needs_attention:
        METRICS.inc(POSITION_RECONCILIATION_NEEDS_ATTENTION, labels, needs_attention)
