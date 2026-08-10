"use client";

import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DashboardHeader } from "@/components/layout/DashboardHeader";
import { TrendingOITable } from "@/components/trending-oi/TrendingOITable";
import { IntervalSelector, type Interval } from "@/components/common/IntervalSelector";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSSE } from "@/lib/realtime/sse-client";
import { oiApi } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { SnapshotCreatedPayload, SSEMessage } from "@/lib/types";

export default function TrendingOIPage() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [interval, setInterval] = useState<Interval>(5);

  const qc = useQueryClient();

  const { data: expiries = [] } = useQuery({
    queryKey: queryKeys.expiries(underlying),
    queryFn: () => oiApi.expiries(underlying),
  });
  const selectedExpiry = expiries[0] ?? null;

  const { data: rows = [], isFetching } = useQuery({
    queryKey: queryKeys.trending(underlying, selectedExpiry ?? "", interval),
    queryFn: () => oiApi.trending(underlying, selectedExpiry!, interval),
    enabled: !!selectedExpiry,
  });

  useSSE<SnapshotCreatedPayload>(
    "snapshot_created",
    useCallback(
      (msg: SSEMessage<SnapshotCreatedPayload>) => {
        if (msg.payload.underlying === underlying && msg.payload.interval_min === interval) {
          qc.invalidateQueries({ queryKey: queryKeys.trending(underlying, selectedExpiry ?? "", interval) });
        }
      },
      [underlying, interval, selectedExpiry, qc],
    ),
  );

  const lastRow = rows[rows.length - 1] ?? null;

  return (
    <div className="flex flex-col gap-0">
      <DashboardHeader
        underlying={underlying}
        spotPrice={lastRow?.spot_price ?? null}
        selectedExpiry={selectedExpiry}
        intervalMin={interval}
        lastUpdateTs={lastRow?.bucket_ts ?? null}
      />

      <div className="flex items-center gap-3 border-b border-terminal-border px-4 py-2">
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

        <Select value={selectedExpiry ?? ""} disabled={!selectedExpiry}>
          <SelectTrigger className="w-36" aria-label="Select expiry">
            <SelectValue placeholder="Loading expiries…" />
          </SelectTrigger>
          <SelectContent>
            {expiries.map((e) => (
              <SelectItem key={e} value={e}>{e}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <IntervalSelector value={interval} onChange={setInterval} />

        <span className="ml-auto text-xs text-terminal-muted">
          {rows.length} snapshots
        </span>
      </div>

      <div className="p-4">
        <TrendingOITable rows={rows} loading={isFetching} />
      </div>
    </div>
  );
}
