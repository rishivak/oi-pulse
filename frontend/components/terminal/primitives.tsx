"use client";

/**
 * Terminal primitives.
 *
 * Every component here is a renderer for a value some pure module in
 * `lib/terminal/` already decided. None of them chooses what a status means, when a
 * value is absent, or whether something is stale — those are decisions, they are
 * tested under `node --test`, and a component that re-made them could disagree with
 * the tests that cover them.
 *
 * Accessibility runs through all of them (brief §28, `13-FRONTEND_IA.md`):
 * status is carried by a glyph and text as well as colour, every badge has a
 * `title` and screen-reader label, tables use `<caption>` and `scope`, and focus is
 * visible on everything interactive.
 */

import type { ReactNode } from "react";

import type { EmptyState, Presented, QualityBadge } from "@/lib/terminal/quality";
import type { ClassifiedError } from "@/lib/terminal/errors";
import type { StateBadge } from "@/lib/terminal/states";
import { renderInstant } from "@/lib/terminal/formatting";

// --------------------------------------------------------------------- value

/**
 * Render a value or the reason it is absent.
 *
 * There is no `fallback` prop. A caller cannot pass `0`, a dash or a previous
 * reading, because the component does not accept one — brief §18's three forbidden
 * substitutions are unexpressible at the call site rather than discouraged in it.
 */
export function Value<T>({
  presented,
  render,
}: {
  presented: Presented<T>;
  render?: (value: T) => ReactNode;
}) {
  if (presented.kind === "absent") {
    return (
      <span
        className="text-terminal-muted italic"
        title={presented.detail ?? presented.text}
        data-absent={presented.reason}
      >
        {presented.text}
      </span>
    );
  }
  const body = render ? render(presented.value) : String(presented.value);
  return (
    <span
      className={presented.stale ? "text-terminal-muted" : undefined}
      data-stale={presented.stale ? "true" : undefined}
      title={presented.stale ? "This value is older than the state beside it." : undefined}
    >
      {body}
      {presented.stale ? (
        <span className="ml-1 text-xs" aria-label="stale">
          ◷
        </span>
      ) : null}
    </span>
  );
}

// -------------------------------------------------------------------- times

/** An instant in IST, with UTC on hover (`13` §7). */
export function Instant({ iso, label }: { iso: string | null; label?: string }) {
  const rendered = renderInstant(iso);
  if (rendered === null) {
    return <span className="text-terminal-muted italic">not recorded</span>;
  }
  return (
    <time dateTime={iso ?? undefined} title={rendered.utc} className="font-mono">
      {label ? <span className="mr-1 text-terminal-muted">{label}</span> : null}
      {rendered.text}
    </time>
  );
}

// ------------------------------------------------------------------- badges

const TONE_CLASS: Record<string, string> = {
  NEUTRAL: "border-terminal-border text-terminal-text",
  POSITIVE: "border-bull-dim text-bull",
  CAUTION: "border-amber-700 text-amber-400",
  NEGATIVE: "border-bear-dim text-bear",
  BLOCKING: "border-bear bg-bear-dim text-terminal-text",
};

export function StateBadgeView({ badge }: { badge: StateBadge }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-xs ${
        TONE_CLASS[badge.tone] ?? TONE_CLASS.NEUTRAL
      }`}
      title={badge.detail ?? badge.srLabel}
    >
      <span aria-hidden="true">{badge.glyph}</span>
      <span>{badge.label}</span>
      <span className="sr-only">{badge.srLabel}</span>
    </span>
  );
}

/**
 * The persistent quality indicator (`13` §7).
 *
 * Expands to the issue list rather than hiding it behind a tooltip: the issues are
 * why the status is what it is, and a status with no visible cause invites the
 * reader to discount it.
 */
export function QualityHeader({ badge }: { badge: QualityBadge }) {
  return (
    <details className="group" open={badge.issues.length > 0 && badge.status !== "OK"}>
      <summary
        className="flex cursor-pointer list-none items-center gap-2 rounded px-2 py-1 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        title={badge.srLabel}
      >
        <span className="text-terminal-muted">DATA QUALITY</span>
        <span aria-hidden="true">{badge.glyph}</span>
        <span>{badge.label}</span>
        <span className="sr-only">{badge.srLabel}</span>
        {badge.issues.length > 0 ? (
          <span className="text-terminal-muted">({badge.issues.length})</span>
        ) : null}
      </summary>
      {badge.issues.length > 0 ? (
        <ul className="mt-1 space-y-0.5 px-2 text-xs text-terminal-muted">
          {badge.issues.map((issue, index) => (
            <li key={`${issue.code}-${index}`}>
              <span className="font-mono">{issue.code}</span> — {issue.message}
            </li>
          ))}
        </ul>
      ) : null}
    </details>
  );
}

/**
 * Wrapper for a derived panel under a degraded or unreliable state.
 *
 * Desaturation is the visual half of `13` §7. The other half — withholding the
 * numbers themselves — happens in `gateDerived`, because a desaturated but readable
 * number still gets read.
 */
export function DerivedPanel({
  badge,
  children,
}: {
  badge: QualityBadge;
  children: ReactNode;
}) {
  if (!badge.desaturateDerived) return <>{children}</>;
  return (
    <div className="opacity-50 saturate-0" data-desaturated="true" role="group">
      <p className="mb-1 text-xs text-terminal-muted">
        State is {badge.label}. Derived values below are withheld rather than shown as
        though they were trustworthy.
      </p>
      {children}
    </div>
  );
}

// -------------------------------------------------------- empty / error / load

export function EmptyStateView({ state }: { state: EmptyState }) {
  return (
    <div
      className="rounded border border-terminal-border bg-terminal-surface p-4"
      role="status"
      data-empty={state.kind}
    >
      <p className="font-mono text-sm">{state.title}</p>
      <p className="mt-1 text-xs text-terminal-muted">{state.detail}</p>
    </div>
  );
}

/**
 * A backend error, explained in operator terms.
 *
 * The backend's own message is shown beneath our explanation rather than instead of
 * it: ours says what it means for the screen, theirs says what actually happened,
 * and replacing the second with the first loses the detail that resolves it.
 */
export function ErrorPanel({
  error,
  onRetry,
}: {
  error: ClassifiedError;
  onRetry?: () => void;
}) {
  const blocking = error.meaning.presentation === "BLOCKING";
  return (
    <div
      className={`rounded border p-4 ${
        blocking ? "border-bear bg-bear-dim" : "border-terminal-border bg-terminal-surface"
      }`}
      role="alert"
      data-error-code={error.meaning.code}
    >
      <p className="font-mono text-sm">
        <span aria-hidden="true">{blocking ? "⚠ " : ""}</span>
        {error.meaning.title}
        <span className="ml-2 text-xs text-terminal-muted">{error.meaning.code}</span>
      </p>
      <p className="mt-1 text-xs">{error.meaning.guidance}</p>
      {error.message ? (
        <p className="mt-2 font-mono text-xs text-terminal-muted">{error.message}</p>
      ) : null}
      {onRetry && error.meaning.readRetryable ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-terminal-border px-2 py-1 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          Ask again
        </button>
      ) : null}
    </div>
  );
}

export function LoadingRows({ rows = 6, label }: { rows?: number; label: string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className="space-y-1">
      <span className="sr-only">Loading {label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="h-5 animate-pulse rounded bg-terminal-surface"
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

// -------------------------------------------------------------------- tables

export interface Column<T> {
  readonly key: string;
  readonly header: string;
  /** Units belong in the header, once, rather than beside every cell (§27). */
  readonly unit?: string;
  readonly align?: "left" | "right";
  readonly render: (row: T) => ReactNode;
}

/**
 * A dense table with real table semantics.
 *
 * `<caption>` and `scope="col"` are not decoration: a screen reader announcing
 * "column: OI, row: 25000" is the difference between a usable blotter and a wall of
 * numbers, and `13` §1.7 asks for density, which makes the structure matter more
 * rather than less.
 */
export function DenseTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  rowClassName,
}: {
  caption: string;
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
  rowClassName?: (row: T) => string | undefined;
}) {
  return (
    <table className="w-full border-collapse font-mono text-xs">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="border-b border-terminal-border text-terminal-muted">
          {columns.map((column) => (
            <th
              key={column.key}
              scope="col"
              className={`px-2 py-1 font-normal ${
                column.align === "right" ? "text-right" : "text-left"
              }`}
            >
              {column.header}
              {column.unit ? (
                <span className="ml-1 text-[10px] opacity-70">({column.unit})</span>
              ) : null}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr
            key={rowKey(row, index)}
            className={`border-b border-terminal-border/40 ${rowClassName?.(row) ?? ""}`}
          >
            {columns.map((column) => (
              <td
                key={column.key}
                className={`px-2 py-1 ${column.align === "right" ? "text-right" : "text-left"}`}
              >
                {column.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
