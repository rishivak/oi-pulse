"use client";

import { useMemo } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/common/DataTable";
import {
  deltaArrow,
  deltaColorClass,
  fmtIV,
  fmtLakh,
  fmtPrice,
  oiSignalColorClass,
  oiSignalLabel,
} from "@/lib/utils/formatters";
import { cn } from "@/lib/utils";
import type { StrikeSnapshot } from "@/lib/types";

interface OptionChainTableProps {
  strikes: StrikeSnapshot[];
  spotPrice: number | null;
  loading?: boolean;
}

function isATM(strike: number, spot: number | null): boolean {
  if (spot == null) return false;
  // ATM = closest to spot among displayed strikes (simplified: within 0.5% of spot)
  return Math.abs(strike - spot) / spot < 0.005;
}

export function OptionChainTable({ strikes, spotPrice, loading }: OptionChainTableProps) {
  const columns = useMemo<ColumnDef<StrikeSnapshot>[]>(
    () => [
      // ── Call side ────────────────────────────────────────────────────────────
      {
        accessorKey: "call_oi",
        header: "CE OI",
        cell: ({ getValue }) => (
          <span className="tabular-nums text-bull/90">{fmtLakh(getValue<number | null>())}</span>
        ),
        size: 80,
      },
      {
        accessorKey: "call_oi_change",
        header: "CE ΔOI",
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return (
            <span className={cn("tabular-nums font-medium", deltaColorClass(v))}>
              {deltaArrow(v)} {v != null ? fmtLakh(Math.abs(v)) : "—"}
            </span>
          );
        },
        size: 80,
      },
      {
        accessorKey: "call_ltp",
        header: "CE LTP",
        cell: ({ getValue }) => (
          <span className="tabular-nums">{fmtPrice(getValue<number | null>())}</span>
        ),
        size: 80,
      },
      {
        accessorKey: "call_iv",
        header: "CE IV",
        cell: ({ getValue }) => (
          <span className="tabular-nums text-terminal-muted">{fmtIV(getValue<number | null>())}</span>
        ),
        size: 60,
      },
      // ── Strike ────────────────────────────────────────────────────────────────
      {
        accessorKey: "strike",
        header: "Strike",
        cell: ({ getValue, row }) => {
          const strike = getValue<number>();
          const atm = isATM(strike, spotPrice);
          return (
            <span
              className={cn(
                "tabular-nums font-bold text-sm",
                atm
                  ? "rounded bg-accent px-1.5 py-0.5 text-white"
                  : "text-terminal-text",
              )}
              title={atm ? "At-the-Money" : undefined}
            >
              {strike.toLocaleString("en-IN")}
              {atm && <span className="sr-only"> (ATM)</span>}
            </span>
          );
        },
        size: 80,
        enableSorting: true,
      },
      // ── Put side ──────────────────────────────────────────────────────────────
      {
        accessorKey: "put_iv",
        header: "PE IV",
        cell: ({ getValue }) => (
          <span className="tabular-nums text-terminal-muted">{fmtIV(getValue<number | null>())}</span>
        ),
        size: 60,
      },
      {
        accessorKey: "put_ltp",
        header: "PE LTP",
        cell: ({ getValue }) => (
          <span className="tabular-nums">{fmtPrice(getValue<number | null>())}</span>
        ),
        size: 80,
      },
      {
        accessorKey: "put_oi_change",
        header: "PE ΔOI",
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return (
            <span className={cn("tabular-nums font-medium", deltaColorClass(v))}>
              {deltaArrow(v)} {v != null ? fmtLakh(Math.abs(v)) : "—"}
            </span>
          );
        },
        size: 80,
      },
      {
        accessorKey: "put_oi",
        header: "PE OI",
        cell: ({ getValue }) => (
          <span className="tabular-nums text-bear/90">{fmtLakh(getValue<number | null>())}</span>
        ),
        size: 80,
      },
      // ── Signal ────────────────────────────────────────────────────────────────
      {
        id: "pcr",
        header: "PCR",
        accessorFn: (row) =>
          row.call_oi && row.put_oi ? (row.put_oi / row.call_oi).toFixed(2) : null,
        cell: ({ getValue }) => (
          <span className="tabular-nums text-terminal-muted">{getValue<string | null>() ?? "—"}</span>
        ),
        size: 60,
        enableSorting: false,
      },
    ],
    [spotPrice],
  );

  const sortedStrikes = useMemo(
    () => [...strikes].sort((a, b) => a.strike - b.strike),
    [strikes],
  );

  return (
    <DataTable
      data={sortedStrikes}
      columns={columns}
      loading={loading}
      caption={`Option chain${spotPrice ? ` — Spot: ${fmtPrice(spotPrice)}` : ""}`}
      emptyMessage="No strike data available. Select an underlying and expiry."
    />
  );
}
