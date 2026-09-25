/**
 * Chart data mapping. `13-FRONTEND_IA.md` §8, remediation brief §9 and §10.
 *
 * A chart is where `missing -> 0` does the most damage, because a line drawn
 * through a gap does not look like missing data — it looks like a measurement.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { absent, present, qualityBadge } from "@/lib/terminal/quality";
import {
  chartState,
  toSeriesPoints,
  tooltipReading,
} from "@/lib/terminal/screens/chartData";

const at = (iso: string, value: string | null) => ({
  marketTime: iso,
  value: value === null ? absent("NOT_COMPUTED") : present(value),
});

test("an absent reading becomes whitespace, so the line breaks rather than bridging", () => {
  const series = toSeriesPoints([
    at("2026-03-03T06:00:00Z", "10"),
    at("2026-03-03T06:01:00Z", null),
    at("2026-03-03T06:02:00Z", "12"),
  ]);
  assert.equal(series.points.length, 3);
  assert.equal(series.points[1].value, undefined);
  assert.equal(series.gaps, 1);
  // Never zero, and never the neighbouring value.
  assert.notEqual(series.points[1].value, 0);
});

test("the gap count is surfaced, so a continuous-looking line is qualified", () => {
  const series = toSeriesPoints([at("2026-03-03T06:00:00Z", null)]);
  assert.match(series.gapNotice ?? "", /Nothing is interpolated across a gap/);
});

test("a complete series carries no notice", () => {
  assert.equal(toSeriesPoints([at("2026-03-03T06:00:00Z", "1")]).gapNotice, null);
});

test("points are strictly ascending, because the library throws otherwise", () => {
  const series = toSeriesPoints([
    at("2026-03-03T06:02:00Z", "12"),
    at("2026-03-03T06:00:00Z", "10"),
    at("2026-03-03T06:01:00Z", "11"),
  ]);
  const times = series.points.map((p) => p.time);
  assert.deepEqual(times, [...times].sort((a, b) => a - b));
  assert.equal(new Set(times).size, times.length);
});

test("a duplicate instant keeps the last reading rather than emitting two points", () => {
  const series = toSeriesPoints([
    at("2026-03-03T06:00:00Z", "10"),
    at("2026-03-03T06:00:00Z", "11"),
  ]);
  assert.equal(series.points.length, 1);
  assert.equal(series.points[0].value, 11);
});

test("a row with no market time is dropped and counted, not plotted at zero", () => {
  const series = toSeriesPoints([
    { marketTime: null, value: present("10") },
    at("2026-03-03T06:00:00Z", "11"),
  ]);
  assert.equal(series.points.length, 1);
  assert.equal(series.undated, 1);
  assert.match(series.gapNotice ?? "", /carried no market time/);
});

test("a non-numeric value becomes whitespace rather than NaN", () => {
  const series = toSeriesPoints([
    { marketTime: "2026-03-03T06:00:00Z", value: present("not a number") },
  ]);
  assert.equal(series.points[0].value, undefined);
});

test("an unreliable state withholds the plot entirely", () => {
  const series = toSeriesPoints([at("2026-03-03T06:00:00Z", "10")]);
  const state = chartState(qualityBadge("UNRELIABLE"), series);
  assert.equal(state.availability, "WITHHELD");
  assert.match(state.detail, /still reads as a trend/);
});

test("an unknown state withholds too", () => {
  const series = toSeriesPoints([at("2026-03-03T06:00:00Z", "10")]);
  assert.equal(chartState(qualityBadge(undefined), series).availability, "WITHHELD");
});

test("an empty series is not a flat line", () => {
  const state = chartState(qualityBadge("OK"), toSeriesPoints([]));
  assert.equal(state.availability, "EMPTY");
  assert.match(state.detail, /not a flat line/);
});

test("a healthy series with points is plottable", () => {
  const series = toSeriesPoints([at("2026-03-03T06:00:00Z", "10")]);
  assert.equal(chartState(qualityBadge("OK"), series).availability, "PLOTTABLE");
});

test("the tooltip names both axes, never a bare timestamp", () => {
  const reading = tooltipReading(
    { time: Math.floor(Date.parse("2026-03-03T06:00:00Z") / 1000), value: 14.8 },
    "2026-03-05T09:00:00Z",
    "%",
  );
  assert.equal(reading.marketTime, "2026-03-03T06:00:00.000Z");
  assert.equal(reading.knowledgeTime, "2026-03-05T09:00:00Z");
  assert.equal(reading.value, "14.8");
  assert.equal(reading.unit, "%");
});

test("a tooltip on a gap says there is no value there", () => {
  const reading = tooltipReading({ time: 1_772_000_000 }, null, "%");
  assert.equal(reading.value, "no value at this point");
  assert.equal(reading.knowledgeTime, "latest");
});
