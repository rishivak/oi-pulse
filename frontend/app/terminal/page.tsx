"use client";

/**
 * Command Center — what is happening, and where should I look?
 *
 * `13-FRONTEND_IA.md` §3. It summarizes and routes; it does not contain everything,
 * and §1.2 rejects the giant dashboard explicitly.
 *
 * Two properties of this screen are load-bearing.
 *
 * **No directional recommendation appears anywhere on it.** §3 says so in as many
 * words, and nothing below computes or suggests a view.
 *
 * **The contradictions panel is deliberate.** §3: "A terminal that only shows
 * confirming information trains overconfidence; surfacing tension between signals
 * is the single most valuable thing this screen does." The statements come from the
 * rules' own contradicting evidence, never from comparing signals here.
 */

import Link from "next/link";

import type { MarketStateDto, SignalDto } from "@/lib/api/dto";
import { COMMAND_CENTER, WORKFLOW_SCREENS } from "@/lib/terminal/screens";
import { emptyState, presentValue, qualityBadge } from "@/lib/terminal/quality";
import { collectContradictions, signalRow } from "@/lib/terminal/screens/signals";
import { market, signals } from "@/lib/terminal/endpoints";
import { timeQuery, linkTo } from "@/lib/terminal/urlState";
import { effectiveKnowledgeTime } from "@/lib/terminal/time";
import { DerivedPanel, Instant, StateBadgeView, Value } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

export default function CommandCenterPage() {
  const { state, error } = useViewState();
  const underlyingId = state.underlyingId ?? 1;
  const times = timeQuery(state);

  const stateQuery = useTerminalQuery<MarketStateDto>(
    market.state({ underlying_id: underlyingId, ...times }),
  );
  // `/signals` requires a market_time; without a pinned one there is no historical
  // question to ask, so the list is requested only when the user has chosen a moment.
  const signalsQuery = useTerminalQuery<SignalDto[]>(
    state.axes.marketTime === null
      ? null
      : signals.list({
          underlying_id: underlyingId,
          market_time: state.axes.marketTime,
          knowledge_time: effectiveKnowledgeTime(state.axes) ?? undefined,
        }),
  );

  const quality = stateQuery.data?.quality ?? qualityBadge(undefined);

  return (
    <ScreenFrame screen={COMMAND_CENTER} state={state} quality={quality}>
      {error ? (
        <p className="mb-3 rounded border border-bear bg-bear-dim p-2 text-xs" role="alert">
          {error}
        </p>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-2">
        <section
          aria-label="Spot and session"
          className="rounded border border-terminal-border p-3"
        >
          <h2 className="font-mono text-xs text-terminal-muted">SPOT</h2>
          <QueryPanel
            query={stateQuery}
            label="market state"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
          >
            {(envelope) => (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
                <dt className="text-terminal-muted">LTP</dt>
                <dd>
                  <Value
                    presented={presentValue(envelope.data.spot.ltp, "NO_DATA_FOR_PERIOD", {
                      stale: envelope.data.spot.stale,
                    })}
                  />
                </dd>
                <dt className="text-terminal-muted">SESSION</dt>
                <dd>{envelope.data.session_phase}</dd>
                <dt className="text-terminal-muted">OBSERVED</dt>
                <dd>
                  <Instant iso={envelope.data.spot.observed_at} />
                </dd>
                <dt className="text-terminal-muted">EXPIRIES</dt>
                <dd>{envelope.data.expiries.length}</dd>
              </dl>
            )}
          </QueryPanel>
        </section>

        <section
          aria-label="Active signals"
          className="rounded border border-terminal-border p-3"
        >
          <h2 className="font-mono text-xs text-terminal-muted">ACTIVE SIGNALS</h2>
          {state.axes.marketTime === null ? (
            <p className="mt-2 text-xs text-terminal-muted">
              Pin a market time to query signals. The signal list is a point-in-time
              question and has no &quot;latest&quot; answer.
            </p>
          ) : (
            <QueryPanel
              query={signalsQuery}
              label="signals"
              empty={emptyState("NO_DATA_FOR_PERIOD")}
              isEmpty={(envelope) => envelope.data.length === 0}
            >
              {(envelope) => (
                <ul className="mt-2 space-y-1">
                  {envelope.data.map(signalRow).map((row) => (
                    <li key={row.signalId} className="flex items-center gap-2 font-mono text-xs">
                      <StateBadgeView badge={row.badge} />
                      <Link
                        href={linkTo("/terminal/signals", state)}
                        className="text-accent underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                      >
                        {row.type}@v{row.ruleVersion}
                      </Link>
                      <span className="text-terminal-muted">{row.strength ?? "—"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </QueryPanel>
          )}
        </section>

        <section
          aria-label="Contradictions"
          className="rounded border border-amber-800 p-3 lg:col-span-2"
        >
          <h2 className="font-mono text-xs text-amber-400">CONTRADICTIONS</h2>
          <p className="mt-1 text-xs text-terminal-muted">
            Tension between signals, taken from each rule&apos;s own contradicting
            evidence. Nothing here is inferred by comparing signals to each other.
          </p>
          {signalsQuery.data ? (
            <DerivedPanel badge={signalsQuery.data.quality}>
              {collectContradictions(signalsQuery.data.data).length === 0 ? (
                <p className="mt-2 text-xs text-terminal-muted">
                  No contradicting evidence was recorded on the signals in view. That is
                  not the same as the signals agreeing.
                </p>
              ) : (
                <ul className="mt-2 space-y-1">
                  {collectContradictions(signalsQuery.data.data).map((c, index) => (
                    <li key={`${c.signalId}-${index}`} className="text-xs">
                      <span aria-hidden="true">⚠ </span>
                      {c.statement}
                      {c.metricRef ? (
                        <span className="ml-2 font-mono text-terminal-muted">
                          {c.metricRef}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </DerivedPanel>
          ) : null}
        </section>
      </div>

      <section aria-label="Where to look next" className="mt-4">
        <h2 className="font-mono text-xs text-terminal-muted">WHERE TO LOOK</h2>
        <ul className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          {WORKFLOW_SCREENS.map((screen) => (
            <li key={screen.id}>
              <Link
                href={linkTo(screen.route, state)}
                className="block rounded border border-terminal-border px-2 py-1 text-xs hover:bg-terminal-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              >
                <span className="font-mono">{screen.title}</span>
                <span className="block text-terminal-muted">{screen.question}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </ScreenFrame>
  );
}
