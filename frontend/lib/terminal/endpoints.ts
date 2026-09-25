/**
 * Every backend path the terminal is allowed to build, in one place.
 *
 * Phase 12 brief §24: "Every frontend API call should correspond to an existing
 * backend contract." That is only checkable if there is a finite, statically
 * readable set of paths, so every request in the terminal is constructed by a
 * function here and nowhere else.
 *
 * Each call to {@link endpoint} passes a **literal** method and a **literal** route
 * template. `tools/check_frontend_contract.py` reads those literals out of this file
 * and fails when one is not in `contract.generated.ts`'s `ROUTES` — which is itself
 * generated from `oipulse/api/*.py`. A screen therefore cannot be wired to a path
 * the backend does not serve, which is the mechanical form of `18-ROADMAP.md` Phase
 * 12's "No screen ships ahead of its backend — no 'Coming soon' pages."
 *
 * One absence is deliberate and is documented rather than filled in:
 * **`/stream/events`** is specified (`12` §3) but not implemented. There is no
 * function for it. See `lib/terminal/realtime.ts`.
 *
 * `/journal` was the other, and is no longer: the Phase 12 remediation added the
 * read half of the contract `12` §3 specifies, so the Journal screen has a real
 * backend and ships.
 */

import type { HttpMethod } from "@/lib/terminal/errors";

export type QueryValue = string | number | boolean | null | undefined;

export interface Endpoint {
  readonly method: HttpMethod;
  /** The route template, exactly as the backend declares it. */
  readonly template: string;
  /** The template with path parameters substituted. */
  readonly path: string;
  readonly query: Readonly<Record<string, QueryValue>>;
}

/**
 * Build one endpoint.
 *
 * Throws when a `{placeholder}` is left unsubstituted. A path containing a literal
 * brace would 404, and a 404 reads as "no data for this period" — a wrong but
 * plausible answer, which is the failure mode this whole module exists to prevent.
 */
export function endpoint(
  method: HttpMethod,
  template: string,
  params: Readonly<Record<string, string | number>> = {},
  query: Readonly<Record<string, QueryValue>> = {},
): Endpoint {
  let path = template;
  for (const [key, value] of Object.entries(params)) {
    path = path.replaceAll(`{${key}}`, encodeURIComponent(String(value)));
  }
  const unresolved = path.match(/\{[^}]+\}/);
  if (unresolved) {
    throw new Error(
      `endpoint ${method} ${template}: path parameter ${unresolved[0]} was not supplied`,
    );
  }
  return { method, template, path, query };
}

/** Serialise the query string, dropping absent values rather than sending "null". */
export function queryString(query: Readonly<Record<string, QueryValue>>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === "") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length === 0 ? "" : `?${parts.join("&")}`;
}

export function endpointUrl(ep: Endpoint): string {
  return `${ep.path}${queryString(ep.query)}`;
}

/** The two time axes as query parameters. Both, always (`12` §2). */
export interface TimeQuery {
  readonly market_time?: string;
  readonly knowledge_time?: string;
  readonly decision_time?: string;
}

// --------------------------------------------------------------------- ops

export const ops = {
  health: () => endpoint("GET", "/ops/health"),
  ready: () => endpoint("GET", "/ops/ready"),
};

// ------------------------------------------------------------- market state

export const market = {
  state: (q: TimeQuery & { underlying_id: number; expiry_id?: number }) =>
    endpoint("GET", "/market/state", {}, q),
};

// ---------------------------------------------------------------- analytics

export const features = {
  list: (q: { scope?: string; identifier?: string } = {}) =>
    endpoint("GET", "/features", {}, q),
  definition: (identifier: string, version: number) =>
    endpoint("GET", "/features/{identifier}/versions/{version}", { identifier, version }),
  values: (
    identifier: string,
    q: TimeQuery & { underlying_id: number; expiry_id?: number; version?: number },
  ) => endpoint("GET", "/features/{identifier}/values", { identifier }, q),
};

// ------------------------------------------------------------------ signals

export const signals = {
  types: () => endpoint("GET", "/signals/types"),
  typeDefinition: (signal_type: string, version: number) =>
    endpoint("GET", "/signals/types/{signal_type}/versions/{version}", {
      signal_type,
      version,
    }),
  list: (
    q: TimeQuery & { underlying_id?: number; signal_type?: string; status?: string },
  ) => endpoint("GET", "/signals", {}, q),
  detail: (signal_id: string) => endpoint("GET", "/signals/{signal_id}", { signal_id }),
  history: (signal_id: string) =>
    endpoint("GET", "/signals/{signal_id}/history", { signal_id }),
};

// ------------------------------------------------------------------- alerts

export const alerts = {
  rules: () => endpoint("GET", "/alerts/rules"),
  rule: (rule_id: string) => endpoint("GET", "/alerts/rules/{rule_id}", { rule_id }),
  createRule: () => endpoint("POST", "/alerts/rules"),
  deleteRule: (rule_id: string) =>
    endpoint("DELETE", "/alerts/rules/{rule_id}", { rule_id }),
  /** Dry run. `meta.persisted` and `meta.delivered` are both false by contract. */
  testRule: (rule_id: string) =>
    endpoint("POST", "/alerts/rules/{rule_id}/test", { rule_id }),
  occurrences: (q: { rule_id?: string; limit?: number } = {}) =>
    endpoint("GET", "/alerts/occurrences", {}, q),
  /** Acknowledgement is delivery state only; `meta.signal_unchanged` says so. */
  acknowledge: (occurrence_id: string) =>
    endpoint("POST", "/alerts/occurrences/{occurrence_id}/acknowledge", { occurrence_id }),
};

// ----------------------------------------------------------------- research

export const research = {
  studies: () => endpoint("GET", "/research/studies"),
  study: (study_id: string, version: number) =>
    endpoint("GET", "/research/studies/{study_id}/versions/{version}", {
      study_id,
      version,
    }),
  createStudy: () => endpoint("POST", "/research/studies"),
  deleteStudy: (study_id: string, version: number) =>
    endpoint("DELETE", "/research/studies/{study_id}/versions/{version}", {
      study_id,
      version,
    }),
  runStudy: (study_id: string) =>
    endpoint("POST", "/research/studies/{study_id}/run", { study_id }),
  results: (q: { study_id?: string; limit?: number } = {}) =>
    endpoint("GET", "/research/results", {}, q),
  result: (content_hash: string) =>
    endpoint("GET", "/research/results/{content_hash}", { content_hash }),
  datasets: (q: { limit?: number } = {}) => endpoint("GET", "/research/datasets", {}, q),
  dataset: (content_hash: string) =>
    endpoint("GET", "/research/datasets/{content_hash}", { content_hash }),
  signalEvaluations: (q: { signal_type?: string; limit?: number } = {}) =>
    endpoint("GET", "/research/signal-evaluations", {}, q),
};

// ------------------------------------------------------------------- replay

export const replay = {
  sessions: () => endpoint("GET", "/replay/sessions"),
  createSession: () => endpoint("POST", "/replay/sessions"),
  session: (session_id: string) =>
    endpoint("GET", "/replay/sessions/{session_id}", { session_id }),
  /** play / pause / step / seek / speed. There is no order verb on this router. */
  control: (session_id: string) =>
    endpoint("POST", "/replay/sessions/{session_id}/control", { session_id }),
  state: (session_id: string) =>
    endpoint("GET", "/replay/sessions/{session_id}/state", { session_id }),
};

// ----------------------------------------------------------------- backtest

export const backtest = {
  runs: (q: { limit?: number } = {}) => endpoint("GET", "/backtest/runs", {}, q),
  createRun: () => endpoint("POST", "/backtest/runs"),
  run: (run_id: string) => endpoint("GET", "/backtest/runs/{run_id}", { run_id }),
  results: (run_id: string) =>
    endpoint("GET", "/backtest/runs/{run_id}/results", { run_id }),
  resultByHash: (content_hash: string) =>
    endpoint("GET", "/backtest/results/{content_hash}", { content_hash }),
  trades: (run_id: string) => endpoint("GET", "/backtest/runs/{run_id}/trades", { run_id }),
  equityCurve: (run_id: string) =>
    endpoint("GET", "/backtest/runs/{run_id}/equity-curve", { run_id }),
};

// ------------------------------------------------------------ paper trading

/**
 * Paper only. There is no `/trading` router in the backend and no function here that
 * could reach one; `oipulse/api/app.py` states why. Every response on this router
 * carries `mode: PAPER` and `live_execution_available: false`, and
 * `lib/terminal/mode.ts` refuses to render an order surface that does not.
 */
export const paperTrading = {
  accounts: () => endpoint("GET", "/paper-trading/accounts"),
  createAccount: () => endpoint("POST", "/paper-trading/accounts"),
  account: (account_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}", { account_id }),
  createIntent: (account_id: string) =>
    endpoint("POST", "/paper-trading/accounts/{account_id}/intents", { account_id }),
  intent: (account_id: string, intent_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/intents/{intent_id}", {
      account_id,
      intent_id,
    }),
  orders: (account_id: string, q: { status?: string; limit?: number } = {}) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/orders", { account_id }, q),
  order: (account_id: string, order_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/orders/{order_id}", {
      account_id,
      order_id,
    }),
  orderEvents: (account_id: string, order_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/orders/{order_id}/events", {
      account_id,
      order_id,
    }),
  cancelOrder: (account_id: string, order_id: string) =>
    endpoint("POST", "/paper-trading/accounts/{account_id}/orders/{order_id}/cancel", {
      account_id,
      order_id,
    }),
  fills: (account_id: string, q: { limit?: number } = {}) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/fills", { account_id }, q),
  positions: (account_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/positions", { account_id }),
  pnl: (account_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/pnl", { account_id }),
  /** The provenance chain for one order, server-assembled. */
  audit: (account_id: string, order_id: string) =>
    endpoint("GET", "/paper-trading/accounts/{account_id}/audit/{order_id}", {
      account_id,
      order_id,
    }),
};

// --------------------------------------------------------------------- risk

/**
 * There is deliberately no "approve" function. `oipulse/api/app.py`: an approval is
 * only ever the output of a server-side evaluation, and `/risk/evaluate` triggers
 * the engine rather than accepting a verdict. The brief §13 requires the same:
 * "Do not let users directly manufacture approvals from the UI."
 */
export const risk = {
  profiles: () => endpoint("GET", "/risk/profiles"),
  profile: (policy_id: string, version: number) =>
    endpoint("GET", "/risk/profiles/{policy_id}/versions/{version}", { policy_id, version }),
  putProfile: () => endpoint("PUT", "/risk/profiles"),
  state: (account_id: string, q: TimeQuery = {}) =>
    endpoint("GET", "/risk/state/{account_id}", { account_id }, q),
  limitStatus: (account_id: string, q: TimeQuery = {}) =>
    endpoint("GET", "/risk/status/{account_id}", { account_id }, q),
  evaluate: () => endpoint("POST", "/risk/evaluate"),
  decisions: (q: { account_id?: string; intent_id?: string; limit?: number } = {}) =>
    endpoint("GET", "/risk/decisions", {}, q),
  decision: (intent_id: string, sequence_no: number) =>
    endpoint("GET", "/risk/decisions/{intent_id}/{sequence_no}", { intent_id, sequence_no }),
  engageKillSwitch: () => endpoint("POST", "/risk/kill-switch"),
  clearKillSwitch: () => endpoint("DELETE", "/risk/kill-switch"),
};

// ------------------------------------------------------------------ journal

/**
 * Read-only. `12` §3 says "CRUD on entries"; writing is a domain action and
 * `journal_entries` carries a `source_event_key`, so entries are derived from
 * events rather than authored. No phase specifies an authoring path and none is
 * invented, so there is no create, update or delete function here.
 */
export const journal = {
  entries: (q: {
    account_id: string;
    entry_type?: string;
    order_id?: string;
    since?: string;
    until?: string;
    cursor?: string;
    limit?: number;
  }) => endpoint("GET", "/journal/entries", {}, q),
  entry: (entry_id: string) =>
    endpoint("GET", "/journal/entries/{entry_id}", { entry_id }),
};

// ----------------------------------------------------------- OMS / reconcile

export const reconciliation = {
  status: () => endpoint("GET", "/reconciliation/status"),
  runs: (q: { limit?: number } = {}) => endpoint("GET", "/reconciliation/runs", {}, q),
  run: (run_id: string) => endpoint("GET", "/reconciliation/runs/{run_id}", { run_id }),
  trigger: () => endpoint("POST", "/reconciliation/trigger"),
  orders: (q: { state?: string; unresolved_only?: boolean; limit?: number } = {}) =>
    endpoint("GET", "/reconciliation/orders", {}, q),
  order: (order_id: string) =>
    endpoint("GET", "/reconciliation/orders/{order_id}", { order_id }),
  orderEvents: (order_id: string) =>
    endpoint("GET", "/reconciliation/orders/{order_id}/events", { order_id }),
  /** Provider truth, or an explicit statement that none is available. */
  providerState: (order_id: string) =>
    endpoint("GET", "/reconciliation/orders/{order_id}/provider-state", { order_id }),
  cancelOrder: (order_id: string) =>
    endpoint("POST", "/reconciliation/orders/{order_id}/cancel", { order_id }),
};

// ---------------------------------------------------------------- portfolio

export const portfolio = {
  summary: (q: TimeQuery & { account_id: string; portfolio_id?: string }) =>
    endpoint("GET", "/portfolio", {}, q),
  positions: (
    q: TimeQuery & { account_id: string; portfolio_id?: string; include_closed?: boolean },
  ) => endpoint("GET", "/portfolio/positions", {}, q),
  exposure: (q: TimeQuery & { account_id: string }) =>
    endpoint("GET", "/portfolio/exposure", {}, q),
  pnl: (q: TimeQuery & { account_id: string }) => endpoint("GET", "/portfolio/pnl", {}, q),
  greeks: (q: TimeQuery & { account_id: string }) =>
    endpoint("GET", "/portfolio/greeks", {}, q),
  attribution: (q: TimeQuery & { account_id: string; group_by?: string }) =>
    endpoint("GET", "/portfolio/attribution", {}, q),
  snapshots: (q: { account_id?: string; limit?: number } = {}) =>
    endpoint("GET", "/portfolio/snapshots", {}, q),
  snapshot: (content_digest: string) =>
    endpoint("GET", "/portfolio/snapshots/{content_digest}", { content_digest }),
  reconcilePositions: () => endpoint("POST", "/portfolio/position-reconciliation"),
  positionReconciliation: (run_id: string) =>
    endpoint("GET", "/portfolio/position-reconciliation/{run_id}", { run_id }),
};
