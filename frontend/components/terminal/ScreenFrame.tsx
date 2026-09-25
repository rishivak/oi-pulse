"use client";

/**
 * The shell every terminal screen renders inside.
 *
 * It exists to make four things structurally impossible to omit: the screen's
 * question (`13-FRONTEND_IA.md` §1.1, one screen one question), the two-axis time
 * control (§4, on every analysis and research screen), the data-quality indicator
 * (§7, persistent), and the real-time capability state (brief §21, so a polled view
 * is never mistaken for a live one).
 *
 * A screen that forgot one of these would still look finished, which is exactly why
 * the frame supplies them rather than each page remembering to.
 */

import type { ReactNode } from "react";

import type { QualityBadge } from "@/lib/terminal/quality";
import type { ScreenSpec } from "@/lib/terminal/screens";
import type { ViewState } from "@/lib/terminal/urlState";
import { QualityHeader } from "@/components/terminal/primitives";
import { TimeAxisControl } from "@/components/terminal/TimeAxisControl";
import { isLive } from "@/lib/terminal/time";
import { realtimeStatus } from "@/lib/terminal/realtime";

export function ScreenFrame({
  screen,
  state,
  quality,
  toolbar,
  children,
}: {
  screen: ScreenSpec;
  state: ViewState;
  quality: QualityBadge;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  const realtime = realtimeStatus({ live: isLive(state.axes) });
  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-mono text-base">{screen.title}</h1>
          {/* The question is rendered, not implied by the title. */}
          <p className="text-xs text-terminal-muted">{screen.question}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span
            className="inline-flex items-center gap-1 rounded border border-terminal-border px-1.5 py-0.5 font-mono text-xs"
            title={realtime.explanation}
            data-realtime={realtime.capability}
          >
            <span aria-hidden="true">{realtime.glyph}</span>
            {realtime.label}
            <span className="sr-only">{realtime.srLabel}</span>
          </span>
          <QualityHeader badge={quality} />
        </div>
      </header>

      <TimeAxisControl state={state} />
      {toolbar ? <div className="flex flex-wrap items-center gap-2">{toolbar}</div> : null}

      <main className="min-h-0 flex-1 overflow-auto" aria-live="polite">
        {children}
      </main>
    </div>
  );
}
