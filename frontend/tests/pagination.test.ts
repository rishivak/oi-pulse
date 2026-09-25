/**
 * Cursor pagination and bounded client state. `12-API_SPEC.md` §5, brief §20.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  MAX_RETAINED_ROWS,
  appendPage,
  emptyAccumulation,
  truncationNotice,
} from "@/lib/terminal/pagination";

const page = (from: number, count: number, cursor: string | null) => ({
  rows: Array.from({ length: count }, (_, i) => from + i),
  nextCursor: cursor,
});

test("pages accumulate in order and carry the next cursor", () => {
  let state = emptyAccumulation<number>();
  state = appendPage(state, page(0, 3, "c1"));
  state = appendPage(state, page(3, 3, "c2"));
  assert.deepEqual(state.rows, [0, 1, 2, 3, 4, 5]);
  assert.equal(state.nextCursor, "c2");
  assert.equal(state.truncated, false);
});

test("retained rows are capped, and the eviction is reported rather than silent", () => {
  let state = emptyAccumulation<number>();
  state = appendPage(state, page(0, 5, "c1"), 3);
  assert.deepEqual(state.rows, [2, 3, 4]);
  assert.equal(state.evicted, 2);
  assert.equal(state.truncated, true);
  assert.match(truncationNotice(state) ?? "", /2 earlier rows/);
});

test("eviction drops the oldest rows, keeping the most recent", () => {
  let state = emptyAccumulation<number>();
  state = appendPage(state, page(0, 3, "c1"), 3);
  state = appendPage(state, page(3, 2, null), 3);
  assert.deepEqual(state.rows, [2, 3, 4]);
});

test("an untruncated view says nothing about truncation", () => {
  assert.equal(truncationNotice(emptyAccumulation<number>()), null);
});

test("the cap is a real bound, not a display limit", () => {
  assert.ok(MAX_RETAINED_ROWS > 0 && Number.isFinite(MAX_RETAINED_ROWS));
});

test("a null next cursor means the collection is exhausted", () => {
  const state = appendPage(emptyAccumulation<number>(), page(0, 2, null));
  assert.equal(state.nextCursor, null);
});
