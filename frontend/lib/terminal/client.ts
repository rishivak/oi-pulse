/**
 * The terminal's only request surface.
 *
 * Everything the terminal asks of the backend goes through {@link request}. That is
 * not a style preference: `tools/check_frontend_contract.py` verifies that every
 * path the terminal builds exists in the backend, and it can only verify the paths
 * it can find. A component that called `fetch` directly would be invisible to that
 * check, so the guard also fails on any `fetch` outside this file.
 *
 * ### The base URL is not configurable from the browser
 *
 * `BASE` is a fixed same-origin prefix. The real backend address lives in
 * `next.config.mjs` as a **server-only** environment variable and is proxied; it is
 * never a `NEXT_PUBLIC_` value, so it is not inlined into the client bundle and no
 * query parameter, header or setting can redirect a request somewhere else. Brief
 * §23: "no arbitrary backend URL access".
 *
 * ### CSRF
 *
 * `17-SECURITY.md` §7 requires three defences on state-changing methods:
 * `SameSite=Lax` on the session cookie, Origin validation, and a double-submit
 * token in a header the browser cannot set cross-origin. The first two are
 * server-side. The third is this client's job, and {@link request} sends the token
 * on every `POST`/`PUT`/`PATCH`/`DELETE`.
 *
 * **The backend does not yet validate it.** No authentication or CSRF middleware is
 * mounted in `oipulse/api/app.py`; `17-SECURITY.md` §3 and §7 specify the session
 * and the token, and neither is implemented. Sending the header is the half of the
 * contract this layer owns, and saying so here is better than a comment implying
 * protection that is not in force. The gap is recorded in the Phase 12 report.
 *
 * ### Retries
 *
 * There are none. Brief §22: "Do not silently retry state-changing operations."
 * `lib/terminal/errors.ts` decides retryability and the *caller* acts on it, so a
 * retry is always something a screen chose, visibly, rather than something the
 * transport did on its behalf.
 */

import { type ApiErrorBody, type ClassifiedError, classifyError } from "@/lib/terminal/errors";
import { type Endpoint, endpointUrl } from "@/lib/terminal/endpoints";
import { type Envelope, parseEnvelope } from "@/lib/terminal/envelope";

/**
 * Same-origin prefix, rewritten to the v2 API by `next.config.mjs`.
 *
 * `12-API_SPEC.md` §5 specifies `/api/v2/` as the client-visible prefix. The
 * application itself mounts its routers at the root (`oipulse/api/app.py`), so the
 * prefix is applied by the proxy rather than by the app. Keeping the documented
 * prefix on the client side means a future move of the prefix into the app is a
 * configuration change and not a change to every call site.
 */
export const BASE = "/api/v2";

/** Header name for the double-submit token (`17` §7). */
export const CSRF_HEADER = "X-OIPulse-CSRF";
/** Cookie the server sets alongside the session, readable so it can be echoed. */
export const CSRF_COOKIE = "oipulse_csrf";

export class ApiRequestFailed extends Error {
  readonly classified: ClassifiedError;
  readonly endpoint: Endpoint;

  constructor(endpoint: Endpoint, classified: ClassifiedError) {
    super(
      `${endpoint.method} ${endpoint.path}: ${classified.meaning.title}` +
        (classified.message ? ` — ${classified.message}` : ""),
    );
    this.name = "ApiRequestFailed";
    this.classified = classified;
    this.endpoint = endpoint;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export function isStateChanging(method: string): boolean {
  return method !== "GET" && method !== "HEAD";
}

export interface RequestOptions {
  readonly body?: unknown;
  readonly signal?: AbortSignal;
}

/**
 * Issue one request and return its parsed envelope.
 *
 * The envelope parse is not optional and not deferred: a caller receives `data`
 * only together with the quality badge that qualifies it (`12` §2).
 */
export async function request<T>(
  endpoint: Endpoint,
  options: RequestOptions = {},
): Promise<Envelope<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (isStateChanging(endpoint.method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token !== null) headers[CSRF_HEADER] = token;
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${endpointUrl(endpoint)}`, {
      method: endpoint.method,
      headers,
      // The session cookie is `HttpOnly` and host-scoped (`17` §3); same-origin is
      // sufficient and does not widen the cookie to a cross-site request.
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch {
    // No response at all. Distinct from any HTTP status, and blocking: nothing on
    // the screen can be trusted to be current.
    throw new ApiRequestFailed(endpoint, classifyError(null, null));
  }

  const text = await response.text();
  let body: unknown = null;
  if (text !== "") {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    throw new ApiRequestFailed(endpoint, classifyError(response.status, body as ApiErrorBody));
  }
  return parseEnvelope<T>(body);
}

/**
 * Whether a repeated submission was recognised as a duplicate.
 *
 * `oipulse/api/paper_trading.py` is idempotent on the intent's content-addressed
 * identity and reports `meta.duplicate`. The terminal surfaces that rather than
 * showing a second order: the operator needs to know their resubmission changed
 * nothing, and the alternative — two rows for one intent — is how a book comes to
 * look twice its size.
 */
export function wasDuplicate(envelope: Envelope<unknown>): boolean {
  return envelope.meta.raw.duplicate === true;
}
