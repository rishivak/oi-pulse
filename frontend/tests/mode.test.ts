/**
 * Execution mode and the three live-trading gates.
 * `13-FRONTEND_IA.md` §6, `17-SECURITY.md` §4, Phase 12 brief §12 and §23.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  LIVE_TRADING_GATES,
  LIVE_WITHHELD_NOTICE,
  executionMode,
  liveTradingRenderable,
} from "@/lib/terminal/mode";

test("all three gates are closed, and each says where that is established", () => {
  assert.equal(LIVE_TRADING_GATES.length, 3);
  for (const gate of LIVE_TRADING_GATES) {
    assert.equal(gate.satisfied, false);
    assert.notEqual(gate.evidence, "");
  }
  assert.deepEqual(
    LIVE_TRADING_GATES.map((g) => g.id),
    ["LIVE_TRADING_ENABLED", "LIVE_TRADING_CONFIRMED", "LIVE_TRADE_PERMISSION"],
  );
});

test("live trading cannot render", () => {
  assert.equal(liveTradingRenderable(), false);
});

test("a response that states PAPER with live unavailable allows order entry", () => {
  const badge = executionMode({ mode: "PAPER", live_execution_available: false });
  assert.equal(badge.mode, "PAPER");
  assert.equal(badge.orderEntryAllowed, true);
  assert.match(badge.explanation, /no live adapter/);
});

test("an unlabelled response is UNKNOWN, and order entry is withheld", () => {
  const badge = executionMode({});
  assert.equal(badge.mode, "UNKNOWN");
  assert.equal(badge.orderEntryAllowed, false);
  assert.match(badge.explanation, /withheld rather than assumed/);
});

test("PAPER without the live-unavailable statement is still UNKNOWN", () => {
  // Half the contract is not the contract: a route that said PAPER but stopped
  // saying live execution was unavailable would be a route worth stopping at.
  assert.equal(executionMode({ mode: "PAPER" }).orderEntryAllowed, false);
});

test("a response claiming live availability does not unlock anything", () => {
  const badge = executionMode({ mode: "LIVE", live_execution_available: true });
  assert.equal(badge.mode, "UNKNOWN");
  assert.equal(badge.orderEntryAllowed, false);
  assert.equal(liveTradingRenderable(), false);
});

test("the withheld notice explains the absence rather than leaving a gap", () => {
  assert.match(LIVE_WITHHELD_NOTICE, /All three gates/);
  assert.match(LIVE_WITHHELD_NOTICE, /including a disabled one/);
});

test("the PAPER badge is legible without colour", () => {
  const badge = executionMode({ mode: "PAPER", live_execution_available: false });
  assert.notEqual(badge.glyph, "");
  assert.match(badge.srLabel, /no live orders are possible/);
});
