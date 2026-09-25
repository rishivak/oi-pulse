/**
 * Query keys, derived from the endpoint rather than written beside it.
 *
 * Brief §20 forbids "duplicate API requests". TanStack Query deduplicates by key,
 * so two components asking the same question share one request — but only if they
 * produce the same key, and hand-written keys drift from the URLs they stand for.
 *
 * {@link keyFor} derives the key from the `Endpoint` itself, so identical requests
 * are identical keys by construction. Query values are sorted, because
 * `?a=1&b=2` and `?b=2&a=1` are the same question and must not be two cache entries.
 */

import type { Endpoint } from "@/lib/terminal/endpoints";

export type QueryKey = readonly [string, string, string];

export function keyFor(endpoint: Endpoint): QueryKey {
  const query = Object.entries(endpoint.query)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `${key}=${String(value)}`)
    .sort()
    .join("&");
  return [endpoint.method, endpoint.path, query];
}

/**
 * Whether a response may be cached indefinitely.
 *
 * `12-API_SPEC.md` §5: "A response for a past `(market_time, knowledge_time,
 * build_context_id)` is immutable and may be cached indefinitely — a useful
 * property that falls out of the bitemporal model." A request with no pinned
 * market time is asking about the present and is never immutable.
 */
export function isImmutable(endpoint: Endpoint): boolean {
  const marketTime = endpoint.query.market_time;
  return typeof marketTime === "string" && marketTime !== "";
}

/** Seconds a result stays fresh. Historical answers never go stale. */
export function staleTimeMs(endpoint: Endpoint, degradedRefreshMs: number): number {
  return isImmutable(endpoint) ? Number.POSITIVE_INFINITY : degradedRefreshMs;
}
