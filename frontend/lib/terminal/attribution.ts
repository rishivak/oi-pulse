/**
 * Attribution presentation. The residual is a row, not a rounding error.
 *
 * Phase 12 brief §16: "Display residual explicitly. Never hide residuals simply to
 * produce a visually balanced chart." Phase 11 brief §13 said the same about the
 * backend: "Never hide a residual by forcing component values to sum."
 *
 * Two rules follow, and both are structural rather than advisory.
 *
 * **The residual row is not optional.** {@link attributionRows} appends it
 * unconditionally, including when it is zero, and {@link chartSegments} includes it
 * as a segment. There is no flag to suppress it, so no screen can be written that
 * omits it and no chart can be balanced by leaving it out.
 *
 * **Nothing here computes P&L.** Brief §15: "Do not duplicate P&L calculations in
 * the frontend." `residual` arrives from `oipulse/trading/portfolio/attribution.py`,
 * where it is a derived property `total_pnl - explained` with no settable field, and
 * the database enforces `total_pnl = explained + residual`. This module reads that
 * number. It does not subtract anything to obtain it — if it did, a frontend
 * arithmetic difference could make a non-reconciling result look reconciled.
 * {@link reconciliationNotice} reports the backend's own `reconciles` flag for the
 * same reason.
 */

/** The canonical hierarchy (brief §16), matching Phase 11's grouping keys. */
export type AttributionLevel =
  | "PORTFOLIO"
  | "STRATEGY"
  | "UNDERLYING"
  | "INSTRUMENT"
  | "TRADE";

export const ATTRIBUTION_HIERARCHY: readonly AttributionLevel[] = [
  "PORTFOLIO",
  "STRATEGY",
  "UNDERLYING",
  "INSTRUMENT",
  "TRADE",
] as const;

/** The marker Phase 11 uses for a component it could not attribute. */
export const UNATTRIBUTED = "UNATTRIBUTED";

/** Distinguishes the residual row from a component in every consumer. */
export const RESIDUAL_ROW_ID = "__residual__";

export interface AttributionComponentInput {
  readonly component: string;
  readonly amount: string | number;
  /** Set when the component could not be computed at all. */
  readonly uncomputed?: boolean;
}

export interface AttributionInput {
  readonly bucket: string;
  readonly bucketId: string | null;
  readonly totalPnl: string | number;
  readonly explained: string | number;
  /** Read, never derived. See the module docstring. */
  readonly residual: string | number;
  readonly residualFraction: string | number | null;
  readonly reconciles: boolean;
  readonly method: string;
  readonly methodVersion: number;
  readonly components: readonly AttributionComponentInput[];
  readonly uncomputedComponents: readonly string[];
}

export interface AttributionRow {
  readonly id: string;
  readonly label: string;
  readonly amount: string;
  /** True for the residual row, so it can be styled distinctly but never dropped. */
  readonly isResidual: boolean;
  /** True when the backend reported the component as not computed. */
  readonly uncomputed: boolean;
  readonly note: string | null;
}

const RESIDUAL_NOTE =
  "Unexplained by the model. Not allocated to any component — a large residual is " +
  "information about the decomposition, not something to distribute.";

function asText(value: string | number): string {
  return typeof value === "string" ? value : String(value);
}

/**
 * Component rows followed by the residual row.
 *
 * The residual is appended after the components rather than merged into the list,
 * so a caller that slices, sorts or filters the components cannot lose it by
 * accident: it is not one of them.
 */
export function attributionRows(input: AttributionInput): AttributionRow[] {
  const uncomputed = new Set(input.uncomputedComponents);
  const rows: AttributionRow[] = input.components.map((c) => ({
    id: c.component,
    label: c.component === UNATTRIBUTED ? "Unattributed" : c.component,
    amount: asText(c.amount),
    isResidual: false,
    uncomputed: c.uncomputed === true || uncomputed.has(c.component),
    note:
      c.component === UNATTRIBUTED
        ? "P&L the method attributes to no named component."
        : null,
  }));
  rows.push({
    id: RESIDUAL_ROW_ID,
    label: "Residual",
    amount: asText(input.residual),
    isResidual: true,
    uncomputed: false,
    note: RESIDUAL_NOTE,
  });
  return rows;
}

export interface ChartSegment {
  readonly id: string;
  readonly label: string;
  readonly amount: string;
  readonly isResidual: boolean;
}

/**
 * Chart segments, residual included.
 *
 * A stacked bar that omits the residual is a picture of a decomposition that
 * balanced, drawn from one that did not. The residual segment is always emitted,
 * and {@link chartIncludesResidual} exists so a test can assert it on any series a
 * screen actually renders.
 */
export function chartSegments(input: AttributionInput): ChartSegment[] {
  return attributionRows(input).map((r) => ({
    id: r.id,
    label: r.label,
    amount: r.amount,
    isResidual: r.isResidual,
  }));
}

export function chartIncludesResidual(segments: readonly ChartSegment[]): boolean {
  return segments.some((s) => s.isResidual);
}

export interface ReconciliationNotice {
  readonly reconciles: boolean;
  readonly label: string;
  readonly glyph: string;
  readonly detail: string;
}

/**
 * Report the backend's reconciliation flag verbatim.
 *
 * Deliberately does not check the arithmetic itself. `total_pnl = explained +
 * residual` is enforced by a database constraint and by the Phase 11 model; a second
 * check here, done in JavaScript floating point against decimal strings, could only
 * ever disagree with the authority — and would disagree in the direction of
 * declaring a correct result broken.
 */
export function reconciliationNotice(input: AttributionInput): ReconciliationNotice {
  return input.reconciles
    ? {
        reconciles: true,
        label: "RECONCILES",
        glyph: "●",
        detail: `total = explained + residual, per ${input.method}@v${input.methodVersion}.`,
      }
    : {
        reconciles: false,
        label: "DOES NOT RECONCILE",
        glyph: "✕",
        detail:
          "The backend reports that the components and residual do not sum to the " +
          "total. The figures are shown unaltered; nothing is adjusted to make them agree.",
      };
}

/** Human label for the residual's share, when the backend supplied one. */
export function residualShare(input: AttributionInput): string | null {
  if (input.residualFraction === null || input.residualFraction === undefined) return null;
  const n = Number(input.residualFraction);
  if (!Number.isFinite(n)) return null;
  return `${(n * 100).toFixed(1)}% of total P&L`;
}
