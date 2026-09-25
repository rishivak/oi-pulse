/**
 * Execution mode, and the three gates the terminal never opens.
 *
 * `13-FRONTEND_IA.md` §6: "A `PAPER` / `LIVE` badge is persistent and unmissable
 * wherever an account is in context. Live trading UI does not render at all unless
 * **all three** gates hold (`17-SECURITY.md` §4): the `LIVE_TRADING_ENABLED` feature
 * flag, the second confirmation environment variable, and the `LIVE_TRADE`
 * permission on the principal."
 *
 * In this build none of the three can hold, and the reason is structural rather
 * than configured:
 *
 * 1. `oipulse/trading/brokers/capability.py` holds `LIVE_EXECUTION_ENABLED = False`
 *    as a module constant, not a setting.
 * 2. `UpstoxBrokerAdapter` declares an empty capability set and every submitting
 *    method raises before reaching a network — there is no wire format to reach one
 *    with.
 * 3. The `LIVE_TRADE` permission is not implemented, and `oipulse/api/app.py`
 *    records that there is no `/trading` router to gate.
 *
 * So {@link liveTradingRenderable} is typed to return the literal `false`. It is not
 * a function that consults configuration and currently answers no; it is a function
 * whose type says it cannot answer anything else, and `tools/check_terminal_boundary.py`
 * asserts that annotation is still there.
 *
 * The other half is the refusal to *assume* paper. {@link executionMode} returns
 * `UNKNOWN` when a response does not state its mode, and the order surface is
 * withheld on `UNKNOWN`. Defaulting an unlabelled account to `PAPER` would put an
 * order ticket in front of an operator on the strength of a missing field.
 */

export type ExecutionMode = "PAPER" | "UNKNOWN";

export interface LiveGate {
  readonly id: "LIVE_TRADING_ENABLED" | "LIVE_TRADING_CONFIRMED" | "LIVE_TRADE_PERMISSION";
  readonly description: string;
  /** Always false in this build. */
  readonly satisfied: boolean;
  /** Where the impossibility is established, so the claim is checkable. */
  readonly evidence: string;
}

export const LIVE_TRADING_GATES: readonly LiveGate[] = [
  {
    id: "LIVE_TRADING_ENABLED",
    description: "the LIVE_TRADING_ENABLED feature flag",
    satisfied: false,
    evidence:
      "oipulse/trading/brokers/capability.py holds LIVE_EXECUTION_ENABLED = False as a " +
      "module constant; flipping it is a reviewable code change, and it would not be enough",
  },
  {
    id: "LIVE_TRADING_CONFIRMED",
    description: "the second explicit confirmation environment variable",
    satisfied: false,
    evidence:
      "oipulse/core/config.py requires LIVE_TRADING_CONFIRMED alongside the flag and " +
      "refuses a configuration that sets one without the other",
  },
  {
    id: "LIVE_TRADE_PERMISSION",
    description: "the LIVE_TRADE permission on the principal",
    satisfied: false,
    evidence:
      "not implemented: 17-SECURITY.md §4 defines it, and oipulse/api/app.py records " +
      "that there is no /trading router for it to gate",
  },
] as const;

/**
 * Whether any live-trading UI may render.
 *
 * The return type is the literal `false`, so no caller can be written that handles a
 * `true` branch, and no future edit can widen the answer without changing this
 * signature in a diff.
 */
export function liveTradingRenderable(): false {
  return false;
}

export interface ModeBadge {
  readonly mode: ExecutionMode;
  readonly label: string;
  /** Colour-independent (§28). */
  readonly glyph: string;
  readonly srLabel: string;
  /** Whether an order-entry surface may render at all. */
  readonly orderEntryAllowed: boolean;
  readonly explanation: string;
}

/**
 * Derive the badge from a response's `meta`.
 *
 * `PAPER` requires the backend to have said so *and* to have said live execution is
 * unavailable. Either statement missing yields `UNKNOWN` and withholds order entry.
 */
export function executionMode(meta: Readonly<Record<string, unknown>>): ModeBadge {
  const declared = meta.mode ?? meta.execution_mode;
  const liveAvailable = meta.live_execution_available ?? meta.live_execution_enabled;
  const isPaper = declared === "PAPER" && liveAvailable === false;
  if (isPaper) {
    return {
      mode: "PAPER",
      label: "PAPER",
      glyph: "▣",
      srLabel: "paper trading mode; no live orders are possible",
      orderEntryAllowed: true,
      explanation:
        "Simulated execution. No order from this screen can reach a broker: there is " +
        "no live adapter, and the three live-trading gates are all closed.",
    };
  }
  return {
    mode: "UNKNOWN",
    label: "MODE UNKNOWN",
    glyph: "?",
    srLabel: "execution mode unknown; order entry is withheld",
    orderEntryAllowed: false,
    explanation:
      "This response did not state `mode: PAPER` with `live_execution_available: false`. " +
      "Order entry is withheld rather than assumed to be simulated.",
  };
}

/** The line shown wherever someone might wonder where the live controls are. */
export const LIVE_WITHHELD_NOTICE =
  "Live trading is not part of this build. All three gates in 17-SECURITY.md §4 are " +
  "closed and no broker adapter can submit an order, so no live control is rendered — " +
  "including a disabled one.";
