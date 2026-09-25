/**
 * Real-time behaviour. Phase 12 brief §21: do not invent a stream around an
 * endpoint that does not exist; show the degraded state correctly.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { ROUTE_PATHS } from "@/lib/api/contract.generated";
import {
  DEGRADED_REFRESH_MS,
  SPECIFIED_STREAM_CHANNELS,
  STREAM_ENDPOINT_IMPLEMENTED,
  realtimeStatus,
} from "@/lib/terminal/realtime";

test("the SSE endpoint really is absent from the backend", () => {
  assert.equal(
    [...ROUTE_PATHS].some((t) => t.includes("/stream")),
    false,
  );
  // The constant must agree with the generated contract, or the terminal would
  // either poll a working stream or stream a missing endpoint.
  assert.equal(STREAM_ENDPOINT_IMPLEMENTED, false);
});

test("a live view polls, and says it is not a live feed", () => {
  const status = realtimeStatus({ live: true });
  assert.equal(status.capability, "POLLED");
  assert.equal(status.refreshIntervalMs, DEGRADED_REFRESH_MS);
  assert.match(status.label, /NOT A LIVE FEED/);
  assert.match(status.explanation, /not implemented/);
});

test("an unchanged value under polling is explicitly ambiguous", () => {
  assert.match(
    realtimeStatus({ live: true }).explanation,
    /may mean nothing moved or may mean nothing has been re-read/,
  );
});

test("a pinned historical view does not refresh at all", () => {
  const status = realtimeStatus({ live: false });
  assert.equal(status.capability, "MANUAL");
  assert.equal(status.refreshIntervalMs, null);
  assert.match(status.explanation, /immutable/);
});

test("the specified channels are recorded so the gap stays legible", () => {
  assert.deepEqual(SPECIFIED_STREAM_CHANNELS, [
    "market_state",
    "signals",
    "alerts",
    "orders",
    "portfolio",
    "data_quality",
  ]);
});

test("every realtime status is legible without colour", () => {
  for (const live of [true, false]) {
    const status = realtimeStatus({ live });
    assert.notEqual(status.glyph, "");
    assert.notEqual(status.srLabel, "");
  }
});
