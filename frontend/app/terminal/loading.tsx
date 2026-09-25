/**
 * Route-level loading state (`13-FRONTEND_IA.md` §8).
 *
 * It says the terminal is waiting, not that there is nothing to show. A blank
 * route while a request is in flight reads as an empty book.
 */
export default function TerminalLoading() {
  return (
    <div className="p-6" role="status" aria-live="polite" aria-busy="true">
      <p className="font-mono text-sm text-terminal-muted">Loading screen…</p>
      <p className="mt-1 text-xs text-terminal-muted">
        No data has arrived yet. This is not an empty result.
      </p>
    </div>
  );
}
