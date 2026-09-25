/**
 * Quality and availability, and the refusal to paper over either.
 *
 * `13-FRONTEND_IA.md` §1.4: "Quality is always visible. Every screen shows
 * data-quality status. A number without its reliability is misleading." §7 adds that
 * an `UNRELIABLE` state "visibly desaturates derived panels rather than rendering
 * them as though they were trustworthy", and that empty states must be "distinct and
 * honest ... Never a zero, never a blank chart."
 *
 * The Phase 12 brief §18 states the prohibition as three substitutions that must not
 * happen:
 *
 *     missing      -> 0
 *     unavailable  -> previous
 *     unreliable   -> latest
 *
 * The defence here is structural. {@link presentValue} returns a discriminated union,
 * so there is no code path on which a component receives a bare `number` that might
 * secretly be a stand-in. To render an absent value a component must first destructure
 * an `Absent`, and an `Absent` carries the reason — which is the thing the user needed.
 */

/** The envelope-level quality status (`12-API_SPEC.md` §2 response envelope). */
export type QualityStatus = "OK" | "DEGRADED" | "UNRELIABLE" | "UNKNOWN";

/**
 * Why a particular value is not being shown.
 *
 * These are backend vocabulary, not frontend inventions: the first five are the
 * `12` §4 error codes, `STALE` comes from the envelope's issue list, and
 * `RECONCILIATION_REQUIRED` from the OMS states in `11-TRADING.md` §5.
 */
export type AbsenceReason =
  | "NO_DATA_FOR_PERIOD"
  | "FEATURE_NOT_AVAILABLE"
  | "INSUFFICIENT_HISTORY"
  | "QUALITY_REQUIREMENTS_UNMET"
  | "STATE_UNRELIABLE"
  | "STALE"
  | "UNKNOWN"
  | "RECONCILIATION_REQUIRED"
  | "NOT_COMPUTED"
  | "BACKEND_UNAVAILABLE";

export interface Present<T> {
  readonly kind: "present";
  readonly value: T;
  /** Set when the value arrived but is older than the state it sits beside. */
  readonly stale: boolean;
}

export interface Absent {
  readonly kind: "absent";
  readonly reason: AbsenceReason;
  /** The sentence shown in place of the value. Never blank, never "0". */
  readonly text: string;
  /** Longer operator-facing detail, from the backend where it supplied one. */
  readonly detail: string | null;
}

export type Presented<T> = Present<T> | Absent;

const ABSENCE_TEXT: Readonly<Record<AbsenceReason, string>> = {
  NO_DATA_FOR_PERIOD: "no data collected for this period",
  FEATURE_NOT_AVAILABLE: "not yet available at this timestamp",
  INSUFFICIENT_HISTORY: "insufficient history for this statistic",
  QUALITY_REQUIREMENTS_UNMET: "feature unavailable — quality requirements unmet",
  STATE_UNRELIABLE: "state unreliable — value withheld",
  STALE: "stale",
  UNKNOWN: "unknown",
  RECONCILIATION_REQUIRED: "reconciliation required",
  NOT_COMPUTED: "not computed",
  BACKEND_UNAVAILABLE: "backend unavailable",
};

export function absent(reason: AbsenceReason, detail: string | null = null): Absent {
  return { kind: "absent", reason, text: ABSENCE_TEXT[reason], detail };
}

export function present<T>(value: T, stale = false): Present<T> {
  return { kind: "present", value, stale };
}

/**
 * Wrap a value that may legitimately be missing.
 *
 * `null` and `undefined` become an `Absent` carrying `reason`; they never become
 * zero, and the previous value is not available to this function to fall back to,
 * by construction.
 */
export function presentValue<T>(
  value: T | null | undefined,
  reason: AbsenceReason,
  opts: { stale?: boolean; detail?: string | null } = {},
): Presented<T> {
  if (value === null || value === undefined) return absent(reason, opts.detail ?? null);
  return present(value, opts.stale ?? false);
}

export interface QualityIssue {
  readonly code: string;
  readonly message: string;
}

export interface QualityBadge {
  readonly status: QualityStatus;
  readonly label: string;
  /** Colour-independent marker (§28). Status is legible without colour vision. */
  readonly glyph: string;
  /** Screen-reader text; never relies on the glyph alone. */
  readonly srLabel: string;
  /** True when derived panels must be desaturated (`13` §7). */
  readonly desaturateDerived: boolean;
  /** True when the terminal must refuse to present derived values as trustworthy. */
  readonly withholdDerived: boolean;
  readonly issues: readonly QualityIssue[];
}

const BADGES: Readonly<
  Record<QualityStatus, Omit<QualityBadge, "issues" | "status">>
> = {
  OK: {
    label: "OK",
    glyph: "●",
    srLabel: "data quality OK",
    desaturateDerived: false,
    withholdDerived: false,
  },
  DEGRADED: {
    label: "DEGRADED",
    glyph: "◐",
    srLabel: "data quality degraded",
    desaturateDerived: false,
    withholdDerived: false,
  },
  UNRELIABLE: {
    label: "UNRELIABLE",
    glyph: "✕",
    srLabel: "data quality unreliable",
    desaturateDerived: true,
    withholdDerived: true,
  },
  UNKNOWN: {
    label: "UNKNOWN",
    glyph: "?",
    srLabel: "data quality unknown",
    desaturateDerived: true,
    withholdDerived: true,
  },
};

/**
 * Build the header badge.
 *
 * An absent or unrecognised status becomes `UNKNOWN`, never `OK`. Defaulting an
 * unlabelled response to healthy is the single most dangerous thing this file could
 * do: it would make an API that stopped sending quality look like an API reporting
 * good quality.
 */
export function qualityBadge(
  status: string | null | undefined,
  issues: readonly QualityIssue[] = [],
): QualityBadge {
  const key: QualityStatus =
    status === "OK" || status === "DEGRADED" || status === "UNRELIABLE"
      ? status
      : "UNKNOWN";
  return { status: key, ...BADGES[key], issues };
}

/**
 * Whether a derived panel may render its numbers.
 *
 * `13` §7 requires an `UNRELIABLE` state to desaturate derived panels. This goes one
 * step further for the numbers themselves: on `UNRELIABLE` and `UNKNOWN` the derived
 * value is replaced by its reason. A desaturated but readable number still gets read.
 */
export function gateDerived<T>(
  badge: QualityBadge,
  value: T | null | undefined,
  reason: AbsenceReason = "NOT_COMPUTED",
): Presented<T> {
  if (badge.withholdDerived) {
    return absent(
      badge.status === "UNRELIABLE" ? "STATE_UNRELIABLE" : "UNKNOWN",
      badge.issues.map((i) => i.message).join("; ") || null,
    );
  }
  return presentValue(value, reason, { stale: badge.status === "DEGRADED" });
}

/** The distinct empty states `13` §7 requires be told apart. */
export type EmptyStateKind =
  | "NO_DATA_FOR_PERIOD"
  | "INSUFFICIENT_HISTORY"
  | "QUALITY_REQUIREMENTS_UNMET"
  | "NOT_YET_AVAILABLE"
  | "NO_MATCHING_FILTER";

export interface EmptyState {
  readonly kind: EmptyStateKind;
  readonly title: string;
  readonly detail: string;
}

const EMPTY_STATES: Readonly<Record<EmptyStateKind, Omit<EmptyState, "kind">>> = {
  NO_DATA_FOR_PERIOD: {
    title: "No data collected for this period",
    detail: "Nothing was observed in the requested window. This is not a zero.",
  },
  INSUFFICIENT_HISTORY: {
    title: "Insufficient history for this statistic",
    detail: "The statistic needs a longer lookback than the stored history provides.",
  },
  QUALITY_REQUIREMENTS_UNMET: {
    title: "Feature unavailable — quality requirements unmet",
    detail: "The feature declined to compute rather than compute from degraded inputs.",
  },
  NOT_YET_AVAILABLE: {
    title: "Not yet available at this timestamp",
    detail: "Its availability is later than the decision time this view is filtered to.",
  },
  NO_MATCHING_FILTER: {
    title: "No rows match the current filter",
    detail: "Data exists for this period; the filter excluded all of it.",
  },
};

export function emptyState(kind: EmptyStateKind): EmptyState {
  return { kind, ...EMPTY_STATES[kind] };
}
