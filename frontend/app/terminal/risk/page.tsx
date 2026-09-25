"use client";

/**
 * Risk — what are my limits and utilization?
 *
 * `13-FRONTEND_IA.md` §6: "Every limit with current utilization, decision history
 * with reasons, kill switch (prominent, confirmed)." The screen also carries the
 * OMS and reconciliation surface, because `11-TRADING.md` §5 ties them together:
 * an order whose state cannot be established is a risk fact before it is an
 * operations fact.
 *
 * **There is no approve control.** Phase 12 brief §13: "Do not let users directly
 * manufacture approvals from the UI." The evaluate button asks the server to
 * decide; `riskPreview` renders what came back, and `submissionPermitted` reads the
 * server's own `is_approved` rather than inferring approval from an absence of
 * breaches.
 *
 * **The three kinds of "not breached" stay apart.** `limitSummary` reports passed,
 * not-configured and not-evaluable separately, and refuses to describe an account
 * as within its limits while any limit was never evaluated.
 */

import { useState } from "react";

import type { DiscrepancyDto, OrderDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { limitSummary, omsRow } from "@/lib/terminal/screens/trading";
import { reconciliation, risk } from "@/lib/terminal/endpoints";
import { timeQuery } from "@/lib/terminal/urlState";
import { discrepancyBadge, needsAttention } from "@/lib/terminal/states";
import {
  DenseTable,
  Instant,
  StateBadgeView,
} from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import {
  useTerminalMutation,
  useTerminalQuery,
} from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("risk");

/**
 * The kill switch, prominent and confirmed.
 *
 * Two deliberate steps. It is a state-changing request that stops trading, and a
 * single misplaced click either halting a book or — worse, on the clear path —
 * resuming one is not an outcome worth saving a click for.
 */
function KillSwitch() {
  const [armed, setArmed] = useState(false);
  const engage = useTerminalMutation<unknown>(risk.engageKillSwitch());

  return (
    <section
      aria-label="Kill switch"
      className="rounded border border-bear bg-bear-dim/40 p-3"
    >
      <h2 className="font-mono text-sm">KILL SWITCH</h2>
      <p className="mt-1 text-xs">
        Engaging stops the risk gate from approving any further intent. It does not
        cancel resting orders and does not close positions.
      </p>
      {!armed ? (
        <button
          type="button"
          onClick={() => setArmed(true)}
          className="mt-2 rounded border border-bear px-3 py-1 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          Engage kill switch…
        </button>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-xs">Confirm:</span>
          <button
            type="button"
            disabled={engage.isPending}
            onClick={() => engage.mutate({ reason: "engaged from the terminal" })}
            className="rounded border border-bear bg-bear px-3 py-1 font-mono text-xs text-terminal-bg disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            Engage now
          </button>
          <button
            type="button"
            onClick={() => setArmed(false)}
            className="rounded border border-terminal-border px-3 py-1 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            Cancel
          </button>
        </div>
      )}
      {engage.isSuccess ? (
        <p className="mt-2 text-xs" role="status">
          Engaged. No further intent will be approved until it is cleared.
        </p>
      ) : null}
      {engage.isError ? (
        <p className="mt-2 text-xs" role="alert">
          The request failed and nothing was retried. The switch may not be engaged —
          re-read the status above before assuming either way.
        </p>
      ) : null}
    </section>
  );
}

export default function RiskPage() {
  const { state } = useViewState();
  const accountId = state.accountId;
  const times = timeQuery(state);

  const statusQuery = useTerminalQuery<Record<string, unknown>>(
    accountId === null ? null : risk.limitStatus(accountId, times),
  );
  const decisionsQuery = useTerminalQuery<readonly Record<string, unknown>[]>(
    accountId === null ? null : risk.decisions({ account_id: accountId, limit: 50 }),
  );
  const reconStatusQuery = useTerminalQuery<Record<string, unknown>>(
    reconciliation.status(),
  );
  const runsQuery = useTerminalQuery<readonly Record<string, unknown>[]>(
    reconciliation.runs({ limit: 20 }),
  );
  const unresolvedQuery = useTerminalQuery<readonly OrderDto[]>(
    reconciliation.orders({ unresolved_only: true, limit: 100 }),
  );

  const quality = statusQuery.data?.quality ?? qualityBadge(undefined);
  const limits = statusQuery.data ? limitSummary(statusQuery.data.meta.raw) : null;

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      <div className="space-y-5">
        {accountId === null ? (
          <p className="text-xs text-terminal-muted">
            No account is in context. Add <code>?account_id=…</code> to the URL to see
            limits and decisions; reconciliation below is account-independent.
          </p>
        ) : (
          <>
            <section aria-label="Limit utilisation">
              <h2 className="font-mono text-xs text-terminal-muted">LIMITS</h2>
              <QueryPanel
                query={statusQuery}
                label="limit status"
                empty={emptyState("NO_DATA_FOR_PERIOD")}
              >
                {() =>
                  limits === null ? null : (
                    <div className="space-y-2">
                      <dl className="grid grid-cols-4 gap-x-4 font-mono text-xs">
                        <div>
                          <dt className="text-terminal-muted">PASSED</dt>
                          <dd>{limits.counts.PASSED}</dd>
                        </div>
                        <div>
                          <dt className="text-terminal-muted">BREACHED</dt>
                          <dd className={limits.counts.BREACHED > 0 ? "text-bear" : undefined}>
                            {limits.counts.BREACHED}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-terminal-muted">NOT CONFIGURED</dt>
                          <dd>{limits.counts.NOT_CONFIGURED}</dd>
                        </div>
                        <div>
                          <dt className="text-terminal-muted">NOT EVALUABLE</dt>
                          <dd>{limits.counts.NOT_EVALUABLE}</dd>
                        </div>
                      </dl>
                      {limits.caveat ? (
                        <p
                          className="rounded border border-amber-700 bg-amber-950 p-2 text-xs text-amber-200"
                          role="note"
                        >
                          <span aria-hidden="true">⚠ </span>
                          {limits.caveat}
                        </p>
                      ) : (
                        <p className="text-xs text-terminal-muted">
                          Every configured limit was evaluated.
                        </p>
                      )}
                    </div>
                  )
                }
              </QueryPanel>
            </section>

            <section aria-label="Risk decisions">
              <h2 className="font-mono text-xs text-terminal-muted">DECISIONS</h2>
              <p className="text-xs text-terminal-muted">
                A decision is the output of a server-side evaluation. There is no
                control on this screen that creates or alters one.
              </p>
              <QueryPanel
                query={decisionsQuery}
                label="risk decisions"
                empty={emptyState("NO_DATA_FOR_PERIOD")}
                isEmpty={(envelope) => envelope.data.length === 0}
              >
                {(envelope) => (
                  <DenseTable
                    caption="Risk decisions with verdict, reason and evaluation context"
                    columns={[
                      { key: "intent", header: "INTENT", render: (r) => String(r.intent_id ?? "—") },
                      { key: "verdict", header: "VERDICT", render: (r) => String(r.verdict ?? "—") },
                      {
                        key: "evaluated",
                        header: "EVALUATED",
                        render: (r) => (r.evaluated === true ? "yes" : "no"),
                      },
                      { key: "reason", header: "REASON", render: (r) => String(r.reason ?? "—") },
                      {
                        key: "policy",
                        header: "POLICY",
                        render: (r) =>
                          `${String(r.policy_id ?? "—")}@v${String(r.policy_version ?? "?")}`,
                      },
                      {
                        key: "state",
                        header: "RISK STATE",
                        render: (r) => (
                          <span className="text-terminal-muted">
                            {String(r.risk_state_ref ?? "—")}
                          </span>
                        ),
                      },
                    ]}
                    rows={envelope.data}
                    rowKey={(row, index) => `${String(row.intent_id ?? index)}-${index}`}
                  />
                )}
              </QueryPanel>
            </section>
          </>
        )}

        <KillSwitch />

        <section aria-label="Reconciliation">
          <h2 className="font-mono text-xs text-terminal-muted">RECONCILIATION</h2>
          <QueryPanel
            query={reconStatusQuery}
            label="reconciliation status"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
          >
            {(envelope) => (
              <p className="font-mono text-xs">
                execution mode {String(envelope.meta.raw.execution_mode ?? "unstated")} ·
                live execution{" "}
                {envelope.meta.raw.live_execution_enabled === false ? "off" : "unstated"}
              </p>
            )}
          </QueryPanel>

          <QueryPanel
            query={runsQuery}
            label="reconciliation runs"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <div className="mt-2 space-y-3">
                {envelope.data.map((run, index) => {
                  const discrepancies = Array.isArray(run.discrepancies)
                    ? (run.discrepancies as readonly DiscrepancyDto[])
                    : [];
                  return (
                    <article
                      key={String(run.run_id ?? index)}
                      className="rounded border border-terminal-border p-2"
                    >
                      <p className="font-mono text-xs">
                        run {String(run.run_id ?? "—")} · trigger{" "}
                        {String(run.trigger ?? "—")} ·{" "}
                        {run.is_clean === true ? (
                          <span className="text-bull">CLEAN</span>
                        ) : (
                          <span className="text-bear">DISCREPANCIES</span>
                        )}
                      </p>
                      {discrepancies.length > 0 ? (
                        <DenseTable
                          caption="Differences between the local view and the venue's, with what was done"
                          columns={[
                            {
                              key: "kind",
                              header: "KIND",
                              render: (d) => (
                                <StateBadgeView badge={discrepancyBadge(d.kind)} />
                              ),
                            },
                            { key: "resolution", header: "RESOLUTION", render: (d) => d.resolution },
                            { key: "order", header: "ORDER", render: (d) => d.order_id ?? "—" },
                            {
                              key: "provider-order",
                              header: "PROVIDER ORDER",
                              render: (d) => d.provider_order_id ?? "none recorded",
                            },
                            { key: "local", header: "LOCAL", render: (d) => d.local_state ?? "—" },
                            {
                              key: "provider",
                              header: "PROVIDER",
                              render: (d) => d.provider_status ?? "no statement",
                            },
                            {
                              key: "attention",
                              header: "NEEDS ATTENTION",
                              render: (d) =>
                                d.needs_attention || needsAttention(d.resolution) ? (
                                  <span className="text-amber-400">
                                    <span aria-hidden="true">⚠ </span>yes
                                  </span>
                                ) : (
                                  "no"
                                ),
                            },
                          ]}
                          rows={discrepancies}
                          rowKey={(d, i) => `${d.kind}-${d.order_id ?? i}`}
                        />
                      ) : (
                        <p className="text-xs text-terminal-muted">
                          No differences were recorded for this run.
                        </p>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </QueryPanel>
        </section>

        <section aria-label="Orders requiring reconciliation">
          <h2 className="font-mono text-xs text-terminal-muted">
            UNRESOLVED ORDERS
          </h2>
          <QueryPanel
            query={unresolvedQuery}
            label="unresolved orders"
            empty={emptyState("NO_MATCHING_FILTER")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <DenseTable
                caption="Orders whose state cannot be established, with local and provider views side by side"
                columns={[
                  { key: "order", header: "ORDER", render: (r) => r.orderId },
                  {
                    key: "local",
                    header: "LOCAL STATE",
                    render: (r) => <StateBadgeView badge={r.localState} />,
                  },
                  {
                    key: "provider",
                    header: "PROVIDER STATE",
                    render: (r) => (
                      <span className="text-terminal-muted">
                        {r.providerStatus ?? "no statement"}
                      </span>
                    ),
                  },
                  {
                    key: "identity",
                    header: "PROVIDER ID",
                    render: (r) => (
                      <span title={r.providerIdentity.detail}>
                        <span aria-hidden="true">{r.providerIdentity.glyph} </span>
                        {r.providerIdentity.label}
                      </span>
                    ),
                  },
                  {
                    key: "blocked",
                    header: "BLOCKS INSTRUMENT",
                    render: (r) => (r.blocked ? "yes" : "no"),
                  },
                  {
                    key: "knowledge",
                    header: "KNOWLEDGE TIME",
                    render: (r) => <Instant iso={r.knowledgeTime} />,
                  },
                ]}
                rows={envelope.data.map(omsRow)}
                rowKey={(row) => row.orderId}
                rowClassName={(row) => (row.blocked ? "bg-bear-dim/30" : undefined)}
              />
            )}
          </QueryPanel>
          <p className="mt-1 text-xs text-terminal-muted">
            Local and provider views are separate columns and are never merged. Where
            the venue has said nothing, the cell says so rather than showing our state
            twice.
          </p>
        </section>
      </div>
    </ScreenFrame>
  );
}
