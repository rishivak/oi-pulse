"use client";

import { useSSEStatus } from "@/lib/realtime/sse-client";
import { fmtDateTime, fmtPrice } from "@/lib/utils/formatters";
import { cn } from "@/lib/utils";
import { Wifi, WifiOff, Loader2 } from "lucide-react";

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
  const label =
    status === "connected"
      ? "Live"
      : status === "connecting"
        ? "Connecting"
        : status === "paused"
          ? "No Live Feed"
          : "Disconnected";

  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium",
        status === "connected" && "bg-bull-dim text-bull",
        status === "connecting" && "bg-terminal-border text-terminal-muted",
        status === "paused" && "bg-terminal-border text-terminal-text",
        status === "error" || status === "disconnected"
          ? "bg-bear-dim text-bear"
          : "",
      )}
      role="status"
      aria-live="polite"
    >
      {status === "connected" && (
        <Wifi className="h-3 w-3 status-connected" aria-hidden />
      )}
      {status === "connecting" && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
      {status === "paused" && <WifiOff className="h-3 w-3" aria-hidden />}
      {(status === "error" || status === "disconnected") && (
        <WifiOff className="h-3 w-3" aria-hidden />
      )}
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
