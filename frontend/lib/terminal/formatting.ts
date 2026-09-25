/**
 * Display formatting. Deliberately arithmetic-free beyond presentation.
 *
 * `13-FRONTEND_IA.md` §7: "All times IST with an explicit label; UTC available on
 * hover." and §1.3: the frontend "does not compute analytics, and it does not define
 * conventions". So nothing here derives a quantity — it renders one that arrived.
 *
 * India Standard Time is a fixed UTC+05:30 with no daylight saving, so the offset is
 * a constant rather than a zone lookup. That matters here: `Intl` timezone data is
 * not guaranteed identical across the Node version that renders on the server and the
 * browser that hydrates, and a timestamp that shifts between the two is exactly the
 * kind of quiet inconsistency this terminal exists to avoid.
 */

/** Fixed offset. IST has never observed daylight saving. */
export const IST_OFFSET_MINUTES = 330;
export const IST_LABEL = "IST";

const PAD = (n: number, width = 2): string => String(n).padStart(width, "0");

export interface RenderedInstant {
  /** `2026-03-03 11:42:00` */
  readonly date: string;
  readonly time: string;
  /** Always present. A bare number is never shown for an instant. */
  readonly zone: string;
  /** The same instant in UTC, for the hover affordance `13` §7 requires. */
  readonly utc: string;
  /** Full single-line form, for dense tables. */
  readonly text: string;
}

/**
 * Render an ISO instant in IST.
 *
 * Returns `null` rather than a fallback string for unparseable input: a formatter
 * that invents "—" for a malformed timestamp hides a contract violation, and the
 * caller — which knows whether absence is legitimate — should decide.
 */
export function renderInstant(iso: string | null | undefined): RenderedInstant | null {
  if (iso === null || iso === undefined || iso === "") return null;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  const shifted = new Date(ms + IST_OFFSET_MINUTES * 60_000);
  const date = `${shifted.getUTCFullYear()}-${PAD(shifted.getUTCMonth() + 1)}-${PAD(shifted.getUTCDate())}`;
  const time = `${PAD(shifted.getUTCHours())}:${PAD(shifted.getUTCMinutes())}:${PAD(shifted.getUTCSeconds())}`;
  const utcDate = new Date(ms);
  const utc =
    `${utcDate.getUTCFullYear()}-${PAD(utcDate.getUTCMonth() + 1)}-${PAD(utcDate.getUTCDate())} ` +
    `${PAD(utcDate.getUTCHours())}:${PAD(utcDate.getUTCMinutes())}:${PAD(utcDate.getUTCSeconds())} UTC`;
  return { date, time, zone: IST_LABEL, utc, text: `${date} ${time} ${IST_LABEL}` };
}

/**
 * The ingestion-lag pair.
 *
 * `13` §7: "Where `observed_at` and `ingested_at` differ materially, both are shown —
 * hiding ingestion lag hides the reason a live decision differed from a backtest."
 * "Materially" is a display threshold, not an analytic one, and is stated here so it
 * is visible and testable rather than sprinkled through components.
 */
export const MATERIAL_INGESTION_LAG_MS = 1_000;

export interface ObservationTiming {
  readonly observedAt: RenderedInstant | null;
  readonly ingestedAt: RenderedInstant | null;
  readonly lagMs: number | null;
  /** When true, both timestamps must be rendered, not just the first. */
  readonly showBoth: boolean;
}

export function observationTiming(
  observedAt: string | null | undefined,
  ingestedAt: string | null | undefined,
): ObservationTiming {
  const observed = renderInstant(observedAt);
  const ingested = renderInstant(ingestedAt);
  let lagMs: number | null = null;
  if (observedAt && ingestedAt) {
    const a = Date.parse(observedAt);
    const b = Date.parse(ingestedAt);
    if (!Number.isNaN(a) && !Number.isNaN(b)) lagMs = b - a;
  }
  return {
    observedAt: observed,
    ingestedAt: ingested,
    lagMs,
    showBoth: lagMs !== null && Math.abs(lagMs) >= MATERIAL_INGESTION_LAG_MS,
  };
}

/**
 * Render a number with an explicit unit (§27: "explicit units").
 *
 * `decimals` is supplied by the caller from the feature definition's declared
 * precision where one exists. There is no default rounding convention here, because
 * a rounding convention is a convention, and `13` §1.3 puts conventions in the
 * backend.
 */
export function renderQuantity(
  value: number | string | null | undefined,
  unit: string,
  decimals?: number,
): string | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return null;
  const body = decimals === undefined ? String(n) : n.toFixed(decimals);
  return unit === "" ? body : `${body} ${unit}`;
}

/** Signed rendering, for P&L and changes. The sign is always shown, never implied. */
export function renderSigned(
  value: number | string | null | undefined,
  unit: string,
  decimals = 2,
): string | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return null;
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const body = Math.abs(n).toFixed(decimals);
  return unit === "" ? `${sign}${body}` : `${sign}${body} ${unit}`;
}
