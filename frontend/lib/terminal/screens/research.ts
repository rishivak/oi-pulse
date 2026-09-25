/**
 * Research, replay and backtest presentation.
 *
 * `13-FRONTEND_IA.md` §5 asks for three specific honesties, and each is a function
 * here rather than a convention:
 *
 * - Research shows "**raw events and effective sample after clustering**" and
 *   "prominently displays exclusion counts". A result whose effective sample is a
 *   tenth of its raw count is a different result, and only the first number fits
 *   in a headline.
 * - Replay carries "a persistent banner ... because a replay screen that looks like
 *   live is dangerous".
 * - Backtest puts "latency, spread, slippage, fees ... **beside** the headline
 *   number, never in a footnote".
 */

import type {
  BacktestResultDto,
  DatasetDto,
  ReplayContextDto,
  ReplayProgressDto,
  ReplayStepDto,
  SamplingResultDto,
  StudyResultDto,
} from "@/lib/api/dto";
import { type Presented, presentValue } from "@/lib/terminal/quality";
import type { TimeAxes } from "@/lib/terminal/time";

// ------------------------------------------------------------------ research

export interface SampleReading {
  readonly rawEvents: number;
  readonly effectiveSample: number;
  readonly clusters: number;
  readonly droppedOverlapping: number;
  readonly droppedSeparation: number;
  readonly excludedQuality: number;
  readonly meanOverlap: number | null;
  /** Effective / raw, for the caller to render. `null` when there were no events. */
  readonly retention: number | null;
  /** Non-null whenever the effective sample is materially below the raw count. */
  readonly warning: string | null;
}

/** Below this, the headline count is misleading enough to warrant saying so. */
export const MATERIAL_SAMPLE_LOSS = 0.8;

export function sampleReading(sample: SamplingResultDto): SampleReading {
  const retention = sample.raw_events === 0 ? null : sample.effective_sample / sample.raw_events;
  return {
    rawEvents: sample.raw_events,
    effectiveSample: sample.effective_sample,
    clusters: sample.clusters,
    droppedOverlapping: sample.dropped_overlapping,
    droppedSeparation: sample.dropped_separation,
    excludedQuality: sample.excluded_quality,
    meanOverlap: sample.mean_overlap,
    retention,
    warning:
      retention !== null && retention < MATERIAL_SAMPLE_LOSS
        ? `${sample.raw_events} raw events reduce to an effective sample of ` +
          `${sample.effective_sample} after clustering. Statistics computed on ` +
          `overlapping windows are not independent, and significance read off the ` +
          `raw count would be overstated.`
        : null,
  };
}

export interface StudyResultSummary {
  readonly studyId: string;
  readonly studyVersion: number;
  readonly contentHash: string;
  readonly datasetHash: string | null;
  readonly status: string;
  readonly statusDetail: string | null;
  readonly sample: SampleReading;
  readonly queryMode: string;
  readonly knowledgeHorizon: string | null;
  /** Loud: a hindsight study answers a different question from a point-in-time one. */
  readonly isHindsight: boolean;
  readonly buildContextId: string | null;
  readonly featureVersions: Readonly<Record<string, number>>;
  readonly randomSeed: number | null;
}

export function studyResultSummary(result: StudyResultDto): StudyResultSummary {
  return {
    studyId: result.study_id,
    studyVersion: result.study_version,
    contentHash: result.content_hash,
    datasetHash: result.dataset_content_hash,
    status: result.status,
    statusDetail: result.status_detail,
    sample: sampleReading(result.sample),
    queryMode: result.query_mode,
    knowledgeHorizon: result.knowledge_horizon,
    isHindsight: result.is_hindsight,
    buildContextId: result.build_context_id,
    featureVersions: result.feature_versions,
    randomSeed: result.random_seed,
  };
}

export interface DatasetSummary {
  readonly name: string;
  readonly contentHash: string;
  readonly rowCount: number;
  readonly queryMode: string;
  readonly knowledgeHorizon: string | null;
  readonly createdAt: string | null;
}

export function datasetSummary(dataset: DatasetDto): DatasetSummary {
  return {
    name: dataset.name,
    contentHash: dataset.content_hash,
    rowCount: dataset.row_count,
    queryMode: dataset.query_mode,
    knowledgeHorizon: dataset.knowledge_horizon,
    createdAt: dataset.created_at,
  };
}

// -------------------------------------------------------------------- replay

export interface ReplayBanner {
  /** Always rendered while a replay session is in context. */
  readonly persistent: true;
  readonly axes: TimeAxes;
  readonly knowledgeMode: string;
  readonly isHindsight: boolean;
  readonly label: string;
  readonly glyph: string;
  readonly explanation: string;
}

/**
 * The banner `13` §5 requires.
 *
 * `persistent` is the literal `true`, not a boolean: there is no configuration
 * under which a replay screen renders without it, and a component cannot be written
 * that branches on it being false.
 */
export function replayBanner(
  context: ReplayContextDto,
  step: ReplayStepDto | null,
): ReplayBanner {
  const axes: TimeAxes = {
    marketTime: step?.market_time ?? null,
    knowledgeTime: step?.knowledge_time ?? context.pinned_knowledge_horizon,
  };
  return {
    persistent: true,
    axes,
    knowledgeMode: context.knowledge_mode,
    isHindsight: context.is_hindsight,
    label: context.is_hindsight ? "REPLAY — HINDSIGHT" : "REPLAY",
    glyph: context.is_hindsight ? "⚠" : "⏵",
    explanation: context.is_hindsight
      ? "This replay is pinned to a knowledge horizon later than its market time. " +
        "It shows what we now know about those moments, not what was knowable then."
      : "Replayed at the knowledge available at each moment. This is not live.",
  };
}

export interface ReplayProgressReading {
  readonly stepsCompleted: number;
  readonly statesBuilt: number;
  readonly observationsVisible: number;
  readonly checkpointHits: number;
  readonly checkpointMisses: number;
  /** Diagnostic, not a claim about correctness. */
  readonly checkpointHitRate: number | null;
}

export function replayProgress(progress: ReplayProgressDto): ReplayProgressReading {
  const total = progress.checkpoint_hits + progress.checkpoint_misses;
  return {
    stepsCompleted: progress.steps_completed,
    statesBuilt: progress.states_built,
    observationsVisible: progress.observations_visible,
    checkpointHits: progress.checkpoint_hits,
    checkpointMisses: progress.checkpoint_misses,
    checkpointHitRate: total === 0 ? null : progress.checkpoint_hits / total,
  };
}

/** The transport verbs the backend's control endpoint accepts. No order verb. */
export const REPLAY_CONTROLS = ["play", "pause", "step", "seek", "speed"] as const;
export type ReplayControl = (typeof REPLAY_CONTROLS)[number];

// ------------------------------------------------------------------ backtest

export interface AssumptionRow {
  readonly name: string;
  readonly value: string;
}

export interface BacktestSummary {
  readonly runId: string;
  readonly contentHash: string;
  readonly netPnl: Presented<string>;
  readonly grossPnl: Presented<string>;
  readonly maxDrawdown: Presented<string>;
  readonly fillRate: Presented<string>;
  readonly fills: number;
  readonly partialFills: number;
  readonly assumptionBasedFills: number;
  readonly intentsGenerated: number;
  readonly intentsRejectedByRisk: number;
  /** Rendered beside the headline, never collapsed into a note. */
  readonly assumptions: readonly AssumptionRow[];
  readonly assumptionBased: boolean;
  readonly caveats: readonly string[];
  readonly riskEvaluated: boolean;
  readonly strategyDigest: string;
  readonly replayDigest: string;
  readonly fillModelDigest: string;
  /** Non-null when the headline rests on assumed inputs. */
  readonly headlineQualifier: string | null;
}

export function backtestSummary(result: BacktestResultDto): BacktestSummary {
  const stats = result.statistics;
  const assumptions: AssumptionRow[] = Object.entries(result.assumptions).map(
    ([name, value]) => ({ name, value: String(value) }),
  );
  return {
    runId: result.run_id,
    contentHash: result.content_hash,
    netPnl: presentValue(stats.net_pnl, "NOT_COMPUTED"),
    grossPnl: presentValue(stats.gross_pnl, "NOT_COMPUTED"),
    maxDrawdown: presentValue(stats.max_drawdown, "INSUFFICIENT_HISTORY"),
    fillRate: presentValue(stats.fill_rate, "NOT_COMPUTED"),
    fills: stats.fills,
    partialFills: stats.partial_fills,
    assumptionBasedFills: stats.assumption_based_fills,
    intentsGenerated: stats.intents_generated,
    intentsRejectedByRisk: stats.intents_rejected_by_risk,
    assumptions,
    assumptionBased: result.assumption_based,
    caveats: result.caveats,
    riskEvaluated: result.risk_evaluated,
    strategyDigest: result.strategy_digest,
    replayDigest: result.replay_digest,
    fillModelDigest: result.fill_model_digest,
    headlineQualifier:
      result.assumption_based || stats.assumption_based_fills > 0
        ? `${stats.assumption_based_fills} of ${stats.fills} fills were priced against ` +
          `an assumed spread rather than an observed quote. These figures rest on the ` +
          `assumption set shown beside them.`
        : null,
  };
}
