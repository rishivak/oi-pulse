"use client";

/**
 * Backtest — would this have worked?
 *
 * `13-FRONTEND_IA.md` §5: "The assumption set — latency, spread, slippage, fees —
 * is displayed **beside** the headline number, never in a footnote."
 *
 * So the assumptions are laid out in the same block as the P&L, and when any fill
 * was priced against an assumed spread the headline carries a qualifier saying how
 * many. A net P&L resting on invented spreads is a different kind of number from
 * one resting on observed quotes, and the difference has to be visible where the
 * number is read.
 */

import { useState } from "react";

import type { BacktestResultDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { backtestSummary } from "@/lib/terminal/screens/research";
import { backtest } from "@/lib/terminal/endpoints";
import { DenseTable, Value } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("backtest");

export default function BacktestPage() {
  const { state } = useViewState();
  const [selected, setSelected] = useState<string | null>(null);
  const runsQuery = useTerminalQuery<readonly { run_id: string; [key: string]: unknown }[]>(
    backtest.runs({ limit: 50 }),
  );
  const resultQuery = useTerminalQuery<BacktestResultDto>(
    selected === null ? null : backtest.results(selected),
  );
  const quality = resultQuery.data?.quality ?? runsQuery.data?.quality ?? qualityBadge(undefined);
  const summary = resultQuery.data ? backtestSummary(resultQuery.data.data) : null;

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      <div className="space-y-4">
        <section aria-label="Runs">
          <h2 className="font-mono text-xs text-terminal-muted">RUNS</h2>
          <QueryPanel
            query={runsQuery}
            label="backtest runs"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <ul className="mt-1 flex flex-wrap gap-1">
                {envelope.data.map((run) => (
                  <li key={run.run_id}>
                    <button
                      type="button"
                      onClick={() => setSelected(run.run_id)}
                      aria-pressed={selected === run.run_id}
                      className="rounded border border-terminal-border px-2 py-0.5 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      {run.run_id}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </QueryPanel>
        </section>

        {selected === null ? (
          <p className="text-xs text-terminal-muted">Select a run.</p>
        ) : (
          <QueryPanel
            query={resultQuery}
            label="backtest result"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
          >
            {() =>
              summary === null ? null : (
                <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
                  <section
                    aria-label="Result"
                    className="rounded border border-terminal-border p-3"
                  >
                    <h2 className="font-mono text-xs text-terminal-muted">RESULT</h2>
                    <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs sm:grid-cols-3">
                      <div>
                        <dt className="text-terminal-muted">NET P&amp;L</dt>
                        <dd className="text-base">
                          <Value presented={summary.netPnl} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">GROSS P&amp;L</dt>
                        <dd>
                          <Value presented={summary.grossPnl} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">MAX DRAWDOWN</dt>
                        <dd>
                          <Value presented={summary.maxDrawdown} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">FILLS</dt>
                        <dd>
                          {summary.fills} ({summary.partialFills} partial)
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">FILL RATE</dt>
                        <dd>
                          <Value presented={summary.fillRate} />
                        </dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">INTENTS</dt>
                        <dd>
                          {summary.intentsGenerated} ({summary.intentsRejectedByRisk}{" "}
                          rejected by risk)
                        </dd>
                      </div>
                    </dl>

                    {summary.headlineQualifier ? (
                      <p
                        className="mt-3 rounded border border-amber-700 bg-amber-950 p-2 text-xs text-amber-200"
                        role="note"
                      >
                        <span aria-hidden="true">⚠ </span>
                        {summary.headlineQualifier}
                      </p>
                    ) : null}

                    {summary.caveats.length > 0 ? (
                      <ul className="mt-2 space-y-1 text-xs text-amber-300">
                        {summary.caveats.map((caveat, index) => (
                          <li key={index}>
                            <span aria-hidden="true">⚠ </span>
                            {caveat}
                          </li>
                        ))}
                      </ul>
                    ) : null}

                    <p className="mt-2 font-mono text-[10px] text-terminal-muted">
                      result {summary.contentHash} · strategy {summary.strategyDigest} ·
                      replay {summary.replayDigest} · fill model {summary.fillModelDigest} ·
                      risk evaluated {summary.riskEvaluated ? "yes" : "no"}
                    </p>
                  </section>

                  {/* Beside the headline, in the same visual block -- not below it,
                      and not behind a disclosure. */}
                  <section
                    aria-label="Assumptions"
                    className="rounded border border-terminal-border p-3"
                  >
                    <h2 className="font-mono text-xs text-terminal-muted">ASSUMPTIONS</h2>
                    <p className="mt-1 text-xs text-terminal-muted">
                      These produced the numbers on the left. Change one and the result
                      changes.
                    </p>
                    <DenseTable
                      caption="Execution assumptions behind this backtest"
                      columns={[
                        { key: "name", header: "ASSUMPTION", render: (r) => r.name },
                        { key: "value", header: "VALUE", align: "right", render: (r) => r.value },
                      ]}
                      rows={summary.assumptions}
                      rowKey={(row) => row.name}
                    />
                  </section>
                </div>
              )
            }
          </QueryPanel>
        )}
      </div>
    </ScreenFrame>
  );
}
