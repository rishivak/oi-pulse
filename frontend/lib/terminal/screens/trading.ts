/**
 * Paper trading, risk and OMS presentation.
 *
 * Three disciplines, each from a different document, meet on these screens.
 *
 * **Paper only, and never assumed.** `13-FRONTEND_IA.md` §6 requires the badge to
 * be "persistent and unmissable"; `lib/terminal/mode.ts` refuses order entry unless
 * the response states `mode: PAPER` with `live_execution_available: false`.
 *
 * **Approval is a server output.** Brief §13: "Do not let users directly manufacture
 * approvals from the UI." {@link riskPreview} renders a decision the server made;
 * there is no function here that constructs one, and `lib/terminal/endpoints.ts` has
 * no approve route to post it to.
 *
 * **Local and provider views stay separate columns.** Brief §14: "Do not invent
 * provider information that does not exist." {@link omsRow} keeps `state` and
 * `provider_status` apart and reports provider identity through
 * `lib/terminal/states.ts`, which distinguishes "no venue was contacted" from "no
 * id was recorded".
 */

import type { AuditChainDto, FillDto, OrderDto, OrderEventDto } from "@/lib/api/dto";
import { type Endpoint, paperTrading, risk } from "@/lib/terminal/endpoints";
import { type ChainStep, chainStep, summariseChain } from "@/lib/terminal/provenance";
import {
  type ProviderIdentityBadge,
  type StateBadge,
  blocksInstrument,
  cancellable,
  orderStateBadge,
  providerIdentity,
} from "@/lib/terminal/states";

export interface OrderRow {
  readonly orderId: string;
  readonly intentId: string;
  readonly instrumentId: number;
  readonly side: string;
  readonly quantity: number;
  readonly filledQuantity: number;
  readonly remainingQuantity: number;
  readonly averageFillPrice: string | null;
  readonly localState: StateBadge;
  /** The venue's own word, kept distinct from our state. Null when it never spoke. */
  readonly providerStatus: string | null;
  readonly providerIdentity: ProviderIdentityBadge;
  readonly venue: string;
  readonly mode: string;
  readonly isTerminal: boolean;
  readonly isUnresolved: boolean;
  /** No action may be offered while this holds. */
  readonly blocked: boolean;
  readonly canCancel: boolean;
  readonly rejectReason: string | null;
  readonly knowledgeTime: string | null;
  readonly decisionTime: string | null;
}

export function omsRow(order: OrderDto): OrderRow {
  const blocked = blocksInstrument(order.state);
  return {
    orderId: order.order_id,
    intentId: order.intent_id,
    instrumentId: order.instrument_id,
    side: order.side,
    quantity: order.quantity,
    filledQuantity: order.filled_quantity,
    remainingQuantity: order.remaining_quantity,
    averageFillPrice: order.average_fill_price,
    localState: orderStateBadge(order.state),
    providerStatus: order.provider_status,
    providerIdentity: providerIdentity(order.provider_order_id, order.mode),
    venue: order.venue,
    mode: order.mode,
    isTerminal: order.is_terminal,
    isUnresolved: order.is_unresolved,
    blocked,
    // Both must agree: a state that permits cancelling is not enough if the venue
    // identity needed to send the cancel was never recorded.
    canCancel:
      cancellable(order.state) &&
      (order.mode === "PAPER" || order.provider_order_id !== null),
    rejectReason: order.reject_reason,
    knowledgeTime: order.knowledge_time,
    decisionTime: order.decision_time,
  };
}

/**
 * Which instruments are blocked for a strategy, and by which order.
 *
 * `11-TRADING.md` §5: "an UNKNOWN order blocks its instrument for its strategy".
 * Keyed on the pair, not on the instrument alone — a different strategy's unknown
 * order does not block this one, and blocking it would quietly halt unrelated work.
 */
export function blockedInstruments(
  orders: readonly OrderDto[],
): Map<string, readonly string[]> {
  const blocked = new Map<string, string[]>();
  for (const order of orders) {
    if (!blocksInstrument(order.state)) continue;
    const key = `${order.strategy_id ?? "—"}:${order.instrument_id}`;
    const existing = blocked.get(key) ?? [];
    existing.push(order.order_id);
    blocked.set(key, existing);
  }
  return blocked;
}

export function isBlockedFor(
  blocked: ReadonlyMap<string, readonly string[]>,
  strategyId: string | null,
  instrumentId: number,
): boolean {
  return blocked.has(`${strategyId ?? "—"}:${instrumentId}`);
}

export interface OrderEventRow {
  readonly sequence: number;
  readonly fromState: string | null;
  readonly toState: string;
  readonly trigger: string;
  readonly occurredAt: string;
}

/** Transitions in sequence order, with any gap made visible rather than closed. */
export function eventRows(events: readonly OrderEventDto[]): {
  readonly rows: readonly OrderEventRow[];
  readonly missingSequences: readonly number[];
} {
  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);
  const missing: number[] = [];
  for (let i = 1; i < sorted.length; i += 1) {
    for (let gap = sorted[i - 1].sequence + 1; gap < sorted[i].sequence; gap += 1) {
      missing.push(gap);
    }
  }
  return {
    rows: sorted.map((e) => ({
      sequence: e.sequence,
      fromState: e.from_state,
      toState: e.to_state,
      trigger: e.trigger,
      occurredAt: e.occurred_at,
    })),
    missingSequences: missing,
  };
}

export interface FillRow {
  readonly instrumentId: number;
  readonly side: string;
  readonly quantity: number;
  readonly requestedQuantity: number;
  readonly isPartial: boolean;
  readonly price: string;
  readonly referencePrice: string | null;
  readonly priceSource: string;
  readonly slippagePerUnit: string | null;
  /** Shown, because a P&L resting on an assumed spread is a different number. */
  readonly assumptionBased: boolean;
  readonly filledAt: string;
}

export function fillRow(fill: FillDto): FillRow {
  return {
    instrumentId: fill.instrument_id,
    side: fill.side,
    quantity: fill.quantity,
    requestedQuantity: fill.requested_quantity,
    isPartial: fill.is_partial,
    price: fill.price,
    referencePrice: fill.reference_price,
    priceSource: fill.price_source,
    slippagePerUnit: fill.slippage_per_unit,
    assumptionBased: fill.assumption_based,
    filledAt: fill.filled_at,
  };
}

// ---------------------------------------------------------------- provenance

/**
 * The order's decision chain, assembled from recorded references only.
 *
 * Positions and P&L are reached through the portfolio screen rather than through an
 * order-scoped route, so those two steps resolve to portfolio links. Where a
 * reference is absent the step is a stated gap — see `lib/terminal/provenance.ts`.
 */
export function orderChain(accountId: string, order: OrderDto): readonly ChainStep[] {
  const signalHref = (ref: string) => ({
    href: `/terminal/signals?signal_id=${encodeURIComponent(ref)}`,
    endpoint: null as Endpoint | null,
  });
  return [
    chainStep("SIGNAL", order.signal_id, signalHref, "this order records no signal"),
    chainStep(
      "STRATEGY",
      order.strategy_id === null
        ? null
        : `${order.strategy_id}@v${order.strategy_version ?? "?"}`,
      (ref) => ({ href: `/terminal/backtest?strategy=${encodeURIComponent(ref)}`, endpoint: null }),
      "this order records no strategy",
    ),
    chainStep(
      "TRADE_INTENT",
      order.intent_id,
      (ref) => ({
        href: `/terminal/paper-trading?intent_id=${encodeURIComponent(ref)}`,
        endpoint: paperTrading.intent(accountId, ref),
      }),
    ),
    chainStep(
      "RISK_DECISION",
      order.authorizing_risk_decision_id === null
        ? null
        : String(order.authorizing_decision_sequence ?? ""),
      (ref) => ({
        href: `/terminal/risk?intent_id=${encodeURIComponent(order.intent_id)}`,
        endpoint: risk.decision(order.intent_id, Number(ref)),
      }),
      "no risk decision is recorded against this order",
    ),
    chainStep("OMS_ORDER", order.order_id, (ref) => ({
      href: `/terminal/risk?order_id=${encodeURIComponent(ref)}`,
      endpoint: paperTrading.order(accountId, ref),
    })),
    chainStep(
      "FILL",
      order.filled_quantity > 0 ? order.order_id : null,
      (ref) => ({
        href: `/terminal/paper-trading?order_id=${encodeURIComponent(ref)}`,
        endpoint: paperTrading.fills(accountId),
      }),
      "nothing has filled on this order",
    ),
    chainStep("POSITION", order.instrument_id ? String(order.instrument_id) : null, (ref) => ({
      href: `/terminal/portfolio?account_id=${encodeURIComponent(accountId)}&instrument_id=${ref}`,
      endpoint: null,
    })),
    chainStep("PNL", accountId, (ref) => ({
      href: `/terminal/portfolio?account_id=${encodeURIComponent(ref)}`,
      endpoint: paperTrading.pnl(ref),
    })),
  ];
}

export function auditAnswers(chain: AuditChainDto): readonly {
  readonly question: string;
  readonly answer: string;
  readonly recorded: boolean;
}[] {
  return Object.entries(chain.answers).map(([question, value]) => {
    const answer = value === null || value === undefined ? "not recorded" : String(value);
    return {
      question: question.replaceAll("_", " "),
      answer,
      recorded: answer !== "not recorded",
    };
  });
}

export function chainSummary(steps: readonly ChainStep[]) {
  return summariseChain(steps);
}

// ---------------------------------------------------------------------- risk

export type LimitStatus = "PASSED" | "BREACHED" | "NOT_CONFIGURED" | "NOT_EVALUABLE";

export const LIMIT_STATUSES: readonly LimitStatus[] = [
  "PASSED",
  "BREACHED",
  "NOT_CONFIGURED",
  "NOT_EVALUABLE",
] as const;

export interface LimitSummary {
  readonly counts: Readonly<Record<LimitStatus, number>>;
  /**
   * Whether the account can be described as within its limits at all.
   *
   * False whenever anything was not configured or not evaluable.
   * `oipulse/trading/risk/serialisation.py`: "A status page that showed only a
   * green count would report an account with twenty unconfigured limits as
   * comfortably within all of them."
   */
  readonly fullyEvaluated: boolean;
  readonly caveat: string | null;
}

export function limitSummary(meta: Readonly<Record<string, unknown>>): LimitSummary {
  const read = (key: string): number => {
    const value = meta[key];
    return typeof value === "number" ? value : 0;
  };
  const counts = {
    PASSED: read("passed"),
    BREACHED: read("breached"),
    NOT_CONFIGURED: read("not_configured"),
    NOT_EVALUABLE: read("not_evaluable"),
  };
  const incomplete = counts.NOT_CONFIGURED + counts.NOT_EVALUABLE;
  return {
    counts,
    fullyEvaluated: incomplete === 0,
    caveat:
      incomplete === 0
        ? null
        : `${counts.NOT_CONFIGURED} limits are not configured and ` +
          `${counts.NOT_EVALUABLE} could not be evaluated. "Within limits" does not ` +
          `describe them, and they are not counted as passing.`,
  };
}

export interface RiskPreview {
  readonly verdict: string;
  readonly approved: boolean;
  readonly evaluated: boolean;
  readonly reason: string | null;
  /** Present only when the caller supplied a time; a decision does not expire itself. */
  readonly authorizationStatus: string | null;
  readonly actionable: boolean | null;
  readonly breachCount: number;
  readonly unevaluableCount: number;
  readonly policy: string | null;
}

/**
 * Render a decision the server produced.
 *
 * `approved` is read from `meta.is_approved`, never inferred from the absence of
 * breaches: a decision with zero breaches and `evaluated: false` is not an approval,
 * and treating it as one is precisely the manufactured approval §13 prohibits.
 */
export function riskPreview(
  data: Readonly<Record<string, unknown>>,
  meta: Readonly<Record<string, unknown>>,
): RiskPreview {
  return {
    verdict: typeof data.verdict === "string" ? data.verdict : "UNKNOWN",
    approved: meta.is_approved === true,
    evaluated: meta.evaluated === true,
    reason: typeof data.reason === "string" ? data.reason : null,
    authorizationStatus:
      typeof meta.authorization_status === "string" ? meta.authorization_status : null,
    actionable: typeof meta.actionable === "boolean" ? meta.actionable : null,
    breachCount: typeof meta.breach_count === "number" ? meta.breach_count : 0,
    unevaluableCount:
      typeof meta.unevaluable_count === "number" ? meta.unevaluable_count : 0,
    policy: typeof meta.policy === "string" ? meta.policy : null,
  };
}

/** Whether the terminal may offer to submit an intent this decision covers. */
export function submissionPermitted(preview: RiskPreview): boolean {
  if (!preview.evaluated || !preview.approved) return false;
  // An approval with a stated authorization status must still be actionable:
  // `11` §11 adds `approved_until` so a stale approval cannot be submitted later.
  return preview.actionable !== false;
}
