"use client";

/**
 * Journal — what was I thinking?
 *
 * `13-FRONTEND_IA.md` §6. The screen renders the Phase 8 accounting journal over
 * the `/journal/entries` contract `12-API_SPEC.md` §3 specifies: every entry with
 * its cash movement, and the order and fill that caused it.
 *
 * ### What this screen does not show, and says so
 *
 * §6 also describes free-text reflection — "Supports revisiting whether the
 * original hypothesis was correct." There is no column for it in
 * `journal_entries`, no table anywhere, and no writer in any phase, so it is not
 * invented. The notice below states that rather than leaving a reader to wonder
 * why their notes are missing.
 *
 * ### The empty page is two different sentences
 *
 * Nothing in this build writes journal entries. So an empty result means either
 * the account recorded no cash movement or no writer exists, and
 * `journalEmptyState` renders a different sentence for each. An operator who read
 * the second as the first would conclude their account had been quiet.
 */

import { useState } from "react";

import type { JournalEntryDto } from "@/lib/api/dto";
import {
  JOURNAL_ENTRY_TYPES,
  journalAvailability,
  journalEmptyState,
  journalRow,
  journalTotals,
  writerImplemented,
} from "@/lib/terminal/screens/journal";
import { qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { journal } from "@/lib/terminal/endpoints";
import { DenseTable, Instant, Value } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("journal");

export default function JournalPage() {
  const { state } = useViewState();
  const [entryType, setEntryType] = useState<string>("");
  const accountId = state.accountId;

  const query = useTerminalQuery<readonly JournalEntryDto[]>(
    accountId === null
      ? null
      : journal.entries({
          account_id: accountId,
          entry_type: entryType === "" ? undefined : entryType,
          since: state.axes.marketTime ?? undefined,
          limit: 200,
        }),
  );
  const quality = query.data?.quality ?? qualityBadge(undefined);
  const meta = query.data?.meta.raw ?? {};
  const availability = journalAvailability(meta);
  const rows = (query.data?.data ?? []).map(journalRow);
  const totals = journalTotals(rows);

  if (accountId === null) {
    return (
      <ScreenFrame screen={SCREEN} state={state} quality={quality}>
        <p className="text-xs text-terminal-muted">
          No account is in context. Add <code>?account_id=…</code> to the URL, or reach
          this screen from Paper Trading, which carries the account across.
        </p>
      </ScreenFrame>
    );
  }

  return (
    <ScreenFrame
      screen={SCREEN}
      state={state}
      quality={quality}
      toolbar={
        <label className="flex items-center gap-2 font-mono text-xs">
          <span className="text-terminal-muted">ENTRY TYPE</span>
          <select
            className="rounded border border-terminal-border bg-terminal-bg px-2 py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            value={entryType}
            onChange={(event) => setEntryType(event.target.value)}
          >
            <option value="">all</option>
            {JOURNAL_ENTRY_TYPES.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
      }
    >
      <div className="space-y-4">
        {!writerImplemented(meta) ? (
          <p
            className="rounded border border-amber-700 bg-amber-950 p-2 text-xs text-amber-200"
            role="note"
          >
            <span aria-hidden="true">⚠ </span>
            No component in this build writes journal entries. Phase 8 created the
            table and shipped no writer, so this view reads a store that has never
            been populated. An empty result below is not evidence that the account
            was quiet.
          </p>
        ) : null}

        <p className="rounded border border-terminal-border p-2 text-xs text-terminal-muted">
          This is the accounting journal: cash movements, each traceable to the order
          and fill that caused it. The free-text hypothesis notes 13-FRONTEND_IA.md §6
          also describes have no schema in any phase and are not part of this build.
        </p>

        <QueryPanel
          query={query}
          label="journal entries"
          empty={journalEmptyState(availability)}
          isEmpty={(envelope) => envelope.data.length === 0}
        >
          {() => (
            <div className="space-y-2">
              <dl className="flex flex-wrap gap-x-6 font-mono text-xs">
                <div>
                  <dt className="inline text-terminal-muted">ENTRIES </dt>
                  <dd className="inline">{totals.entries}</dd>
                </div>
                <div>
                  <dt className="inline text-terminal-muted">LATEST CASH AFTER </dt>
                  <dd className="inline">
                    <Value presented={totals.latestCashAfter} />
                  </dd>
                </div>
              </dl>

              <DenseTable
                caption="Journal entries with cash movement and the order and fill that caused each"
                columns={[
                  {
                    key: "at",
                    header: "OCCURRED",
                    render: (r) => <Instant iso={r.occurredAt} />,
                  },
                  { key: "type", header: "TYPE", render: (r) => r.entryType },
                  {
                    key: "cash",
                    header: "CASH Δ",
                    unit: "INR",
                    align: "right",
                    render: (r) => r.cashDelta,
                  },
                  {
                    key: "realized",
                    header: "REALIZED Δ",
                    unit: "INR",
                    align: "right",
                    render: (r) => r.realizedPnlDelta,
                  },
                  {
                    key: "fees",
                    header: "FEES Δ",
                    unit: "INR",
                    align: "right",
                    render: (r) => r.feesDelta,
                  },
                  {
                    key: "after",
                    header: "CASH AFTER",
                    unit: "INR",
                    align: "right",
                    render: (r) => <Value presented={r.cashAfter} />,
                  },
                  {
                    key: "order",
                    header: "ORDER",
                    render: (r) =>
                      r.orderId === null ? (
                        <span className="text-terminal-muted">no order</span>
                      ) : (
                        r.orderId
                      ),
                  },
                  {
                    key: "fill",
                    header: "FILL",
                    render: (r) =>
                      r.fillKey === null ? (
                        <span className="text-terminal-muted">no fill</span>
                      ) : (
                        r.fillKey
                      ),
                  },
                ]}
                rows={rows}
                rowKey={(row) => row.entryId}
              />
            </div>
          )}
        </QueryPanel>
      </div>
    </ScreenFrame>
  );
}
