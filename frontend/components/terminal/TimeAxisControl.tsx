"use client";

/**
 * The two-axis time control (`13-FRONTEND_IA.md` §4).
 *
 * Both axes are always visible, and the hindsight state is "visually loud for as
 * long as it is active". The component renders `axisReadings` and `syncIndicator`
 * from `lib/terminal/time.ts`; it does not decide what the axes mean, and it cannot
 * render one without the other because `axisReadings` returns both.
 *
 * Changing either axis rewrites the URL rather than local state. `13` §8 wants
 * every view linkable, and the URL being the source of truth is also what stops a
 * control going inert: the request is built from the URL, so a selector that did
 * not change the URL would visibly not change the data.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import {
  PARAM_KNOWLEDGE_TIME,
  PARAM_MARKET_TIME,
  type ViewState,
} from "@/lib/terminal/urlState";
import { axisReadings, syncIndicator } from "@/lib/terminal/time";
import { Instant } from "@/components/terminal/primitives";

const INDICATOR_CLASS: Record<string, string> = {
  LIVE: "border-bull-dim text-bull",
  IN_SYNC: "border-terminal-border text-terminal-muted",
  HINDSIGHT: "border-amber-500 bg-amber-950 text-amber-300",
};

export function TimeAxisControl({
  state,
  showDecision = false,
}: {
  state: ViewState;
  showDecision?: boolean;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const readings = axisReadings(state.axes, { decision: showDecision });
  const indicator = syncIndicator(state.axes);

  const setParam = (key: string, value: string) => {
    const params = new URLSearchParams(search?.toString() ?? "");
    if (value === "") params.delete(key);
    else params.set(key, value);
    router.replace(`${pathname}?${params.toString()}`);
  };

  return (
    <section
      aria-label="Time axes"
      className={`rounded border px-3 py-2 ${INDICATOR_CLASS[indicator.state]}`}
      data-sync-state={indicator.state}
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
        {readings.map((reading) => (
          <div key={reading.axis} className="contents">
            <dt className="text-terminal-muted" title={reading.meaning}>
              {reading.label}
            </dt>
            <dd>
              {reading.value === null ? (
                <span className="italic text-terminal-muted">{reading.placeholder}</span>
              ) : (
                <Instant iso={reading.value} />
              )}
            </dd>
          </div>
        ))}
      </dl>

      <p className="mt-2 flex items-center gap-2 text-xs">
        <span aria-hidden="true">{indicator.glyph}</span>
        <strong>{indicator.label}</strong>
        <span className="text-terminal-muted">{indicator.explanation}</span>
      </p>

      <div className="mt-2 flex flex-wrap gap-3">
        <label className="flex items-center gap-1 text-xs">
          <span className="text-terminal-muted">Market time</span>
          <input
            type="datetime-local"
            className="rounded border border-terminal-border bg-terminal-bg px-1 py-0.5 font-mono focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            value={(state.axes.marketTime ?? "").slice(0, 16)}
            onChange={(event) =>
              setParam(
                PARAM_MARKET_TIME,
                event.target.value === "" ? "" : `${event.target.value}:00Z`,
              )
            }
          />
        </label>
        <label className="flex items-center gap-1 text-xs">
          <span className="text-terminal-muted">Knowledge time</span>
          <input
            type="datetime-local"
            className="rounded border border-terminal-border bg-terminal-bg px-1 py-0.5 font-mono focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            value={(state.axes.knowledgeTime ?? "").slice(0, 16)}
            onChange={(event) =>
              setParam(
                PARAM_KNOWLEDGE_TIME,
                event.target.value === "" ? "" : `${event.target.value}:00Z`,
              )
            }
          />
        </label>
      </div>

      {indicator.state === "HINDSIGHT" ? (
        <p className="mt-2 text-xs" role="status">
          Every number on this screen was assembled with knowledge that did not exist at
          the market time above. It cannot be used to judge a decision made then.
        </p>
      ) : null}
    </section>
  );
}
