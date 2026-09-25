/**
 * Turning a backend series into chart points, without losing what is missing.
 *
 * `13-FRONTEND_IA.md` §8 specifies the charting technology: "Charts via a single
 * library (`lightweight-charts` for time series where it fits...)". This module is
 * the part of that which can be tested without the library: the mapping from a
 * backend series onto the `{ time, value }` shape it expects, and the decisions
 * about absent, stale and unreliable data that must not be delegated to a chart.
 *
 * ### A gap must stay a gap
 *
 * The brief's §18 prohibition — `missing → 0` — is at its most dangerous on a
 * chart, because a line drawn through a missing point does not look like missing
 * data. It looks like a measurement.
 *
 * `lightweight-charts` has the right primitive: a point with a `time` and no
 * `value` is *whitespace*, and the series breaks rather than interpolating across
 * it. {@link toSeriesPoints} emits whitespace for every absent reading and counts
 * them, so the caption can say how many points are missing from a line the eye
 * reads as continuous.
 *
 * ### The axis is market time, and only market time
 *
 * Brief §10: chart axes and tooltips must not hide `market_time` and
 * `knowledge_time`. A time axis can only carry one of them, so it carries market
 * time — the instant the value describes — and knowledge time travels beside the
 * chart and in every tooltip. Folding the two into one "timestamp" is precisely
 * what `13` §1.6 and §4 exist to prevent.
 */

import type { Presented } from "@/lib/terminal/quality";
import type { QualityBadge } from "@/lib/terminal/quality";

/** `lightweight-charts` UTCTimestamp: whole seconds since the epoch. */
export type UtcSeconds = number;

/** A plotted point, or whitespace when `value` is absent. */
export interface ChartPoint {
  readonly time: UtcSeconds;
  readonly value?: number;
}

export interface SeriesInput {
  readonly marketTime: string | null;
  readonly value: Presented<string>;
}

export interface ChartSeries {
  readonly points: readonly ChartPoint[];
  /** Points present on the axis with no value. The line breaks at each. */
  readonly gaps: number;
  /** Rows dropped because they carried no market time at all. */
  readonly undated: number;
  readonly firstMarketTime: string | null;
  readonly lastMarketTime: string | null;
  /** Non-null when the eye would read the line as more complete than it is. */
  readonly gapNotice: string | null;
}

function toSeconds(iso: string): UtcSeconds | null {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  return Math.floor(ms / 1000);
}

/**
 * Map a backend series onto chart points.
 *
 * Points are sorted by time and de-duplicated, keeping the last reading for an
 * instant: `lightweight-charts` requires strictly ascending times and throws
 * otherwise, and a throw inside a chart takes out the route rather than the panel.
 */
export function toSeriesPoints(rows: readonly SeriesInput[]): ChartSeries {
  const byTime = new Map<UtcSeconds, ChartPoint>();
  let gaps = 0;
  let undated = 0;
  let first: string | null = null;
  let last: string | null = null;

  for (const row of rows) {
    if (row.marketTime === null) {
      undated += 1;
      continue;
    }
    const time = toSeconds(row.marketTime);
    if (time === null) {
      undated += 1;
      continue;
    }
    if (first === null || row.marketTime < first) first = row.marketTime;
    if (last === null || row.marketTime > last) last = row.marketTime;

    if (row.value.kind === "absent") {
      // Whitespace: the point exists on the axis and the line breaks at it.
      byTime.set(time, { time });
      continue;
    }
    const numeric = Number(row.value.value);
    if (!Number.isFinite(numeric)) {
      byTime.set(time, { time });
      continue;
    }
    byTime.set(time, { time, value: numeric });
  }

  const points = [...byTime.values()].sort((a, b) => a.time - b.time);
  gaps = points.filter((point) => point.value === undefined).length;

  return {
    points,
    gaps,
    undated,
    firstMarketTime: first,
    lastMarketTime: last,
    gapNotice:
      gaps === 0 && undated === 0
        ? null
        : `${gaps} point${gaps === 1 ? "" : "s"} have no value and the line breaks at ` +
          `each; ${undated} row${undated === 1 ? "" : "s"} carried no market time and ` +
          `are not plotted. Nothing is interpolated across a gap.`,
  };
}

export type ChartAvailability = "PLOTTABLE" | "WITHHELD" | "EMPTY";

export interface ChartState {
  readonly availability: ChartAvailability;
  readonly title: string;
  readonly detail: string;
}

/**
 * Whether the chart may be drawn at all.
 *
 * An `UNRELIABLE` or `UNKNOWN` state withholds the plot rather than desaturating
 * it. `13` §7 asks for desaturation of derived panels; a chart is worse than a
 * number in this respect, because a faint line is still a shape and a shape is
 * still read as a trend.
 */
export function chartState(badge: QualityBadge, series: ChartSeries): ChartState {
  if (badge.withholdDerived) {
    return {
      availability: "WITHHELD",
      title: `State is ${badge.label} — chart withheld`,
      detail:
        "A line drawn from unreliable values still reads as a trend, so it is not " +
        "drawn. The underlying values remain available in tabular form.",
    };
  }
  if (series.points.length === 0) {
    return {
      availability: "EMPTY",
      title: "No points in this period",
      detail: "Nothing was observed in the requested window. This is not a flat line.",
    };
  }
  return { availability: "PLOTTABLE", title: "", detail: "" };
}

export interface TooltipReading {
  /** Always labelled. Never rendered as a bare timestamp (brief §10). */
  readonly marketTime: string;
  readonly knowledgeTime: string;
  readonly value: string;
  readonly unit: string;
}

/**
 * The tooltip, with both axes named.
 *
 * `knowledgeTime` is the horizon the whole series was fetched at, not a per-point
 * value: the API answers one `(market_time, knowledge_time)` question per request,
 * so every point on the line shares the horizon. Showing it per point makes that
 * explicit at the moment someone reads a value off the chart.
 */
export function tooltipReading(
  point: ChartPoint,
  knowledgeTime: string | null,
  unit: string,
): TooltipReading {
  return {
    marketTime: new Date(point.time * 1000).toISOString(),
    knowledgeTime: knowledgeTime ?? "latest",
    value: point.value === undefined ? "no value at this point" : String(point.value),
    unit,
  };
}
