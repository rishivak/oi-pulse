"use client";

/**
 * Research — does this relationship exist?
 *
 * `13-FRONTEND_IA.md` §5: "Results: **raw events and effective sample after
 * clustering**, distributions, MFE/MAE, breakdowns by regime/expiry/time-of-day.
 * Prominently displays exclusion counts and comparison count. Market time and
 * knowledge time both shown."
 *
 * The effective sample is not a detail. Overlapping forward windows mean the
 * observations are not independent, and a significance figure read off the raw
 * count overstates itself — so the two numbers sit beside each other and a material
 * gap between them is called out rather than left to be noticed.
 *
 * No event-study engine lives here (brief §10). Every number is read from
 * `/research/results`.
 */

import type { DatasetDto, StudyResultDto } from "@/lib/api/dto";
import { emptyState, qualityBadge } from "@/lib/terminal/quality";
import { requireScreen } from "@/lib/terminal/screens";
import { datasetSummary, studyResultSummary } from "@/lib/terminal/screens/research";
import { research } from "@/lib/terminal/endpoints";
import { DenseTable, Instant } from "@/components/terminal/primitives";
import { QueryPanel } from "@/components/terminal/QueryPanel";
import { ScreenFrame } from "@/components/terminal/ScreenFrame";
import { useTerminalQuery } from "@/components/terminal/useTerminalQuery";
import { useViewState } from "@/components/terminal/useViewState";

const SCREEN = requireScreen("research");

export default function ResearchPage() {
  const { state } = useViewState();
  const resultsQuery = useTerminalQuery<readonly StudyResultDto[]>(
    research.results({ limit: 50 }),
  );
  const datasetsQuery = useTerminalQuery<readonly DatasetDto[]>(
    research.datasets({ limit: 50 }),
  );
  const quality = resultsQuery.data?.quality ?? qualityBadge(undefined);

  return (
    <ScreenFrame screen={SCREEN} state={state} quality={quality}>
      <div className="space-y-5">
        <section aria-label="Study results">
          <h2 className="font-mono text-xs text-terminal-muted">RESULTS</h2>
          <QueryPanel
            query={resultsQuery}
            label="study results"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <div className="space-y-3">
                {envelope.data.map(studyResultSummary).map((result) => (
                  <article
                    key={result.contentHash}
                    className="rounded border border-terminal-border p-3"
                  >
                    <header className="flex flex-wrap items-baseline gap-3 font-mono text-xs">
                      <span>
                        {result.studyId}@v{result.studyVersion}
                      </span>
                      <span className="text-terminal-muted">{result.status}</span>
                      {result.isHindsight ? (
                        <span className="rounded border border-amber-500 px-1 text-amber-300">
                          <span aria-hidden="true">⚠ </span>HINDSIGHT
                        </span>
                      ) : null}
                      <span className="text-terminal-muted">{result.queryMode}</span>
                    </header>

                    {result.statusDetail ? (
                      <p className="mt-1 text-xs text-terminal-muted">{result.statusDetail}</p>
                    ) : null}

                    <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs sm:grid-cols-4">
                      <div>
                        <dt className="text-terminal-muted">RAW EVENTS</dt>
                        <dd>{result.sample.rawEvents}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">EFFECTIVE SAMPLE</dt>
                        <dd>{result.sample.effectiveSample}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">CLUSTERS</dt>
                        <dd>{result.sample.clusters}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">MEAN OVERLAP</dt>
                        <dd>{result.sample.meanOverlap ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">DROPPED OVERLAPPING</dt>
                        <dd>{result.sample.droppedOverlapping}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">DROPPED SEPARATION</dt>
                        <dd>{result.sample.droppedSeparation}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">EXCLUDED FOR QUALITY</dt>
                        <dd>{result.sample.excludedQuality}</dd>
                      </div>
                      <div>
                        <dt className="text-terminal-muted">KNOWLEDGE HORIZON</dt>
                        <dd>
                          <Instant iso={result.knowledgeHorizon} />
                        </dd>
                      </div>
                    </dl>

                    {result.sample.warning ? (
                      <p
                        className="mt-2 rounded border border-amber-700 bg-amber-950 p-2 text-xs text-amber-200"
                        role="note"
                      >
                        {result.sample.warning}
                      </p>
                    ) : null}

                    <p className="mt-2 font-mono text-[10px] text-terminal-muted">
                      result {result.contentHash} · dataset {result.datasetHash ?? "—"} ·
                      build context {result.buildContextId ?? "—"} · seed{" "}
                      {result.randomSeed ?? "none"}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </QueryPanel>
        </section>

        <section aria-label="Datasets">
          <h2 className="font-mono text-xs text-terminal-muted">DATASETS</h2>
          <QueryPanel
            query={datasetsQuery}
            label="datasets"
            empty={emptyState("NO_DATA_FOR_PERIOD")}
            isEmpty={(envelope) => envelope.data.length === 0}
          >
            {(envelope) => (
              <DenseTable
                caption="Datasets with content hash, row count and knowledge horizon"
                columns={[
                  { key: "name", header: "NAME", render: (r) => r.name },
                  { key: "rows", header: "ROWS", align: "right", render: (r) => r.rowCount },
                  { key: "mode", header: "QUERY MODE", render: (r) => r.queryMode },
                  {
                    key: "knowledge",
                    header: "KNOWLEDGE HORIZON",
                    render: (r) => <Instant iso={r.knowledgeHorizon} />,
                  },
                  {
                    key: "created",
                    header: "CREATED",
                    render: (r) => <Instant iso={r.createdAt} />,
                  },
                  {
                    key: "hash",
                    header: "CONTENT HASH",
                    render: (r) => (
                      <span className="text-terminal-muted">{r.contentHash}</span>
                    ),
                  },
                ]}
                rows={envelope.data.map(datasetSummary)}
                rowKey={(row) => row.contentHash}
              />
            )}
          </QueryPanel>
        </section>
      </div>
    </ScreenFrame>
  );
}
