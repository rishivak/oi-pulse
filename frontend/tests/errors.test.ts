/**
 * Error classification, and the rule that a write is never retried automatically.
 * `12-API_SPEC.md` §4 and Phase 12 brief §22.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { classifyError, meaningOf, retryDecision } from "@/lib/terminal/errors";

test("the backend's own code wins over the HTTP status", () => {
  const classified = classifyError(422, {
    error: { code: "FEATURE_NOT_AVAILABLE", message: "available at 11:45:02" },
  });
  assert.equal(classified.meaning.code, "FEATURE_NOT_AVAILABLE");
  assert.equal(classified.message, "available at 11:45:02");
});

test("a look-ahead refusal renders as an empty state, not as a failure banner", () => {
  assert.equal(meaningOf("FEATURE_NOT_AVAILABLE").presentation, "EMPTY_STATE");
});

test("an unknown status becomes UNKNOWN_ERROR rather than the nearest familiar code", () => {
  assert.equal(classifyError(418, null).meaning.code, "UNKNOWN_ERROR");
});

test("no response at all is a distinct, blocking condition", () => {
  const classified = classifyError(null, null);
  assert.equal(classified.meaning.code, "NETWORK_UNAVAILABLE");
  assert.equal(classified.meaning.presentation, "BLOCKING");
});

test("FastAPI's bare `detail` string is surfaced, not discarded", () => {
  const classified = classifyError(503, { detail: "no signal reader is configured" });
  assert.equal(classified.meaning.code, "DEPENDENCY_UNAVAILABLE");
  assert.equal(classified.message, "no signal reader is configured");
});

test("503 does not render an empty list", () => {
  // An empty list would be indistinguishable from "no signals fired", which is why
  // the backend answers 503 instead of [].
  assert.equal(meaningOf("DEPENDENCY_UNAVAILABLE").presentation, "PANEL");
  assert.match(meaningOf("DEPENDENCY_UNAVAILABLE").guidance, /indistinguishable/);
});

test("no error code authorises retrying a state-changing request", () => {
  const retryable = classifyError(429, { error: { code: "RATE_LIMITED" } });
  for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
    const decision = retryDecision(method, retryable);
    assert.equal(decision.retry, false, `${method} must not auto-retry`);
    assert.match(decision.reason, /never retried automatically/);
  }
});

test("a transient read may be retried", () => {
  const decision = retryDecision("GET", classifyError(429, null));
  assert.equal(decision.retry, true);
});

test("a look-ahead refusal is never retried, even as a read", () => {
  const error = classifyError(422, { error: { code: "FEATURE_NOT_AVAILABLE" } });
  assert.equal(retryDecision("GET", error).retry, false);
});

test("a 401 is authentication, distinct from a permission refusal", () => {
  // The access gate answers 401 with the code `PERMISSION_DENIED`, because
  // `12-API_SPEC.md` §4 defines no code for "not authenticated". The two need
  // different treatments -- sign in, versus ask for a permission -- so the status
  // wins over the code here.
  const unauth = classifyError(401, { error: { code: "PERMISSION_DENIED" } });
  assert.equal(unauth.meaning.code, "AUTHENTICATION_REQUIRED");
  assert.equal(unauth.meaning.presentation, "BLOCKING");
  assert.match(unauth.meaning.guidance, /does not say which/);

  const forbidden = classifyError(403, { error: { code: "PERMISSION_DENIED" } });
  assert.equal(forbidden.meaning.code, "PERMISSION_DENIED");
  assert.equal(forbidden.meaning.presentation, "PANEL");
});

test("a permission refusal says hiding the control would not have helped", () => {
  assert.match(meaningOf("PERMISSION_DENIED").guidance, /refusal is the server's/);
});

test("neither authentication nor permission is retried automatically", () => {
  for (const status of [401, 403]) {
    const error = classifyError(status, null);
    assert.equal(retryDecision("GET", error).retry, false);
    assert.equal(retryDecision("POST", error).retry, false);
  }
});

test("an UNKNOWN order state blocks rather than warns", () => {
  const meaning = meaningOf("ORDER_STATE_UNKNOWN");
  assert.equal(meaning.presentation, "BLOCKING");
  assert.match(meaning.guidance, /not assumed rejected and not assumed accepted/);
  assert.match(meaning.guidance, /blocks its instrument/);
});

test("risk rejection states that no order was created", () => {
  assert.match(meaningOf("RISK_REJECTED").guidance, /no order was created/);
});
