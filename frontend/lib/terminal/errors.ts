/**
 * Typed API errors, and the rule that a state-changing request is never retried.
 *
 * `12-API_SPEC.md` §4 defines the error codes; this maps each to how the terminal
 * behaves. The mapping is data rather than a switch buried in a component, so
 * "does a `RISK_REJECTED` get retried?" is answerable by reading one table.
 *
 * The Phase 12 brief §22 is explicit: "Do not silently retry state-changing
 * operations." That is enforced in {@link retryDecision} by method first and code
 * second — no error code can authorise a retry of a `POST`. The order matters. A
 * table keyed on the code alone would eventually acquire an entry someone believed
 * was safe to repeat, and a repeated intent submission is a second order.
 */

/** The stable machine-readable codes from `12` §4, plus transport-level cases. */
export type ApiErrorCode =
  | "FEATURE_NOT_AVAILABLE"
  | "INSUFFICIENT_HISTORY"
  | "QUALITY_REQUIREMENTS_UNMET"
  | "NO_DATA_FOR_PERIOD"
  | "STATE_UNRELIABLE"
  | "RISK_REJECTED"
  | "ORDER_STATE_UNKNOWN"
  | "LIVE_TRADING_DISABLED"
  | "AUTHENTICATION_REQUIRED"
  | "PERMISSION_DENIED"
  | "RATE_LIMITED"
  | "VALIDATION_FAILED"
  | "DEPENDENCY_UNAVAILABLE"
  | "SERVER_ERROR"
  | "NETWORK_UNAVAILABLE"
  | "UNKNOWN_ERROR";

export type ErrorPresentation = "EMPTY_STATE" | "INLINE" | "PANEL" | "BLOCKING";

export interface ApiErrorMeaning {
  readonly code: ApiErrorCode;
  readonly httpStatus: number | null;
  readonly title: string;
  /** What the operator should understand, in their terms. */
  readonly guidance: string;
  /** How the screen shows it. An empty state is not an error banner. */
  readonly presentation: ErrorPresentation;
  /** Whether a *read* may be retried automatically. Writes never may. */
  readonly readRetryable: boolean;
}

const MEANINGS: Readonly<Record<ApiErrorCode, Omit<ApiErrorMeaning, "code">>> = {
  FEATURE_NOT_AVAILABLE: {
    httpStatus: 422,
    title: "Not yet available at this timestamp",
    guidance:
      "The feature's availability is later than the decision time in force. " +
      "This is the look-ahead refusal, not a failure — move decision time later to see it.",
    presentation: "EMPTY_STATE",
    readRetryable: false,
  },
  INSUFFICIENT_HISTORY: {
    httpStatus: 422,
    title: "Insufficient history",
    guidance: "The statistic needs more stored history than the period provides.",
    presentation: "EMPTY_STATE",
    readRetryable: false,
  },
  QUALITY_REQUIREMENTS_UNMET: {
    httpStatus: 422,
    title: "Quality requirements unmet",
    guidance: "The feature declined to compute rather than compute from degraded inputs.",
    presentation: "EMPTY_STATE",
    readRetryable: false,
  },
  NO_DATA_FOR_PERIOD: {
    httpStatus: 404,
    title: "No data for this period",
    guidance: "Nothing was observed in the requested window.",
    presentation: "EMPTY_STATE",
    readRetryable: false,
  },
  STATE_UNRELIABLE: {
    httpStatus: 409,
    title: "State unreliable",
    guidance: "The operation was refused because the underlying state is unreliable.",
    presentation: "PANEL",
    readRetryable: false,
  },
  RISK_REJECTED: {
    httpStatus: 422,
    title: "Rejected by risk",
    guidance:
      "Risk refused the intent. The full reason set is in the decision; no order was created.",
    presentation: "PANEL",
    readRetryable: false,
  },
  ORDER_STATE_UNKNOWN: {
    httpStatus: 409,
    title: "Order state unknown — reconciliation required",
    guidance:
      "The order's state cannot be established. It is not assumed rejected and not " +
      "assumed accepted, and it blocks its instrument for this strategy until " +
      "reconciliation resolves it (11-TRADING.md §5).",
    presentation: "BLOCKING",
    readRetryable: false,
  },
  LIVE_TRADING_DISABLED: {
    httpStatus: 403,
    title: "Live trading disabled",
    guidance: "Live execution is off. The terminal has no live order path.",
    presentation: "PANEL",
    readRetryable: false,
  },
  AUTHENTICATION_REQUIRED: {
    httpStatus: 401,
    title: "Not signed in",
    guidance:
      "There is no valid session. It may have expired, been revoked, or never " +
      "existed — the server does not say which, so that a probe cannot learn " +
      "whether a session id was real. Sign in again; nothing on this screen is current.",
    presentation: "BLOCKING",
    readRetryable: false,
  },
  PERMISSION_DENIED: {
    httpStatus: 403,
    title: "Permission denied",
    guidance:
      "The session is valid and lacks the permission this view requires, or the " +
      "resource belongs to another identity. Hiding the control would not have " +
      "changed this: the refusal is the server's.",
    presentation: "PANEL",
    readRetryable: false,
  },
  RATE_LIMITED: {
    httpStatus: 429,
    title: "Rate limited",
    guidance: "Too many requests. Honour Retry-After before asking again.",
    presentation: "INLINE",
    readRetryable: true,
  },
  VALIDATION_FAILED: {
    httpStatus: 422,
    title: "Request rejected",
    guidance: "The request was not valid. Correct the inputs and resubmit deliberately.",
    presentation: "INLINE",
    readRetryable: false,
  },
  DEPENDENCY_UNAVAILABLE: {
    httpStatus: 503,
    title: "Store unavailable",
    guidance:
      "The process serving this view has no reader configured for it. An empty " +
      "list is not shown, because it would be indistinguishable from there being nothing.",
    presentation: "PANEL",
    readRetryable: true,
  },
  SERVER_ERROR: {
    httpStatus: 500,
    title: "Server error",
    guidance: "The backend failed to answer. Nothing can be concluded from this view.",
    presentation: "PANEL",
    readRetryable: true,
  },
  NETWORK_UNAVAILABLE: {
    httpStatus: null,
    title: "Backend unavailable",
    guidance: "No response from the API. Displayed values are not current.",
    presentation: "BLOCKING",
    readRetryable: true,
  },
  UNKNOWN_ERROR: {
    httpStatus: null,
    title: "Unrecognised error",
    guidance: "The backend returned an error this build does not know how to explain.",
    presentation: "PANEL",
    readRetryable: false,
  },
};

export function meaningOf(code: ApiErrorCode): ApiErrorMeaning {
  return { code, ...MEANINGS[code] };
}

export interface ApiErrorBody {
  readonly error?: {
    readonly code?: string;
    readonly message?: string;
    readonly details?: Readonly<Record<string, unknown>>;
  };
  /** FastAPI's default shape, which several routers still use. */
  readonly detail?: unknown;
}

export interface ClassifiedError {
  readonly meaning: ApiErrorMeaning;
  /** The backend's own message, never replaced by ours. */
  readonly message: string | null;
  readonly details: Readonly<Record<string, unknown>> | null;
  readonly httpStatus: number | null;
}

const BY_STATUS: Readonly<Record<number, ApiErrorCode>> = {
  401: "AUTHENTICATION_REQUIRED",
  403: "PERMISSION_DENIED",
  404: "NO_DATA_FOR_PERIOD",
  409: "STATE_UNRELIABLE",
  422: "VALIDATION_FAILED",
  429: "RATE_LIMITED",
  500: "SERVER_ERROR",
  502: "SERVER_ERROR",
  503: "DEPENDENCY_UNAVAILABLE",
  504: "SERVER_ERROR",
};

function isKnownCode(value: unknown): value is ApiErrorCode {
  return typeof value === "string" && value in MEANINGS;
}

/**
 * Turn a response into a classified error.
 *
 * The body's `code` wins when it is one we know, because the backend is the
 * authority on what went wrong. HTTP status is the fallback, and an unmapped status
 * becomes `UNKNOWN_ERROR` rather than being bucketed into the nearest familiar code
 * — a wrong-but-plausible explanation is worse than an admitted gap.
 */
export function classifyError(
  httpStatus: number | null,
  body: ApiErrorBody | null,
): ClassifiedError {
  const raw = body?.error?.code;
  let code: ApiErrorCode;
  if (httpStatus === 401) {
    // The access gate answers 401 with `PERMISSION_DENIED`, because
    // `12-API_SPEC.md` §4 defines no code for "not authenticated". The status is
    // the more specific signal here, and the two conditions need different
    // treatments: one is "sign in", the other is "ask for a permission".
    code = "AUTHENTICATION_REQUIRED";
  } else if (isKnownCode(raw)) {
    code = raw;
  } else if (httpStatus === null) {
    code = "NETWORK_UNAVAILABLE";
  } else {
    code = BY_STATUS[httpStatus] ?? "UNKNOWN_ERROR";
  }
  const message =
    body?.error?.message ??
    (typeof body?.detail === "string" ? body.detail : null) ??
    null;
  return {
    meaning: meaningOf(code),
    message,
    details: body?.error?.details ?? null,
    httpStatus,
  };
}

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RetryDecision {
  readonly retry: boolean;
  readonly reason: string;
}

/**
 * Whether the terminal may reissue a request by itself.
 *
 * Method is checked before code, and the non-`GET` answer is unconditional. A
 * `POST /paper-trading/accounts/{id}/intents` is idempotent *on the server* by
 * content address, but "the server would deduplicate it" is not the same claim as
 * "the client may repeat it", and only the first is established here.
 */
export function retryDecision(method: HttpMethod, error: ClassifiedError): RetryDecision {
  if (method !== "GET") {
    return {
      retry: false,
      reason:
        "state-changing requests are never retried automatically; the operator resubmits",
    };
  }
  if (!error.meaning.readRetryable) {
    return { retry: false, reason: `${error.meaning.code} is not a transient condition` };
  }
  return { retry: true, reason: `${error.meaning.code} may be transient` };
}
