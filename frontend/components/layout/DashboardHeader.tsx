"use client";

import { useQuery } from "@tanstack/react-query";
import { useSSEStatus } from "@/lib/realtime/sse-client";
import { authApi } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { fmtDateTime, fmtPrice } from "@/lib/utils/formatters";
import { cn } from "@/lib/utils";
import { Database, Wifi, WifiOff, Loader2 } from "lucide-react";

interface DashboardHeaderProps {
  underlying: string;
  spotPrice: number | null;
  priceChange?: number | null;
  priceChangePct?: number | null;
  selectedExpiry: string | null;
  intervalMin: number;
  lastUpdateTs: string | null;
}

function ConnectionIndicator() {
  const status = useSSEStatus();
  const { data: me } = useQuery({
    queryKey: queryKeys.me,
    queryFn: authApi.me,
    staleTime: 30_000,
    retry: false,
  });

  const storedMode = me?.access_mode === "stored" || me?.live_market_access === false;

  let label: string;
  let tone: "bull" | "muted" | "stored" | "bear";

  if (storedMode) {
    label = "Stored data";
    tone = "stored";
  } else if (status === "connected") {
    label = "Live";
    tone = "bull";
  } else if (status === "connecting") {
    label = "Connecting";
    tone = "muted";
  } else if (status === "paused") {
    label = "No Live Feed";
    tone = "stored";
  } else {
    label = "Disconnected";
    tone = "bear";
  }

  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium",
        tone === "bull" && "bg-bull-dim text-bull",
        tone === "muted" && "bg-terminal-border text-terminal-muted",
        tone === "stored" && "bg-terminal-border text-terminal-text",
        tone === "bear" && "bg-bear-dim text-bear",
      )}
      role="status"
      aria-live="polite"
    >
      {tone === "bull" && <Wifi className="h-3 w-3 status-connected" aria-hidden />}
      {status === "connecting" && !storedMode && (
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      )}
      {tone === "stored" && <Database className="h-3 w-3" aria-hidden />}
      {tone === "bear" && <WifiOff className="h-3 w-3" aria-hidden />}
      {label}
    </span>
  );
}

export function DashboardHeader({
  underlying,
  spotPrice,
  priceChange,
  priceChangePct,
  selectedExpiry,
  intervalMin,
  lastUpdateTs,
}: DashboardHeaderProps) {
  const isPositive = priceChange != null && priceChange >= 0;

  return (
    <header className="flex flex-wrap items-center gap-4 border-b border-terminal-border bg-terminal-bg px-4 py-2.5">
      {/* Underlying + spot */}
      <div className="flex items-baseline gap-2">
        <span className="text-base font-bold tracking-wide text-terminal-text">{underlying}</span>
        {spotPrice != null && (
          <>
            <span className="font-mono text-lg font-semibold text-terminal-text">
              {fmtPrice(spotPrice)}
            </span>
            {priceChange != null && (
              <span
                className={cn(
                  "text-sm font-medium tabular-nums",
                  isPositive ? "text-bull" : "text-bear",
                )}
                aria-label={`${isPositive ? "Up" : "Down"} ${Math.abs(priceChange)}`}
              >
                {isPositive ? "▲" : "▼"}
                {fmtPrice(Math.abs(priceChange))}
                {priceChangePct != null && ` (${Math.abs(priceChangePct).toFixed(2)}%)`}
              </span>
            )}
          </>
        )}
      </div>

      {/* Metadata pills */}
      <div className="flex flex-wrap items-center gap-2 ml-auto">
        {selectedExpiry && (
          <MetaPill label="Expiry" value={selectedExpiry} />
        )}
        <MetaPill label="Interval" value={`${intervalMin}m`} />
        {lastUpdateTs && (
          <MetaPill label="Updated" value={fmtDateTime(lastUpdateTs)} />
        )}
        <ConnectionIndicator />
      </div>
    </header>
  );
}

function MetaPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-center gap-1 rounded bg-terminal-surface px-2 py-0.5 text-xs">
      <span className="text-terminal-muted">{label}:</span>
      <span className="font-medium text-terminal-text">{value}</span>
    </span>
  );
}
