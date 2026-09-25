/**
 * The single request surface. `lib/terminal/client.ts`.
 *
 * `fetch` is stubbed rather than mocked with a library: Node has a global `fetch`,
 * and replacing it is enough to observe exactly what the client sends.
 */
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

import {
  ApiRequestFailed,
  BASE,
  CSRF_COOKIE,
  CSRF_HEADER,
  isStateChanging,
  request,
  wasDuplicate,
} from "@/lib/terminal/client";
import { alerts, market, signals } from "@/lib/terminal/endpoints";
import { parseEnvelope } from "@/lib/terminal/envelope";

interface Captured {
  url: string;
  init: RequestInit;
}

let captured: Captured[] = [];
const realFetch = globalThis.fetch;

function stub(status: number, body: unknown) {
  captured = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    captured.push({ url: String(url), init });
    return {
      ok: status >= 200 && status < 300,
      status,
      text: async () => (body === undefined ? "" : JSON.stringify(body)),
    };
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  // No document in Node, so `readCookie` returns null unless a test supplies one.
  delete (globalThis as { document?: unknown }).document;
});

afterEach(() => {
  globalThis.fetch = realFetch;
  delete (globalThis as { document?: unknown }).document;
});

test("requests go to the fixed same-origin prefix, never to a configurable host", async () => {
  stub(200, { data: {}, meta: {} });
  await request(market.state({ underlying_id: 1 }));
  assert.equal(captured[0].url, `${BASE}/market/state?underlying_id=1`);
  assert.equal(BASE, "/api/v2");
});

test("the session cookie travels same-origin", async () => {
  stub(200, { data: {}, meta: {} });
  await request(signals.types());
  assert.equal(captured[0].init.credentials, "same-origin");
});

test("a GET carries no CSRF token", async () => {
  stub(200, { data: {}, meta: {} });
  await request(signals.types());
  const headers = captured[0].init.headers as Record<string, string>;
  assert.equal(headers[CSRF_HEADER], undefined);
});

test("a state-changing request echoes the double-submit token", async () => {
  (globalThis as { document?: unknown }).document = {
    cookie: `${CSRF_COOKIE}=tok-123; other=x`,
  };
  stub(200, { data: {}, meta: {} });
  await request(alerts.acknowledge("occ-1"), { body: {} });
  const headers = captured[0].init.headers as Record<string, string>;
  assert.equal(headers[CSRF_HEADER], "tok-123");
  assert.equal(captured[0].init.method, "POST");
});

test("every non-GET method is state-changing", () => {
  assert.equal(isStateChanging("GET"), false);
  assert.equal(isStateChanging("HEAD"), false);
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    assert.equal(isStateChanging(method), true);
  }
});

test("a failed request throws a classified error rather than returning empty data", async () => {
  stub(422, { error: { code: "FEATURE_NOT_AVAILABLE", message: "available at 11:45" } });
  await assert.rejects(
    () => request(signals.types()),
    (error: unknown) => {
      assert.ok(error instanceof ApiRequestFailed);
      assert.equal(error.classified.meaning.code, "FEATURE_NOT_AVAILABLE");
      assert.equal(error.classified.message, "available at 11:45");
      return true;
    },
  );
});

test("a transport failure is distinct from any HTTP status", async () => {
  globalThis.fetch = (async () => {
    throw new TypeError("network down");
  }) as unknown as typeof fetch;
  await assert.rejects(
    () => request(signals.types()),
    (error: unknown) => {
      assert.ok(error instanceof ApiRequestFailed);
      assert.equal(error.classified.meaning.code, "NETWORK_UNAVAILABLE");
      assert.equal(error.classified.httpStatus, null);
      return true;
    },
  );
});

test("nothing is retried by the transport", async () => {
  stub(503, { detail: "no reader configured" });
  await assert.rejects(() => request(signals.types()));
  assert.equal(captured.length, 1, "the client reissued a request by itself");
});

test("a successful response arrives as an envelope, quality included", async () => {
  stub(200, {
    data: { ok: true },
    meta: { quality: { status: "DEGRADED", issues: [] }, market_time: "T" },
  });
  const envelope = await request<{ ok: boolean }>(signals.types());
  assert.equal(envelope.data.ok, true);
  assert.equal(envelope.quality.status, "DEGRADED");
  assert.equal(envelope.meta.marketTime, "T");
});

test("a duplicate submission is reported, not shown as a second order", () => {
  assert.equal(wasDuplicate(parseEnvelope({ data: {}, meta: { duplicate: true } })), true);
  assert.equal(wasDuplicate(parseEnvelope({ data: {}, meta: {} })), false);
});
