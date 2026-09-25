/**
 * State badges. The vocabulary is the backend's; these tests pin the treatments
 * that carry real consequences — chiefly `UNKNOWN` (`11-TRADING.md` §5).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DISCREPANCY_KINDS,
  ORDER_STATES,
  SIGNAL_STATUSES,
  blocksInstrument,
  cancellable,
  discrepancyBadge,
  needsAttention,
  orderStateBadge,
  providerIdentity,
  signalActionable,
  signalStatusBadge,
  signalTerminal,
} from "@/lib/terminal/states";

test("UNKNOWN blocks its instrument and offers no cancel", () => {
  assert.equal(blocksInstrument("UNKNOWN"), true);
  assert.equal(cancellable("UNKNOWN"), false);
  const badge = orderStateBadge("UNKNOWN");
  assert.equal(badge.tone, "BLOCKING");
  assert.match(badge.detail ?? "", /not assumed rejected and not assumed accepted/);
  assert.match(badge.detail ?? "", /will not be resubmitted/);
});

test("a state this build does not recognise blocks rather than being shrugged off", () => {
  assert.equal(blocksInstrument("SOMETHING_NEW"), true);
  assert.equal(cancellable("SOMETHING_NEW"), false);
  assert.equal(orderStateBadge("SOMETHING_NEW").tone, "BLOCKING");
  // Shown verbatim, so an operator sees what the backend said.
  assert.equal(orderStateBadge("SOMETHING_NEW").label, "SOMETHING_NEW");
});

test("cancel is offered only from resting states", () => {
  assert.deepEqual(ORDER_STATES.filter(cancellable), [
    "ACCEPTED",
    "OPEN",
    "PARTIALLY_FILLED",
  ]);
});

test("CANCEL_PENDING does not read as cancelled", () => {
  const badge = orderStateBadge("CANCEL_PENDING");
  assert.notEqual(badge.tone, "POSITIVE");
  assert.match(badge.detail ?? "", /does not mean the order is cancelled/);
});

test("every order state has a glyph and a screen-reader label", () => {
  for (const state of ORDER_STATES) {
    const badge = orderStateBadge(state);
    assert.notEqual(badge.glyph, "");
    assert.notEqual(badge.srLabel, "");
  }
});

test("FORMING is not actionable", () => {
  assert.equal(signalActionable("FORMING"), false);
  assert.match(signalStatusBadge("FORMING").detail ?? "", /every near-miss/);
});

test("only ACTIVE and CONFIRMED are actionable", () => {
  assert.deepEqual(SIGNAL_STATUSES.filter(signalActionable), ["ACTIVE", "CONFIRMED"]);
});

test("the terminal signal states are the three `08` §3 names", () => {
  assert.deepEqual(SIGNAL_STATUSES.filter(signalTerminal), [
    "INVALIDATED",
    "EXPIRED",
    "FADED",
  ]);
});

test("OMS_AHEAD is not presented as something to push to the provider", () => {
  const badge = discrepancyBadge("OMS_AHEAD");
  assert.match(badge.srLabel, /local state shows progress the provider does not/);
  assert.match(badge.detail ?? "", /never resolved by pushing our view onto the provider/);
});

test("an unclassifiable discrepancy is neither an error nor a match", () => {
  const badge = discrepancyBadge("UNKNOWN");
  assert.equal(badge.tone, "BLOCKING");
  assert.match(badge.detail ?? "", /Not an error, and not a match/);
});

test("attention is required exactly on the two resolutions `11` §6 names", () => {
  assert.equal(needsAttention("RECORDED_ONLY"), true);
  assert.equal(needsAttention("UNRESOLVED"), true);
  assert.equal(needsAttention("POSITION_CORRECTED"), false);
  assert.equal(needsAttention("NONE"), false);
});

test("every discrepancy kind renders", () => {
  for (const kind of DISCREPANCY_KINDS) {
    assert.notEqual(discrepancyBadge(kind).glyph, "");
  }
});

test("a missing provider id is stated, never left as a blank cell", () => {
  const badge = providerIdentity(null, "LIVE");
  assert.equal(badge.state, "UNAVAILABLE");
  assert.equal(badge.label, "NO PROVIDER ID");
  assert.match(badge.detail, /cannot be cancelled or queried at the venue/);
});

test("a paper order says there is no provider, not that one is missing", () => {
  const badge = providerIdentity(null, "PAPER");
  assert.equal(badge.state, "NOT_APPLICABLE");
  assert.match(badge.detail, /No venue was contacted/);
});

test("a real provider id is shown as itself", () => {
  assert.equal(providerIdentity("UPX-1", "LIVE").state, "AVAILABLE");
  assert.equal(providerIdentity("UPX-1", "LIVE").label, "UPX-1");
});
