"use client";

/**
 * Route-level error boundary.
 *
 * `13-FRONTEND_IA.md` §8: "Error boundaries and loading states per route — all
 * absent in the legacy app."
 *
 * The reset control is deliberately labelled "Render again" rather than "Retry".
 * This boundary catches a rendering failure, not a request failure; it re-renders
 * and does not reissue anything, and a button that said "Retry" beside a failed
 * order-entry screen would be read as an invitation to resubmit.
 */

import { useEffect } from "react";

export default function TerminalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaced to the browser console rather than posted anywhere: the terminal
    // collects no user content and no identifiers (brief §29).
    console.error("terminal render failed", error);
  }, [error]);

  return (
    <div className="p-6" role="alert">
      <h1 className="font-mono text-base">This screen failed to render.</h1>
      <p className="mt-2 max-w-prose text-sm text-terminal-muted">
        Nothing on the page can be trusted to be current, and no request was retried.
        The failure is a fault in the terminal, not an answer from the backend.
      </p>
      {error.digest ? (
        <p className="mt-2 font-mono text-xs text-terminal-muted">digest {error.digest}</p>
      ) : null}
      <button
        type="button"
        onClick={reset}
        className="mt-4 rounded border border-terminal-border px-3 py-1 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        Render again
      </button>
    </div>
  );
}
