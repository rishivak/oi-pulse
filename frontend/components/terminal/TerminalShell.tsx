"use client";

/**
 * The client half of the terminal's layout.
 *
 * Split from `app/terminal/layout.tsx` because route segment configuration —
 * `export const dynamic` — may only be exported from a Server Component, and the
 * provider and navigation below need client state. The layout stays a server
 * component and delegates to this.
 *
 * There is no `EventSource` here. The legacy dashboard layout opens one on mount;
 * the v2 backend serves no `/stream/events`, and brief §21 forbids inventing a
 * stream around an endpoint that does not exist. Views refresh on the degraded
 * interval instead, and the header says so on every screen.
 */

import { useState } from "react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";

import { TerminalNav } from "@/components/terminal/TerminalNav";

export function TerminalShell({ children }: { children: React.ReactNode }) {
  // Created in state rather than at module scope: a module-level client is shared
  // across requests on the server, which would leak one user's cached responses
  // into another's render.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Per-query values come from `useTerminalQuery`, which knows whether the
            // request is historical and therefore immutable. The defaults here are
            // the conservative case: never retry, never refetch on a whim.
            retry: false,
            refetchOnWindowFocus: false,
          },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <div className="flex h-screen flex-col bg-terminal-bg text-terminal-text">
        <TerminalNav />
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      </div>
    </QueryClientProvider>
  );
}
