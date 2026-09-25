/**
 * The two time axes, as first-class values.
 *
 * `13-FRONTEND_IA.md` §1.6: "Time is a first-class control, on both axes ... Both are
 * always visible; hindsight is never implicit." §4 makes the same point negatively:
 *
 * > A single `as_of` control is not enough for serious research. Without seeing both, a
 * > user cannot tell *"what the system knew then"* from *"what we know now about then"*
 * > — and those two readings support opposite conclusions.
 *
 * So this module never produces a single "timestamp". Every function that returns
 * something renderable returns a labelled pair, and the label is part of the value
 * rather than something a component is trusted to add.
 *
 * `decision_time` is the third concept and is deliberately *not* a third axis. Per
 * `05-DATA_LIFECYCLE_PIT.md` §2 and `12-API_SPEC.md` §2 it is a request parameter on
 * availability-filtered endpoints, defaulting to `knowledge_time`, and is never a
 * stored field. It is modelled here as exactly that: a derived request value, not a
 * member of `TimeAxes`.
 */

/** Which mode the backend used to answer. Echoed in every envelope's `meta`. */
export type TemporalSemantics =
  | "knowledge_at"
  | "market_truth_at"
  | "tradable_information_at";

/** The axis pair. `null` market time means "latest" — the live reading. */
export interface TimeAxes {
  /** Valid time: which instant in the market. `null` → latest. */
  readonly marketTime: string | null;
  /** Knowledge horizon: what we knew by then. `null` → equals `marketTime`. */
  readonly knowledgeTime: string | null;
}

export const LIVE_AXES: TimeAxes = { marketTime: null, knowledgeTime: null };

/**
 * Rejected by `12-API_SPEC.md` §2 as incoherent, and rejected here before a request
 * is built. Sending it would produce a 422 the user could not act on; refusing it in
 * the control lets the control say why.
 */
export class IncoherentTimeAxes extends Error {
  readonly marketTime: string;
  readonly knowledgeTime: string;

  constructor(marketTime: string, knowledgeTime: string) {
    super(
      `knowledge time ${knowledgeTime} precedes market time ${marketTime}. ` +
        `Knowing something before it was true is not a mode the system has; ` +
        `knowledge_time < market_time is rejected (12-API_SPEC.md §2).`,
    );
    this.name = "IncoherentTimeAxes";
    this.marketTime = marketTime;
    this.knowledgeTime = knowledgeTime;
  }
}

/** The knowledge horizon actually in force: explicit, or equal to market time. */
export function effectiveKnowledgeTime(axes: TimeAxes): string | null {
  return axes.knowledgeTime ?? axes.marketTime;
}

/**
 * `decision_time` defaults to `knowledge_time` (`12` §2). Exposed as a function of
 * the axes rather than as state, so it cannot acquire an independent life and become
 * the fifth stored dimension `05` §2 forbids.
 */
export function defaultDecisionTime(axes: TimeAxes): string | null {
  return effectiveKnowledgeTime(axes);
}

/** Which repository mode a given pair maps onto. Throws on the incoherent case. */
export function resolveSemantics(axes: TimeAxes): TemporalSemantics {
  const { marketTime } = axes;
  const knowledge = axes.knowledgeTime;
  if (marketTime === null || knowledge === null || knowledge === marketTime) {
    return "knowledge_at";
  }
  if (knowledge < marketTime) throw new IncoherentTimeAxes(marketTime, knowledge);
  return "market_truth_at";
}

/**
 * True when the user is looking at a past moment with later knowledge.
 *
 * This is the state `13` §4 requires to be "unmistakable" and "visually loud for as
 * long as it is active". It is computed, never stored as a toggle, so the banner and
 * the request can never disagree about whether hindsight is in force.
 */
export function isHindsight(axes: TimeAxes): boolean {
  return (
    axes.marketTime !== null &&
    axes.knowledgeTime !== null &&
    axes.knowledgeTime > axes.marketTime
  );
}

/** True when no market time is pinned — the terminal is tracking the present. */
export function isLive(axes: TimeAxes): boolean {
  return axes.marketTime === null;
}

export type AxisId = "market" | "knowledge" | "decision";

export interface AxisLabel {
  readonly axis: AxisId;
  /** Always spelled out. Never "time", never "as of". */
  readonly label: string;
  readonly meaning: string;
}

export const AXIS_LABELS: Readonly<Record<AxisId, AxisLabel>> = {
  market: {
    axis: "market",
    label: "MARKET TIME",
    meaning: "the instant in the market this describes",
  },
  knowledge: {
    axis: "knowledge",
    label: "KNOWLEDGE TIME",
    meaning: "what had reached OI Pulse by then",
  },
  decision: {
    axis: "decision",
    label: "DECISION TIME",
    meaning: "the horizon results were filtered to; a request parameter, never stored",
  },
};

export interface AxisReading {
  readonly axis: AxisId;
  readonly label: string;
  readonly meaning: string;
  /** ISO instant, or `null` for "latest". */
  readonly value: string | null;
  /** What to show when `value` is null. Never an empty cell. */
  readonly placeholder: string;
}

/**
 * The renderable form of the pair. Components take this and lay it out; they never
 * pick which timestamps to show, which is how `13` §7's "Do not present a single
 * timestamp that hides different temporal meanings" is enforced rather than hoped for.
 */
export function axisReadings(axes: TimeAxes, opts: { decision?: boolean } = {}): AxisReading[] {
  const readings: AxisReading[] = [
    { ...AXIS_LABELS.market, value: axes.marketTime, placeholder: "latest" },
    {
      ...AXIS_LABELS.knowledge,
      value: effectiveKnowledgeTime(axes),
      placeholder: "latest",
    },
  ];
  if (opts.decision) {
    readings.push({
      ...AXIS_LABELS.decision,
      value: defaultDecisionTime(axes),
      placeholder: "latest",
    });
  }
  return readings;
}

export type SyncState = "IN_SYNC" | "HINDSIGHT" | "LIVE";

export interface SyncIndicator {
  readonly state: SyncState;
  readonly label: string;
  /** Color-independent marker (§28: status is never communicated by colour alone). */
  readonly glyph: string;
  readonly explanation: string;
  /** Whether the indicator must remain visible for as long as the state holds. */
  readonly persistent: boolean;
}

export function syncIndicator(axes: TimeAxes): SyncIndicator {
  if (isHindsight(axes)) {
    return {
      state: "HINDSIGHT",
      label: "HINDSIGHT",
      glyph: "⚠",
      explanation: "Showing what we now know about that moment.",
      persistent: true,
    };
  }
  if (isLive(axes)) {
    return {
      state: "LIVE",
      label: "LIVE",
      glyph: "●",
      explanation: "Tracking the present; knowledge time equals market time.",
      persistent: true,
    };
  }
  return {
    state: "IN_SYNC",
    label: "in sync",
    glyph: "●",
    explanation: "Showing what the system knew at that moment.",
    persistent: true,
  };
}
