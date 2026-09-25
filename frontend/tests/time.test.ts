/**
 * The two time axes.
 *
 * `13-FRONTEND_IA.md` §1.6 and §4. These tests exist because the failure they guard
 * against is silent: a terminal that collapses the axes still renders, still looks
 * right, and answers a different question from the one asked.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  AXIS_LABELS,
  IncoherentTimeAxes,
  axisReadings,
  defaultDecisionTime,
  effectiveKnowledgeTime,
  isHindsight,
  isLive,
  resolveSemantics,
  syncIndicator,
} from "@/lib/terminal/time";

const T = "2026-03-03T11:42:00Z";
const LATER = "2026-03-05T09:00:00Z";
const EARLIER = "2026-03-03T11:00:00Z";

test("knowledge time defaults to market time rather than to latest", () => {
  assert.equal(effectiveKnowledgeTime({ marketTime: T, knowledgeTime: null }), T);
});

test("decision time defaults to the knowledge horizon, not to now", () => {
  assert.equal(defaultDecisionTime({ marketTime: T, knowledgeTime: LATER }), LATER);
});

test("equal axes resolve to knowledge_at", () => {
  assert.equal(resolveSemantics({ marketTime: T, knowledgeTime: T }), "knowledge_at");
  assert.equal(resolveSemantics({ marketTime: T, knowledgeTime: null }), "knowledge_at");
});

test("a later knowledge time resolves to market_truth_at", () => {
  assert.equal(resolveSemantics({ marketTime: T, knowledgeTime: LATER }), "market_truth_at");
});

test("knowledge before market time is refused before a request is built", () => {
  assert.throws(
    () => resolveSemantics({ marketTime: T, knowledgeTime: EARLIER }),
    IncoherentTimeAxes,
  );
});

test("hindsight is computed from the axes, never stored as a toggle", () => {
  assert.equal(isHindsight({ marketTime: T, knowledgeTime: LATER }), true);
  assert.equal(isHindsight({ marketTime: T, knowledgeTime: T }), false);
  // No market time means live: there is no "then" to have hindsight about.
  assert.equal(isHindsight({ marketTime: null, knowledgeTime: LATER }), false);
});

test("the indicator is loud and persistent while hindsight is active", () => {
  const indicator = syncIndicator({ marketTime: T, knowledgeTime: LATER });
  assert.equal(indicator.state, "HINDSIGHT");
  assert.equal(indicator.label, "HINDSIGHT");
  assert.equal(indicator.persistent, true);
  assert.match(indicator.explanation, /what we now know about that moment/);
});

test("every sync state carries a glyph, so status survives without colour", () => {
  for (const axes of [
    { marketTime: null, knowledgeTime: null },
    { marketTime: T, knowledgeTime: T },
    { marketTime: T, knowledgeTime: LATER },
  ]) {
    assert.notEqual(syncIndicator(axes).glyph, "");
  }
});

test("live is the absence of a pinned market time", () => {
  assert.equal(isLive({ marketTime: null, knowledgeTime: null }), true);
  assert.equal(isLive({ marketTime: T, knowledgeTime: null }), false);
});

test("readings are labelled pairs; no single unlabelled timestamp is produced", () => {
  const readings = axisReadings({ marketTime: T, knowledgeTime: LATER });
  assert.equal(readings.length, 2);
  assert.deepEqual(
    readings.map((r) => r.label),
    ["MARKET TIME", "KNOWLEDGE TIME"],
  );
  for (const reading of readings) {
    assert.notEqual(reading.label, "");
    assert.notEqual(reading.meaning, "");
  }
});

test("decision time is opt-in and labelled as a request parameter", () => {
  const readings = axisReadings({ marketTime: T, knowledgeTime: LATER }, { decision: true });
  assert.equal(readings.length, 3);
  assert.equal(readings[2].label, "DECISION TIME");
  assert.match(AXIS_LABELS.decision.meaning, /never stored/);
});

test("a null axis renders a placeholder rather than an empty cell", () => {
  const readings = axisReadings({ marketTime: null, knowledgeTime: null });
  assert.equal(readings[0].value, null);
  assert.equal(readings[0].placeholder, "latest");
});
