"use client";

/**
 * Read the shared view state out of the URL.
 *
 * `13-FRONTEND_IA.md` §8 puts time, expiry and underlying in URL parameters so any
 * view is linkable. Reading them here rather than holding them in a store is also
 * what keeps the selectors honest: the request is built from this value, so a
 * control that did not change the URL would visibly not change the data — the
 * legacy `expiries[0]` defect (§4) becomes impossible rather than discouraged.
 *
 * An incoherent `(market_time, knowledge_time)` pair in the URL throws from
 * `parseViewState`. It is caught here and returned as an error for the screen to
 * render, because a thrown error from a hook would take out the whole route and
 * lose the address bar that caused it.
 */

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";

import { EMPTY_VIEW_STATE, type ViewState, parseViewState } from "@/lib/terminal/urlState";
import { IncoherentTimeAxes } from "@/lib/terminal/time";

export interface ViewStateResult {
  readonly state: ViewState;
  readonly error: string | null;
}

export function useViewState(): ViewStateResult {
  const search = useSearchParams();
  const query = search?.toString() ?? "";
  return useMemo(() => {
    try {
      return { state: parseViewState(query), error: null };
    } catch (cause) {
      return {
        state: EMPTY_VIEW_STATE,
        error:
          cause instanceof IncoherentTimeAxes
            ? cause.message
            : "The time parameters in this URL could not be read.",
      };
    }
  }, [query]);
}
