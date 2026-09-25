"use client";

/**
 * Paper Trading — what would this trade do?
 *
 * `13-FRONTEND_IA.md` §6: "Intent builder (multi-leg), pre-trade risk preview
 * showing every limit and its utilization, order blotter with full state including
 * `UNKNOWN`, fills, positions."
 *
 * Four safety properties, each enforced by a pure module rather than by this
 * component's care:
 *
 * 1. **The mode is never assumed.** `executionMode` returns `UNKNOWN` unless the
 *    response states `mode: PAPER` with `live_execution_available: false`, and
 *    order entry is withheld on `UNKNOWN`.
 * 2. **No live path exists.** `liveTradingRenderable()` is typed to return the
 *    literal `false`, and `lib/terminal/endpoints.ts` has no live route to call.
 * 3. **`UNKNOWN` blocks.** `blockedInstruments` keys on `(strategy, instrument)`
 *    per `11-TRADING.md` §5, and a blocked pair offers no action.
 * 4. **A resubmission is never automatic.** `useTerminalMutation` sets
 *    `retry: false`, and a duplicate is reported as a duplicate rather than
 *    appearing as a second order.
 */

import { useState } from "react";

import type { FillDto, OrderDto, PaperAccountDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import {
  blockedInstruments,
  isBlockedFor,
  fillRow,
  omsRow,
  orderChain,
} from "@/lib/terminal/screens/trading";
import { LIVE_WITHHELD_NOTICE, executionMode } from "@/lib/terminal/mode";
import { paperTrading } from "@/lib/terminal/endpoints";
import { wasDuplicate } from "@/lib/terminal/client";
import {
  DenseTable,
  Instant,
  StateBadgeView,
} from "@/components/terminal/primitives";
import { IntentBuilder } from "@/components/terminal/IntentBuilder";
import { ProvenanceTrail } from "@/components/terminal/ProvenanceTrail";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import {
  useTerminalMutation,
  useTerminalQuery,
} from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("paper-trading");

function CancelButton({
  accountId,
  orderId,
  enabled,
}: {
  accountId: string;
  orderId: string;
  enabled: boolean;
}) {
  const mutation = useTerminalMutation<unknown>(
    paperTrading.cancelOrder(accountId, orderId),
  );
  if (!enabled) {
    return (
      <span className="text-terminal-muted" title="This order cannot be cancelled from here.">
        —
      </span>
    );
  }
  return (
    <button
      type="button"
      disabled={mutation.isPending}
      onClick={() => mutation.mutate(undefined)}
      className="rounded border border-terminal-border px-2 py-0.5 text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
      {mutation.isPending ? "Cancelling…" : "Cancel"}
    </button>
  );
}

export default function PaperTradingPage() {
  const { state } = useViewState();
  const [selectedOrder, setSelectedOrder] = useState<string | null>(null);

  const accountsQuery = useTerminalQuery<readonly PaperAccountDto[]>(
    paperTrading.accounts(),
  );
  const accountId = state.accountId ?? accountsQuery.data?.data[0]?.account_id ?? null;

  const ordersQuery = useTerminalQuery<readonly OrderDto[]>(
    accountId === null ? null : paperTrading.orders(accountId, { limit: 200 }),
  );
  const fillsQuery = useTerminalQuery<readonly FillDto[]>(
    accountId === null ? null : paperTrading.fills(accountId, { limit: 200 }),
  );

  const quality = ordersQuery.data?.quality ?? accountsQuery.data?.quality ?? qualityBadge(undefined);
  const mode = executionMode(
    ordersQuery.data?.meta.raw ?? accountsQuery.data?.meta.raw ?? {},
  );
  const orders = ordersQuery.data?.data ?? [];
  const blocked = blockedInstruments(orders);
  const chosen = orders.find((order) => order.order_id === selectedOrder) ?? null;
  const duplicate = ordersQuery.data ? wasDuplicate(ordersQuery.data) : false;

  return (
    <ScreenFrame
      screen={SCREEN}
      state={state}
      quality={quality}
      toolbar={
        <span
          className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-xs ${
            mode.mode === "PAPER"
              ? "border-bull-dim text-bull"
              : "border-amber-500 text-amber-300"
          }`}
          title={mode.explanation}
          data-execution-mode={mode.mode}
        >
          <span aria-hidden="true">{mode.glyph}</span>
          {mode.label}
          <span className="sr-only">{mode.srLabel}</span>
        </span>
      }
    >
      <div className="space-y-4">
        <p className="rounded border border-terminal-border p-2 text-xs text-terminal-muted">
          {LIVE_WITHHELD_NOTICE}
        </p>

        {!mode.orderEntryAllowed ? (
          <p className="rounded border border-amber-600 bg-amber-950 p-2 text-xs text-amber-200" role="alert">
            <span aria-hidden="true">⚠ </span>
            {mode.explanation}
          </p>
        ) : null}

        {duplicate ? (
          <p className="rounded border border-terminal-border p-2 text-xs" role="status">
            This intent was already submitted. The backend recognised it by content
            address and applied nothing; no second order was created.
          </p>
        ) : null}

        {blocked.size > 0 ? (
          <p className="rounded border border-bear bg-bear-dim p-2 text-xs" role="alert">
            <span aria-hidden="true">⚠ </span>
            {blocked.size} instrument/strategy pair
            {blocked.size === 1 ? " is" : "s are"} blocked by an order whose state cannot
            be established. Those orders are not assumed rejected, are not assumed
            accepted, and will not be resubmitted until reconciliation resolves them.
          </p>
        ) : null}

        {accountId !== null ? (
          <IntentBuilder
            accountId={accountId}
            axes={state.axes}
            orderEntryAllowed={mode.orderEntryAllowed}
            disabledReason={mode.orderEntryAllowed ? null : mode.explanation}
          />
        ) : null}

        <section aria-label="Order blotter">
          <h2 className="font-mono text-xs text-terminal-muted">BLOTTER</h2>
          {accountId === null ? (
            <p className="text-xs text-terminal-muted">No paper account is available.</p>
          ) : (
            <QueryPanel
              query={ordersQuery}
              label="orders"
              empty={emptyState("NO_DATA_FOR_PERIOD")}
              isEmpty={(envelope) => envelope.data.length === 0}
            >
              {(envelope) => (
                <DenseTable
                  caption="Paper orders with local state, provider status and provider identity"
                  columns={[
                    {
                      key: "order",
                      header: "ORDER",
                      render: (r) => (
                        <button
                          type="button"
                          onClick={() => setSelectedOrder(r.orderId)}
                          className="text-accent underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                        >
                          {r.orderId}
                        </button>
                      ),
                    },
                    { key: "instrument", header: "INSTRUMENT", render: (r) => r.instrumentId },
                    { key: "side", header: "SIDE", render: (r) => r.side },
                    { key: "qty", header: "QTY", align: "right", render: (r) => r.quantity },
                    {
                      key: "filled",
                      header: "FILLED",
                      align: "right",
                      render: (r) => `${r.filledQuantity}/${r.quantity}`,
                    },
                    {
                      key: "local",
                      header: "LOCAL STATE",
                      render: (r) => <StateBadgeView badge={r.localState} />,
                    },
                    {
                      key: "provider-status",
                      header: "PROVIDER STATE",
                      render: (r) => (
                        <span className="text-terminal-muted">
                          {r.providerStatus ?? "no statement"}
                        </span>
                      ),
                    },
                    {
                      key: "provider-id",
                      header: "PROVIDER ID",
                      render: (r) => (
                        <span title={r.providerIdentity.detail}>
                          <span aria-hidden="true">{r.providerIdentity.glyph} </span>
                          {r.providerIdentity.label}
                          <span className="sr-only">{r.providerIdentity.srLabel}</span>
                        </span>
                      ),
                    },
                    {
                      key: "cancel",
                      header: "ACTION",
                      render: (r) => (
                        <CancelButton
                          accountId={accountId}
                          orderId={r.orderId}
                          enabled={
                            r.canCancel &&
                            !isBlockedFor(blocked, null, r.instrumentId) &&
                            mode.orderEntryAllowed
                          }
                        />
                      ),
                    },
                  ]}
                  rows={envelope.data.map(omsRow)}
                  rowKey={(row) => row.orderId}
                  rowClassName={(row) => (row.blocked ? "bg-bear-dim/30" : undefined)}
                />
              )}
            </QueryPanel>
          )}
        </section>

        {chosen !== null && accountId !== null ? (
          <ProvenanceTrail
            steps={orderChain(accountId, chosen)}
            label={`Provenance for order ${chosen.order_id}`}
          />
        ) : null}

        <section aria-label="Fills">
          <h2 className="font-mono text-xs text-terminal-muted">FILLS</h2>
          {accountId === null ? null : (
            <QueryPanel
              query={fillsQuery}
              label="fills"
              empty={emptyState("NO_DATA_FOR_PERIOD")}
              isEmpty={(envelope) => envelope.data.length === 0}
            >
              {(envelope) => (
                <DenseTable
                  caption="Fills with price source and whether the price rested on an assumption"
                  columns={[
                    { key: "instrument", header: "INSTRUMENT", render: (r) => r.instrumentId },
                    { key: "side", header: "SIDE", render: (r) => r.side },
                    {
                      key: "qty",
                      header: "QTY",
                      align: "right",
                      render: (r) =>
                        r.isPartial ? `${r.quantity}/${r.requestedQuantity}` : r.quantity,
                    },
                    { key: "price", header: "PRICE", unit: "INR", align: "right", render: (r) => r.price },
                    { key: "source", header: "PRICE SOURCE", render: (r) => r.priceSource },
                    {
                      key: "slippage",
                      header: "SLIPPAGE/UNIT",
                      align: "right",
                      render: (r) => r.slippagePerUnit ?? "—",
                    },
                    {
                      key: "assumed",
                      header: "ASSUMED SPREAD",
                      render: (r) =>
                        r.assumptionBased ? (
                          <span className="text-amber-400" title="Priced against an assumed spread, not an observed quote.">
                            <span aria-hidden="true">⚠ </span>yes
                          </span>
                        ) : (
                          "no"
                        ),
                    },
                    { key: "at", header: "FILLED AT", render: (r) => <Instant iso={r.filledAt} /> },
                  ]}
                  rows={envelope.data.map(fillRow)}
                  rowKey={(row, index) => `${row.instrumentId}-${row.filledAt}-${index}`}
                />
              )}
            </QueryPanel>
          )}
        </section>
      </div>
    </ScreenFrame>
  );
}
