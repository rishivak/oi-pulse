"use client";

/**
 * Positioning — where is positioning, and where is it moving?
 *
 * `13-FRONTEND_IA.md` §4: "Walls with history, migration entities with origin →
 * destination and duration, concentration, buildup classification with its
 * evidence, top ΔOI." Each of those is a registered Phase 4 feature, so the screen
 * renders the registry's answers rather than computing anything.
 */

import { FeatureScreen } from "@/components/terminal/FeatureScreen";
import { requireScreen } from "@/lib/terminal/screens";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("positioning");

export default function PositioningPage() {
  const { state } = useViewState();
  return <FeatureScreen screen={SCREEN} group="positioning" state={state} />;
}
