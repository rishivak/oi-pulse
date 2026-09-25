"use client";

/**
 * Alerts — what do I want to be told about?
 *
 * `13-FRONTEND_IA.md` §4: "Rule builder over features/signals/state, dry-run
 * against historical state, occurrence history, delivery status."
 *
 * Phase 12 brief §9 is the rule this screen exists to respect:
 *
 *     Signal truth  ≠  Alert delivery state
 *
 * Acknowledging is a write, and it is the only one here. The confirmation states
 * plainly what it did not change, and the claim comes from the response's
 * `meta.signal_unchanged` rather than from this component's confidence.
 */

import type { AlertOccurrenceDto, AlertRuleDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import {
  ACKNOWLEDGEMENT_EFFECT,
  acknowledgementEffect,
  alertRuleRow,
  occurrenceRow,
} from "@/lib/terminal/screens/signals";
import { alerts } from "@/lib/terminal/endpoints";
import { DenseTable, Instant } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import {
  useTerminalMutation,
  useTerminalQuery,
} from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("alerts");

function AcknowledgeButton({ occurrenceId }: { occurrenceId: string }) {
  const mutation = useTerminalMutation<unknown>(alerts.acknowledge(occurrenceId));
  const effect = mutation.data ? acknowledgementEffect(mutation.data.meta.raw) : null;
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={mutation.isPending || mutation.isSuccess}
        onClick={() => mutation.mutate(undefined)}
        title={ACKNOWLEDGEMENT_EFFECT}
        className="rounded border border-terminal-border px-2 py-0.5 text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        {mutation.isSuccess ? "Acknowledged" : "Acknowledge"}
      </button>
      {effect ? (
        <span className="text-[10px] text-terminal-muted">
          {effect.signalUnchanged
            ? "signal unchanged"
            : "the response did not confirm the signal was left unchanged"}
        </span>
      ) : null}
    </span>
  );
}

export default function AlertsPage() {
  const { state } = useViewState();
  const rulesQuery = useTerminalQuery<readonly AlertRuleDto[]>(alerts.rules());
  const occurrencesQuery = useTerminalQuery<readonly AlertOccurrenceDto[]>(
    alerts.occurrences({ limit: 100 }),
  );
  const quality = occurrencesQuery.data?.quality ?? qualityBadge(undefined);

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      <div className="space-y-4">
        <p className="rounded border border-terminal-border p-2 text-xs text-terminal-muted">
          {ACKNOWLEDGEMENT_EFFECT}
        </p>

        <section aria-label="Alert rules">
          <h2 className="font-mono text-xs text-terminal-muted">RULES</h2>
          <QueryPanel
            query={rulesQuery}
            label="alert rules"
            empty={emptyState("NO_MATCHING_FILTER")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <DenseTable
                caption="Alert rules with channel, severity, thresholds and destination"
                columns={[
                  { key: "type", header: "SIGNAL TYPE", render: (r) => r.signalType },
                  { key: "channel", header: "CHANNEL", render: (r) => r.channel },
                  { key: "severity", header: "SEVERITY", render: (r) => r.severity },
                  {
                    key: "statuses",
                    header: "ON STATUSES",
                    render: (r) => r.onStatuses.join(", "),
                  },
                  {
                    key: "strength",
                    header: "MIN STRENGTH",
                    align: "right",
                    render: (r) => r.minStrength,
                  },
                  {
                    key: "enabled",
                    header: "ENABLED",
                    render: (r) => (r.enabled ? "yes" : "no"),
                  },
                  {
                    key: "destination",
                    header: "DESTINATION",
                    render: (r) => (
                      <span title="A destination name that deployment configuration resolves. Never a credential.">
                        {r.destination ?? "—"}
                      </span>
                    ),
                  },
                ]}
                rows={envelope.data.map(alertRuleRow)}
                rowKey={(row) => row.id}
              />
            )}
          </QueryPanel>
        </section>

        <section aria-label="Occurrences">
          <h2 className="font-mono text-xs text-terminal-muted">OCCURRENCES</h2>
          <QueryPanel
            query={occurrencesQuery}
            label="alert occurrences"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <DenseTable
                caption="Alert occurrences with delivery state, separate from signal truth"
                columns={[
                  { key: "type", header: "SIGNAL TYPE", render: (r) => r.signalType },
                  { key: "severity", header: "SEVERITY", render: (r) => r.severity },
                  {
                    key: "triggered",
                    header: "TRIGGERED",
                    render: (r) => <Instant iso={r.triggeredAt} />,
                  },
                  {
                    key: "available",
                    header: "AVAILABLE AT",
                    render: (r) => <Instant iso={r.availableAt} />,
                  },
                  {
                    key: "delivery",
                    header: "DELIVERY",
                    render: (r) => (
                      <span data-delivery={r.deliveryState}>
                        {r.deliveryState}
                        <span className="ml-1 text-terminal-muted">
                          ({r.attempts} attempt{r.attempts === 1 ? "" : "s"})
                        </span>
                      </span>
                    ),
                  },
                  {
                    key: "ack",
                    header: "ACKNOWLEDGE",
                    render: (r) =>
                      r.acknowledgedAt === null ? (
                        <AcknowledgeButton occurrenceId={r.occurrenceId} />
                      ) : (
                        <span className="text-terminal-muted">
                          <Instant iso={r.acknowledgedAt} /> {r.acknowledgedBy ?? ""}
                        </span>
                      ),
                  },
                ]}
                rows={envelope.data.map(occurrenceRow)}
                rowKey={(row) => row.occurrenceId}
              />
            )}
          </QueryPanel>
        </section>
      </div>
    </ScreenFrame>
  );
}
