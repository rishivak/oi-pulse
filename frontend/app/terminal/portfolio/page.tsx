"use client";

/**
 * Portfolio — what do I hold, and how is it performing?
 *
 * `13-FRONTEND_IA.md` §6: "Positions with greeks, realized/unrealized P&L,
 * exposure, margin, drawdown. Attribution ... **with the residual shown**."
 *
 * Nothing here computes P&L (brief §15). The residual is rendered by
 * `AttributionTable`, which appends it unconditionally, and the completeness
 * notice travels with every total it qualifies rather than sitting in a footnote.
 *
 * Three absences are shown as absences rather than as zeros: margin when no model
 * produced a figure, greeks for positions the snapshot could not cover, and the
 * return methodology, which is `SIMPLE_PERIOD` and says so because it is not
 * comparable with a TWR or MWR number.
 */

import type {
  AttributionResultDto,
  PortfolioPositionDto,
  PortfolioSnapshotDto,
} from "@/lib/api/dto";
import type { AttributionInput } from "@/lib/terminal/attribution";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import {
  greeksReading,
  marginReading,
  positionRow,
  returnReading,
  valuationSummary,
} from "@/lib/terminal/screens/portfolio";
import { portfolio } from "@/lib/terminal/endpoints";
import { timeQuery } from "@/lib/terminal/urlState";
import { AttributionTable } from "@/components/terminal/AttributionTable";
import { DenseTable, Instant, Value } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("portfolio");

/** Map the wire shape onto the display input. No arithmetic; `residual` is read. */
function toAttributionInput(dto: AttributionResultDto): AttributionInput {
  return {
    bucket: dto.bucket,
    bucketId: dto.bucket_id,
    totalPnl: dto.total_pnl,
    explained: dto.explained,
    residual: dto.residual,
    residualFraction: dto.residual_fraction,
    reconciles: dto.reconciles,
    method: dto.method,
    methodVersion: dto.method_version,
    components: dto.components.map((c) => ({
      component: c.component,
      amount: c.amount,
      uncomputed: c.uncomputed,
    })),
    uncomputedComponents: dto.uncomputed_components,
  };
}

export default function PortfolioPage() {
  const { state } = useViewState();
  const accountId = state.accountId;
  const times = timeQuery(state);

  const snapshotQuery = useTerminalQuery<PortfolioSnapshotDto>(
    accountId === null ? null : portfolio.summary({ account_id: accountId, ...times }),
  );
  const positionsQuery = useTerminalQuery<readonly PortfolioPositionDto[]>(
    accountId === null
      ? null
      : portfolio.positions({ account_id: accountId, include_closed: true, ...times }),
  );
  const attributionQuery = useTerminalQuery<AttributionResultDto>(
    accountId === null ? null : portfolio.attribution({ account_id: accountId, ...times }),
  );

  const quality = snapshotQuery.data?.quality ?? qualityBadge(undefined);

  if (accountId === null) {
    return (
      <ScreenFrame screen={SCREEN} state={state} quality={quality}>
        <p className="text-xs text-terminal-muted">
          No account is in context. Add <code>?account_id=…</code> to the URL, or reach
          this screen from Paper Trading, which carries the account across.
        </p>
      </ScreenFrame>
    );
  }

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      <div className="space-y-5">
        <QueryPanel
          query={snapshotQuery}
          label="portfolio snapshot"
          empty={emptyState("NO_DATA_FOR_PERIOD")}
        >
          {(envelope) => {
            const snapshot = envelope.data;
            const valuation = valuationSummary(snapshot.valuation);
            const margin = marginReading(snapshot);
            const returns = returnReading(snapshot);
            const greeks = greeksReading(snapshot);
            return (
              <div className="space-y-3">
                {valuation.completeness.text ? (
                  <p
                    className="rounded border border-amber-700 bg-amber-950 p-2 text-xs text-amber-200"
                    role="note"
                  >
                    <span aria-hidden="true">⚠ </span>
                    {valuation.completeness.text}
                  </p>
                ) : null}

                <section
                  aria-label="Totals"
                  className="rounded border border-terminal-border p-3"
                >
                  <h2 className="font-mono text-xs text-terminal-muted">
                    TOTALS AT <Instant iso={snapshot.market_time} /> · KNOWLEDGE{" "}
                    <Instant iso={snapshot.knowledge_time} />
                  </h2>
                  <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs sm:grid-cols-4">
                    <div>
                      <dt className="text-terminal-muted">EQUITY</dt>
                      <dd>{snapshot.equity}</dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">CASH</dt>
                      <dd>{snapshot.cash}</dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">MARKET VALUE</dt>
                      <dd>
                        <Value presented={valuation.totalMarketValue} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">UNREALIZED</dt>
                      <dd>
                        <Value presented={valuation.totalUnrealizedPnl} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">REALIZED</dt>
                      <dd>{snapshot.realized_pnl}</dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">FEES</dt>
                      <dd>{snapshot.fees}</dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">GROSS EXPOSURE</dt>
                      <dd>
                        <Value presented={valuation.grossExposure} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-terminal-muted">NET EXPOSURE</dt>
                      <dd>
                        <Value presented={valuation.netExposure} />
                      </dd>
                    </div>
                  </dl>
                </section>

                <div className="grid gap-3 md:grid-cols-3">
                  <section
                    aria-label="Margin"
                    className="rounded border border-terminal-border p-3"
                  >
                    <h2 className="font-mono text-xs text-terminal-muted">MARGIN</h2>
                    <p className="mt-1 font-mono text-xs">
                      <Value presented={margin.utilisation} />
                    </p>
                    <p className="mt-1 text-[10px] text-terminal-muted">
                      basis: {margin.basis ?? "none stated"}
                    </p>
                  </section>

                  <section
                    aria-label="Return"
                    className="rounded border border-terminal-border p-3"
                  >
                    <h2 className="font-mono text-xs text-terminal-muted">RETURN</h2>
                    <p className="mt-1 font-mono text-xs">
                      <Value presented={returns.value} />{" "}
                      <span className="text-terminal-muted">{returns.methodology}</span>
                    </p>
                    <p className="mt-1 text-[10px] text-terminal-muted">{returns.caveat}</p>
                  </section>

                  <section
                    aria-label="Greeks"
                    className="rounded border border-terminal-border p-3"
                  >
                    <h2 className="font-mono text-xs text-terminal-muted">GREEKS</h2>
                    <dl className="mt-1 grid grid-cols-4 gap-x-2 font-mono text-xs">
                      <div>
                        <dt className="text-terminal-muted">Δ</dt>
                        <dd>
                          <Value presented={greeks.delta} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">Γ</dt>
                        <dd>
                          <Value presented={greeks.gamma} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">ν</dt>
                        <dd>
                          <Value presented={greeks.vega} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">Θ</dt>
                        <dd>
                          <Value presented={greeks.theta} />
                        </dd>
                      </div>
                    </dl>
                    <p className="mt-1 text-[10px] text-terminal-muted">
                      covers {greeks.coverage}
                      {greeks.complete ? "" : " — not the whole book"}
                    </p>
                  </section>
                </div>
              </div>
            );
          }}
        </QueryPanel>

        <section aria-label="Positions">
          <h2 className="font-mono text-xs text-terminal-muted">POSITIONS</h2>
          <QueryPanel
            query={positionsQuery}
            label="positions"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <DenseTable
                caption="Open and closed positions with cost basis method and realized P&L"
                columns={[
                  { key: "underlying", header: "UNDERLYING", render: (r) => r.underlyingId ?? "—" },
                  { key: "expiry", header: "EXPIRY", render: (r) => r.expiryId ?? "—" },
                  { key: "qty", header: "QTY", align: "right", render: (r) => r.quantity },
                  {
                    key: "status",
                    header: "STATUS",
                    render: (r) => (r.closed ? "CLOSED" : r.status),
                  },
                  {
                    key: "avg",
                    header: "AVG PRICE",
                    unit: "INR",
                    align: "right",
                    render: (r) => <Value presented={r.averagePrice} />,
                  },
                  {
                    key: "basis",
                    header: "COST BASIS",
                    unit: "INR",
                    align: "right",
                    render: (r) => <Value presented={r.costBasis} />,
                  },
                  { key: "method", header: "METHOD", render: (r) => r.costBasisMethod },
                  { key: "realized", header: "REALIZED", unit: "INR", align: "right", render: (r) => r.realizedPnl },
                  {
                    key: "economics",
                    header: "VALUABLE",
                    render: (r) =>
                      r.economicsKnown ? (
                        "yes"
                      ) : (
                        <span
                          className="text-amber-400"
                          title="No contract economics are known for this instrument, so it cannot be valued."
                        >
                          <span aria-hidden="true">⚠ </span>no economics
                        </span>
                      ),
                  },
                ]}
                rows={envelope.data.map((p) => positionRow(p))}
                rowKey={(row, index) => `${row.underlyingId}-${row.expiryId}-${index}`}
                rowClassName={(row) => (row.closed ? "text-terminal-muted" : undefined)}
              />
            )}
          </QueryPanel>
        </section>

        <section aria-label="Attribution">
          <h2 className="font-mono text-xs text-terminal-muted">ATTRIBUTION</h2>
          <QueryPanel
            query={attributionQuery}
            label="attribution"
            empty={emptyState("NOT_YET_AVAILABLE")}
          >
            {(envelope) => <AttributionTable input={toAttributionInput(envelope.data)} />}
          </QueryPanel>
        </section>
      </div>
    </ScreenFrame>
  );
}
