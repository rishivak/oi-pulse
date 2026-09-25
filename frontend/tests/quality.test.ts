/**
 * Quality, availability and the three forbidden substitutions.
 *
 * Phase 12 brief §18 names them: `missing -> 0`, `unavailable -> previous`,
 * `unreliable -> latest`. Each is tested as a property of the API rather than as a
 * behaviour of one screen, because a substitution introduced in a helper reaches
 * every screen at once.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  absent,
  emptyState,
  gateDerived,
  presentValue,
  qualityBadge,
} from "@/lib/terminal/quality";

test("a missing value becomes an explained absence, never zero", () => {
  const result = presentValue<number>(null, "NO_DATA_FOR_PERIOD");
  assert.equal(result.kind, "absent");
  if (result.kind !== "absent") return;
  assert.equal(result.reason, "NO_DATA_FOR_PERIOD");
  assert.match(result.text, /no data collected/);
  // The specific defect: nothing anywhere in the union carries a numeric default.
  assert.equal("value" in result, false);
});

test("zero is a value and is preserved as one", () => {
  const result = presentValue(0, "NO_DATA_FOR_PERIOD");
  assert.equal(result.kind, "present");
  if (result.kind !== "present") return;
  assert.equal(result.value, 0);
});

test("an absent value carries no access to a previous one", () => {
  // `presentValue` takes no prior-value argument, so `unavailable -> previous` is
  // not a policy decision anywhere -- it is unexpressible. `Function.length` counts
  // parameters before the first defaulted one, so the two required ones are the
  // value and its reason, and there is no third slot a previous value could occupy.
  assert.equal(presentValue.length, 2);
  const gap = absent("STALE");
  assert.equal(Object.hasOwn(gap, "value"), false);
});

test("an unlabelled quality status is UNKNOWN, never OK", () => {
  assert.equal(qualityBadge(undefined).status, "UNKNOWN");
  assert.equal(qualityBadge(null).status, "UNKNOWN");
  assert.equal(qualityBadge("SOMETHING_NEW").status, "UNKNOWN");
  assert.equal(qualityBadge("OK").status, "OK");
});

test("UNKNOWN and UNRELIABLE both withhold derived values", () => {
  for (const status of [undefined, "UNRELIABLE"]) {
    const badge = qualityBadge(status);
    assert.equal(badge.withholdDerived, true);
    assert.equal(badge.desaturateDerived, true);
    const gated = gateDerived(badge, 42);
    assert.equal(gated.kind, "absent");
  }
});

test("UNRELIABLE withholds even when a number arrived", () => {
  const gated = gateDerived(qualityBadge("UNRELIABLE", []), 1.23);
  assert.equal(gated.kind, "absent");
  if (gated.kind !== "absent") return;
  assert.equal(gated.reason, "STATE_UNRELIABLE");
});

test("DEGRADED shows the value and marks it, rather than hiding it", () => {
  const gated = gateDerived(qualityBadge("DEGRADED"), 1.23);
  assert.equal(gated.kind, "present");
  if (gated.kind !== "present") return;
  assert.equal(gated.value, 1.23);
  assert.equal(gated.stale, true);
});

test("every badge carries a glyph and a screen-reader label", () => {
  for (const status of ["OK", "DEGRADED", "UNRELIABLE", "UNKNOWN"]) {
    const badge = qualityBadge(status);
    assert.notEqual(badge.glyph, "");
    assert.notEqual(badge.srLabel, "");
  }
});

test("issues reach the badge so the header can expand to them", () => {
  const badge = qualityBadge("DEGRADED", [{ code: "GAP", message: "coverage 0.4" }]);
  assert.equal(badge.issues.length, 1);
});

test("the empty states are distinct and none of them is a zero", () => {
  const kinds = [
    "NO_DATA_FOR_PERIOD",
    "INSUFFICIENT_HISTORY",
    "QUALITY_REQUIREMENTS_UNMET",
    "NOT_YET_AVAILABLE",
    "NO_MATCHING_FILTER",
  ] as const;
  const titles = new Set(kinds.map((k) => emptyState(k).title));
  assert.equal(titles.size, kinds.length);
  for (const kind of kinds) {
    const state = emptyState(kind);
    assert.notEqual(state.title, "");
    assert.notEqual(state.detail, "");
    assert.doesNotMatch(state.title, /^0$/);
  }
});

test("no data for a period says so rather than implying a zero reading", () => {
  assert.match(emptyState("NO_DATA_FOR_PERIOD").detail, /not a zero/);
});
