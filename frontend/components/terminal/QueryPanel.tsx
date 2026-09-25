"use client";

/**
 * Loading, error and empty, decided in one place.
 *
 * Every screen has the same three non-success states and `13-FRONTEND_IA.md` §7
 * requires them to be distinct and honest. Written per screen they drift: one
 * renders a spinner for a 503, another renders an empty table, and the table is
 * the dangerous one because an empty table is a claim.
 *
 * The order is deliberate. Loading wins over error so a refetch after a failure
 * does not flash the old error; error wins over empty so a failed request is never
 * shown as "no data for this period". Those two are exactly the confusions that
 * make an operator draw a conclusion from a screen that had nothing to say.
 */

import type { ReactNode } from "react";

import type { EmptyState } from "@/lib/terminal/quality";
import { ErrorPanel, EmptyStateView, LoadingRows } from "@/components/terminal/primitives";
import { classify } from "@/components/terminal/useTerminalQuery";

export interface QueryLike<T> {
  readonly data?: T;
  readonly isPending: boolean;
  readonly isError: boolean;
  readonly error: unknown;
  readonly refetch?: () => void;
}

export function QueryPanel<T>({
  query,
  label,
  empty,
  isEmpty,
  children,
}: {
  query: QueryLike<T>;
  label: string;
  empty: EmptyState;
  isEmpty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <LoadingRows label={label} />;
  if (query.isError || query.data === undefined) {
    return (
      <ErrorPanel
        error={classify(query.error)}
        onRetry={query.refetch ? () => query.refetch?.() : undefined}
      />
    );
  }
  if (isEmpty?.(query.data)) return <EmptyStateView state={empty} />;
  return <>{children(query.data)}</>;
}
