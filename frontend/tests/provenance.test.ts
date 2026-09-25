/**
 * Provenance navigation. `13-FRONTEND_IA.md` §7 and Phase 12 brief §19.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { signals } from "@/lib/terminal/endpoints";
import {
  DECISION_CHAIN_ORDER,
  METRIC_CHAIN_ORDER,
  NOT_RECORDED,
  chainStep,
  inCanonicalOrder,
  summariseChain,
} from "@/lib/terminal/provenance";

const resolveSignal = (ref: string) => ({
  href: `/terminal/signals?signal_id=${ref}`,
  endpoint: signals.detail(ref),
});

test("a recorded reference becomes a followable step", () => {
  const step = chainStep("SIGNAL", "sig_1", resolveSignal);
  assert.equal(step.available, true);
  assert.equal(step.reference, "sig_1");
  assert.equal(step.href, "/terminal/signals?signal_id=sig_1");
  assert.equal(step.endpoint?.template, "/signals/{signal_id}");
  assert.equal(step.unavailableReason, null);
});

test("a missing reference becomes a stated gap, not a link to nowhere", () => {
  const step = chainStep("RISK_DECISION", null, resolveSignal);
  assert.equal(step.available, false);
  assert.equal(step.href, null);
  assert.equal(step.endpoint, null);
  assert.notEqual(step.unavailableReason, null);
});

test("the backend's `not recorded` sentinel renders as a gap, not as an id", () => {
  const step = chainStep("STRATEGY", NOT_RECORDED, resolveSignal);
  assert.equal(step.available, false);
  assert.equal(step.reference, null);
});

test("a partial chain is never summarised as complete", () => {
  const summary = summariseChain([
    chainStep("SIGNAL", "sig_1", resolveSignal),
    chainStep("EVIDENCE", null, resolveSignal),
  ]);
  assert.equal(summary.complete, false);
  assert.equal(summary.resolvedCount, 1);
  assert.equal(summary.gapCount, 1);
});

test("an empty chain is not complete either", () => {
  assert.equal(summariseChain([]).complete, false);
});

test("the decision chain is the one brief §19 names, in order", () => {
  assert.deepEqual(DECISION_CHAIN_ORDER, [
    "SIGNAL",
    "EVIDENCE",
    "STRATEGY",
    "TRADE_INTENT",
    "RISK_DECISION",
    "OMS_ORDER",
    "FILL",
    "POSITION",
    "PNL",
  ]);
});

test("the metric drill-through is the one `13` §7 names, in order", () => {
  assert.deepEqual(METRIC_CHAIN_ORDER, [
    "FEATURE_DEFINITION",
    "FEATURE_INPUTS",
    "MARKET_STATE",
    "RAW_OBSERVATIONS",
  ]);
});

test("a chain rendered out of order is detected", () => {
  const steps = [
    chainStep("FILL", "f1", resolveSignal),
    chainStep("SIGNAL", "s1", resolveSignal),
  ];
  assert.equal(inCanonicalOrder(steps, DECISION_CHAIN_ORDER), false);
  assert.equal(inCanonicalOrder([...steps].reverse(), DECISION_CHAIN_ORDER), true);
});

test("a gap step still occupies its position, so the chain stays readable", () => {
  const steps = [
    chainStep("SIGNAL", "s1", resolveSignal),
    chainStep("EVIDENCE", null, resolveSignal),
    chainStep("TRADE_INTENT", "i1", resolveSignal),
  ];
  assert.equal(inCanonicalOrder(steps, DECISION_CHAIN_ORDER), true);
  assert.equal(steps[1].label, "Evidence");
});
