/**
 * Provenance navigation: the canonical chain, and the honest gap.
 *
 * Two traversals are specified, and they are different things.
 *
 * **The metric drill-through** (`13-FRONTEND_IA.md` §7):
 *
 *     value -> feature definition (name, version, formula, units, convention, availability)
 *           -> inputs (the metric values consumed)
 *           -> MarketState (observed_at, coherence mode, quality)
 *           -> raw observations (with observed_at / ingested_at)
 *
 * **The decision chain** (Phase 12 brief §19):
 *
 *     Signal -> Evidence -> Strategy -> TradeIntent -> RiskDecision -> OMS Order
 *            -> Fill -> Position -> P&L
 *
 * Both are built the same way and under the same rule: **a link is either a real
 * reference or an explicit gap.** `oipulse/trading/audit.py` states the backend's
 * version of this — "If a link is missing the answer is *not recorded*, never a
 * reconstruction — a plausible guess in an audit trail is worse than a gap, because
 * a gap is visible." {@link chainStep} produces an unavailable step carrying the
 * reason, never a link to a search page or a prose summary standing in for a
 * reference. Brief §19: "Do not replace evidence with free-text explanations."
 */

import type { Endpoint } from "@/lib/terminal/endpoints";

export type ChainNodeKind =
  | "SIGNAL"
  | "EVIDENCE"
  | "STRATEGY"
  | "TRADE_INTENT"
  | "RISK_DECISION"
  | "OMS_ORDER"
  | "FILL"
  | "POSITION"
  | "PNL"
  | "FEATURE_DEFINITION"
  | "FEATURE_INPUTS"
  | "MARKET_STATE"
  | "RAW_OBSERVATIONS";

export interface ChainStep {
  readonly kind: ChainNodeKind;
  readonly label: string;
  /** The identity being traversed to, when one was recorded. */
  readonly reference: string | null;
  /** In-terminal destination, when the step is reachable. */
  readonly href: string | null;
  /** The backend call that resolves it, for tests and for the network panel. */
  readonly endpoint: Endpoint | null;
  readonly available: boolean;
  /** Why the step cannot be followed. Never null when `available` is false. */
  readonly unavailableReason: string | null;
}

const LABELS: Readonly<Record<ChainNodeKind, string>> = {
  SIGNAL: "Signal",
  EVIDENCE: "Evidence",
  STRATEGY: "Strategy",
  TRADE_INTENT: "Trade intent",
  RISK_DECISION: "Risk decision",
  OMS_ORDER: "OMS order",
  FILL: "Fill",
  POSITION: "Position",
  PNL: "P&L",
  FEATURE_DEFINITION: "Feature definition",
  FEATURE_INPUTS: "Inputs",
  MARKET_STATE: "MarketState",
  RAW_OBSERVATIONS: "Raw observations",
};

/** The sentinel the backend uses; recognised so it renders as a gap, not as an id. */
export const NOT_RECORDED = "not recorded";

export function chainStep(
  kind: ChainNodeKind,
  reference: string | null | undefined,
  resolve: (ref: string) => { href: string; endpoint: Endpoint | null },
  gapReason = "no reference was recorded on the preceding step",
): ChainStep {
  const ref = reference === NOT_RECORDED ? null : (reference ?? null);
  if (ref === null || ref === "") {
    return {
      kind,
      label: LABELS[kind],
      reference: null,
      href: null,
      endpoint: null,
      available: false,
      unavailableReason: gapReason,
    };
  }
  const { href, endpoint } = resolve(ref);
  return {
    kind,
    label: LABELS[kind],
    reference: ref,
    href,
    endpoint,
    available: true,
    unavailableReason: null,
  };
}

/** The decision chain in its canonical order (brief §19). */
export const DECISION_CHAIN_ORDER: readonly ChainNodeKind[] = [
  "SIGNAL",
  "EVIDENCE",
  "STRATEGY",
  "TRADE_INTENT",
  "RISK_DECISION",
  "OMS_ORDER",
  "FILL",
  "POSITION",
  "PNL",
] as const;

/** The metric drill-through in its canonical order (`13` §7). */
export const METRIC_CHAIN_ORDER: readonly ChainNodeKind[] = [
  "FEATURE_DEFINITION",
  "FEATURE_INPUTS",
  "MARKET_STATE",
  "RAW_OBSERVATIONS",
] as const;

export interface ChainSummary {
  readonly steps: readonly ChainStep[];
  readonly resolvedCount: number;
  readonly gapCount: number;
  /** True only when every step resolves. Shown so a partial chain is never read as whole. */
  readonly complete: boolean;
}

export function summariseChain(steps: readonly ChainStep[]): ChainSummary {
  const resolved = steps.filter((s) => s.available).length;
  return {
    steps,
    resolvedCount: resolved,
    gapCount: steps.length - resolved,
    complete: resolved === steps.length && steps.length > 0,
  };
}

/**
 * Assert the chain is presented in its canonical order.
 *
 * Used by the screens rather than by tests alone: a provenance trail rendered out of
 * order stops being a traversal and becomes a list, and the whole point of §19 is
 * that a user can *move through* the chain.
 */
export function inCanonicalOrder(
  steps: readonly ChainStep[],
  order: readonly ChainNodeKind[],
): boolean {
  let cursor = -1;
  for (const step of steps) {
    const index = order.indexOf(step.kind);
    if (index <= cursor) return false;
    cursor = index;
  }
  return true;
}
