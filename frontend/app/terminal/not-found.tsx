import Link from "next/link";

import { WITHHELD_SCREENS } from "@/lib/terminal/screens";

/**
 * A route that does not exist.
 *
 * The withheld screens are named here, with the reason. `13-FRONTEND_IA.md` §2
 * forbids a "Coming soon" page and forbids listing a screen whose backend is not
 * real — but someone who followed an old link to one deserves to be told why it is
 * gone rather than shown a generic 404.
 */
export default function TerminalNotFound() {
  return (
    <div className="p-6">
      <h1 className="font-mono text-base">No such screen.</h1>
      <p className="mt-2 text-sm text-terminal-muted">
        <Link href="/terminal" className="text-accent underline">
          Return to the Command Center
        </Link>
        .
      </p>
      <section className="mt-6">
        <h2 className="font-mono text-xs text-terminal-muted">
          SCREENS IN THE DESIGN THAT ARE NOT BUILT
        </h2>
        <ul className="mt-2 space-y-2">
          {WITHHELD_SCREENS.map((screen) => (
            <li key={screen.id} className="max-w-prose text-xs">
              <strong className="font-mono">{screen.title}</strong> — {screen.reason}{" "}
              <span className="text-terminal-muted">
                Specified in {screen.specifiedIn}. Unblocked by: {screen.unblockedBy}.
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
