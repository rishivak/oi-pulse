"use client";

/**
 * Attribution, with the residual as a row and as a segment.
 *
 * `13-FRONTEND_IA.md` §6 and Phase 12 brief §16. The component takes its rows from
 * `attributionRows`, which appends the residual unconditionally, so there is no
 * prop that hides it and no code path on which a balanced-looking table is
 * achievable by omission.
 *
 * The bar is drawn from `chartSegments`, which includes the residual for the same
 * reason. Segment widths are proportional to the absolute amounts *as supplied* —
 * no component's value is adjusted to make the bar fill.
 */

import type { AttributionInput } from "@/lib/terminal/attribution";
import {
  attributionRows,
  chartSegments,
  reconciliationNotice,
  residualShare,
} from "@/lib/terminal/attribution";
import { DenseTable } from "@/components/terminal/primitives";

export function AttributionTable({ input }: { input: AttributionInput }) {
  const rows = attributionRows(input);
  const segments = chartSegments(input);
  const notice = reconciliationNotice(input);
  const share = residualShare(input);
  const magnitudes = segments.map((s) => Math.abs(Number(s.amount) || 0));
  const total = magnitudes.reduce((a, b) => a + b, 0);

  return (
    <section aria-label={`P&L attribution — ${input.bucket}`} className="space-y-3">
      <header className="flex flex-wrap items-baseline gap-3 font-mono text-xs">
        <span className="text-terminal-muted">METHOD</span>
        <span>
          {input.method}@v{input.methodVersion}
        </span>
        <span
          className={notice.reconciles ? "text-bull" : "text-bear"}
          title={notice.detail}
        >
          <span aria-hidden="true">{notice.glyph} </span>
          {notice.label}
        </span>
      </header>

      {!notice.reconciles ? (
        <p className="rounded border border-bear bg-bear-dim p-2 text-xs" role="alert">
          {notice.detail}
        </p>
      ) : null}

      <div
        className="flex h-4 w-full overflow-hidden rounded border border-terminal-border"
        role="img"
        aria-label={segments
          .map((s) => `${s.label} ${s.amount}`)
          .join("; ")}
      >
        {segments.map((segment, index) => (
          <div
            key={segment.id}
            className={segment.isResidual ? "bg-amber-600" : "bg-accent-dim"}
            style={{
              width: total === 0 ? `${100 / segments.length}%` : `${(magnitudes[index] / total) * 100}%`,
            }}
            title={`${segment.label}: ${segment.amount}`}
            data-residual={segment.isResidual ? "true" : undefined}
          />
        ))}
      </div>

      <DenseTable
        caption={`Attribution components and residual for ${input.bucket}`}
        columns={[
          {
            key: "label",
            header: "Component",
            render: (row) => (
              <span
                className={row.isResidual ? "font-semibold text-amber-400" : undefined}
                title={row.note ?? undefined}
              >
                {row.label}
                {row.uncomputed ? (
                  <span className="ml-1 text-terminal-muted">(not computed)</span>
                ) : null}
              </span>
            ),
          },
          {
            key: "amount",
            header: "Amount",
            unit: "INR",
            align: "right",
            render: (row) => <span className="font-mono">{row.amount}</span>,
          },
        ]}
        rows={rows}
        rowKey={(row) => row.id}
        rowClassName={(row) => (row.isResidual ? "border-t border-amber-700" : undefined)}
      />

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 font-mono text-xs">
        <dt className="text-terminal-muted">TOTAL P&amp;L</dt>
        <dd>{String(input.totalPnl)}</dd>
        <dt className="text-terminal-muted">EXPLAINED</dt>
        <dd>{String(input.explained)}</dd>
        <dt className="text-terminal-muted">RESIDUAL</dt>
        <dd className="text-amber-400">
          {String(input.residual)}
          {share ? <span className="ml-2 text-terminal-muted">{share}</span> : null}
        </dd>
      </dl>

      <p className="text-xs text-terminal-muted">
        The residual is what the method does not explain. It is reported, not
        distributed: a large residual is information about the decomposition.
      </p>
    </section>
  );
}
