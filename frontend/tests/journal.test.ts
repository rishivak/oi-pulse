/**
 * Journal presentation. `13-FRONTEND_IA.md` §6, `12-API_SPEC.md` §3.
 *
 * The property worth testing is the one an operator's conclusion depends on: an
 * empty journal means two very different things, and the screen has to say which.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { JournalEntryDto } from "@/lib/api/dto";
import {
  JOURNAL_ENTRY_TYPES,
  journalAvailability,
  journalEmptyState,
  journalRow,
  journalTotals,
  writerImplemented,
} from "@/lib/terminal/screens/journal";

const entry = (over: Partial<JournalEntryDto> = {}): JournalEntryDto => ({
  entry_id: "j1",
  account_id: "a1",
  entry_type: "FILL",
  occurred_at: "2026-03-03T06:12:15Z",
  cash_delta: "-5320.00",
  realized_pnl_delta: "0.00",
  fees_delta: "20.00",
  cash_after: "94680.00",
  order_id: "o1",
  fill_key: "f1",
  source_event_key: "k1",
  ...over,
});

test("an entry carries the links 12-API_SPEC.md §3 requires", () => {
  const row = journalRow(entry());
  assert.equal(row.orderId, "o1");
  assert.equal(row.fillKey, "f1");
  assert.equal(row.sourceEventKey, "k1");
});

test("an entry with no order says so rather than showing a blank", () => {
  const row = journalRow(entry({ order_id: null, fill_key: null }));
  assert.equal(row.orderId, null);
  assert.equal(row.fillKey, null);
});

test("an empty account and an absent writer are different sentences", () => {
  const quiet = journalEmptyState("NO_ENTRIES_RECORDED");
  const absent = journalEmptyState("NO_WRITER_IMPLEMENTED");
  assert.notEqual(quiet.title, absent.title);
  assert.match(quiet.detail, /fact about the account, not about the system/);
  assert.match(absent.detail, /never been populated/);
  assert.match(absent.detail, /do not read it as a quiet account/);
});

test("an unstated availability is treated as the more cautious reading", () => {
  // Being wrong in this direction tells the operator to conclude nothing, which
  // is the safe thing to be wrong about.
  assert.equal(journalAvailability({}), "NO_WRITER_IMPLEMENTED");
  assert.equal(journalAvailability({ availability: "nonsense" }), "NO_WRITER_IMPLEMENTED");
  assert.equal(journalAvailability({ availability: "AVAILABLE" }), "AVAILABLE");
  assert.equal(
    journalAvailability({ availability: "NO_ENTRIES_RECORDED" }),
    "NO_ENTRIES_RECORDED",
  );
});

test("the writer flag is read from the response, never assumed", () => {
  assert.equal(writerImplemented({}), false);
  assert.equal(writerImplemented({ writer_implemented: true }), true);
});

test("the entry types are the backend's six", () => {
  assert.deepEqual(JOURNAL_ENTRY_TYPES, [
    "OPENING",
    "FILL",
    "FEE",
    "RESERVATION",
    "RELEASE",
    "REALIZED_PNL",
  ]);
});

test("totals do not re-derive the running balance", () => {
  // `cash_after` is the backend's figure. Summing the deltas here would be a
  // second computation of the same quantity, and a disagreement between the two
  // would be unresolvable from the UI.
  const rows = [entry({ cash_after: "94680.00" }), entry({ entry_id: "j2", cash_after: "100000.00" })].map(
    journalRow,
  );
  const totals = journalTotals(rows);
  assert.equal(totals.entries, 2);
  assert.equal(totals.latestCashAfter.kind === "present" && totals.latestCashAfter.value, "94680.00");
});

test("an empty page reports no balance rather than zero", () => {
  const totals = journalTotals([]);
  assert.equal(totals.entries, 0);
  assert.equal(totals.latestCashAfter.kind, "absent");
});
