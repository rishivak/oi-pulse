/**
 * Option Surface rows. `13-FRONTEND_IA.md` §4.
 *
 * > Dense CE ‖ strike ‖ PE grid: OI, ΔOI, LTP, bid/ask, IV, delta, gamma. Expiry
 * > selector **functional** across all expiries.
 *
 * The one thing this module refuses to simplify is staleness. A leg carries three
 * independent flags — `quote_stale`, `oi_stale`, `greeks_stale` — and they are
 * genuinely independent: a fresh quote says nothing about whether the greeks beside
 * it were recomputed. Collapsing them into one row-level "stale" would let a fresh
 * price vouch for a stale delta, which is exactly the mistake that makes a hedge
 * look right.
 */

import type { ExpiryDto, MarketStateDto, OptionLegDto } from "@/lib/api/dto";
import { type Presented, presentValue } from "@/lib/terminal/quality";

export interface LegCell {
  readonly instrumentId: number;
  readonly ltp: Presented<string>;
  readonly bid: Presented<string>;
  readonly ask: Presented<string>;
  readonly oi: Presented<number>;
  readonly volume: Presented<number>;
  readonly iv: Presented<string>;
  readonly delta: Presented<string>;
  readonly gamma: Presented<string>;
  readonly quoteStale: boolean;
  readonly oiStale: boolean;
  readonly greeksStale: boolean;
  readonly quoteObservedAt: string | null;
  readonly greeksObservedAt: string | null;
}

export interface SurfaceRow {
  readonly strike: string;
  readonly call: LegCell | null;
  readonly put: LegCell | null;
  /** True when the strike is the nearest to spot the backend reported as ATM. */
  readonly isAtm: boolean;
}

function toCell(leg: OptionLegDto): LegCell {
  return {
    instrumentId: leg.instrument_id,
    // Each field carries its own reason: a null LTP and a null delta are absent for
    // different causes, and a single "no data" for the row would say neither.
    ltp: presentValue(leg.ltp, "NO_DATA_FOR_PERIOD", { stale: leg.quote_stale }),
    bid: presentValue(leg.bid, "NO_DATA_FOR_PERIOD", { stale: leg.quote_stale }),
    ask: presentValue(leg.ask, "NO_DATA_FOR_PERIOD", { stale: leg.quote_stale }),
    oi: presentValue(leg.oi, "NO_DATA_FOR_PERIOD", { stale: leg.oi_stale }),
    volume: presentValue(leg.volume, "NO_DATA_FOR_PERIOD", { stale: leg.quote_stale }),
    iv: presentValue(leg.iv, "NOT_COMPUTED", { stale: leg.greeks_stale }),
    delta: presentValue(leg.delta, "NOT_COMPUTED", { stale: leg.greeks_stale }),
    gamma: presentValue(leg.gamma, "NOT_COMPUTED", { stale: leg.greeks_stale }),
    quoteStale: leg.quote_stale,
    oiStale: leg.oi_stale,
    greeksStale: leg.greeks_stale,
    quoteObservedAt: leg.quote_observed_at,
    greeksObservedAt: leg.greeks_observed_at,
  };
}

export interface Surface {
  readonly expiryId: number;
  readonly expiryDate: string;
  readonly rows: readonly SurfaceRow[];
  readonly coverageRatio: number | null;
  readonly missingLegCount: number;
  readonly pcr: Presented<string>;
  readonly atmStrike: string | null;
  readonly totalCallOi: Presented<number>;
  readonly totalPutOi: Presented<number>;
}

/**
 * Build one expiry's grid.
 *
 * Strikes are ordered numerically rather than lexically — string ordering puts
 * 10000 between 1 and 2 — and every strike present on either side gets a row, so a
 * leg that exists only as a put is visible rather than dropped for having no pair.
 */
export function buildSurface(expiry: ExpiryDto): Surface {
  const byStrike = new Map<string, { call?: OptionLegDto; put?: OptionLegDto }>();
  for (const leg of expiry.legs) {
    const strike = leg.strike ?? "";
    if (strike === "") continue;
    const slot = byStrike.get(strike) ?? {};
    if (leg.option_type === "CE" || leg.option_type === "CALL") slot.call = leg;
    else slot.put = leg;
    byStrike.set(strike, slot);
  }
  const atm = expiry.aggregates.atm_strike;
  const rows: SurfaceRow[] = [...byStrike.entries()]
    .sort((a, b) => Number(a[0]) - Number(b[0]))
    .map(([strike, slot]) => ({
      strike,
      call: slot.call ? toCell(slot.call) : null,
      put: slot.put ? toCell(slot.put) : null,
      isAtm: atm !== null && Number(strike) === Number(atm),
    }));
  return {
    expiryId: expiry.expiry_id,
    expiryDate: expiry.expiry_date,
    rows,
    coverageRatio: expiry.coverage_ratio,
    missingLegCount: expiry.missing_leg_count,
    pcr: presentValue(expiry.aggregates.pcr, "NOT_COMPUTED"),
    atmStrike: atm,
    totalCallOi: presentValue(expiry.aggregates.total_call_oi, "NO_DATA_FOR_PERIOD"),
    totalPutOi: presentValue(expiry.aggregates.total_put_oi, "NO_DATA_FOR_PERIOD"),
  };
}

/**
 * Every expiry in the state, in date order.
 *
 * `13` §4 names the defect this replaces: "The legacy inert `expiries[0]` selector
 * is the specific defect being designed out." Returning all of them, and selecting
 * from URL state, is what makes the selector functional rather than decorative.
 */
export function buildSurfaces(state: MarketStateDto): readonly Surface[] {
  return [...state.expiries]
    .sort((a, b) => a.expiry_date.localeCompare(b.expiry_date))
    .map(buildSurface);
}

export function selectSurface(
  surfaces: readonly Surface[],
  expiry: string | null,
): Surface | null {
  if (surfaces.length === 0) return null;
  if (expiry === null || expiry === "front") return surfaces[0];
  if (expiry === "next") return surfaces[1] ?? null;
  return (
    surfaces.find((s) => s.expiryDate === expiry || String(s.expiryId) === expiry) ?? null
  );
}
