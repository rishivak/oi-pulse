"use client";

/**
 * The provenance trail (`13-FRONTEND_IA.md` §7, Phase 12 brief §19).
 *
 * A gap renders as a step, in its position, saying why it cannot be followed. It
 * does not render as a missing row, and it does not render as free text standing in
 * for the reference — brief §19: "Do not replace evidence with free-text
 * explanations." A chain with a hole in it is a fact about the trade, and hiding
 * the hole would make an unexplainable order look explained.
 */

import Link from "next/link";

import type { ChainStep } from "@/lib/terminal/provenance";
import { summariseChain } from "@/lib/terminal/provenance";

export function ProvenanceTrail({
  steps,
  label,
}: {
  steps: readonly ChainStep[];
  label: string;
}) {
  const summary = summariseChain(steps);
  return (
    <section aria-label={label} className="rounded border border-terminal-border p-3">
      <h2 className="font-mono text-xs text-terminal-muted">{label}</h2>
      <p className="mt-1 text-xs">
        {summary.complete ? (
          <span>
            <span aria-hidden="true">● </span>
            Complete: every step resolves to a recorded reference.
          </span>
        ) : (
          <span>
            <span aria-hidden="true">⚠ </span>
            {summary.resolvedCount} of {steps.length} steps resolve.{" "}
            {summary.gapCount} cannot be followed.
          </span>
        )}
      </p>
      <ol className="mt-2 space-y-1">
        {steps.map((step, index) => (
          <li key={step.kind} className="flex items-baseline gap-2 font-mono text-xs">
            <span className="w-4 text-right text-terminal-muted" aria-hidden="true">
              {index + 1}
            </span>
            <span className="w-32 text-terminal-muted">{step.label}</span>
            {step.available && step.href !== null ? (
              <Link
                href={step.href}
                className="text-accent underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              >
                {step.reference}
              </Link>
            ) : (
              <span className="italic text-terminal-muted" data-gap={step.kind}>
                not recorded — {step.unavailableReason}
              </span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
