import type { Metadata } from "next";

import { TerminalShell } from "@/components/terminal/TerminalShell";

/**
 * The v2 professional terminal.
 *
 * A real path segment rather than a `(group)`: route groups are stripped from the
 * URL, so `app/(terminal)/page.tsx` would resolve to `/` and collide with the
 * legacy dashboard's root page. `/terminal/*` keeps the two apps apart.
 *
 * The legacy `(dashboard)` group is untouched. It talks to the separate `backend/`
 * application over `/oi/*`; `18-ROADMAP.md`'s cutover and legacy removal is its own
 * operational step, and Phase 12 adds the v2 terminal beside it rather than
 * deleting a working surface.
 *
 * ### Why the whole segment is dynamic
 *
 * Every screen reads `(market_time, knowledge_time, underlying, expiry)` from the
 * URL through `useSearchParams`, which cannot be resolved at build time. Declaring
 * the segment dynamic states that directly instead of leaving Next to fail on a
 * prerender, and it is the honest description: a terminal screen has no static
 * form. Historical answers are still cached, but by the query layer, where the
 * immutability argument in `12-API_SPEC.md` §5 actually applies.
 */
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "OI Pulse — Terminal",
  description: "Operator and research terminal over the OI Pulse v2 domain services",
};

export default function TerminalLayout({ children }: { children: React.ReactNode }) {
  return <TerminalShell>{children}</TerminalShell>;
}
