"use client";

/**
 * Volatility — what is volatility doing?
 *
 * `13-FRONTEND_IA.md` §4: "ATM IV series, skew curve, term structure across
 * expiries, realized vs implied, IV rank (or *insufficient history*)."
 *
 * The parenthesis is the point. `IV_RANK` needs a long lookback, and when the
 * stored history is shorter the backend answers `INSUFFICIENT_HISTORY` rather than
 * computing from what it has. That renders as its own empty state, never as a zero
 * rank.
 */

import { FeatureScreen } from "@/components/terminal/FeatureScreen";
import { requireScreen } from "@/lib/terminal/screens";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("volatility");

export default function VolatilityPage() {
  const { state } = useViewState();
  return <FeatureScreen screen={SCREEN} group="volatility" state={state} />;
}
