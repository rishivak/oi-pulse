"use client";

/**
 * The shared body of the three analytics screens.
 *
 * Positioning, Volatility and Market Structure ask different questions of the same
 * machinery: the Phase 4 registry says what a feature *is*, and
 * `/features/{id}/values` says what it was. `13-FRONTEND_IA.md` §1.3 keeps the
 * meaning on the backend — "it does not compute analytics, and it does not define
 * conventions" — so this component renders definitions and values and derives
 * nothing.
 *
 * Two honesty properties are worth naming.
 *
 * **A feature the registry does not offer is reported, not dropped.**
 * `partitionAvailable` returns both lists, and the missing ones are shown. A
 * silently shorter screen would make an unavailable capability look like a quiet
 * market.
 *
 * **`available_at` is displayed.** Derived values have an availability and raw
 * observations do not (`12` §2). Showing it is what lets a reader see that a
 * number they are looking at was not yet consumable at the decision time in force.
 */

import type { FeatureSpecDto } from "@/lib/api/dto";
import type { ScreenSpec } from "@/lib/terminal/screens";
import type { ViewState } from "@/lib/terminal/urlState";
import {
  type AnalyticsScreen,
  type FeatureValueRowDto,
  featureIdentity,
  featureValueRow,
  partitionAvailable,
} from "@/lib/terminal/screens/analytics";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { features } from "@/lib/terminal/endpoints";
import { effectiveKnowledgeTime } from "@/lib/terminal/time";
import {
  DenseTable,
  DerivedPanel,
  Instant,
  Value,
} from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";

export function FeatureScreen({
  screen,
  group,
  state,
}: {
  screen: ScreenSpec;
  group: AnalyticsScreen;
  state: ViewState;
}) {
  const underlyingId = state.underlyingId ?? 1;
  const registryQuery = useTerminalQuery<readonly FeatureSpecDto[]>(features.list());
  const registered = (registryQuery.data?.data ?? []).map((spec) => spec.identifier);
  const { available, missing } = partitionAvailable(group, registered);

  // One feature at a time: the selected one, from the URL, defaulting to the first
  // the registry actually offers. Requesting all of them on mount would be the
  // "repeated large-data transfers" brief §20 warns about.
  const selected = available[0] ?? null;
  const knowledge = effectiveKnowledgeTime(state.axes);
  const valuesQuery = useTerminalQuery<readonly FeatureValueRowDto[]>(
    selected === null || state.axes.marketTime === null
      ? null
      : features.values(selected, {
          underlying_id: underlyingId,
          market_time: state.axes.marketTime,
          knowledge_time: knowledge ?? undefined,
        }),
  );

  const quality =
    valuesQuery.data?.quality ?? registryQuery.data?.quality ?? qualityBadge(undefined);

  // The registry lists every version, and `/features/{id}/values` without an
  // explicit `version` serves the latest. Showing v1's definition beside v3's
  // numbers would attribute the wrong formula, units and convention to them, so the
  // highest version is selected here to match what the values endpoint will use.
  const spec = (registryQuery.data?.data ?? [])
    .filter((candidate) => candidate.identifier === selected)
    .reduce<FeatureSpecDto | null>(
      (best, candidate) => (best === null || candidate.version > best.version ? candidate : best),
      null,
    );
  const identity = spec ? featureIdentity(spec) : null;

  return (
    <ScreenFrame screen={screen} state={state} quality={quality}>
      <div className="space-y-4">
        <section aria-label="Features on this screen">
          <h2 className="font-mono text-xs text-terminal-muted">FEATURES</h2>
          <QueryPanel
            query={registryQuery}
            label="feature registry"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
          >
            {() => (
              <div className="mt-1 space-y-1 text-xs">
                <p className="font-mono">{available.join(" · ") || "none registered"}</p>
                {missing.length > 0 ? (
                  <p className="text-amber-400">
                    <span aria-hidden="true">⚠ </span>
                    Not offered by this backend: {missing.join(", ")}. These are not
                    empty — they are absent from the registry.
                  </p>
                ) : null}
              </div>
            )}
          </QueryPanel>
        </section>

        {identity ? (
          <section
            aria-label="Feature definition"
            className="rounded border border-terminal-border p-3"
          >
            <h2 className="font-mono text-xs text-terminal-muted">
              DEFINITION — {identity.label}
            </h2>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
              <dt className="text-terminal-muted">MEANS</dt>
              <dd className="font-sans">{identity.definition}</dd>
              <dt className="text-terminal-muted">FORMULA</dt>
              <dd>{identity.formula ?? "not published"}</dd>
              <dt className="text-terminal-muted">UNITS</dt>
              <dd>{identity.units ?? "dimensionless"}</dd>
              <dt className="text-terminal-muted">CONVENTION</dt>
              <dd>{identity.normalization ?? "none declared"}</dd>
              <dt className="text-terminal-muted">SCOPE</dt>
              <dd>{identity.scope}</dd>
              <dt className="text-terminal-muted">AVAILABILITY DELAY</dt>
              <dd>
                {identity.availabilityDelaySeconds === null
                  ? "none"
                  : `${identity.availabilityDelaySeconds} s`}
              </dd>
              <dt className="text-terminal-muted">INPUTS</dt>
              <dd>{identity.inputs.join(", ") || "—"}</dd>
              <dt className="text-terminal-muted">DEPENDS ON</dt>
              <dd>{identity.dependsOn.join(", ") || "—"}</dd>
            </dl>
          </section>
        ) : null}

        <section aria-label="Feature values">
          <h2 className="font-mono text-xs text-terminal-muted">VALUES</h2>
          {state.axes.marketTime === null ? (
            <p className="mt-1 text-xs text-terminal-muted">
              Pin a market time. A feature series is a point-in-time question, and the
              endpoint filters results to available_at ≤ decision time.
            </p>
          ) : selected === null ? (
            <p className="mt-1 text-xs text-terminal-muted">
              No feature on this screen is registered by the backend in use.
            </p>
          ) : (
            <DerivedPanel badge={quality}>
              <QueryPanel
                query={valuesQuery}
                label={`${selected} values`}
                empty={emptyState("NOT_YET_AVAILABLE")}
                isEmpty={(envelope) => envelope.data.length === 0}
              >
                {(envelope) => (
                  <DenseTable
                    caption={`${selected} values with market time, knowledge time and availability`}
                    columns={[
                      {
                        key: "market",
                        header: "MARKET TIME",
                        render: (row) => <Instant iso={row.marketTime} />,
                      },
                      {
                        key: "knowledge",
                        header: "KNOWLEDGE TIME",
                        render: (row) => <Instant iso={row.knowledgeTime} />,
                      },
                      {
                        key: "available",
                        header: "AVAILABLE AT",
                        render: (row) => <Instant iso={row.availableAt} />,
                      },
                      {
                        key: "value",
                        header: "VALUE",
                        unit: identity?.units ?? undefined,
                        align: "right",
                        render: (row) => <Value presented={row.value} />,
                      },
                      {
                        key: "quality",
                        header: "QUALITY",
                        render: (row) => row.qualityStatus ?? "—",
                      },
                    ]}
                    rows={envelope.data.map(featureValueRow)}
                    rowKey={(row, index) => `${row.marketTime ?? index}`}
                  />
                )}
              </QueryPanel>
            </DerivedPanel>
          )}
        </section>
      </div>
    </ScreenFrame>
  );
}
