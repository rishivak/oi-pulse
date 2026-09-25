/**
 * Screen view models, part two: research, replay, backtest, trading and portfolio.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { OrderDto, PortfolioSnapshotDto, ValuationResultDto } from "@/lib/api/dto";
import {
  MATERIAL_SAMPLE_LOSS,
  REPLAY_CONTROLS,
  backtestSummary,
  replayBanner,
  replayProgress,
  sampleReading,
} from "@/lib/terminal/screens/research";
import {
  blockedInstruments,
  eventRows,
  isBlockedFor,
  limitSummary,
  omsRow,
  orderChain,
  riskPreview,
  submissionPermitted,
} from "@/lib/terminal/screens/trading";
import {
  completeness,
  greeksReading,
  marginReading,
  positionRow,
  reconciliationVerdict,
  returnReading,
  valuationSummary,
} from "@/lib/terminal/screens/portfolio";
import { inCanonicalOrder, DECISION_CHAIN_ORDER } from "@/lib/terminal/provenance";

// ------------------------------------------------------------------ research

const sample = (over: Record<string, unknown> = {}) =>
  ({
    policy: {},
    raw_events: 100,
    effective_sample: 92,
    clusters: 8,
    dropped_overlapping: 5,
    dropped_separation: 2,
    excluded_quality: 1,
    mean_overlap: 0.1,
    ...over,
  }) as never;

test("raw and effective sample are both reported", () => {
  const reading = sampleReading(sample());
  assert.equal(reading.rawEvents, 100);
  assert.equal(reading.effectiveSample, 92);
  assert.equal(reading.warning, null);
});

test("a materially reduced sample carries an explicit warning", () => {
  const reading = sampleReading(sample({ effective_sample: 20 }));
  assert.ok(reading.retention !== null && reading.retention < MATERIAL_SAMPLE_LOSS);
  assert.match(reading.warning ?? "", /not independent/);
  assert.match(reading.warning ?? "", /significance read off the raw count would be overstated/);
});

test("exclusion counts survive into the reading", () => {
  const reading = sampleReading(sample());
  assert.equal(reading.droppedOverlapping, 5);
  assert.equal(reading.droppedSeparation, 2);
  assert.equal(reading.excludedQuality, 1);
});

test("no events means no retention figure rather than a division by zero", () => {
  assert.equal(sampleReading(sample({ raw_events: 0, effective_sample: 0 })).retention, null);
});

// -------------------------------------------------------------------- replay

const context = (over: Record<string, unknown> = {}) =>
  ({
    run_id: "r1",
    underlying_ids: [1],
    expiry_ids: [10],
    period: {},
    step_mode: "FIXED_INTERVAL",
    interval_seconds: 60,
    speed: 1,
    knowledge_mode: "POINT_IN_TIME",
    pinned_knowledge_horizon: null,
    is_hindsight: false,
    build_context_id: "bc",
    feature_versions: {},
    rule_versions: {},
    content_digest: "sha",
    ...over,
  }) as never;

test("the replay banner is always persistent", () => {
  const banner = replayBanner(context(), null);
  assert.equal(banner.persistent, true);
  assert.match(banner.explanation, /This is not live/);
});

test("a hindsight replay is labelled loudly and says what it shows", () => {
  const banner = replayBanner(context({ is_hindsight: true }), null);
  assert.equal(banner.label, "REPLAY — HINDSIGHT");
  assert.equal(banner.glyph, "⚠");
  assert.match(banner.explanation, /not what was knowable then/);
});

test("the banner carries both axes from the current step", () => {
  const banner = replayBanner(context(), {
    index: 3,
    market_time: "2026-03-03T11:42:00Z",
    knowledge_time: "2026-03-03T11:42:00Z",
    is_lockstep: true,
  });
  assert.equal(banner.axes.marketTime, "2026-03-03T11:42:00Z");
  assert.equal(banner.axes.knowledgeTime, "2026-03-03T11:42:00Z");
});

test("the transport verbs are the backend's; no order verb is among them", () => {
  assert.deepEqual(REPLAY_CONTROLS, ["play", "pause", "step", "seek", "speed"]);
  for (const control of REPLAY_CONTROLS) {
    assert.doesNotMatch(control, /order|submit|cancel/);
  }
});

test("checkpoint hit rate is null rather than zero when nothing was attempted", () => {
  const reading = replayProgress({
    steps_completed: 0,
    states_built: 0,
    observations_visible: 0,
    checkpoint_hits: 0,
    checkpoint_misses: 0,
  });
  assert.equal(reading.checkpointHitRate, null);
});

// ------------------------------------------------------------------ backtest

const backtest = (over: Record<string, unknown> = {}) =>
  ({
    run_id: "b1",
    content_hash: "h",
    strategy: {},
    strategy_digest: "sd",
    replay_digest: "rd",
    fill_model_digest: "fd",
    statistics: {
      intents_generated: 10,
      intents_rejected_by_risk: 2,
      orders_submitted: 8,
      fills: 8,
      partial_fills: 1,
      fill_rate: "1.00",
      assumption_based_fills: 0,
      rejections: 0,
      rejection_reasons: {},
      gross_pnl: "1000",
      net_pnl: "900",
      realized_pnl: "900",
      unrealized_pnl: "0",
      fees: "50",
      slippage_cost: "50",
      turnover: "10000",
      max_drawdown: "-120",
    },
    assumptions: { latency_ms: 250, spread_bps: 5, slippage_model: "PROPORTIONAL" },
    assumption_based: false,
    caveats: [],
    risk_evaluated: true,
    fills: [],
    final_ledger: {},
    build_context_id: "bc",
    execution: {},
    ...over,
  }) as never;

test("the assumption set is a first-class list beside the headline", () => {
  const summary = backtestSummary(backtest());
  assert.equal(summary.assumptions.length, 3);
  assert.deepEqual(
    summary.assumptions.map((a) => a.name).sort(),
    ["latency_ms", "slippage_model", "spread_bps"],
  );
});

test("an assumption-based result qualifies its own headline", () => {
  const summary = backtestSummary(
    backtest({ assumption_based: true, statistics: { ...(backtest() as never as { statistics: Record<string, unknown> }).statistics, assumption_based_fills: 3 } }),
  );
  assert.match(summary.headlineQualifier ?? "", /assumed spread rather than an observed quote/);
});

test("a fully observed result carries no spurious qualifier", () => {
  assert.equal(backtestSummary(backtest()).headlineQualifier, null);
});

test("an absent max drawdown is insufficient history, not zero", () => {
  const stats = { ...(backtest() as never as { statistics: Record<string, unknown> }).statistics, max_drawdown: null };
  const summary = backtestSummary(backtest({ statistics: stats }));
  assert.equal(summary.maxDrawdown.kind, "absent");
});

// ------------------------------------------------------------------- trading

const order = (over: Partial<OrderDto> = {}): OrderDto =>
  ({
    order_id: "o1",
    account_id: "a1",
    intent_id: "i1",
    instrument_id: 500,
    side: "BUY",
    quantity: 50,
    order_type: "LIMIT",
    limit_price: "106.40",
    state: "OPEN",
    venue: "PAPER",
    mode: "PAPER",
    filled_quantity: 0,
    remaining_quantity: 50,
    average_fill_price: null,
    is_terminal: false,
    is_unresolved: false,
    provider_order_id: null,
    provider_status: null,
    provider_event_time: null,
    reject_reason: null,
    reject_detail: null,
    authorizing_risk_decision_id: "rd1",
    authorizing_decision_sequence: 1,
    signal_id: "sig_1",
    signal_version: 2,
    strategy_id: "strat",
    strategy_version: 1,
    build_context_id: "bc",
    state_checkpoint_ref: "ck",
    config_digest: "cd",
    client_order_attempt_id: "ca",
    knowledge_time: "2026-03-03T11:42:00Z",
    decision_time: "2026-03-03T11:42:00Z",
    created_at: null,
    received_at: null,
    events: [],
    ...over,
  }) as OrderDto;

test("local state and provider status stay separate columns", () => {
  const row = omsRow(order({ state: "OPEN", provider_status: "PENDING", mode: "LIVE" }));
  assert.equal(row.localState.value, "OPEN");
  assert.equal(row.providerStatus, "PENDING");
});

test("an UNKNOWN order is blocked and offers no cancel", () => {
  const row = omsRow(order({ state: "UNKNOWN" }));
  assert.equal(row.blocked, true);
  assert.equal(row.canCancel, false);
});

test("a live order with no provider id cannot be cancelled", () => {
  const row = omsRow(order({ state: "OPEN", mode: "LIVE", provider_order_id: null }));
  assert.equal(row.canCancel, false);
  assert.equal(row.providerIdentity.state, "UNAVAILABLE");
});

test("a paper order can be cancelled without a provider id", () => {
  assert.equal(omsRow(order({ state: "OPEN" })).canCancel, true);
  assert.equal(omsRow(order({ state: "OPEN" })).providerIdentity.state, "NOT_APPLICABLE");
});

test("an UNKNOWN order blocks its own strategy and instrument, not everyone's", () => {
  const blocked = blockedInstruments([
    order({ state: "UNKNOWN", strategy_id: "A", instrument_id: 500 }),
  ]);
  assert.equal(isBlockedFor(blocked, "A", 500), true);
  assert.equal(isBlockedFor(blocked, "B", 500), false);
  assert.equal(isBlockedFor(blocked, "A", 501), false);
});

test("a gap in the event sequence is reported, not closed over", () => {
  const { rows, missingSequences } = eventRows([
    { order_id: "o1", sequence: 3, from_state: "OPEN", to_state: "FILLED", trigger: "T", occurred_at: "t", payload: {} },
    { order_id: "o1", sequence: 1, from_state: null, to_state: "CREATED", trigger: "T", occurred_at: "t", payload: {} },
  ]);
  assert.deepEqual(rows.map((r) => r.sequence), [1, 3]);
  assert.deepEqual(missingSequences, [2]);
});

test("the order chain is in canonical order and names its gaps", () => {
  const steps = orderChain("a1", order({ signal_id: null }));
  assert.equal(inCanonicalOrder(steps, DECISION_CHAIN_ORDER), true);
  const signalStep = steps.find((s) => s.kind === "SIGNAL");
  assert.equal(signalStep?.available, false);
  assert.match(signalStep?.unavailableReason ?? "", /records no signal/);
});

test("unconfigured and unevaluable limits are not counted as passing", () => {
  const summary = limitSummary({ passed: 5, breached: 0, not_configured: 20, not_evaluable: 1 });
  assert.equal(summary.fullyEvaluated, false);
  assert.match(summary.caveat ?? "", /not counted as passing/);
  assert.equal(summary.counts.PASSED, 5);
});

test("a fully evaluated account carries no caveat", () => {
  const summary = limitSummary({ passed: 5, breached: 1, not_configured: 0, not_evaluable: 0 });
  assert.equal(summary.fullyEvaluated, true);
  assert.equal(summary.caveat, null);
});

test("approval is read from the server's flag, never inferred from zero breaches", () => {
  const unevaluated = riskPreview({ verdict: "APPROVED" }, { evaluated: false, breach_count: 0 });
  assert.equal(unevaluated.approved, false);
  assert.equal(submissionPermitted(unevaluated), false);
});

test("an approval that is no longer actionable does not permit submission", () => {
  const stale = riskPreview(
    { verdict: "APPROVED" },
    { evaluated: true, is_approved: true, actionable: false, authorization_status: "EXPIRED" },
  );
  assert.equal(stale.approved, true);
  assert.equal(submissionPermitted(stale), false);
});

test("a live approval permits submission", () => {
  const live = riskPreview(
    { verdict: "APPROVED" },
    { evaluated: true, is_approved: true, actionable: true },
  );
  assert.equal(submissionPermitted(live), true);
});

// ----------------------------------------------------------------- portfolio

const valuation = (over: Partial<ValuationResultDto> = {}): ValuationResultDto =>
  ({
    market_time: "2026-03-03T11:42:00Z",
    knowledge_time: "2026-03-03T11:42:00Z",
    positions: [],
    total_market_value: "10000",
    total_unrealized_pnl: "250",
    gross_exposure: "20000",
    net_exposure: "5000",
    is_complete: true,
    unvalued_instruments: [],
    state_quality: "OK",
    market_state_ref: "ms",
    build_context_id: "bc",
    ...over,
  }) as ValuationResultDto;

test("an incomplete valuation still shows totals, with the notice beside them", () => {
  const summary = valuationSummary(valuation({ is_complete: false, unvalued_instruments: [7, 9] }));
  assert.equal(summary.totalMarketValue.kind, "present");
  assert.equal(summary.totalMarketValue.kind === "present" && summary.totalMarketValue.stale, true);
  assert.match(summary.completeness.text ?? "", /describe part of the book/);
  assert.deepEqual(summary.completeness.unvaluedInstruments, [7, 9]);
});

test("a complete valuation carries no notice", () => {
  assert.equal(valuationSummary(valuation()).completeness.text, null);
  assert.equal(completeness(true, []).text, null);
});

test("unknown margin is absent, not zero utilisation", () => {
  const snapshot = { margin_utilisation: null, margin_basis: null } as PortfolioSnapshotDto;
  const reading = marginReading(snapshot);
  assert.equal(reading.utilisation.kind, "absent");
  assert.match(reading.utilisation.kind === "absent" ? reading.utilisation.detail ?? "" : "", /not zero utilisation/);
});

test("margin is shown with the basis that produced it", () => {
  const snapshot = { margin_utilisation: "0.42", margin_basis: "SPAN_APPROX" } as PortfolioSnapshotDto;
  assert.equal(marginReading(snapshot).basis, "SPAN_APPROX");
});

test("the return states its methodology and is not called comparable", () => {
  const snapshot = {
    returns: { simple_period_return: "0.05", return_methodology: "SIMPLE_PERIOD" },
  } as PortfolioSnapshotDto;
  const reading = returnReading(snapshot);
  assert.equal(reading.methodology, "SIMPLE_PERIOD");
  assert.match(reading.caveat, /not comparable with a TWR or MWR figure/);
});

test("partial greeks are marked and their coverage stated", () => {
  const snapshot = {
    greeks: {
      delta: "120",
      gamma: null,
      vega: null,
      theta: null,
      is_complete: false,
      positions_included: 3,
      positions_total: 10,
    },
  } as PortfolioSnapshotDto;
  const reading = greeksReading(snapshot);
  assert.equal(reading.complete, false);
  assert.equal(reading.coverage, "3 of 10 positions");
  assert.equal(reading.delta.kind === "present" && reading.delta.stale, true);
});

test("a position without economics is flagged as unvaluable", () => {
  const row = positionRow({
    quantity: 50,
    average_price: "106.40",
    cost_basis: "5320",
    cost_basis_method: "AVERAGE",
    realized_pnl: "0",
    fees: "20",
    status: "OPEN",
    economics: null,
    underlying_id: 1,
    expiry_id: 10,
    opened_at: null,
    last_fill_at: null,
    last_fill_key: null,
    fills_applied: 1,
  });
  assert.equal(row.economicsKnown, false);
  assert.equal(row.costBasisMethod, "AVERAGE");
});

test("an unclean reconciliation says what was corrected and what was not", () => {
  const verdict = reconciliationVerdict({
    run_id: "r",
    account_id: "a",
    portfolio_id: null,
    as_of: "t",
    started_at: null,
    completed_at: null,
    local_snapshot: {},
    provider_snapshot: {},
    discrepancies: [],
    matched: 8,
    mismatched: 2,
    corrections_applied: 1,
    is_clean: false,
    needs_attention: true,
    content_digest: "d",
  });
  assert.equal(verdict.clean, false);
  assert.match(verdict.detail, /no other corrective action is authorised/);
});
