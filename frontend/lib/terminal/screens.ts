/**
 * The authoritative screen registry.
 *
 * Phase 12 brief §5 asks for the mapping to be created rather than assumed:
 *
 *     Screen -> purpose -> API(s) -> backend domain -> key interactions -> tests
 *
 * It lives in code rather than in the report so it can be checked. Every entry in
 * `apis` is a `"METHOD /path"` key, and `tools/check_frontend_contract.py` fails
 * when one is absent from the generated contract — which is generated from
 * `oipulse/api/*.py`. A screen therefore cannot claim a backend it does not have.
 *
 * ### How many screens
 *
 * The design gives three counts and they do not all agree, so this states what was
 * built and why rather than pretending the arithmetic resolves.
 *
 * `13-FRONTEND_IA.md` §2 draws **fourteen** names: a Command Center that
 * "summarizes and routes; it does not contain everything", and thirteen screens
 * beneath it. `18-ROADMAP.md` Phase 12's objective says "The twelve workflow
 * screens" while its own deliverables line lists all fourteen — the roadmap
 * contradicts itself, and no reading makes both halves true.
 * `20-ARCHITECTURE_FREEZE.md` §286 gives the gate that is actually testable:
 * **"twelve screens, none ahead of its backend"**.
 *
 * The qualifier is the operative part, and `13` §2 states it as a rule: "Screens
 * appear **only when their backend capability is real**. No 'Coming soon' pages —
 * the legacy app shipped three stubs over working endpoints, which is worse than
 * not listing them."
 *
 * All fourteen now satisfy it. Journal was withheld in the first Phase 12 pass
 * because `12-API_SPEC.md` §3 specified `/journal` and no router served it; the
 * remediation added the read half of that contract, so the screen has a real
 * backend and ships. {@link WITHHELD_SCREENS} is consequently empty, and the
 * `none ahead of its backend` condition holds for every entry below.
 *
 * What the Journal screen shows is the Phase 8 accounting journal, not the
 * free-text hypothesis notes §6 also describes — those have no schema in any phase
 * and are not invented. `lib/terminal/screens/journal.ts` carries that distinction
 * into the UI.
 */

export type BackendDomain =
  | "MARKET_STATE"
  | "ANALYTICS"
  | "SIGNALS"
  | "ALERTS"
  | "RESEARCH"
  | "REPLAY"
  | "BACKTEST"
  | "PAPER_TRADING"
  | "RISK"
  | "OMS"
  | "PORTFOLIO";

export interface ScreenSpec {
  readonly id: string;
  readonly title: string;
  /** The one question this screen answers (`13` §1.1). */
  readonly question: string;
  readonly route: string;
  readonly domains: readonly BackendDomain[];
  /** `"METHOD /path"` keys, checked against the generated contract. */
  readonly apis: readonly string[];
  readonly interactions: readonly string[];
  /** Whether the two-axis time control applies (`13` §4: every analysis screen). */
  readonly temporal: boolean;
  /** Test files covering it. */
  readonly tests: readonly string[];
}

export const COMMAND_CENTER: ScreenSpec = {
  id: "command-center",
  title: "Command Center",
  question: "What is happening, and where should I look?",
  route: "/terminal",
  domains: ["MARKET_STATE", "ANALYTICS", "SIGNALS"],
  apis: ["GET /market/state", "GET /features/{identifier}/values", "GET /signals"],
  interactions: [
    "route to any workflow screen carrying the shared underlying, expiry and time axes",
    "surface contradictions between signals rather than only confirming evidence",
  ],
  temporal: true,
  tests: ["frontend/tests/screens.test.ts", "tests/phase12/test_terminal_screens.py"],
};

export const WORKFLOW_SCREENS: readonly ScreenSpec[] = [
  {
    id: "option-surface",
    title: "Option Surface",
    question: "What does the chain look like?",
    route: "/terminal/option-surface",
    domains: ["MARKET_STATE"],
    apis: ["GET /market/state"],
    interactions: [
      "expiry selector propagated into the query and shared across screens",
      "per-leg staleness shown for quote, OI and greeks independently",
    ],
    temporal: true,
    tests: ["frontend/tests/optionSurface.test.ts"],
  },
  {
    id: "positioning",
    title: "Positioning",
    question: "Where is positioning, and where is it moving?",
    route: "/terminal/positioning",
    domains: ["ANALYTICS", "MARKET_STATE"],
    apis: [
      "GET /features",
      "GET /features/{identifier}/values",
      "GET /features/{identifier}/versions/{version}",
    ],
    interactions: [
      "walls, migrations and buildup classification read from the feature registry",
      "drill through any value to its definition, inputs, MarketState and observations",
    ],
    temporal: true,
    tests: ["frontend/tests/featureScreens.test.ts"],
  },
  {
    id: "volatility",
    title: "Volatility",
    question: "What is volatility doing?",
    route: "/terminal/volatility",
    domains: ["ANALYTICS"],
    apis: [
      "GET /features",
      "GET /features/{identifier}/values",
      "GET /features/{identifier}/versions/{version}",
    ],
    interactions: [
      "ATM IV, skew, term structure and realized-vs-implied from versioned features",
      "IV rank renders its insufficient-history state rather than a zero",
    ],
    temporal: true,
    tests: ["frontend/tests/featureScreens.test.ts"],
  },
  {
    id: "market-structure",
    title: "Market Structure",
    question: "What levels and what regime?",
    route: "/terminal/market-structure",
    domains: ["ANALYTICS", "MARKET_STATE"],
    apis: [
      "GET /features",
      "GET /features/{identifier}/values",
      "GET /market/state",
    ],
    interactions: [
      "GEX profile and flip level shown with the dealer convention in force",
      "regime shown with its evidence, never as a bare label",
    ],
    temporal: true,
    tests: ["frontend/tests/featureScreens.test.ts"],
  },
  {
    id: "signals",
    title: "Signals",
    question: "What is developing, and why?",
    route: "/terminal/signals",
    domains: ["SIGNALS"],
    apis: [
      "GET /signals",
      "GET /signals/{signal_id}",
      "GET /signals/{signal_id}/history",
      "GET /signals/types",
      "GET /signals/types/{signal_type}/versions/{version}",
    ],
    interactions: [
      "supporting and contradicting evidence side by side, each traceable",
      "lifecycle history with the invalidation condition",
    ],
    temporal: true,
    tests: ["frontend/tests/signals.test.ts"],
  },
  {
    id: "alerts",
    title: "Alerts",
    question: "What do I want to be told about?",
    route: "/terminal/alerts",
    domains: ["ALERTS", "SIGNALS"],
    apis: [
      "GET /alerts/rules",
      "GET /alerts/rules/{rule_id}",
      "POST /alerts/rules",
      "DELETE /alerts/rules/{rule_id}",
      "POST /alerts/rules/{rule_id}/test",
      "GET /alerts/occurrences",
      "POST /alerts/occurrences/{occurrence_id}/acknowledge",
    ],
    interactions: [
      "dry-run a rule against historical state without persisting or delivering",
      "acknowledge an occurrence; the underlying signal is explicitly unchanged",
    ],
    temporal: true,
    tests: ["frontend/tests/alerts.test.ts"],
  },
  {
    id: "research",
    title: "Research",
    question: "Does this relationship exist?",
    route: "/terminal/research",
    domains: ["RESEARCH"],
    apis: [
      "GET /research/studies",
      "GET /research/studies/{study_id}/versions/{version}",
      "POST /research/studies",
      "POST /research/studies/{study_id}/run",
      "GET /research/results",
      "GET /research/results/{content_hash}",
      "GET /research/datasets",
      "GET /research/datasets/{content_hash}",
      "GET /research/signal-evaluations",
    ],
    interactions: [
      "raw event count and effective sample after clustering shown together",
      "exclusion counts and sampling policy shown beside every result",
    ],
    temporal: true,
    tests: ["frontend/tests/research.test.ts"],
  },
  {
    id: "replay",
    title: "Replay",
    question: "What did it look like as it happened?",
    route: "/terminal/replay",
    domains: ["REPLAY"],
    apis: [
      "GET /replay/sessions",
      "POST /replay/sessions",
      "GET /replay/sessions/{session_id}",
      "POST /replay/sessions/{session_id}/control",
      "GET /replay/sessions/{session_id}/state",
    ],
    interactions: [
      "play, pause, step, seek and speed; there is no order verb on this router",
      "a persistent banner shows market time and knowledge horizon at all times",
    ],
    temporal: true,
    tests: ["frontend/tests/replay.test.ts"],
  },
  {
    id: "backtest",
    title: "Backtest",
    question: "Would this have worked?",
    route: "/terminal/backtest",
    domains: ["BACKTEST"],
    apis: [
      "GET /backtest/runs",
      "POST /backtest/runs",
      "GET /backtest/runs/{run_id}",
      "GET /backtest/runs/{run_id}/results",
      "GET /backtest/runs/{run_id}/trades",
      "GET /backtest/runs/{run_id}/equity-curve",
      "GET /backtest/results/{content_hash}",
    ],
    interactions: [
      "the assumption set sits beside the headline number, never in a footnote",
      "per-trade drill-through to the state that produced the intent",
    ],
    temporal: true,
    tests: ["frontend/tests/backtest.test.ts"],
  },
  {
    id: "paper-trading",
    title: "Paper Trading",
    question: "What would this trade do?",
    route: "/terminal/paper-trading",
    domains: ["PAPER_TRADING", "RISK"],
    apis: [
      "GET /paper-trading/accounts",
      "GET /paper-trading/accounts/{account_id}",
      "POST /paper-trading/accounts/{account_id}/intents",
      "GET /paper-trading/accounts/{account_id}/intents/{intent_id}",
      "GET /paper-trading/accounts/{account_id}/orders",
      "GET /paper-trading/accounts/{account_id}/orders/{order_id}",
      "GET /paper-trading/accounts/{account_id}/orders/{order_id}/events",
      "POST /paper-trading/accounts/{account_id}/orders/{order_id}/cancel",
      "GET /paper-trading/accounts/{account_id}/fills",
      "GET /paper-trading/accounts/{account_id}/positions",
      "GET /paper-trading/accounts/{account_id}/pnl",
      "GET /paper-trading/accounts/{account_id}/audit/{order_id}",
      "POST /risk/evaluate",
    ],
    interactions: [
      "intent builder with a pre-trade risk preview obtained from the server",
      "blotter showing every state including UNKNOWN, which blocks further action",
      "PAPER badge persistent; order entry withheld when mode is not stated",
    ],
    temporal: true,
    tests: ["frontend/tests/paperTrading.test.ts"],
  },
  {
    id: "portfolio",
    title: "Portfolio",
    question: "What do I hold, and how is it performing?",
    route: "/terminal/portfolio",
    domains: ["PORTFOLIO"],
    apis: [
      "GET /portfolio",
      "GET /portfolio/positions",
      "GET /portfolio/exposure",
      "GET /portfolio/pnl",
      "GET /portfolio/greeks",
      "GET /portfolio/attribution",
      "GET /portfolio/snapshots",
      "GET /portfolio/snapshots/{content_digest}",
      "POST /portfolio/position-reconciliation",
      "GET /portfolio/position-reconciliation/{run_id}",
    ],
    interactions: [
      "attribution sliceable by strategy, underlying, instrument and trade",
      "residual shown as its own row and its own chart segment, always",
      "unvalued instruments named rather than excluded from the total silently",
    ],
    temporal: true,
    tests: ["frontend/tests/portfolio.test.ts", "frontend/tests/attribution.test.ts"],
  },
  {
    id: "journal",
    title: "Journal",
    question: "What was I thinking?",
    route: "/terminal/journal",
    domains: ["PAPER_TRADING"],
    apis: ["GET /journal/entries", "GET /journal/entries/{entry_id}"],
    interactions: [
      "filter by entry type, order and period; cursor pagination",
      "every entry links to the order and fill that caused it",
      "an empty page says whether the account was quiet or the writer is absent",
    ],
    temporal: true,
    tests: ["frontend/tests/journal.test.ts"],
  },
  {
    id: "risk",
    title: "Risk",
    question: "What are my limits and utilization?",
    route: "/terminal/risk",
    domains: ["RISK", "OMS"],
    apis: [
      "GET /risk/profiles",
      "GET /risk/profiles/{policy_id}/versions/{version}",
      "GET /risk/state/{account_id}",
      "GET /risk/status/{account_id}",
      "GET /risk/decisions",
      "GET /risk/decisions/{intent_id}/{sequence_no}",
      "POST /risk/kill-switch",
      "DELETE /risk/kill-switch",
      "GET /reconciliation/status",
      "GET /reconciliation/runs",
      "GET /reconciliation/runs/{run_id}",
      "GET /reconciliation/orders",
      "GET /reconciliation/orders/{order_id}",
      "GET /reconciliation/orders/{order_id}/events",
      "GET /reconciliation/orders/{order_id}/provider-state",
      "POST /reconciliation/trigger",
    ],
    interactions: [
      "every limit with its utilization, and every decision with its full reason set",
      "kill switch, prominent and confirmed",
      "OMS and provider state shown as distinct columns, never merged",
    ],
    temporal: true,
    tests: ["frontend/tests/risk.test.ts", "frontend/tests/oms.test.ts"],
  },
] as const;

export const SCREENS: readonly ScreenSpec[] = [COMMAND_CENTER, ...WORKFLOW_SCREENS];

export interface WithheldScreen {
  readonly id: string;
  readonly title: string;
  readonly question: string;
  readonly specifiedIn: string;
  /** Why it is not shipped. */
  readonly reason: string;
  /** What would have to exist for it to ship. */
  readonly unblockedBy: string;
}

/**
 * Named in the design and deliberately not built. Currently empty.
 *
 * The list is kept rather than deleted because the rule it serves outlives the one
 * entry it used to hold: a screen whose backend is not real must not appear in the
 * UI at all — not greyed out, not "coming soon" — and when that happens again the
 * omission should be written down here rather than looking like a lapse.
 *
 * Journal was the only entry. The Phase 12 remediation implemented the `/journal`
 * read contract `12-API_SPEC.md` §3 specifies, so it moved into
 * {@link WORKFLOW_SCREENS}.
 */
export const WITHHELD_SCREENS: readonly WithheldScreen[] = [] as const;

export function screenById(id: string): ScreenSpec | undefined {
  return SCREENS.find((s) => s.id === id);
}

/**
 * The spec for a screen that must exist.
 *
 * Every page calls this at module scope with its own id, so deleting a registry
 * entry while leaving its route in place fails at import rather than rendering a
 * screen with no declared backend, no question and no tests.
 */
export function requireScreen(id: string): ScreenSpec {
  const screen = screenById(id);
  if (screen === undefined) {
    throw new Error(
      `no screen "${id}" in the registry. A route must not exist without an entry: ` +
        `the entry is what declares its backend, and the contract guard checks it.`,
    );
  }
  return screen;
}

/** Every route key any screen depends on, for the contract guard and for tests. */
export function allDeclaredApis(): readonly string[] {
  return [...new Set(SCREENS.flatMap((s) => s.apis))].sort();
}
