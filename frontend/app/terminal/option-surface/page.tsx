"use client";

/**
 * Option Surface — what does the chain look like?
 *
 * `13-FRONTEND_IA.md` §4: "Dense CE ‖ strike ‖ PE grid ... Expiry selector
 * **functional** across all expiries."
 *
 * The expiry selector writes to the URL, and the request is built from the URL, so
 * selecting an expiry necessarily changes the data. That is the legacy
 * `expiries[0]` defect designed out rather than fixed.
 *
 * Staleness is per field, not per row. A leg carries independent `quote_stale`,
 * `oi_stale` and `greeks_stale` flags and they are rendered independently — a
 * fresh price must not vouch for a stale delta.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { MarketStateDto } from "@/lib/api/dto";
import type { LegCell, SurfaceRow } from "@/lib/terminal/screens/optionSurface";
import type { Presented } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { buildSurfaces, selectSurface } from "@/lib/terminal/screens/optionSurface";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { market } from "@/lib/terminal/endpoints";
import { PARAM_EXPIRY, timeQuery } from "@/lib/terminal/urlState";
import { DenseTable, Value } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("option-surface");

/** Render one field of a leg, or an em dash where the leg itself is absent. */
function cellValue<T>(cell: LegCell | null, pick: (c: LegCell) => Presented<T>) {
  if (cell === null) return <span className="text-terminal-muted">—</span>;
  return <Value presented={pick(cell)} />;
}

export default function OptionSurfacePage() {
  const { state } = useViewState();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const underlyingId = state.underlyingId ?? 1;

  const query = useTerminalQuery<MarketStateDto>(
    market.state({ underlying_id: underlyingId, ...timeQuery(state) }),
  );
  const quality = query.data?.quality ?? qualityBadge(undefined);
  const surfaces = query.data ? buildSurfaces(query.data.data) : [];
  const surface = selectSurface(surfaces, state.expiry);

  const selectExpiry = (value: string) => {
    const params = new URLSearchParams(search?.toString() ?? "");
    params.set(PARAM_EXPIRY, value);
    router.replace(`${pathname}?${params.toString()}`);
  };

  return (
    <ScreenFrame
      screen={SCREEN}
      state={state}
      quality={quality}
      toolbar={
        <label className="flex items-center gap-2 font-mono text-xs">
          <span className="text-terminal-muted">EXPIRY</span>
          <select
            className="rounded border border-terminal-border bg-terminal-bg px-2 py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            value={state.expiry ?? "front"}
            onChange={(event) => selectExpiry(event.target.value)}
          >
            <option value="front">front</option>
            <option value="next">next</option>
            {surfaces.map((s) => (
              <option key={s.expiryId} value={s.expiryDate}>
                {s.expiryDate}
              </option>
            ))}
          </select>
          {/* Every expiry in the state is offered, not just the first. */}
          <span className="text-terminal-muted">{surfaces.length} available</span>
        </label>
      }
    >
      <QueryPanel
        query={query}
        label="option chain"
        empty={emptyState("NO_DATA_FOR_PERIOD")}
        isEmpty={() => surfaces.length === 0}
      >
        {() =>
          surface === null ? (
            <p className="text-xs text-terminal-muted">
              No expiry matches the current selection. The state carries{" "}
              {surfaces.length} expiries; this is not an empty chain.
            </p>
          ) : (
            <div className="space-y-3">
              <dl className="flex flex-wrap gap-x-6 gap-y-1 font-mono text-xs">
                <div>
                  <dt className="inline text-terminal-muted">COVERAGE </dt>
                  <dd className="inline">
                    {surface.coverageRatio === null
                      ? "unknown"
                      : surface.coverageRatio.toFixed(2)}
                  </dd>
                </div>
                <div>
                  <dt className="inline text-terminal-muted">MISSING LEGS </dt>
                  <dd className="inline">{surface.missingLegCount}</dd>
                </div>
                <div>
                  <dt className="inline text-terminal-muted">PCR </dt>
                  <dd className="inline">
                    <Value presented={surface.pcr} />
                  </dd>
                </div>
                <div>
                  <dt className="inline text-terminal-muted">ATM </dt>
                  <dd className="inline">{surface.atmStrike ?? "unknown"}</dd>
                </div>
              </dl>

              <DenseTable<SurfaceRow>
                caption={`Option chain for expiry ${surface.expiryDate}: calls, strike, puts`}
                columns={[
                  {
                    key: "c-oi",
                    header: "CE OI",
                    align: "right",
                    render: (r) => cellValue(r.call, (c) => c.oi),
                  },
                  {
                    key: "c-iv",
                    header: "CE IV",
                    unit: "%",
                    align: "right",
                    render: (r) => cellValue(r.call, (c) => c.iv),
                  },
                  {
                    key: "c-delta",
                    header: "CE Δ",
                    align: "right",
                    render: (r) => cellValue(r.call, (c) => c.delta),
                  },
                  {
                    key: "c-ltp",
                    header: "CE LTP",
                    unit: "INR",
                    align: "right",
                    render: (r) => cellValue(r.call, (c) => c.ltp),
                  },
                  {
                    key: "strike",
                    header: "STRIKE",
                    align: "right",
                    render: (r) => (
                      <span className={r.isAtm ? "font-semibold text-accent" : undefined}>
                        {r.strike}
                        {r.isAtm ? <span className="sr-only"> at the money</span> : null}
                      </span>
                    ),
                  },
                  {
                    key: "p-ltp",
                    header: "PE LTP",
                    unit: "INR",
                    align: "right",
                    render: (r) => cellValue(r.put, (c) => c.ltp),
                  },
                  {
                    key: "p-delta",
                    header: "PE Δ",
                    align: "right",
                    render: (r) => cellValue(r.put, (c) => c.delta),
                  },
                  {
                    key: "p-iv",
                    header: "PE IV",
                    unit: "%",
                    align: "right",
                    render: (r) => cellValue(r.put, (c) => c.iv),
                  },
                  {
                    key: "p-oi",
                    header: "PE OI",
                    align: "right",
                    render: (r) => cellValue(r.put, (c) => c.oi),
                  },
                ]}
                rows={surface.rows}
                rowKey={(row) => row.strike}
              />
              <p className="text-xs text-terminal-muted">
                A ◷ marks a value older than the state beside it. Quote, OI and greek
                staleness are tracked separately: a fresh price does not imply fresh greeks.
              </p>
            </div>
          )
        }
      </QueryPanel>
    </ScreenFrame>
  );
}
