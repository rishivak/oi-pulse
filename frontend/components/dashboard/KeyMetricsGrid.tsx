"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  deltaArrow,
  deltaColorClass,
  fmtLakh,
  fmtPCR,
  fmtPrice,
} from "@/lib/utils/formatters";
import { cn } from "@/lib/utils";
import type { OISnapshotSummary, SnapshotCreatedPayload } from "@/lib/types";

interface KeyMetricsGridProps {
  snapshot: OISnapshotSummary | null;
  livePayload?: SnapshotCreatedPayload | null;
}

export function KeyMetricsGrid({ snapshot, livePayload }: KeyMetricsGridProps) {
  // Prefer live SSE payload for freshest numbers, fall back to last REST snapshot
  const callOI = livePayload?.total_call_oi ?? snapshot?.total_call_oi ?? null;
  const putOI = livePayload?.total_put_oi ?? snapshot?.total_put_oi ?? null;
  const pcr = livePayload?.pcr ?? snapshot?.pcr ?? null;
  const netChange = livePayload?.net_oi_change ?? null;

  const pcrSentiment =
    pcr == null ? "neutral" : pcr > 1.2 ? "bull" : pcr < 0.8 ? "bear" : "neutral";

  const metrics = [
    {
      title: "Total Call OI",
      value: fmtLakh(callOI),
      sub: "Contracts (Lakh)",
    },
    {
      title: "Total Put OI",
      value: fmtLakh(putOI),
      sub: "Contracts (Lakh)",
    },
    {
      title: "PCR",
      value: fmtPCR(pcr),
      sub: pcr != null ? (pcr > 1 ? "Bearish tilt" : "Bullish tilt") : "—",
      badge: pcrSentiment,
    },
    {
      title: "Net OI Change",
      value: netChange != null ? `${deltaArrow(netChange)} ${fmtLakh(Math.abs(netChange))}` : "—",
      sub: "vs previous bucket",
      colorClass: deltaColorClass(netChange),
    },
  ];

  return (
    <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {metrics.map(({ title, value, sub, badge, colorClass }) => (
        <Card key={title}>
          <CardHeader>
            <CardTitle>{title}</CardTitle>
            {badge && (
              <Badge variant={badge as "bull" | "bear" | "neutral"}>
                {badge === "bull" ? "Bearish" : badge === "bear" ? "Bullish" : "Neutral"}
              </Badge>
            )}
          </CardHeader>
          <CardContent>
            <p className={cn("font-mono text-xl font-bold", colorClass ?? "text-terminal-text")}>
              {value}
            </p>
            <p className="mt-0.5 text-xs text-terminal-muted">{sub}</p>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}
