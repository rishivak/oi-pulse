/**
 * Cursor pagination and bounded client state.
 *
 * `12-API_SPEC.md` §5: "cursor-based on `(observed_at, id)`; offset pagination is
 * not offered on observation-scale collections." Brief §20 adds the client side:
 * avoid "repeated large-data transfers" and "unbounded client-side state".
 *
 * The second is the one that bites in a terminal. An operator leaves a blotter open
 * for a session; every page appended to an in-memory list is memory that is never
 * released, and the tab that was fast at 09:15 is unusable by 15:30. So
 * {@link appendPage} enforces a cap and reports what it dropped rather than
 * silently discarding — a table that quietly stopped being complete is worse than
 * one that says how much of the collection it holds.
 */

export interface Page<T> {
  readonly rows: readonly T[];
  readonly nextCursor: string | null;
}

/** Default page size. Small enough to render densely, large enough to fill a screen. */
export const DEFAULT_PAGE_SIZE = 100;

/**
 * Hard ceiling on rows retained in the browser for one collection.
 *
 * Not a display limit: the table virtualises. This is the bound on retained state,
 * and it exists because there is no upper bound on how long a terminal stays open.
 */
export const MAX_RETAINED_ROWS = 2_000;

export interface Accumulated<T> {
  readonly rows: readonly T[];
  readonly nextCursor: string | null;
  /** Rows dropped from the front to stay within the cap. */
  readonly evicted: number;
  /** True once anything has been evicted; the view must say so. */
  readonly truncated: boolean;
}

export function emptyAccumulation<T>(): Accumulated<T> {
  return { rows: [], nextCursor: null, evicted: 0, truncated: false };
}

export function appendPage<T>(
  current: Accumulated<T>,
  page: Page<T>,
  maxRows = MAX_RETAINED_ROWS,
): Accumulated<T> {
  const combined = [...current.rows, ...page.rows];
  const overflow = Math.max(0, combined.length - maxRows);
  const rows = overflow === 0 ? combined : combined.slice(overflow);
  const evicted = current.evicted + overflow;
  return {
    rows,
    nextCursor: page.nextCursor,
    evicted,
    truncated: evicted > 0,
  };
}

export function truncationNotice(state: Accumulated<unknown>): string | null {
  if (!state.truncated) return null;
  return (
    `Showing the most recent ${state.rows.length} rows; ${state.evicted} earlier rows ` +
    `were released to bound memory. Narrow the filter or the period to see them.`
  );
}
