"use client";

/**
 * Replay — what did it look like as it happened?
 *
 * `13-FRONTEND_IA.md` §5: "**A persistent banner shows market time and knowledge
 * horizon** ... because a replay screen that looks like live is dangerous."
 *
 * The banner comes from `replayBanner`, whose `persistent` field is the literal
 * `true`. There is no branch on which this screen renders without it.
 *
 * The transport verbs are the backend's — play, pause, step, seek, speed — and
 * there is no order verb among them. `oipulse/api/app.py` records the same absence
 * on the router: "replay's control surface is play/pause/step/seek/speed".
 */

import { useState } from "react";

import type { ReplayContextDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { REPLAY_CONTROLS, type ReplayControl, replayBanner } from "@/lib/terminal/screens/research";
import { replay } from "@/lib/terminal/endpoints";
import { axisReadings } from "@/lib/terminal/time";
import { DenseTable, Instant } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import {
  useTerminalMutation,
  useTerminalQuery,
} from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("replay");

function TransportControls({ sessionId }: { sessionId: string }) {
  const mutation = useTerminalMutation<unknown>(replay.control(sessionId));
  return (
    <div role="group" aria-label="Replay transport" className="flex flex-wrap gap-1">
      {REPLAY_CONTROLS.map((control: ReplayControl) => (
        <button
          key={control}
          type="button"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate({ action: control })}
          className="rounded border border-terminal-border px-2 py-0.5 font-mono text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          {control}
        </button>
      ))}
      {mutation.isError ? (
        <span className="text-xs text-bear" role="alert">
          The control was refused. Nothing was retried.
        </span>
      ) : null}
    </div>
  );
}

export default function ReplayPage() {
  const { state } = useViewState();
  const [selected, setSelected] = useState<string | null>(null);
  const sessionsQuery = useTerminalQuery<readonly ReplayContextDto[]>(replay.sessions());
  const sessionQuery = useTerminalQuery<ReplayContextDto>(
    selected === null ? null : replay.session(selected),
  );
  const quality = sessionQuery.data?.quality ?? sessionsQuery.data?.quality ?? qualityBadge(undefined);
  const context = sessionQuery.data?.data ?? null;
  const banner = context ? replayBanner(context, null) : null;

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      <div className="space-y-4">
        {/* Rendered before anything else on the screen, and never conditionally
            on a preference: a replay that looks live is the danger. */}
        {banner ? (
          <section
            aria-label="Replay clock"
            className={`rounded border px-3 py-2 ${
              banner.isHindsight
                ? "border-amber-500 bg-amber-950 text-amber-200"
                : "border-accent text-terminal-text"
            }`}
            data-replay-banner="true"
          >
            <p className="font-mono text-xs">
              <span aria-hidden="true">{banner.glyph} </span>
              <strong>{banner.label}</strong>
              <span className="ml-2 text-terminal-muted">{banner.knowledgeMode}</span>
            </p>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 font-mono text-xs">
              {axisReadings(banner.axes).map((reading) => (
                <div key={reading.axis} className="contents">
                  <dt className="text-terminal-muted">{reading.label}</dt>
                  <dd>
                    {reading.value === null ? (
                      <span className="italic text-terminal-muted">
                        not stepped yet
                      </span>
                    ) : (
                      <Instant iso={reading.value} />
                    )}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-1 text-xs">{banner.explanation}</p>
          </section>
        ) : null}

        <section aria-label="Replay sessions">
          <h2 className="font-mono text-xs text-terminal-muted">SESSIONS</h2>
          <QueryPanel
            query={sessionsQuery}
            label="replay sessions"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <DenseTable
                caption="Replay sessions with knowledge mode, step mode and content digest"
                columns={[
                  {
                    key: "run",
                    header: "RUN",
                    render: (r) => (
                      <button
                        type="button"
                        onClick={() => setSelected(r.run_id)}
                        className="text-accent underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                      >
                        {r.run_id}
                      </button>
                    ),
                  },
                  { key: "step", header: "STEP MODE", render: (r) => r.step_mode },
                  {
                    key: "interval",
                    header: "INTERVAL",
                    unit: "s",
                    align: "right",
                    render: (r) => r.interval_seconds ?? "—",
                  },
                  { key: "speed", header: "SPEED", align: "right", render: (r) => `${r.speed}×` },
                  { key: "knowledge", header: "KNOWLEDGE MODE", render: (r) => r.knowledge_mode },
                  {
                    key: "hindsight",
                    header: "HINDSIGHT",
                    render: (r) =>
                      r.is_hindsight ? (
                        <span className="text-amber-400">
                          <span aria-hidden="true">⚠ </span>yes
                        </span>
                      ) : (
                        "no"
                      ),
                  },
                  {
                    key: "digest",
                    header: "DIGEST",
                    render: (r) => (
                      <span className="text-terminal-muted">{r.content_digest}</span>
                    ),
                  },
                ]}
                rows={envelope.data}
                rowKey={(row) => row.run_id}
              />
            )}
          </QueryPanel>
        </section>

        {selected !== null ? (
          <section aria-label="Transport">
            <h2 className="font-mono text-xs text-terminal-muted">TRANSPORT</h2>
            <TransportControls sessionId={selected} />
            <p className="mt-1 text-xs text-terminal-muted">
              Play, pause, step, seek and speed. There is no order verb on the replay
              router: a replayed strategy produces intents, and those are evaluated in
              a backtest run, not submitted from here.
            </p>
          </section>
        ) : null}
      </div>
    </ScreenFrame>
  );
}
