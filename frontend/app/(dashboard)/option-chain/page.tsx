"use client";

import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DashboardHeader } from "@/components/layout/DashboardHeader";
import { OptionChainTable } from "@/components/option-chain/OptionChainTable";
import { IntervalSelector, type Interval } from "@/components/common/IntervalSelector";
import { StrikeRangeSelector } from "@/components/common/StrikeRangeSelector";
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

export default function OptionChainPage() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [interval, setInterval] = useState<Interval>(5);
  const [atmRange, setAtmRange] = useState(10);

  const qc = useQueryClient();

  const { data: expiries = [] } = useQuery({
    queryKey: queryKeys.expiries(underlying),
    queryFn: () => oiApi.expiries(underlying),
  });
  const selectedExpiry = expiries[0] ?? null;

  const { data: strikesData, isFetching } = useQuery({
    queryKey: queryKeys.strikes(underlying, selectedExpiry ?? "", interval, atmRange),
    queryFn: () => oiApi.strikes(underlying, selectedExpiry!, interval, atmRange),
    enabled: !!selectedExpiry,
  });

  useSSE<SnapshotCreatedPayload>(
    "snapshot_created",
    useCallback(
      (msg: SSEMessage<SnapshotCreatedPayload>) => {
        if (msg.payload.underlying === underlying && msg.payload.interval_min === interval) {
          qc.invalidateQueries({
            queryKey: queryKeys.strikes(underlying, selectedExpiry ?? "", interval, atmRange),
          });
        }
      },
      [underlying, interval, selectedExpiry, atmRange, qc],
    ),
  );

  return (
    <div className="flex flex-col gap-0">
      <DashboardHeader
        underlying={underlying}
        spotPrice={strikesData?.spot_price ?? null}
        selectedExpiry={selectedExpiry}
        intervalMin={interval}
        lastUpdateTs={strikesData?.bucket_ts ?? null}
      />

      <div className="flex flex-wrap items-center gap-3 border-b border-terminal-border px-4 py-2">
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
        <StrikeRangeSelector value={atmRange} onChange={setAtmRange} />

        <span className="ml-auto text-xs text-terminal-muted">
          {strikesData?.strikes.length ?? 0} strikes
        </span>
      </div>

      <div className="p-4">
        <OptionChainTable
          strikes={strikesData?.strikes ?? []}
          spotPrice={strikesData?.spot_price ?? null}
          loading={isFetching}
        />
      </div>
    </div>
  );
}
