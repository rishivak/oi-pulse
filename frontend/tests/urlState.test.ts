/**
 * Shared view state in the URL. `13-FRONTEND_IA.md` §4 and §8.
 *
 * The named defect: "The legacy inert `expiries[0]` selector is the specific defect
 * being designed out." A selector is inert when changing it does not change the
 * request; these tests assert the round trip that makes inertness impossible.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { IncoherentTimeAxes } from "@/lib/terminal/time";
import {
  EMPTY_VIEW_STATE,
  linkTo,
  parseViewState,
  serialiseViewState,
  timeQuery,
} from "@/lib/terminal/urlState";

test("the expiry selection round-trips through the URL", () => {
  const state = { ...EMPTY_VIEW_STATE, underlyingId: 1, expiry: "2026-03-26" };
  assert.equal(parseViewState(serialiseViewState(state)).expiry, "2026-03-26");
});

test("a changed expiry changes the outgoing query", () => {
  const front = parseViewState("?underlying_id=1&expiry=front");
  const next = parseViewState("?underlying_id=1&expiry=next");
  assert.notEqual(serialiseViewState(front), serialiseViewState(next));
});

test("both axes round-trip", () => {
  const state = parseViewState(
    "?market_time=2026-03-03T11:42:00Z&knowledge_time=2026-03-05T09:00:00Z",
  );
  assert.equal(state.axes.marketTime, "2026-03-03T11:42:00Z");
  assert.equal(state.axes.knowledgeTime, "2026-03-05T09:00:00Z");
  assert.deepEqual(parseViewState(serialiseViewState(state)).axes, state.axes);
});

test("an incoherent pair in the URL is refused rather than silently corrected", () => {
  assert.throws(
    () => parseViewState("?market_time=2026-03-03T11:42:00Z&knowledge_time=2026-03-01T09:00:00Z"),
    IncoherentTimeAxes,
  );
});

test("an unparseable underlying becomes null, never zero", () => {
  assert.equal(parseViewState("?underlying_id=NIFTY").underlyingId, null);
  assert.equal(parseViewState("?underlying_id=").underlyingId, null);
  assert.equal(parseViewState("?underlying_id=1").underlyingId, 1);
});

test("the live URL is short; a pinned one is visibly different", () => {
  assert.equal(serialiseViewState(EMPTY_VIEW_STATE), "");
  assert.notEqual(
    serialiseViewState({ ...EMPTY_VIEW_STATE, axes: { marketTime: "T", knowledgeTime: null } }),
    "",
  );
});

test("knowledge_time is sent only when the user pinned one", () => {
  assert.deepEqual(
    timeQuery({ ...EMPTY_VIEW_STATE, axes: { marketTime: "T", knowledgeTime: null } }),
    { market_time: "T" },
  );
  assert.deepEqual(
    timeQuery({ ...EMPTY_VIEW_STATE, axes: { marketTime: "T", knowledgeTime: "K" } }),
    { market_time: "T", knowledge_time: "K" },
  );
});

test("navigation carries the shared state rather than resetting it", () => {
  const state = { ...EMPTY_VIEW_STATE, underlyingId: 1, expiry: "front" };
  assert.equal(linkTo("/terminal/signals", state), "/terminal/signals?underlying_id=1&expiry=front");
});
