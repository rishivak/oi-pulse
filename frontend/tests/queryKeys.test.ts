/**
 * Cache keys and immutability. `12-API_SPEC.md` §5 and brief §20.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { isImmutable, keyFor, staleTimeMs } from "@/lib/terminal/queryKeys";
import { market, signals } from "@/lib/terminal/endpoints";

test("the same question is the same key, whatever order the parameters arrive in", () => {
  const a = keyFor(market.state({ underlying_id: 1, market_time: "T", knowledge_time: "K" }));
  const b = keyFor(market.state({ knowledge_time: "K", market_time: "T", underlying_id: 1 }));
  assert.deepEqual(a, b);
});

test("different questions are different keys", () => {
  assert.notDeepEqual(
    keyFor(market.state({ underlying_id: 1 })),
    keyFor(market.state({ underlying_id: 2 })),
  );
});

test("absent parameters do not create a distinct key", () => {
  assert.deepEqual(
    keyFor(market.state({ underlying_id: 1 })),
    keyFor(market.state({ underlying_id: 1, market_time: undefined })),
  );
});

test("the method is part of the key", () => {
  assert.equal(keyFor(signals.types())[0], "GET");
});

test("a pinned market time makes the answer immutable", () => {
  assert.equal(isImmutable(market.state({ underlying_id: 1, market_time: "T" })), true);
  assert.equal(isImmutable(market.state({ underlying_id: 1 })), false);
});

test("an immutable answer never goes stale, so it is never refetched", () => {
  assert.equal(
    staleTimeMs(market.state({ underlying_id: 1, market_time: "T" }), 30_000),
    Number.POSITIVE_INFINITY,
  );
  assert.equal(staleTimeMs(market.state({ underlying_id: 1 }), 30_000), 30_000);
});
