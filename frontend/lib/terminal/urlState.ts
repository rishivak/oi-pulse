/**
 * Shared view state, held in the URL.
 *
 * `13-FRONTEND_IA.md` §8: "Shared time/expiry/underlying state in URL parameters, so
 * any view is linkable and shareable — including a historical `as_of`. This falls
 * directly out of the API being time-parameterized."
 *
 * The URL is the single source of truth for these four values. That is a deliberate
 * choice against a client-side store, and the reason is §4's other requirement: the
 * expiry selector is "**real state**, propagated into every query and shared across
 * screens. The legacy inert `expiries[0]` selector is the specific defect being
 * designed out." A selector backed by component state can be inert without anything
 * failing; a selector backed by the URL cannot, because the URL is what the request
 * is built from.
 *
 * Brief §25 requires local UI state and domain state to be separated. Nothing here
 * is domain state: these are *query parameters*, and changing one changes which
 * question is asked, never what the answer was.
 */

import { type TimeAxes, IncoherentTimeAxes } from "@/lib/terminal/time";

export interface ViewState {
  readonly underlyingId: number | null;
  /** A concrete expiry id, or one of the relative selectors `12` §2 allows. */
  readonly expiry: string | null;
  readonly axes: TimeAxes;
  readonly accountId: string | null;
}

export const EMPTY_VIEW_STATE: ViewState = {
  underlyingId: null,
  expiry: null,
  axes: { marketTime: null, knowledgeTime: null },
  accountId: null,
};

export const PARAM_UNDERLYING = "underlying_id";
export const PARAM_EXPIRY = "expiry";
export const PARAM_MARKET_TIME = "market_time";
export const PARAM_KNOWLEDGE_TIME = "knowledge_time";
export const PARAM_ACCOUNT = "account_id";

/** The relative expiry selectors the API resolves against `market_time` (`12` §2). */
export const RELATIVE_EXPIRIES = ["front", "next", "monthly", "all"] as const;

function readParam(params: URLSearchParams, key: string): string | null {
  const raw = params.get(key);
  return raw === null || raw === "" ? null : raw;
}

/**
 * Read view state from a query string.
 *
 * An unparseable `underlying_id` becomes `null` rather than `0`. Zero is a valid
 * looking id, and a screen that requested underlying 0 would get a 404 that reads
 * as "no data" — the substitution the brief §18 forbids, arriving through the back
 * door of a parser.
 */
export function parseViewState(search: string | URLSearchParams): ViewState {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const rawUnderlying = readParam(params, PARAM_UNDERLYING);
  const underlyingId =
    rawUnderlying !== null && /^\d+$/.test(rawUnderlying) ? Number(rawUnderlying) : null;
  const marketTime = readParam(params, PARAM_MARKET_TIME);
  const knowledgeTime = readParam(params, PARAM_KNOWLEDGE_TIME);
  if (marketTime !== null && knowledgeTime !== null && knowledgeTime < marketTime) {
    throw new IncoherentTimeAxes(marketTime, knowledgeTime);
  }
  return {
    underlyingId,
    expiry: readParam(params, PARAM_EXPIRY),
    axes: { marketTime, knowledgeTime },
    accountId: readParam(params, PARAM_ACCOUNT),
  };
}

/**
 * Serialise view state back to a query string.
 *
 * Absent values are omitted rather than written as empty parameters, so the "live,
 * latest" URL is the short one and a pinned historical URL is visibly different
 * from it in the address bar.
 */
export function serialiseViewState(state: ViewState): string {
  const params = new URLSearchParams();
  if (state.underlyingId !== null) {
    params.set(PARAM_UNDERLYING, String(state.underlyingId));
  }
  if (state.expiry !== null) params.set(PARAM_EXPIRY, state.expiry);
  if (state.axes.marketTime !== null) {
    params.set(PARAM_MARKET_TIME, state.axes.marketTime);
  }
  if (state.axes.knowledgeTime !== null) {
    params.set(PARAM_KNOWLEDGE_TIME, state.axes.knowledgeTime);
  }
  if (state.accountId !== null) params.set(PARAM_ACCOUNT, state.accountId);
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}

/** Carry the shared state onto another screen, so navigation does not reset it. */
export function linkTo(path: string, state: ViewState): string {
  return `${path}${serialiseViewState(state)}`;
}

/**
 * The time parameters for an outgoing request.
 *
 * `knowledge_time` is sent only when the user pinned one. Sending
 * `knowledge_time = market_time` explicitly would be equivalent, but it would also
 * make every request look like a deliberate two-axis query in the server log, and
 * the distinction between "defaulted" and "chosen" is worth keeping.
 */
export function timeQuery(state: ViewState): {
  market_time?: string;
  knowledge_time?: string;
} {
  const query: { market_time?: string; knowledge_time?: string } = {};
  if (state.axes.marketTime !== null) query.market_time = state.axes.marketTime;
  if (state.axes.knowledgeTime !== null) query.knowledge_time = state.axes.knowledgeTime;
  return query;
}
