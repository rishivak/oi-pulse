"use client";

/**
 * Signals — what is developing, and why?
 *
 * `13-FRONTEND_IA.md` §4: "Detail shows supporting and contradicting evidence, each
 * traceable to metric → state → observation, plus the invalidation condition."
 *
 * Nothing on this screen changes signal truth. Phase 12 brief §9 keeps signal truth
 * and alert delivery state apart, and there is no control here that writes: the
 * only mutating alert action lives on the Alerts screen and is an acknowledgement,
 * which the backend reports as `signal_unchanged`.
 */

import type { SignalDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { signalRow, splitEvidence } from "@/lib/terminal/screens/signals";
import { signals } from "@/lib/terminal/endpoints";
import { effectiveKnowledgeTime } from "@/lib/terminal/time";
import { DenseTable, Instant, StateBadgeView } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("signals");

export default function SignalsPage() {
  const { state } = useViewState();
  const query = useTerminalQuery<readonly SignalDto[]>(
    state.axes.marketTime === null
      ? null
      : signals.list({
          underlying_id: state.underlyingId ?? undefined,
          market_time: state.axes.marketTime,
          knowledge_time: effectiveKnowledgeTime(state.axes) ?? undefined,
        }),
  );
  const quality = query.data?.quality ?? qualityBadge(undefined);

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      {state.axes.marketTime === null ? (
        <p className="text-xs text-terminal-muted">
          Pin a market time. `/signals` filters results to available_at ≤ decision
          time, and without a moment there is no horizon to filter to.
        </p>
      ) : (
        <QueryPanel
          query={query}
          label="signals"
          empty={emptyState("NO_DATA_FOR_PERIOD")}
          isEmpty={(envelope) => envelope.data.length === 0}
        >
          {(envelope) => (
            <div className="space-y-4">
              <p className="font-mono text-xs text-terminal-muted">
                DECISION TIME{" "}
                <Instant iso={envelope.meta.decisionTime ?? envelope.meta.knowledgeTime} />{" "}
                — results are filtered to values available by then.
              </p>

              <DenseTable
                caption="Signals with status, strength, horizon and availability"
                columns={[
                  { key: "type", header: "TYPE", render: (r) => `${r.type}@v${r.ruleVersion}` },
                  {
                    key: "status",
                    header: "STATUS",
                    render: (r) => <StateBadgeView badge={r.badge} />,
                  },
                  {
                    key: "actionable",
                    header: "ACTIONABLE",
                    render: (r) => (r.actionable ? "yes" : "no"),
                  },
                  { key: "strength", header: "STRENGTH", align: "right", render: (r) => r.strength ?? "—" },
                  {
                    key: "market",
                    header: "MARKET TIME",
                    render: (r) => <Instant iso={r.marketTime} />,
                  },
                  {
                    key: "knowledge",
                    header: "KNOWLEDGE TIME",
                    render: (r) => <Instant iso={r.knowledgeTime} />,
                  },
                  {
                    key: "available",
                    header: "AVAILABLE AT",
                    render: (r) => <Instant iso={r.availableAt} />,
                  },
                  {
                    key: "expires",
                    header: "EXPIRES",
                    render: (r) => <Instant iso={r.expiresAt} />,
                  },
                ]}
                rows={envelope.data.map(signalRow)}
                rowKey={(row) => row.signalId}
              />

              {envelope.data.map((signal) => {
                const split = splitEvidence(signal);
                const row = signalRow(signal);
                return (
                  <section
                    key={signal.signal_id}
                    aria-label={`Evidence for ${signal.type}`}
                    className="rounded border border-terminal-border p-3"
                  >
                    <h2 className="font-mono text-xs">
                      {signal.type}@v{signal.rule_version}{" "}
                      <span className="text-terminal-muted">{signal.signal_id}</span>
                    </h2>
                    <p className="mt-1 text-xs">
                      <span className="text-terminal-muted">INVALIDATES WHEN: </span>
                      {row.invalidationCondition ?? "no condition recorded"}
                    </p>
                    <div className="mt-2 grid gap-3 md:grid-cols-2">
                      <div>
                        <h3 className="font-mono text-xs text-terminal-muted">SUPPORTING</h3>
                        {split.supporting.length === 0 ? (
                          <p className="text-xs text-terminal-muted">
                            Not returned by this endpoint; open the signal detail route.
                          </p>
                        ) : (
                          <ul className="mt-1 space-y-1 text-xs">
                            {split.supporting.map((e, i) => (
                              <li key={i}>
                                {e.statement}
                                {e.metric_ref ? (
                                  <span className="ml-2 font-mono text-terminal-muted">
                                    {e.metric_ref}
                                  </span>
                                ) : null}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                      <div>
                        <h3 className="font-mono text-xs text-terminal-muted">
                          CONTRADICTING
                        </h3>
                        {split.assessedNoContradiction ? (
                          <p className="mt-1 text-xs">
                            Assessed; none observed.{" "}
                            <span className="text-terminal-muted">
                              This is a finding, not an empty list.
                            </span>
                          </p>
                        ) : (
                          <ul className="mt-1 space-y-1 text-xs">
                            {(split.contradicting ?? []).map((e, i) => (
                              <li key={i}>
                                <span aria-hidden="true">⚠ </span>
                                {e.statement}
                                {e.metric_ref ? (
                                  <span className="ml-2 font-mono text-terminal-muted">
                                    {e.metric_ref}
                                  </span>
                                ) : null}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                    <p className="mt-2 font-mono text-[10px] text-terminal-muted">
                      rule {signal.provenance.rule_type}@v{signal.provenance.rule_version} ·
                      build context {signal.provenance.build_context_id ?? "—"} · inputs{" "}
                      {signal.provenance.inputs_digest ?? "—"} · digest {signal.content_digest}
                    </p>
                  </section>
                );
              })}
            </div>
          )}
        </QueryPanel>
      )}
    </ScreenFrame>
  );
}
