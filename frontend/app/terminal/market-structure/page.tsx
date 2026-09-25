"use client";

/**
 * Market Structure — what levels and what regime?
 *
 * `13-FRONTEND_IA.md` §4: "GEX profile with flip level", and §3 shows the dealer
 * convention rendered beside the number. That convention comes from the feature
 * definition's `normalization`, which `FeatureScreen` displays: `oipulse/api/app.py`
 * gives the reason — "a user hovering GEX sees the dealer convention in force
 * rather than reading the source".
 */

import { FeatureScreen } from "@/components/terminal/FeatureScreen";
import { requireScreen } from "@/lib/terminal/screens";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("market-structure");

export default function MarketStructurePage() {
  const { state } = useViewState();
  return <FeatureScreen screen={SCREEN} group="marketStructure" state={state} />;
}
