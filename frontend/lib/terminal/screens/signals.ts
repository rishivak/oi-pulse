/**
 * Signals and alerts presentation.
 *
 * `13-FRONTEND_IA.md` §4: the detail view shows "supporting and contradicting
 * evidence, each traceable to metric → state → observation, plus the invalidation
 * condition". Phase 12 brief §9 adds the separation that must not be blurred:
 *
 *     Signal truth  ≠  Alert delivery state
 *
 * Nothing in this module derives a signal's status, strength or lifecycle: those
 * arrive decided. Acknowledgement is modelled as a property of the *occurrence*,
 * and {@link acknowledgementEffect} states in the UI what
 * `oipulse/api/alerts.py` states on the wire with `meta.signal_unchanged`.
 */

import type { AlertOccurrenceDto, AlertRuleDto, EvidenceDto, SignalDto } from "@/lib/api/dto";
import { type StateBadge, signalActionable, signalStatusBadge, signalTerminal } from "@/lib/terminal/states";

export interface EvidenceSplit {
  readonly supporting: readonly EvidenceDto[];
  /**
   * `null` means the rule assessed contradiction and found none. An empty array
   * would be the same shape as "we did not look", and `08`'s
   * `contradiction_assessment` exists precisely to keep those apart.
   */
  readonly contradicting: readonly EvidenceDto[] | null;
  readonly assessedNoContradiction: boolean;
}

export function splitEvidence(
  signal: SignalDto,
  supporting: readonly EvidenceDto[] = [],
): EvidenceSplit {
  const assessment = signal.contradiction_assessment;
  if (assessment === "NONE_OBSERVED") {
    return { supporting, contradicting: null, assessedNoContradiction: true };
  }
  return { supporting, contradicting: assessment, assessedNoContradiction: false };
}

export interface SignalRow {
  readonly signalId: string;
  readonly type: string;
  readonly ruleVersion: number;
  readonly strength: string | null;
  readonly badge: StateBadge;
  readonly actionable: boolean;
  readonly terminal: boolean;
  readonly marketTime: string | null;
  readonly knowledgeTime: string | null;
  /** Derived values have an availability; this is it, and it is shown. */
  readonly availableAt: string | null;
  readonly expiresAt: string | null;
  readonly invalidationCondition: string | null;
  readonly qualityStatus: string;
  readonly contentDigest: string;
}

export function signalRow(signal: SignalDto): SignalRow {
  return {
    signalId: signal.signal_id,
    type: signal.type,
    ruleVersion: signal.rule_version,
    strength: signal.strength,
    badge: signalStatusBadge(signal.status),
    actionable: signalActionable(signal.status),
    terminal: signalTerminal(signal.status),
    marketTime: signal.market_time,
    knowledgeTime: signal.knowledge_time,
    availableAt: signal.available_at,
    expiresAt: signal.expires_at,
    invalidationCondition: signal.invalidation_condition,
    qualityStatus: signal.quality_status,
    contentDigest: signal.content_digest,
  };
}

/**
 * The Command Center's contradictions panel (`13` §3).
 *
 * > A terminal that only shows confirming information trains overconfidence;
 * > surfacing tension between signals is the single most valuable thing this screen
 * > does.
 *
 * The statements come from the rules' own contradicting evidence. Nothing is
 * inferred by comparing signals to each other — that would be analysis, and
 * analysis belongs in the backend (`13` §1.3).
 */
export interface Contradiction {
  readonly signalId: string;
  readonly signalType: string;
  readonly statement: string;
  readonly metricRef: string | null;
  readonly observationRefs: readonly string[];
}

export function collectContradictions(signals: readonly SignalDto[]): Contradiction[] {
  const out: Contradiction[] = [];
  for (const signal of signals) {
    const assessment = signal.contradiction_assessment;
    if (assessment === "NONE_OBSERVED") continue;
    for (const evidence of assessment) {
      out.push({
        signalId: signal.signal_id,
        signalType: signal.type,
        statement: evidence.statement,
        metricRef: evidence.metric_ref,
        observationRefs: evidence.observation_refs,
      });
    }
  }
  return out;
}

// -------------------------------------------------------------------- alerts

export interface AlertRuleRow {
  readonly id: string;
  readonly signalType: string;
  readonly channel: string;
  readonly severity: string;
  readonly onStatuses: readonly string[];
  readonly minStrength: string;
  readonly enabled: boolean;
  readonly destination: string | null;
  readonly configDigest: string;
}

export function alertRuleRow(rule: AlertRuleDto): AlertRuleRow {
  return {
    id: rule.id,
    signalType: rule.signal_type,
    channel: rule.channel,
    severity: rule.severity,
    onStatuses: rule.on_statuses,
    minStrength: rule.min_strength,
    enabled: rule.enabled,
    // Echoed as configured. `oipulse/alerts/serialisation.py` notes this "is never a
    // credential -- it names a destination that deployment configuration resolves".
    destination: rule.destination,
    configDigest: rule.config_digest,
  };
}

export type DeliveryState = "DELIVERED" | "PENDING" | "FAILED" | "ACKNOWLEDGED";

export interface OccurrenceRow {
  readonly occurrenceId: string;
  readonly ruleId: string;
  readonly signalId: string;
  readonly signalType: string;
  readonly severity: string;
  readonly channel: string;
  readonly triggeredAt: string | null;
  readonly availableAt: string | null;
  readonly deliveryState: DeliveryState;
  readonly attempts: number;
  readonly acknowledgedAt: string | null;
  readonly acknowledgedBy: string | null;
}

export function occurrenceRow(occurrence: AlertOccurrenceDto): OccurrenceRow {
  const failed =
    !occurrence.delivered &&
    occurrence.attempts.length > 0 &&
    occurrence.attempts.every((a) => a.status !== "DELIVERED");
  const state: DeliveryState = occurrence.acknowledged_at
    ? "ACKNOWLEDGED"
    : occurrence.delivered
      ? "DELIVERED"
      : failed
        ? "FAILED"
        : "PENDING";
  return {
    occurrenceId: occurrence.occurrence_id,
    ruleId: occurrence.rule_id,
    signalId: occurrence.signal_id,
    signalType: occurrence.signal_type,
    severity: occurrence.severity,
    channel: occurrence.channel,
    triggeredAt: occurrence.triggered_at,
    availableAt: occurrence.available_at,
    deliveryState: state,
    attempts: occurrence.attempts.length,
    acknowledgedAt: occurrence.acknowledged_at,
    acknowledgedBy: occurrence.acknowledged_by,
  };
}

/** What acknowledging changes, and what it does not. Shown beside the control. */
export const ACKNOWLEDGEMENT_EFFECT =
  "Acknowledging records that you saw the alert. It is delivery state only: the " +
  "signal's status, strength and evidence are unchanged, and a later query over " +
  "signals returns exactly what it would have returned before.";

export function acknowledgementEffect(meta: Readonly<Record<string, unknown>>): {
  readonly signalUnchanged: boolean;
  readonly notice: string;
} {
  return {
    // Reported by the backend; not assumed. If a future change made acknowledgement
    // touch signal truth, this would stop claiming it had not.
    signalUnchanged: meta.signal_unchanged === true,
    notice: ACKNOWLEDGEMENT_EFFECT,
  };
}

/** A dry run must be visibly a dry run (`13` §4: "dry-run against historical state"). */
export function dryRunNotice(meta: Readonly<Record<string, unknown>>): string {
  const persisted = meta.persisted === true;
  const delivered = meta.delivered === true;
  if (!persisted && !delivered) {
    return "Dry run. Nothing was persisted and nothing was delivered.";
  }
  return (
    `This run reported persisted=${String(persisted)} and delivered=${String(delivered)}. ` +
    `It was not a dry run.`
  );
}
