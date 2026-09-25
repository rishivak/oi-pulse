/**
 * The response envelope, and the refusal to render a number without its reliability.
 *
 * `12-API_SPEC.md` §2 defines `{ data, meta }` where `meta` carries both time axes,
 * the `semantics` that served the request, `quality`, `coherence_mode` and
 * `provenance`. The spec's own justification for making them mandatory is the
 * contract this module enforces:
 *
 * > Quality and provenance travel with **every** response. A consumer cannot render
 * > a number without having been told how reliable it is.
 *
 * "Cannot" is doing real work. {@link parseEnvelope} returns the quality badge as
 * part of the parsed value, so a screen that wants `data` has already been handed
 * the badge — it cannot reach one without the other. A response whose `meta` omits
 * quality yields `UNKNOWN`, which withholds derived values, rather than yielding a
 * silent `OK`.
 */

import { type QualityBadge, type QualityIssue, qualityBadge } from "@/lib/terminal/quality";
import type { TemporalSemantics } from "@/lib/terminal/time";

export interface Provenance {
  readonly buildContextId: string | null;
  readonly contentDigest: string | null;
  readonly featureVersions: Readonly<Record<string, number>> | null;
  readonly observationRefs: readonly string[] | null;
  readonly assembledAt: string | null;
}

export interface EnvelopeMeta {
  readonly marketTime: string | null;
  readonly knowledgeTime: string | null;
  /** Present only on availability-filtered endpoints (`12` §2). */
  readonly decisionTime: string | null;
  readonly semantics: TemporalSemantics | null;
  readonly coherenceMode: string | null;
  readonly provenance: Provenance;
  /** Everything else the route put in `meta`, untouched. */
  readonly raw: Readonly<Record<string, unknown>>;
}

export interface Envelope<T> {
  readonly data: T;
  readonly meta: EnvelopeMeta;
  readonly quality: QualityBadge;
}

export class MalformedEnvelope extends Error {
  constructor(detail: string) {
    super(`response envelope is malformed: ${detail}`);
    this.name = "MalformedEnvelope";
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asSemantics(value: unknown): TemporalSemantics | null {
  return value === "knowledge_at" ||
    value === "market_truth_at" ||
    value === "tradable_information_at"
    ? value
    : null;
}

function parseIssues(value: unknown): QualityIssue[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => {
    const row = asRecord(entry);
    const type = asStringOrNull(row.type) ?? asStringOrNull(row.code) ?? "ISSUE";
    const severity = asStringOrNull(row.severity);
    const detail = asStringOrNull(row.detail) ?? asStringOrNull(row.message) ?? "";
    return {
      code: type,
      message: severity === null ? detail || type : `${severity}: ${detail || type}`,
    };
  });
}

function parseProvenance(meta: Record<string, unknown>): Provenance {
  const p = asRecord(meta.provenance);
  const versions = asRecord(p.feature_versions);
  const numeric: Record<string, number> = {};
  for (const [key, value] of Object.entries(versions)) {
    if (typeof value === "number") numeric[key] = value;
  }
  const refs = Array.isArray(p.observation_refs)
    ? p.observation_refs.filter((r): r is string => typeof r === "string")
    : null;
  return {
    buildContextId:
      asStringOrNull(p.build_context_id) ?? asStringOrNull(meta.build_context_id),
    contentDigest: asStringOrNull(p.content_digest) ?? asStringOrNull(meta.content_digest),
    featureVersions: Object.keys(numeric).length > 0 ? numeric : null,
    observationRefs: refs,
    assembledAt: asStringOrNull(p.assembled_at),
  };
}

/**
 * Parse `{ data, meta }`.
 *
 * `data` is returned as-is and typed by the caller; this layer validates the
 * envelope, not the payload. Payload shape is covered by
 * `frontend/lib/api/dto.ts`, whose fields are checked against the generated
 * contract by `tools/check_frontend_contract.py` — a runtime validator here would
 * duplicate that check and could disagree with it.
 */
export function parseEnvelope<T>(body: unknown): Envelope<T> {
  const root = asRecord(body);
  if (!("data" in root)) {
    throw new MalformedEnvelope("no `data` key; every 12-API_SPEC.md §2 response has one");
  }
  const meta = asRecord(root.meta);
  const quality = asRecord(meta.quality);
  return {
    data: root.data as T,
    meta: {
      marketTime: asStringOrNull(meta.market_time),
      knowledgeTime: asStringOrNull(meta.knowledge_time),
      decisionTime: asStringOrNull(meta.decision_time),
      semantics: asSemantics(meta.semantics),
      coherenceMode: asStringOrNull(meta.coherence_mode),
      provenance: parseProvenance(meta),
      raw: meta,
    },
    // Absent quality becomes UNKNOWN, which withholds derived values. A route that
    // stops sending quality must not start looking healthy.
    quality: qualityBadge(asStringOrNull(quality.status), parseIssues(quality.issues)),
  };
}

/**
 * The axes the response actually answered on, which may differ from those asked for.
 *
 * Screens display these rather than the requested values. When a backend resolves
 * `latest` to a concrete instant, showing the request would tell the user a time
 * that is not the time they are looking at.
 */
export function answeredAxes(meta: EnvelopeMeta): {
  marketTime: string | null;
  knowledgeTime: string | null;
} {
  return { marketTime: meta.marketTime, knowledgeTime: meta.knowledgeTime };
}
