"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DashboardHeader } from "@/components/layout/DashboardHeader";
import { oiApi } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { TimeframeValue } from "@/lib/types";
import { deltaArrow, deltaColorClass, fmtDateTime, fmtLakh, fmtPCR, fmtPrice } from "@/lib/utils/formatters";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const TIMEFRAMES: TimeframeValue[] = ["1m", "5m", "15m", "30m", "1h"];

export default function OIHistoryPage() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [timeframe, setTimeframe] = useState<TimeframeValue>("30m");

  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [forDate, setForDate] = useState(today);

  const { data: expiries = [] } = useQuery({
    queryKey: queryKeys.expiries(underlying),
    queryFn: () => oiApi.expiries(underlying),
  });

  const selectedExpiry = expiries[0] ?? "";

  const { data: rows = [], isFetching } = useQuery({
    queryKey: queryKeys.historyBars(underlying, selectedExpiry, timeframe, forDate),
    queryFn: () => oiApi.historyBars(underlying, selectedExpiry, timeframe, forDate),
    enabled: !!selectedExpiry,
  });

  const latest = rows[rows.length - 1] ?? null;

  return (
    <div className="flex flex-col gap-0">
      <DashboardHeader
        underlying={underlying}
        spotPrice={latest?.close_ltp ?? null}
        selectedExpiry={selectedExpiry || null}
        intervalMin={timeframe === "1h" ? 60 : Number(timeframe.replace("m", ""))}
        lastUpdateTs={latest?.bucket_end ?? null}
      />

      <div className="flex flex-wrap items-center gap-3 border-b border-terminal-border px-4 py-2">
        <span className="text-xs font-medium text-terminal-muted">Mode: Historical</span>

        <Select value={underlying} onValueChange={setUnderlying}>
          <SelectTrigger className="w-32" aria-label="Select underlying">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["NIFTY", "BANKNIFTY", "SENSEX"].map((u) => (
              <SelectItem key={u} value={u}>{u}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={selectedExpiry} disabled={!selectedExpiry}>
          <SelectTrigger className="w-40" aria-label="Select expiry">
            <SelectValue placeholder="Loading expiries..." />
          </SelectTrigger>
          <SelectContent>
            {expiries.map((e) => (
              <SelectItem key={e} value={e}>{e}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={timeframe} onValueChange={(v) => setTimeframe(v as TimeframeValue)}>
          <SelectTrigger className="w-28" aria-label="Select timeframe">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TIMEFRAMES.map((tf) => (
              <SelectItem key={tf} value={tf}>{tf}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <input
          type="date"
          className="h-9 rounded-md border border-terminal-border bg-terminal-surface px-2 text-sm text-terminal-text"
          value={forDate}
          onChange={(e) => setForDate(e.target.value)}
          aria-label="Select historical date"
        />

        <span className="ml-auto text-xs text-terminal-muted">
          {isFetching ? "Loading..." : `${rows.length} buckets`}
        </span>
      </div>

      <div className="overflow-auto p-4">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-terminal-border text-left text-terminal-muted">
              <th className="px-2 py-2 font-medium">Time</th>
              <th className="px-2 py-2 font-medium">LTP</th>
              <th className="px-2 py-2 font-medium">LTP Change</th>
              <th className="px-2 py-2 font-medium">OI</th>
              <th className="px-2 py-2 font-medium">OI Change</th>
              <th className="px-2 py-2 font-medium">PCR</th>
              <th className="px-2 py-2 font-medium">Interpretation</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.bucket_start} className="border-b border-terminal-border/50">
                <td className="px-2 py-2 text-terminal-muted">
                  {fmtDateTime(row.bucket_start)} - {fmtDateTime(row.bucket_end)}
                </td>
                <td className="px-2 py-2 tabular-nums">{fmtPrice(row.close_ltp)}</td>
                <td className={cn("px-2 py-2 tabular-nums", deltaColorClass(row.ltp_change))}>
                  {deltaArrow(row.ltp_change)} {row.ltp_change != null ? fmtPrice(Math.abs(row.ltp_change)) : "-"}
                </td>
                <td className="px-2 py-2 tabular-nums">{fmtLakh(row.close_oi)}</td>
                <td className={cn("px-2 py-2 tabular-nums", deltaColorClass(row.oi_change))}>
                  {deltaArrow(row.oi_change)} {row.oi_change != null ? fmtLakh(Math.abs(row.oi_change)) : "-"}
                </td>
                <td className="px-2 py-2 tabular-nums">{fmtPCR(row.pcr)}</td>
                <td className="px-2 py-2">{row.interpretation}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-2 py-8 text-center text-terminal-muted">
                  No historical buckets available for this selection.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
