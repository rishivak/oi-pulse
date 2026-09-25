"use client";

/**
 * Navigation, generated from the screen registry.
 *
 * Built from `SCREENS` rather than written out, so a screen cannot appear in the
 * navigation without being in the registry — and the registry's every API is
 * checked against the backend by `tools/check_frontend_contract.py`. That chain is
 * what makes `13-FRONTEND_IA.md` §2's "Screens appear only when their backend
 * capability is real" a property of the build rather than a habit.
 *
 * Withheld screens are absent entirely. Not greyed out, not labelled "coming soon":
 * `13` §2 rejects exactly that, and a disabled item still tells a user the feature
 * is nearly there.
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { SCREENS } from "@/lib/terminal/screens";

export function TerminalNav() {
  const pathname = usePathname();
  const search = useSearchParams();
  const query = search?.toString() ?? "";
  const suffix = query === "" ? "" : `?${query}`;

  return (
    <nav aria-label="Terminal screens" className="border-b border-terminal-border">
      <ul className="flex flex-wrap gap-1 px-2 py-1">
        {SCREENS.map((screen) => {
          const active = pathname === screen.route;
          return (
            <li key={screen.id}>
              <Link
                href={`${screen.route}${suffix}`}
                aria-current={active ? "page" : undefined}
                title={screen.question}
                className={`inline-block rounded px-2 py-1 font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
                  active
                    ? "bg-terminal-surface text-terminal-text"
                    : "text-terminal-muted hover:text-terminal-text"
                }`}
              >
                {screen.title}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
