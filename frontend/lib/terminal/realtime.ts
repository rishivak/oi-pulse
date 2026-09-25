/**
 * Real-time updates: what exists, and the honest handling of what does not.
 *
 * `12-API_SPEC.md` §3 specifies `GET /stream/events` as SSE with channels
 * `market_state`, `signals`, `alerts`, `orders`, `portfolio`, `data_quality`, and
 * `13-FRONTEND_IA.md` §8 calls for SSE with market-hours-aware reconnect.
 *
 * **That endpoint is not implemented.** `oipulse/api/app.py` mounts eleven routers
 * and none of them serves `/stream`. The Phase 12 brief §21 is unambiguous about
 * what to do here:
 *
 * > Do NOT invent a frontend stream around an endpoint that does not exist. Where a
 * > real-time capability is intentionally deferred, clearly display/preserve the
 * > correct degraded behavior.
 *
 * So there is no `EventSource` in this module and no reconnect loop. What there is
 * instead: a declared capability state that screens render, and a bounded refresh
 * policy that is explicitly *not* a stream and says so in the UI. The distinction
 * matters operationally — an operator who believes they are watching a live feed
 * will read an unchanged number as "nothing moved" rather than "nothing arrived".
 */

export type RealtimeCapability = "STREAMING" | "POLLED" | "MANUAL";

export interface RealtimeStatus {
  readonly capability: RealtimeCapability;
  readonly label: string;
  readonly glyph: string;
  readonly srLabel: string;
  /** Milliseconds between refreshes, or `null` when refresh is manual. */
  readonly refreshIntervalMs: number | null;
  readonly explanation: string;
}

/**
 * Whether the backend serves the SSE endpoint `12` §3 specifies.
 *
 * A constant rather than a probe: probing would make the terminal's behaviour depend
 * on whether a 404 arrived, and a transient 404 would silently downgrade a working
 * stream. When `/stream/events` is implemented this becomes a real capability check
 * against the generated contract.
 */
export const STREAM_ENDPOINT_IMPLEMENTED = false;

/**
 * Refresh cadence for live views, used only because streaming is unavailable.
 *
 * Brief §20 forbids "unnecessary polling". Thirty seconds is not a live feed and is
 * not presented as one; it is the slowest interval at which a stale-marked panel is
 * still worth re-asking for. Historical views do not refresh at all — a past
 * `(market_time, knowledge_time)` is immutable (`12` §5), so re-requesting it can
 * only return the same bytes.
 */
export const DEGRADED_REFRESH_MS = 30_000;

const STATUSES: Readonly<Record<RealtimeCapability, Omit<RealtimeStatus, "capability">>> = {
  STREAMING: {
    label: "STREAMING",
    glyph: "≋",
    srLabel: "live stream connected",
    refreshIntervalMs: null,
    explanation: "Updates arrive as they happen.",
  },
  POLLED: {
    label: "POLLED — NOT A LIVE FEED",
    glyph: "↻",
    srLabel: "polled every 30 seconds; this is not a live feed",
    refreshIntervalMs: DEGRADED_REFRESH_MS,
    explanation:
      "The SSE endpoint specified in 12-API_SPEC.md §3 is not implemented, so this " +
      "view re-asks every 30 seconds. An unchanged value may mean nothing moved or " +
      "may mean nothing has been re-read yet.",
  },
  MANUAL: {
    label: "PINNED",
    glyph: "⏸",
    srLabel: "pinned to a historical moment; not refreshing",
    refreshIntervalMs: null,
    explanation:
      "Pinned to a historical (market time, knowledge time). That answer is immutable, " +
      "so it is not re-requested.",
  },
};

export function realtimeStatus(opts: { live: boolean }): RealtimeStatus {
  if (!opts.live) return { capability: "MANUAL", ...STATUSES.MANUAL };
  if (STREAM_ENDPOINT_IMPLEMENTED) return { capability: "STREAMING", ...STATUSES.STREAMING };
  return { capability: "POLLED", ...STATUSES.POLLED };
}

/** The channels `12` §3 specifies, recorded so the gap is legible rather than lost. */
export const SPECIFIED_STREAM_CHANNELS = [
  "market_state",
  "signals",
  "alerts",
  "orders",
  "portfolio",
  "data_quality",
] as const;
