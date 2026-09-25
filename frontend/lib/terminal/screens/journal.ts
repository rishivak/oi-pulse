/**
 * Journal presentation.
 *
 * `13-FRONTEND_IA.md` §6 describes the Journal screen as "Entries linked to
 * signals, intents and trades. Supports revisiting whether the original hypothesis
 * was correct." `12-API_SPEC.md` §3 specifies `/journal` as "CRUD on entries,
 * linkable to signals, intents and trades."
 *
 * What the backend actually holds is an **accounting journal**: the Phase 8
 * `journal_entries` table records cash movements — `entry_type`, `cash_delta`,
 * `realized_pnl_delta`, `fees_delta`, `cash_after` — each traceable to an order and
 * a fill, and through the order to the intent, the risk decision and the signal.
 * The hypothesis notes §6 also describes have no column, no table and no writer in
 * any phase, and are not invented here.
 *
 * ### The empty state is two states
 *
 * Nothing in this build writes journal entries: Phase 8 created the table and
 * shipped no writer. So an empty page means either *this account recorded no cash
 * movement* — a fact about trading — or *no component writes entries* — a fact about
 * the system. {@link journalEmptyState} keeps them apart, because an operator who
 * read the second as the first would conclude their account had been quiet.
 */

import type { JournalEntryDto } from "@/lib/api/dto";
import { type EmptyState, type Presented, presentValue } from "@/lib/terminal/quality";

/** Mirrors `JournalAvailability` in `oipulse/trading/journal.py`. */
export type JournalAvailability =
  | "AVAILABLE"
  | "NO_ENTRIES_RECORDED"
  | "NO_WRITER_IMPLEMENTED";

/** Mirrors `JournalEntryType`. Checked against the backend by the boundary guard. */
export const JOURNAL_ENTRY_TYPES = [
  "OPENING",
  "FILL",
  "FEE",
  "RESERVATION",
  "RELEASE",
  "REALIZED_PNL",
] as const;

export type JournalEntryType = (typeof JOURNAL_ENTRY_TYPES)[number];

export interface JournalRow {
  readonly entryId: string;
  readonly entryType: string;
  readonly occurredAt: string;
  readonly cashDelta: string;
  readonly realizedPnlDelta: string;
  readonly feesDelta: string;
  readonly cashAfter: Presented<string>;
  /** The link `12` §3 requires. `null` where the entry had no order. */
  readonly orderId: string | null;
  readonly fillKey: string | null;
  readonly sourceEventKey: string;
}

export function journalRow(entry: JournalEntryDto): JournalRow {
  return {
    entryId: entry.entry_id,
    entryType: entry.entry_type,
    occurredAt: entry.occurred_at,
    cashDelta: entry.cash_delta,
    realizedPnlDelta: entry.realized_pnl_delta,
    feesDelta: entry.fees_delta,
    cashAfter: presentValue(entry.cash_after, "NOT_COMPUTED"),
    orderId: entry.order_id,
    fillKey: entry.fill_key,
    sourceEventKey: entry.source_event_key,
  };
}

/**
 * Read the availability the backend reported.
 *
 * An unrecognised or absent value becomes `NO_WRITER_IMPLEMENTED`, the more
 * cautious of the two empty readings: it tells the operator not to conclude
 * anything from the emptiness, which is the safe thing to be wrong about.
 */
export function journalAvailability(
  meta: Readonly<Record<string, unknown>>,
): JournalAvailability {
  const value = meta.availability;
  if (value === "AVAILABLE" || value === "NO_ENTRIES_RECORDED") return value;
  return "NO_WRITER_IMPLEMENTED";
}

export function writerImplemented(meta: Readonly<Record<string, unknown>>): boolean {
  return meta.writer_implemented === true;
}

/**
 * The empty state for an availability, distinct per reason.
 *
 * `NO_WRITER_IMPLEMENTED` is not one of `quality.ts`'s five kinds — those describe
 * data that could have been there. This one describes a component that does not
 * exist, which is a different sentence and deserves its own.
 */
export function journalEmptyState(availability: JournalAvailability): EmptyState {
  if (availability === "NO_ENTRIES_RECORDED") {
    return {
      kind: "NO_DATA_FOR_PERIOD",
      title: "No journal entries for this account and period",
      detail:
        "The journal store is readable and holds nothing here. This is a fact about " +
        "the account, not about the system.",
    };
  }
  return {
    kind: "NO_DATA_FOR_PERIOD",
    title: "No component in this build writes journal entries",
    detail:
      "Phase 8 created `journal_entries` and shipped no writer, so the table has " +
      "never been populated. This emptiness says nothing about whether the account " +
      "traded — do not read it as a quiet account.",
  };
}

export interface JournalTotals {
  readonly entries: number;
  /** Closing cash from the most recent entry, or absent when there is none. */
  readonly latestCashAfter: Presented<string>;
}

/**
 * Summarise a page.
 *
 * Deliberately does not sum the deltas. `cash_after` is the backend's running
 * balance and adding the deltas here would be a second, competing computation of
 * the same quantity — `13-FRONTEND_IA.md` §1.3 puts that on the backend, and a
 * frontend total that disagreed with `cash_after` would be unresolvable from the UI.
 */
export function journalTotals(rows: readonly JournalRow[]): JournalTotals {
  return {
    entries: rows.length,
    latestCashAfter:
      rows.length === 0
        ? presentValue<string>(null, "NO_DATA_FOR_PERIOD")
        : rows[0].cashAfter,
  };
}
