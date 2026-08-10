"use client";

import { useMemo } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/common/DataTable";
import {
  deltaArrow,
  deltaColorClass,
  fmtDateTime,
  fmtLakh,
  fmtPCR,
  fmtPrice,
} from "@/lib/utils/formatters";
import { cn } from "@/lib/utils";
import type { TrendingOIRow } from "@/lib/types";

interface TrendingOITableProps {
  rows: TrendingOIRow[];
  loading?: boolean;
}

export function TrendingOITable({ rows, loading }: TrendingOITableProps) {
  const columns = useMemo<ColumnDef<TrendingOIRow>[]>(
    () => [
      {
        accessorKey: "bucket_ts",
        header: "Time",
        cell: ({ getValue }) => (
          <span className="tabular-nums text-terminal-muted">
            {fmtDateTime(getValue<string>())}
          </span>
        ),
        size: 100,
      },
      {
        accessorKey: "spot_price",
        header: "LTP",
        cell: ({ getValue }) => (
          <span className="tabular-nums font-medium">{fmtPrice(getValue<number | null>())}</span>
        ),
        size: 90,
      },
      {
        accessorKey: "total_call_oi",
        header: "Call OI",
        cell: ({ getValue }) => (
          <span className="tabular-nums">{fmtLakh(getValue<number | null>())}</span>
        ),
        size: 90,
      },
      {
        accessorKey: "total_put_oi",
        header: "Put OI",
        cell: ({ getValue }) => (
          <span className="tabular-nums">{fmtLakh(getValue<number | null>())}</span>
        ),
        size: 90,
      },
      {
        accessorKey: "call_oi_change",
        header: "ΔCall OI",
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return (
            <span className={cn("tabular-nums font-medium", deltaColorClass(v))}>
              {deltaArrow(v)} {v != null ? fmtLakh(Math.abs(v)) : "—"}
            </span>
          );
        },
        size: 90,
      },
      {
        accessorKey: "put_oi_change",
        header: "ΔPut OI",
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return (
            <span className={cn("tabular-nums font-medium", deltaColorClass(v))}>
              {deltaArrow(v)} {v != null ? fmtLakh(Math.abs(v)) : "—"}
            </span>
          );
        },
        size: 90,
      },
      {
        id: "net_oi_change",
        header: "Net ΔOI",
        accessorFn: (row) =>
          row.call_oi_change != null && row.put_oi_change != null
            ? row.call_oi_change + row.put_oi_change
            : null,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return (
            <span className={cn("tabular-nums font-semibold", deltaColorClass(v))}>
              {deltaArrow(v)} {v != null ? fmtLakh(Math.abs(v)) : "—"}
            </span>
          );
        },
        size: 90,
      },
      {
        accessorKey: "pcr",
        header: "PCR",
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          const color =
            v == null
              ? "text-terminal-muted"
              : v > 1.2
              ? "text-bull"
              : v < 0.8
              ? "text-bear"
              : "text-terminal-text";
          return <span className={cn("tabular-nums font-medium", color)}>{fmtPCR(v)}</span>;
        },
        size: 70,
      },
      {
        id: "sentiment",
        header: "Sentiment",
        accessorFn: (row) => row.pcr,
        cell: ({ getValue }) => {
          const pcr = getValue<number | null>();
          if (pcr == null) return <span className="text-terminal-muted">—</span>;
          if (pcr > 1.2)
            return (
              <span className="flex items-center gap-1 text-bear">
                ▼ Bearish
              </span>
            );
          if (pcr < 0.8)
            return (
              <span className="flex items-center gap-1 text-bull">
                ▲ Bullish
              </span>
            );
          return <span className="text-terminal-muted">→ Neutral</span>;
        },
        size: 90,
        enableSorting: false,
      },
    ],
    [],
  );

  return (
    <DataTable
      data={rows}
      columns={columns}
      loading={loading}
      caption="Trending Open Interest by time bucket"
      emptyMessage="No snapshots collected yet. Start the collector to see data."
    />
  );
}
