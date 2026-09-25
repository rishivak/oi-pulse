/**
 * Display formatting. `13-FRONTEND_IA.md` §7 (times) and Phase 12 brief §27 (units).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  IST_OFFSET_MINUTES,
  MATERIAL_INGESTION_LAG_MS,
  observationTiming,
  renderInstant,
  renderQuantity,
  renderSigned,
} from "@/lib/terminal/formatting";

test("instants render in IST with the zone always labelled", () => {
  const rendered = renderInstant("2026-03-03T06:12:15Z");
  assert.equal(rendered?.date, "2026-03-03");
  assert.equal(rendered?.time, "11:42:15");
  assert.equal(rendered?.zone, "IST");
  assert.equal(rendered?.text, "2026-03-03 11:42:15 IST");
});

test("UTC is available beside the IST rendering", () => {
  assert.equal(renderInstant("2026-03-03T06:12:15Z")?.utc, "2026-03-03 06:12:15 UTC");
});

test("the offset is the fixed +05:30 IST has always used", () => {
  assert.equal(IST_OFFSET_MINUTES, 330);
});

test("the IST date rolls over correctly across midnight UTC", () => {
  assert.equal(renderInstant("2026-03-02T20:00:00Z")?.date, "2026-03-03");
});

test("an unparseable instant returns null rather than inventing a dash", () => {
  assert.equal(renderInstant("not a time"), null);
  assert.equal(renderInstant(null), null);
  assert.equal(renderInstant(""), null);
});

test("material ingestion lag requires both timestamps to be shown", () => {
  const timing = observationTiming("2026-03-03T06:12:15Z", "2026-03-03T06:12:19Z");
  assert.equal(timing.lagMs, 4000);
  assert.equal(timing.showBoth, true);
});

test("immaterial lag does not force a second timestamp into a dense table", () => {
  const timing = observationTiming("2026-03-03T06:12:15Z", "2026-03-03T06:12:15.200Z");
  assert.equal(timing.showBoth, false);
  assert.ok(Math.abs(timing.lagMs ?? 0) < MATERIAL_INGESTION_LAG_MS);
});

test("lag is null, not zero, when one timestamp is missing", () => {
  assert.equal(observationTiming("2026-03-03T06:12:15Z", null).lagMs, null);
});

test("quantities carry an explicit unit", () => {
  assert.equal(renderQuantity("25120.4", "INR", 2), "25120.40 INR");
  assert.equal(renderQuantity(14.8, "%", 1), "14.8 %");
  assert.equal(renderQuantity(5, ""), "5");
});

test("a null quantity renders as null, for the caller to explain", () => {
  assert.equal(renderQuantity(null, "INR"), null);
  assert.equal(renderQuantity("", "INR"), null);
  assert.equal(renderQuantity("abc", "INR"), null);
});

test("no rounding is applied unless the caller states the precision", () => {
  assert.equal(renderQuantity(1.23456, ""), "1.23456");
});

test("signs are shown, never implied", () => {
  assert.equal(renderSigned(1250, "INR"), "+1250.00 INR");
  assert.equal(renderSigned(-1250, "INR"), "−1250.00 INR");
  assert.equal(renderSigned(0, "INR"), "0.00 INR");
});
