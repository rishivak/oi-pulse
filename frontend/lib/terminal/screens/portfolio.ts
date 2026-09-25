/**
 * Portfolio presentation.
 *
 * `13-FRONTEND_IA.md` §6 and Phase 12 brief §15. The rule that shapes every
 * function here is that a total is never shown without what qualifies it: the
 * Phase 11 serializer puts `is_complete` and the unvalued instruments beside every
 * figure because "omitting either when it happens to be favourable is how a partial
 * book comes to read as a whole one".
 *
 * Nothing recomputes P&L (brief §15). Every number below is read; the functions
 * decide what is *shown beside* it, not what it is.
 */

import type {
  PortfolioPositionDto,
  PortfolioSnapshotDto,
  PositionReconciliationRunDto,
  ValuationResultDto,
} from "@/lib/api/dto";
import { type Presented, absent, presentValue } from "@/lib/terminal/quality";
import { type StateBadge, discrepancyBadge, needsAttention } from "@/lib/terminal/states";

export interface CompletenessNotice {
  readonly complete: boolean;
  readonly unvaluedCount: number;
  readonly unvaluedInstruments: readonly number[];
  /** Shown adjacent to every total this qualifies. Never a footnote. */
  readonly text: string | null;
}

export function completeness(
  isComplete: boolean,
  unvalued: readonly number[],
): CompletenessNotice {
  if (isComplete) {
    return { complete: true, unvaluedCount: 0, unvaluedInstruments: [], text: null };
  }
  return {
    complete: false,
    unvaluedCount: unvalued.length,
    unvaluedInstruments: unvalued,
    text:
      `${unvalued.length} position${unvalued.length === 1 ? "" : "s"} could not be ` +
      `valued and are excluded from these totals: ${unvalued.join(", ")}. The figures ` +
      `describe part of the book.`,
  };
}

export interface PositionRow {
  readonly instrumentId: number | null;
  readonly underlyingId: number | null;
  readonly expiryId: number | null;
  readonly quantity: number;
  readonly averagePrice: Presented<string>;
  readonly costBasis: Presented<string>;
  /** Stated, never assumed: FIFO, LIFO and average cost give different numbers. */
  readonly costBasisMethod: string;
  readonly realizedPnl: string;
  readonly fees: string;
  readonly status: string;
  readonly closed: boolean;
  /** A position with no economics cannot be valued, and says so. */
  readonly economicsKnown: boolean;
  readonly openedAt: string | null;
  readonly lastFillAt: string | null;
  readonly fillsApplied: number;
}

export function positionRow(
  position: PortfolioPositionDto,
  instrumentId: number | null = null,
): PositionRow {
  return {
    instrumentId,
    underlyingId: position.underlying_id,
    expiryId: position.expiry_id,
    quantity: position.quantity,
    averagePrice: presentValue(position.average_price, "NOT_COMPUTED"),
    costBasis: presentValue(position.cost_basis, "NOT_COMPUTED"),
    costBasisMethod: position.cost_basis_method,
    realizedPnl: position.realized_pnl,
    fees: position.fees,
    status: position.status,
    closed: position.quantity === 0,
    economicsKnown: position.economics !== null,
    openedAt: position.opened_at,
    lastFillAt: position.last_fill_at,
    fillsApplied: position.fills_applied,
  };
}

export interface ValuationSummary {
  readonly marketTime: string;
  readonly knowledgeTime: string;
  readonly totalMarketValue: Presented<string>;
  readonly totalUnrealizedPnl: Presented<string>;
  readonly grossExposure: Presented<string>;
  readonly netExposure: Presented<string>;
  readonly completeness: CompletenessNotice;
  readonly stateQuality: string | null;
  readonly marketStateRef: string | null;
}

/**
 * Totals, each carrying its completeness.
 *
 * When the valuation is incomplete the totals are still shown — suppressing them
 * would hide the part of the book that *is* known — but the notice travels with
 * them, and `presentValue` marks them stale so a component cannot render them in
 * the same weight as a complete figure.
 */
export function valuationSummary(valuation: ValuationResultDto): ValuationSummary {
  const notice = completeness(valuation.is_complete, valuation.unvalued_instruments);
  const qualify = (value: string) =>
    presentValue(value, "NOT_COMPUTED", { stale: !valuation.is_complete });
  return {
    marketTime: valuation.market_time,
    knowledgeTime: valuation.knowledge_time,
    totalMarketValue: qualify(valuation.total_market_value),
    totalUnrealizedPnl: qualify(valuation.total_unrealized_pnl),
    grossExposure: qualify(valuation.gross_exposure),
    netExposure: qualify(valuation.net_exposure),
    completeness: notice,
    stateQuality: valuation.state_quality,
    marketStateRef: valuation.market_state_ref,
  };
}

export interface MarginReading {
  readonly utilisation: Presented<string>;
  /** Which model produced it. Absent basis means the number means nothing. */
  readonly basis: string | null;
}

/**
 * Margin utilisation with its basis.
 *
 * Phase 11 returns `null` rather than `0` when margin is unknown, and this keeps
 * that distinction: a zero utilisation bar and an unknown one look identical, and
 * the first says there is headroom while the second says nothing at all.
 */
export function marginReading(snapshot: PortfolioSnapshotDto): MarginReading {
  if (snapshot.margin_utilisation === null) {
    return {
      utilisation: absent(
        "NOT_COMPUTED",
        "No margin model produced a figure. This is not zero utilisation.",
      ),
      basis: snapshot.margin_basis,
    };
  }
  return {
    utilisation: presentValue(snapshot.margin_utilisation, "NOT_COMPUTED"),
    basis: snapshot.margin_basis,
  };
}

export interface ReturnReading {
  readonly value: Presented<string>;
  /** `SIMPLE_PERIOD`. Neither TWR nor MWR exists, and the label says which. */
  readonly methodology: string;
  readonly caveat: string;
}

export function returnReading(snapshot: PortfolioSnapshotDto): ReturnReading {
  return {
    value: presentValue(snapshot.returns.simple_period_return, "NOT_COMPUTED"),
    methodology: snapshot.returns.return_methodology,
    caveat:
      "A simple period return over starting capital. It is not time-weighted and " +
      "not money-weighted, so it is not comparable with a TWR or MWR figure.",
  };
}

export interface GreeksReading {
  readonly delta: Presented<string>;
  readonly gamma: Presented<string>;
  readonly vega: Presented<string>;
  readonly theta: Presented<string>;
  readonly complete: boolean;
  readonly coverage: string;
}

/**
 * Portfolio greeks, with the share of the book they cover.
 *
 * Greeks are supplied to Phase 11, never computed by it, so a snapshot commonly
 * covers fewer positions than it holds. Showing a delta without saying how much of
 * the book it describes invites it to be read as the whole exposure.
 */
export function greeksReading(snapshot: PortfolioSnapshotDto): GreeksReading {
  const g = snapshot.greeks;
  const partial = !g.is_complete;
  const qualify = (value: string | null) =>
    presentValue(value, "NOT_COMPUTED", { stale: partial });
  return {
    delta: qualify(g.delta),
    gamma: qualify(g.gamma),
    vega: qualify(g.vega),
    theta: qualify(g.theta),
    complete: g.is_complete,
    coverage: `${g.positions_included} of ${g.positions_total} positions`,
  };
}

// ------------------------------------------------------- position reconciliation

export interface DiscrepancyRow {
  readonly instrumentId: number;
  readonly badge: StateBadge;
  readonly resolution: string;
  readonly localQuantity: number | null;
  readonly providerQuantity: number | null;
  readonly providerAveragePrice: string | null;
  readonly detail: string;
  readonly needsAttention: boolean;
}

export function discrepancyRows(
  run: PositionReconciliationRunDto,
): readonly DiscrepancyRow[] {
  return run.discrepancies.map((d) => ({
    instrumentId: d.instrument_id,
    badge: discrepancyBadge(d.kind),
    resolution: d.resolution,
    localQuantity: d.local_quantity,
    providerQuantity: d.provider_quantity,
    providerAveragePrice: d.provider_average_price,
    detail: d.detail,
    needsAttention: d.needs_attention || needsAttention(d.resolution),
  }));
}

export interface ReconciliationVerdict {
  readonly clean: boolean;
  readonly needsAttention: boolean;
  readonly label: string;
  readonly glyph: string;
  readonly detail: string;
}

export function reconciliationVerdict(
  run: PositionReconciliationRunDto,
): ReconciliationVerdict {
  if (run.is_clean) {
    return {
      clean: true,
      needsAttention: false,
      label: "CLEAN",
      glyph: "●",
      detail: `${run.matched} positions matched.`,
    };
  }
  return {
    clean: false,
    needsAttention: run.needs_attention,
    label: "DISCREPANCIES",
    glyph: "⚠",
    detail:
      `${run.mismatched} of ${run.matched + run.mismatched} positions differ. ` +
      `${run.corrections_applied} correction${run.corrections_applied === 1 ? "" : "s"} ` +
      `were applied; the rest are recorded and await a decision, because no other ` +
      `corrective action is authorised.`,
  };
}
