/**
 * State badges. Backend vocabulary, rendered without softening.
 *
 * Every enum here is copied from the backend, not paraphrased: `OrderState` from
 * `oipulse/trading/orders.py`, `SignalStatus` from `oipulse/signals/model.py`,
 * `DiscrepancyKind` and `Resolution` from `oipulse/trading/reconciliation/model.py`,
 * and `PositionDiscrepancyKind` from `oipulse/trading/portfolio/reconciliation.py`.
 * `tools/check_terminal_boundary.py` compares these lists against those sources, so
 * a state added to the domain cannot quietly render as "unknown" in the terminal.
 *
 * Every badge carries a `glyph` and an `srLabel` as well as a tone. §28 requires
 * status to survive without colour, and a trading terminal that says "red means
 * rejected" excludes a reader who cannot see the difference — on a screen where the
 * difference is an order.
 *
 * The one state this module treats specially is `UNKNOWN`. `11-TRADING.md` §5 calls
 * it "the state most systems omit", and its rules are hard: never assume rejected,
 * never assume accepted, never resubmit, and the order blocks its instrument for its
 * strategy until reconciliation resolves it. The badge says all of that, and
 * {@link blocksInstrument} is what screens consult before offering any action.
 */

export type BadgeTone = "NEUTRAL" | "POSITIVE" | "CAUTION" | "NEGATIVE" | "BLOCKING";

export interface StateBadge {
  readonly value: string;
  readonly label: string;
  readonly glyph: string;
  readonly tone: BadgeTone;
  readonly srLabel: string;
  readonly detail: string | null;
}

function badge(
  value: string,
  glyph: string,
  tone: BadgeTone,
  srLabel: string,
  detail: string | null = null,
): StateBadge {
  return { value, label: value, glyph, tone, srLabel, detail };
}

// ------------------------------------------------------------------- orders

export const ORDER_STATES = [
  "CREATED",
  "SUBMITTING",
  "SUBMITTED",
  "ACCEPTED",
  "OPEN",
  "PARTIALLY_FILLED",
  "FILLED",
  "CANCEL_PENDING",
  "CANCELLED",
  "REJECTED",
  "EXPIRED",
  "UNKNOWN",
] as const;

export type OrderState = (typeof ORDER_STATES)[number];

const ORDER_BADGES: Readonly<Record<OrderState, StateBadge>> = {
  CREATED: badge("CREATED", "○", "NEUTRAL", "order created, not yet submitted"),
  SUBMITTING: badge(
    "SUBMITTING",
    "◔",
    "CAUTION",
    "order in flight; the outcome is not yet known",
    "Handed to an adapter. Between here and an answer the order is genuinely in flight.",
  ),
  SUBMITTED: badge("SUBMITTED", "◑", "NEUTRAL", "acknowledged by the venue"),
  ACCEPTED: badge("ACCEPTED", "◕", "NEUTRAL", "taken by the venue"),
  OPEN: badge("OPEN", "◍", "NEUTRAL", "resting, awaiting a fillable quote"),
  PARTIALLY_FILLED: badge("PARTIALLY FILLED", "◐", "NEUTRAL", "partially filled"),
  FILLED: badge("FILLED", "●", "POSITIVE", "fully filled"),
  CANCEL_PENDING: badge(
    "CANCEL PENDING",
    "◌",
    "CAUTION",
    "cancel requested, not yet confirmed",
    "A cancel request does not mean the order is cancelled (11-TRADING.md §4).",
  ),
  CANCELLED: badge("CANCELLED", "⊘", "NEUTRAL", "cancelled"),
  REJECTED: badge("REJECTED", "✕", "NEGATIVE", "rejected by the venue"),
  EXPIRED: badge("EXPIRED", "⊗", "NEUTRAL", "expired"),
  UNKNOWN: badge(
    "UNKNOWN",
    "⚠",
    "BLOCKING",
    "order state unknown; reconciliation required before any further action",
    "The venue may have accepted this order and the answer was lost. It is not " +
      "assumed rejected and not assumed accepted, it will not be resubmitted, and it " +
      "blocks this instrument for this strategy until reconciliation resolves it " +
      "(11-TRADING.md §5).",
  ),
};

export function orderStateBadge(value: string): StateBadge {
  return (
    ORDER_BADGES[value as OrderState] ??
    badge(
      value,
      "?",
      "BLOCKING",
      `unrecognised order state ${value}`,
      "This build does not know this state. It is shown verbatim and treated as blocking.",
    )
  );
}

/**
 * Whether an order forbids further action on its instrument.
 *
 * Unrecognised states block too. A terminal that shrugged at a state it did not
 * understand would offer a cancel button beside an order it cannot describe.
 */
export function blocksInstrument(value: string): boolean {
  return value === "UNKNOWN" || !(ORDER_STATES as readonly string[]).includes(value);
}

/** Whether a cancel control may be offered. Never from UNKNOWN. */
export function cancellable(value: string): boolean {
  if (blocksInstrument(value)) return false;
  return value === "OPEN" || value === "ACCEPTED" || value === "PARTIALLY_FILLED";
}

// ------------------------------------------------------------------ signals

export const SIGNAL_STATUSES = [
  "FORMING",
  "ACTIVE",
  "CONFIRMED",
  "INVALIDATED",
  "EXPIRED",
  "FADED",
] as const;

export type SignalStatus = (typeof SIGNAL_STATUSES)[number];

const SIGNAL_BADGES: Readonly<Record<SignalStatus, StateBadge>> = {
  FORMING: badge(
    "FORMING",
    "◔",
    "CAUTION",
    "forming; entry conditions only partially met",
    "Not actionable. Treating FORMING as actionable would make every near-miss a signal.",
  ),
  ACTIVE: badge("ACTIVE", "●", "POSITIVE", "active"),
  CONFIRMED: badge("CONFIRMED", "◉", "POSITIVE", "confirmed"),
  INVALIDATED: badge("INVALIDATED", "✕", "NEGATIVE", "invalidated; terminal"),
  EXPIRED: badge("EXPIRED", "⊗", "NEUTRAL", "expired; terminal"),
  FADED: badge("FADED", "○", "NEUTRAL", "faded; terminal"),
};

export function signalStatusBadge(value: string): StateBadge {
  return (
    SIGNAL_BADGES[value as SignalStatus] ??
    badge(value, "?", "CAUTION", `unrecognised signal status ${value}`)
  );
}

/** `08` §3: only ACTIVE and CONFIRMED are states a consumer may act on. */
export function signalActionable(value: string): boolean {
  return value === "ACTIVE" || value === "CONFIRMED";
}

export function signalTerminal(value: string): boolean {
  return value === "INVALIDATED" || value === "EXPIRED" || value === "FADED";
}

// ----------------------------------------------------------- reconciliation

export const DISCREPANCY_KINDS = [
  "MATCH",
  "PROVIDER_AHEAD",
  "OMS_AHEAD",
  "QUANTITY_MISMATCH",
  "PRICE_MISMATCH",
  "STATUS_MISMATCH",
  "MISSING_AT_PROVIDER",
  "MISSING_LOCALLY",
  "UNKNOWN",
] as const;

export const RESOLUTIONS = [
  "NONE",
  "ORDER_STATE_APPLIED",
  "FILL_INSERTED",
  "POSITION_CORRECTED",
  "RECORDED_ONLY",
  "UNRESOLVED",
] as const;

export const POSITION_DISCREPANCY_KINDS = [
  "MATCH",
  "QUANTITY_MISMATCH",
  "SIDE_MISMATCH",
  "MISSING_AT_PROVIDER",
  "MISSING_LOCALLY",
  "UNKNOWN",
] as const;

export type DiscrepancyKind = (typeof DISCREPANCY_KINDS)[number];
export type Resolution = (typeof RESOLUTIONS)[number];

const DISCREPANCY_BADGES: Readonly<Record<DiscrepancyKind, StateBadge>> = {
  MATCH: badge("MATCH", "●", "POSITIVE", "local and provider views agree"),
  PROVIDER_AHEAD: badge(
    "PROVIDER AHEAD",
    "→",
    "CAUTION",
    "the provider has progressed further than recorded locally",
  ),
  OMS_AHEAD: badge(
    "OMS AHEAD",
    "←",
    "NEGATIVE",
    "local state shows progress the provider does not",
    "Almost always a local bug, and never resolved by pushing our view onto the provider.",
  ),
  QUANTITY_MISMATCH: badge("QUANTITY MISMATCH", "≠", "NEGATIVE", "quantities differ"),
  PRICE_MISMATCH: badge("PRICE MISMATCH", "≠", "NEGATIVE", "prices differ"),
  STATUS_MISMATCH: badge("STATUS MISMATCH", "≠", "NEGATIVE", "statuses differ"),
  MISSING_AT_PROVIDER: badge(
    "MISSING AT PROVIDER",
    "⊘",
    "NEGATIVE",
    "we hold an order the provider does not",
  ),
  MISSING_LOCALLY: badge(
    "MISSING LOCALLY",
    "⊕",
    "NEGATIVE",
    "the provider holds an order we do not",
  ),
  UNKNOWN: badge(
    "UNKNOWN",
    "⚠",
    "BLOCKING",
    "evidence insufficient to classify",
    "Not an error, and not a match. No corrective action is authorised from here.",
  ),
};

export function discrepancyBadge(value: string): StateBadge {
  return (
    DISCREPANCY_BADGES[value as DiscrepancyKind] ??
    badge(value, "?", "BLOCKING", `unrecognised discrepancy kind ${value}`)
  );
}

/** `11` §6: alert on unresolved or unexpected discrepancies. */
export function needsAttention(resolution: string): boolean {
  return resolution === "RECORDED_ONLY" || resolution === "UNRESOLVED";
}

// ---------------------------------------------------------- provider truth

export type ProviderIdentityState = "AVAILABLE" | "UNAVAILABLE" | "NOT_APPLICABLE";

export interface ProviderIdentityBadge {
  readonly state: ProviderIdentityState;
  readonly label: string;
  readonly glyph: string;
  readonly srLabel: string;
  readonly detail: string;
}

/**
 * Whether a provider order id exists, stated rather than implied by a blank cell.
 *
 * Brief §14: "Do not invent provider information that does not exist." An empty
 * column reads as "none"; this makes the difference between *no venue id was ever
 * returned* and *the field is blank* explicit, which is the difference between an
 * order that can be cancelled and one that cannot.
 */
export function providerIdentity(
  providerOrderId: string | null | undefined,
  mode: string | null | undefined,
): ProviderIdentityBadge {
  if (mode === "PAPER") {
    return {
      state: "NOT_APPLICABLE",
      label: "NO PROVIDER",
      glyph: "—",
      srLabel: "paper order; there is no provider identity",
      detail: "Simulated execution. No venue was contacted, so no venue id exists.",
    };
  }
  if (providerOrderId) {
    return {
      state: "AVAILABLE",
      label: providerOrderId,
      glyph: "●",
      srLabel: `provider order id ${providerOrderId}`,
      detail: "The venue's identity for this order.",
    };
  }
  return {
    state: "UNAVAILABLE",
    label: "NO PROVIDER ID",
    glyph: "⚠",
    srLabel: "no provider order id was recorded",
    detail:
      "No venue identity was recorded. Without one the order cannot be cancelled or " +
      "queried at the venue, and reconciliation must recover it before either is possible.",
  };
}
