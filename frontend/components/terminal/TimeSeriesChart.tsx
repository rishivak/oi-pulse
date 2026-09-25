"use client";

/**
 * An interactive time series. `13-FRONTEND_IA.md` §8.
 *
 * > Charts via a single library (`lightweight-charts` for time series where it
 * > fits; one additional library for surfaces and heatmaps only if genuinely
 * > required — the brief forbids adding a second charting library without a
 * > compelling reason).
 *
 * So this uses `lightweight-charts`, which is already the declared dependency, and
 * there is no second charting library anywhere in the terminal.
 *
 * ### Both axes, on a chart that can only draw one
 *
 * Brief §10: "Chart axes and tooltips must not hide `market_time`,
 * `knowledge_time`." A time axis carries one series of instants, so it carries
 * **market time** — the instant each value describes — and it is labelled as such
 * rather than as "time". **Knowledge time** is fixed for the whole series, because
 * the API answers one `(market_time, knowledge_time)` question per request, so it
 * sits in the caption and is repeated in every tooltip at the moment someone reads
 * a value off the line.
 *
 * ### Gaps are gaps
 *
 * A point with no value is emitted as whitespace and the series breaks at it.
 * Interpolating would draw a measurement that was never taken, which on a chart is
 * indistinguishable from one that was — the most consequential form of the brief's
 * `missing → 0` prohibition.
 *
 * ### Unreliable state withholds the plot
 *
 * `13` §7 desaturates derived panels on an `UNRELIABLE` state. A faint line is
 * still a shape and a shape is still read as a trend, so this withholds the chart
 * entirely and says why, leaving the values available in the table beside it.
 *
 * ### Not executed anywhere
 *
 * This component has never run. `lightweight-charts` cannot be installed in the
 * development environment — the package registry is unreachable — so the chart is
 * unverified by anything except the CI job that builds it. The data mapping it
 * depends on is in `lib/terminal/screens/chartData.ts`, which is pure and is
 * tested; the drawing is not.
 */

import { useEffect, useRef, useState } from "react";
import {
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type UTCTimestamp,
  ColorType,
  createChart,
} from "lightweight-charts";

import type { QualityBadge } from "@/lib/terminal/quality";
import {
  type ChartPoint,
  type SeriesInput,
  type TooltipReading,
  chartState,
  toSeriesPoints,
  tooltipReading,
} from "@/lib/terminal/screens/chartData";
import { EmptyStateView } from "@/components/terminal/primitives";
import { Instant } from "@/components/terminal/primitives";

const AXIS_STYLE = {
  layout: {
    background: { type: ColorType.Solid, color: "#0d1117" },
    textColor: "#8b949e",
    fontFamily: "JetBrains Mono, Fira Code, monospace",
  },
  grid: {
    vertLines: { color: "#30363d" },
    horzLines: { color: "#30363d" },
  },
  rightPriceScale: { borderColor: "#30363d" },
  timeScale: { borderColor: "#30363d", timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 as const },
} as const;

export function TimeSeriesChart({
  rows,
  quality,
  knowledgeTime,
  unit,
  label,
  height = 260,
}: {
  rows: readonly SeriesInput[];
  quality: QualityBadge;
  knowledgeTime: string | null;
  unit: string;
  label: string;
  height?: number;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Line"> | null>(null);
  const [reading, setReading] = useState<TooltipReading | null>(null);

  const mapped = toSeriesPoints(rows);
  const state = chartState(quality, mapped);
  const plottable = state.availability === "PLOTTABLE";

  useEffect(() => {
    if (!plottable || container.current === null) return undefined;

    const created = createChart(container.current, {
      ...AXIS_STYLE,
      height,
      autoSize: true,
    });
    const line = created.addLineSeries({
      color: "#3b82f6",
      lineWidth: 2,
      // Whitespace points break the line instead of being bridged.
      priceLineVisible: false,
      lastValueVisible: true,
    });
    chart.current = created;
    series.current = line;

    const onMove = (params: MouseEventParams) => {
      if (params.time === undefined) {
        setReading(null);
        return;
      }
      const point: ChartPoint = {
        time: params.time as unknown as number,
        value: (() => {
          const datum = params.seriesData.get(line);
          if (datum === undefined) return undefined;
          return "value" in datum ? (datum.value as number) : undefined;
        })(),
      };
      setReading(tooltipReading(point, knowledgeTime, unit));
    };
    created.subscribeCrosshairMove(onMove);

    return () => {
      created.unsubscribeCrosshairMove(onMove);
      created.remove();
      chart.current = null;
      series.current = null;
    };
    // `height`, `knowledgeTime` and `unit` only affect construction and the
    // tooltip closure; the data is applied in the effect below so a new page of
    // points does not tear down the chart and lose the user's zoom.
  }, [plottable, height, knowledgeTime, unit]);

  useEffect(() => {
    if (series.current === null) return;
    series.current.setData(
      mapped.points.map((point) =>
        point.value === undefined
          ? { time: point.time as UTCTimestamp }
          : { time: point.time as UTCTimestamp, value: point.value },
      ),
    );
    // Fit once on first data, then leave the range alone: refitting on every
    // refresh would undo a zoom the operator set deliberately.
    if (mapped.points.length > 0) chart.current?.timeScale().fitContent();
  }, [mapped.points]);

  if (!plottable) {
    return (
      <section aria-label={label} className="space-y-2">
        <ChartCaption knowledgeTime={knowledgeTime} unit={unit} mapped={mapped} />
        <EmptyStateView
          state={{
            kind: state.availability === "WITHHELD" ? "QUALITY_REQUIREMENTS_UNMET" : "NO_DATA_FOR_PERIOD",
            title: state.title,
            detail: state.detail,
          }}
        />
      </section>
    );
  }

  return (
    <section aria-label={label} className="space-y-2">
      <ChartCaption knowledgeTime={knowledgeTime} unit={unit} mapped={mapped} />
      <div
        ref={container}
        role="img"
        aria-label={
          `${label}: ${mapped.points.length} points from ` +
          `${mapped.firstMarketTime ?? "unknown"} to ${mapped.lastMarketTime ?? "unknown"} ` +
          `market time, at knowledge time ${knowledgeTime ?? "latest"}. ` +
          `${mapped.gaps} points have no value.`
        }
        style={{ height }}
        className="rounded border border-terminal-border"
      />
      <div aria-live="polite" className="min-h-[2.5rem] font-mono text-xs">
        {reading === null ? (
          <span className="text-terminal-muted">
            Hover or use the crosshair to read a point. Scroll to zoom, drag to pan.
          </span>
        ) : (
          <dl className="flex flex-wrap gap-x-4">
            <div>
              <dt className="inline text-terminal-muted">MARKET TIME </dt>
              <dd className="inline">
                <Instant iso={reading.marketTime} />
              </dd>
            </div>
            <div>
              <dt className="inline text-terminal-muted">KNOWLEDGE TIME </dt>
              <dd className="inline">
                {reading.knowledgeTime === "latest" ? (
                  <span className="italic text-terminal-muted">latest</span>
                ) : (
                  <Instant iso={reading.knowledgeTime} />
                )}
              </dd>
            </div>
            <div>
              <dt className="inline text-terminal-muted">VALUE </dt>
              <dd className="inline">
                {reading.value}
                {reading.unit ? ` ${reading.unit}` : ""}
              </dd>
            </div>
          </dl>
        )}
      </div>
    </section>
  );
}

function ChartCaption({
  knowledgeTime,
  unit,
  mapped,
}: {
  knowledgeTime: string | null;
  unit: string;
  mapped: ReturnType<typeof toSeriesPoints>;
}) {
  return (
    <div className="space-y-1 font-mono text-xs">
      <p className="text-terminal-muted">
        {/* The axis is named, so no reader has to assume which time it carries. */}
        HORIZONTAL AXIS: MARKET TIME (IST) · VERTICAL AXIS: {unit || "value"}
      </p>
      <p>
        <span className="text-terminal-muted">KNOWLEDGE TIME </span>
        {knowledgeTime === null ? (
          <span className="italic text-terminal-muted">latest</span>
        ) : (
          <Instant iso={knowledgeTime} />
        )}
        <span className="text-terminal-muted">
          {" "}
          — fixed for every point on this series
        </span>
      </p>
      {mapped.gapNotice ? (
        <p className="text-amber-400">
          <span aria-hidden="true">⚠ </span>
          {mapped.gapNotice}
        </p>
      ) : null}
    </div>
  );
}
