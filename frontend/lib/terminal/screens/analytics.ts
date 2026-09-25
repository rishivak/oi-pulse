/**
 * Analytics presentation: the registry, and the values it describes.
 *
 * `13-FRONTEND_IA.md` §1.3 draws the line this module sits on:
 *
 * > The frontend is a consumer of the domain model. It does not compute analytics,
 * > and it does not define conventions. Where the legacy UI decided what PCR > 1.2
 * > means — and two screens disagreed — v2 renders what the API returns.
 *
 * So there is no arithmetic below. What there is: the feature's identity, version,
 * units, convention and availability presented *with* its value, because
 * `oipulse/api/app.py` gives the reason — "a user hovering GEX sees the dealer
 * convention in force rather than reading the source".
 *
 * ### A note on the value rows
 *
 * `GET /features/{identifier}/values` returns whatever the configured
 * `metric_values` reader produced; the route does not reshape it, and Phase 4
 * declared the contract without wiring a durable reader. The row type here is
 * therefore permissive and every field is read defensively. That is a real gap and
 * is recorded as one — a narrower type would be a claim about a shape nothing in
 * this repository has produced.
 */

import type { FeatureSpecDto } from "@/lib/api/dto";
import { type Presented, presentValue } from "@/lib/terminal/quality";

export interface FeatureIdentity {
  readonly identifier: string;
  readonly version: number;
  readonly label: string;
  readonly definition: string;
  readonly formula: string | null;
  readonly units: string | null;
  readonly scope: string;
  readonly normalization: string | null;
  readonly availabilityDelaySeconds: number | null;
  readonly lookbackSeconds: number | null;
  readonly inputs: readonly string[];
  readonly dependsOn: readonly string[];
  readonly qualityRequirements: Readonly<Record<string, unknown>>;
  readonly parameters: Readonly<Record<string, unknown>>;
}

/**
 * The header shown above any value derived from this feature.
 *
 * `label` is `IDENTIFIER@vN`. The version is part of the name rather than a nearby
 * detail: two versions of the same feature are different quantities, and a chart
 * that silently switched between them would be a chart of two things.
 */
export function featureIdentity(spec: FeatureSpecDto): FeatureIdentity {
  return {
    identifier: spec.identifier,
    version: spec.version,
    label: `${spec.identifier}@v${spec.version}`,
    definition: spec.definition,
    formula: spec.formula,
    units: spec.units,
    scope: spec.scope,
    normalization: spec.normalization,
    availabilityDelaySeconds: spec.availability_delay,
    lookbackSeconds: spec.lookback,
    inputs: spec.inputs,
    dependsOn: spec.depends_on,
    qualityRequirements: spec.quality_requirements,
    parameters: spec.parameters,
  };
}

/** Reader-defined row. Every field optional because nothing guarantees them. */
export interface FeatureValueRowDto {
  readonly market_time?: unknown;
  readonly knowledge_time?: unknown;
  readonly available_at?: unknown;
  readonly value?: unknown;
  readonly scope_ref?: unknown;
  readonly quality_status?: unknown;
  readonly [key: string]: unknown;
}

export interface FeatureValueRow {
  readonly marketTime: string | null;
  readonly knowledgeTime: string | null;
  /** Derived values have an availability; raw observations do not (`12` §2). */
  readonly availableAt: string | null;
  readonly value: Presented<string>;
  readonly scopeRef: string | null;
  readonly qualityStatus: string | null;
}

function asString(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}

export function featureValueRow(row: FeatureValueRowDto): FeatureValueRow {
  return {
    marketTime: asString(row.market_time),
    knowledgeTime: asString(row.knowledge_time),
    availableAt: asString(row.available_at),
    value: presentValue(asString(row.value), "NOT_COMPUTED"),
    scopeRef: asString(row.scope_ref),
    qualityStatus: asString(row.quality_status),
  };
}

/**
 * The feature groups each analysis screen draws on.
 *
 * Identifiers only — the registry supplies definitions, versions and conventions at
 * runtime. Listing them here rather than in a component keeps "which features does
 * the Volatility screen show?" answerable without reading JSX, and keeps the answer
 * testable against the registry the backend actually serves.
 */
export const SCREEN_FEATURES = {
  positioning: [
    "OI_CHANGE",
    "OI_CHANGE_PCT",
    "OI_CONCENTRATION",
    "OI_WALL_CALL",
    "OI_WALL_PUT",
    "OI_WALL_MIGRATION",
    "PUT_OI_MIGRATION",
    "CALL_OI_MIGRATION",
    "BUILDUP_CLASSIFICATION",
    "PRICE_OI_RELATIONSHIP",
    "VOLUME_OI_RATIO",
    "PCR",
    "PCR_OI_CHANGE",
  ],
  volatility: [
    "ATM_IV",
    "IV_CHANGE",
    "IV_SKEW_DELTA",
    "IV_SKEW_STRIKE",
    "IV_TERM_STRUCTURE",
    "REALIZED_VOL_CLOSE_TO_CLOSE",
    "REALIZED_VOL_PARKINSON",
    "IMPLIED_REALIZED_SPREAD",
    "IV_RANK",
    "IV_PERCENTILE",
  ],
  marketStructure: [
    "GEX_BY_STRIKE",
    "GEX_TOTAL",
    "GEX_BY_EXPIRY",
    "GEX_CONCENTRATION",
    "GEX_PROFILE",
    "GAMMA_FLIP_LEVEL",
  ],
} as const;

export type AnalyticsScreen = keyof typeof SCREEN_FEATURES;

/**
 * Which of a screen's features the running backend actually registers.
 *
 * Returned as two lists rather than one filtered list. A feature the registry does
 * not offer is not the same as a feature with no values, and a screen that quietly
 * dropped it would make a missing capability look like a quiet market.
 */
export function partitionAvailable(
  screen: AnalyticsScreen,
  registered: readonly string[],
): { readonly available: readonly string[]; readonly missing: readonly string[] } {
  const known = new Set(registered);
  const wanted = SCREEN_FEATURES[screen];
  return {
    available: wanted.filter((f) => known.has(f)),
    missing: wanted.filter((f) => !known.has(f)),
  };
}
