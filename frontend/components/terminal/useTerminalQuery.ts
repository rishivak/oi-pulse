"use client";

/**
 * The hooks every screen reads through.
 *
 * Three brief requirements are met here rather than in each screen, because each
 * screen remembering them is how one of them eventually does not.
 *
 * **§20, no duplicate requests.** The cache key is derived from the endpoint by
 * `keyFor`, so two components asking the same question are one request.
 *
 * **§20, no unnecessary polling.** A pinned historical view never refetches: a past
 * `(market_time, knowledge_time, build_context_id)` is immutable (`12` §5), so the
 * answer cannot have changed. Live views refetch at the degraded interval only
 * because the SSE endpoint `12` §3 specifies is not implemented.
 *
 * **§22, no silent retry of a write.** `useTerminalMutation` sets `retry: false`
 * unconditionally. Read retries are decided by `retryDecision`, which consults the
 * error's own classification instead of counting attempts.
 */

import { useMutation, useQuery } from "@tanstack/react-query";

import type { Endpoint } from "@/lib/terminal/endpoints";
import type { Envelope } from "@/lib/terminal/envelope";
import { ApiRequestFailed, request } from "@/lib/terminal/client";
import { DEGRADED_REFRESH_MS } from "@/lib/terminal/realtime";
import { classifyError, retryDecision } from "@/lib/terminal/errors";
import { isImmutable, keyFor } from "@/lib/terminal/queryKeys";

/** Turn any thrown value into a classification a panel can render. */
export function classify(error: unknown) {
  if (error instanceof ApiRequestFailed) return error.classified;
  return classifyError(null, null);
}

export function useTerminalQuery<T>(endpoint: Endpoint | null, enabled = true) {
  const immutable = endpoint !== null && isImmutable(endpoint);
  return useQuery<Envelope<T>>({
    queryKey: endpoint === null ? ["disabled"] : keyFor(endpoint),
    queryFn: () => request<T>(endpoint as Endpoint),
    enabled: enabled && endpoint !== null,
    // Historical answers are immutable, so they never go stale and never refetch.
    staleTime: immutable ? Number.POSITIVE_INFINITY : DEGRADED_REFRESH_MS,
    refetchInterval: immutable ? false : DEGRADED_REFRESH_MS,
    refetchOnWindowFocus: !immutable,
    retry: (attempt, error) =>
      attempt < 2 && retryDecision("GET", classify(error)).retry,
  });
}

export function useTerminalMutation<T>(endpoint: Endpoint) {
  return useMutation<Envelope<T>, unknown, unknown>({
    mutationFn: (body: unknown) => request<T>(endpoint, { body }),
    // Never. `retryDecision` returns false for every non-GET method and every code;
    // stating it here as well means a future default cannot quietly re-enable it.
    retry: false,
  });
}
