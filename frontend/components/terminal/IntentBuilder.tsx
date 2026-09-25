"use client";

/**
 * The intent builder and its pre-trade risk preview.
 *
 * `13-FRONTEND_IA.md` §6: "Intent builder (multi-leg), pre-trade risk preview
 * showing every limit and its utilization". The chain the screen must preserve
 * (brief §12) is:
 *
 *     Signal -> Strategy -> TradeIntent -> Risk -> Paper Order -> Fill
 *
 * so the signal and strategy fields are part of the form rather than optional
 * metadata: an intent submitted without them produces an order whose provenance
 * chain has a hole in it, and the Provenance panel will say so.
 *
 * ### The preview is a server evaluation
 *
 * The button posts to `/risk/evaluate`, which runs the engine. It does not compute
 * a verdict here and there is no endpoint that would accept one — brief §13: "Do
 * not let users directly manufacture approvals from the UI." Submission is offered
 * only when `submissionPermitted` agrees, and that reads the server's own
 * `is_approved` and `actionable`, never the absence of breaches.
 *
 * ### One unverifiable shape, named
 *
 * `POST /paper-trading/accounts/{id}/intents` takes an untyped `dict` and builds
 * the intent from it, so the request body is the one thing in the terminal that
 * `tools/check_frontend_contract.py` cannot check against a generated key set. The
 * field names below are taken from `TradeIntent.as_dict()` in the generated
 * contract, which is the closest available evidence, and the gap is recorded in the
 * Phase 12 report rather than papered over.
 */

import { useState } from "react";

import { paperTrading, risk } from "@/lib/terminal/endpoints";
import { riskPreview, submissionPermitted } from "@/lib/terminal/screens/trading";
import { wasDuplicate } from "@/lib/terminal/client";
import { classify, useTerminalMutation } from "@/components/terminal/useTerminalQuery";
import { ErrorPanel } from "@/components/terminal/primitives";

/**
 * Every field is a string.
 *
 * Not laziness: the form holds what the operator typed, and narrowing `side` to
 * `"BUY" | "SELL"` here would make a computed-key update (`{...draft, [key]: value}`)
 * untypeable, which is usually resolved with a cast — and a cast in the one place
 * the terminal constructs an order is not a trade worth making. Coercion happens
 * once, in `intentBody`, where it is visible.
 */
export interface IntentDraft {
  readonly instrumentId: string;
  readonly side: string;
  readonly quantity: string;
  readonly orderType: string;
  readonly limitPrice: string;
  readonly signalId: string;
  readonly strategyId: string;
  readonly reason: string;
}

const EMPTY_DRAFT: IntentDraft = {
  instrumentId: "",
  side: "BUY",
  quantity: "",
  orderType: "LIMIT",
  limitPrice: "",
  signalId: "",
  strategyId: "",
  reason: "",
};

/** Build the request body. Pure, so the mapping is inspectable in one place. */
export function intentBody(
  accountId: string,
  draft: IntentDraft,
  axes: { marketTime: string | null; knowledgeTime: string | null },
): Record<string, unknown> {
  return {
    account_id: accountId,
    instrument_id: Number(draft.instrumentId),
    side: draft.side === "SELL" ? "SELL" : "BUY",
    quantity: Number(draft.quantity),
    order_type: draft.orderType === "MARKET" ? "MARKET" : "LIMIT",
    limit_price: draft.orderType === "LIMIT" ? draft.limitPrice : null,
    market_time: axes.marketTime,
    knowledge_time: axes.knowledgeTime ?? axes.marketTime,
    signal_id: draft.signalId === "" ? null : draft.signalId,
    strategy_id: draft.strategyId === "" ? null : draft.strategyId,
    reason: draft.reason === "" ? null : draft.reason,
  };
}

export function IntentBuilder({
  accountId,
  axes,
  orderEntryAllowed,
  disabledReason,
}: {
  accountId: string;
  axes: { marketTime: string | null; knowledgeTime: string | null };
  orderEntryAllowed: boolean;
  disabledReason: string | null;
}) {
  const [draft, setDraft] = useState<IntentDraft>(EMPTY_DRAFT);
  const evaluate = useTerminalMutation<Record<string, unknown>>(risk.evaluate());
  const submit = useTerminalMutation<Record<string, unknown>>(
    paperTrading.createIntent(accountId),
  );

  const preview = evaluate.data
    ? riskPreview(evaluate.data.data, evaluate.data.meta.raw)
    : null;
  const maySubmit =
    orderEntryAllowed && preview !== null && submissionPermitted(preview);

  const field = (key: keyof IntentDraft) => ({
    value: draft[key],
    onChange: (event: { target: { value: string } }) =>
      setDraft((current) => ({ ...current, [key]: event.target.value })),
  });

  const inputClass =
    "rounded border border-terminal-border bg-terminal-bg px-2 py-1 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";

  return (
    <section aria-label="Intent builder" className="rounded border border-terminal-border p-3">
      <h2 className="font-mono text-xs text-terminal-muted">INTENT</h2>

      {!orderEntryAllowed ? (
        <p className="mt-1 text-xs text-amber-300" role="note">
          <span aria-hidden="true">⚠ </span>
          {disabledReason ?? "Order entry is withheld."}
        </p>
      ) : null}

      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">INSTRUMENT ID</span>
          <input className={inputClass} inputMode="numeric" {...field("instrumentId")} />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">SIDE</span>
          <select className={inputClass} {...field("side")}>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">QUANTITY (units)</span>
          <input className={inputClass} inputMode="numeric" {...field("quantity")} />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">ORDER TYPE</span>
          <select className={inputClass} {...field("orderType")}>
            <option value="LIMIT">LIMIT</option>
            <option value="MARKET">MARKET</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">LIMIT PRICE (INR)</span>
          <input
            className={inputClass}
            disabled={draft.orderType !== "LIMIT"}
            {...field("limitPrice")}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">SIGNAL ID</span>
          <input className={inputClass} {...field("signalId")} />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-terminal-muted">STRATEGY ID</span>
          <input className={inputClass} {...field("strategyId")} />
        </label>
        <label className="flex flex-col gap-1 text-xs sm:col-span-2">
          <span className="text-terminal-muted">REASON</span>
          <input className={inputClass} {...field("reason")} />
        </label>
      </div>

      <p className="mt-2 text-[10px] text-terminal-muted">
        Quantity is in units, not lots. A NIFTY lot is 50 units, so one lot at 106.40
        is a turnover of 5,320.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={evaluate.isPending}
          onClick={() => evaluate.mutate(intentBody(accountId, draft, axes))}
          className="rounded border border-terminal-border px-3 py-1 font-mono text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          {evaluate.isPending ? "Evaluating…" : "Evaluate risk"}
        </button>
        <button
          type="button"
          disabled={!maySubmit || submit.isPending}
          title={
            maySubmit
              ? undefined
              : "Submission requires an approval the server produced for this intent."
          }
          onClick={() => submit.mutate(intentBody(accountId, draft, axes))}
          className="rounded border border-accent px-3 py-1 font-mono text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          {submit.isPending ? "Submitting…" : "Submit paper intent"}
        </button>
      </div>

      {preview ? (
        <div className="mt-3 rounded border border-terminal-border p-2" role="status">
          <p className="font-mono text-xs">
            VERDICT {preview.verdict}
            {preview.authorizationStatus ? ` · ${preview.authorizationStatus}` : ""}
            {preview.evaluated ? "" : " · NOT EVALUATED"}
          </p>
          <p className="mt-1 text-xs">
            {preview.breachCount} breach{preview.breachCount === 1 ? "" : "es"} ·{" "}
            {preview.unevaluableCount} limit
            {preview.unevaluableCount === 1 ? "" : "s"} could not be evaluated
            {preview.policy ? ` · policy ${preview.policy}` : ""}
          </p>
          {preview.reason ? <p className="mt-1 text-xs">{preview.reason}</p> : null}
          {!preview.evaluated ? (
            <p className="mt-1 text-xs text-amber-300">
              An unevaluated decision is not an approval. Submission stays disabled.
            </p>
          ) : null}
        </div>
      ) : null}

      {evaluate.isError ? <div className="mt-2"><ErrorPanel error={classify(evaluate.error)} /></div> : null}
      {submit.isError ? <div className="mt-2"><ErrorPanel error={classify(submit.error)} /></div> : null}

      {submit.data ? (
        <p className="mt-2 text-xs" role="status">
          {wasDuplicate(submit.data)
            ? "Already submitted. The backend recognised this intent by content address and applied nothing — no second order exists."
            : "Submitted. Follow it in the blotter below."}
        </p>
      ) : null}
    </section>
  );
}
