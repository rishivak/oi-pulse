/**
 * Payload types, each tied to the backend shape it describes.
 *
 * Phase 12 brief §24 asks for generated contracts and forbids hand-duplicated DTOs
 * "if generated/shared contracts are available". Routes and key sets *are*
 * generated — `contract.generated.ts` is produced from `oipulse/api/*.py` by
 * `tools/export_api_contract.py`. What cannot be generated here is the *types* of
 * those keys: the backend is untyped on the wire (`dict[str, Any]` returns), no
 * OpenAPI document can be produced because `fastapi` is not installable in this
 * environment, and the value types live in Python dataclasses that the AST exporter
 * reads names from rather than types.
 *
 * So the field *names* are not hand-maintained even though the types are. Every
 * interface below carries a `@contract` tag naming the generated entry it describes,
 * and `tools/check_frontend_contract.py` fails when an interface declares a field
 * that entry does not contain. The failure mode that costs an afternoon — a screen
 * reading `order.status` when the backend sends `order.state` — is caught
 * mechanically; the one this cannot catch is a type mismatch on a field that does
 * exist, which is recorded as a limitation rather than implied away.
 *
 * Decimals arrive as **strings**. `oipulse/marketstate/serialisation.py` explains
 * why: "JSON numbers are IEEE doubles in most clients, and a rupee price that
 * round-trips through a double is no longer the price the venue quoted." They are
 * typed as `string` here and are never parsed into a number for arithmetic —
 * only for display width and sign.
 */

// ------------------------------------------------------------- market state
// The MarketState envelope is assembled inline by `state_to_envelope` rather than
// by a domain `as_dict`, so there is no generated entry for its nested shapes.
// Marked so the guard reports it as uncovered rather than silently skipping it.

/** @contract none — nested literal in `oipulse/marketstate/serialisation.py` */
export interface SpotDto {
  readonly ltp: string | null;
  readonly change: string | null;
  readonly prev_close: string | null;
  readonly open: string | null;
  readonly high: string | null;
  readonly low: string | null;
  readonly observed_at: string | null;
  readonly age_seconds: number | null;
  readonly stale: boolean;
}

/** @contract none — nested literal in `oipulse/marketstate/serialisation.py` */
export interface OptionLegDto {
  readonly instrument_id: number;
  readonly strike: string | null;
  readonly option_type: string;
  readonly ltp: string | null;
  readonly bid: string | null;
  readonly ask: string | null;
  readonly volume: number | null;
  readonly oi: number | null;
  readonly provider_prev_oi: number | null;
  readonly iv: string | null;
  readonly delta: string | null;
  readonly gamma: string | null;
  readonly theta: string | null;
  readonly vega: string | null;
  readonly quote_observed_at: string | null;
  readonly greeks_observed_at: string | null;
  readonly quote_age_seconds: number | null;
  readonly greeks_age_seconds: number | null;
  /** Three independent staleness flags: a fresh quote does not make greeks fresh. */
  readonly quote_stale: boolean;
  readonly oi_stale: boolean;
  readonly greeks_stale: boolean;
}

/** @contract none — nested literal in `oipulse/marketstate/serialisation.py` */
export interface ExpiryDto {
  readonly expiry_id: number;
  readonly expiry_date: string;
  readonly coverage_ratio: number | null;
  readonly missing_leg_count: number;
  readonly aggregates: {
    readonly total_call_oi: number | null;
    readonly total_put_oi: number | null;
    readonly pcr: string | null;
    readonly atm_strike: string | null;
  };
  readonly surfaces: {
    readonly oi_by_strike: Readonly<Record<string, readonly (number | null)[]>>;
    readonly iv_by_strike: Readonly<Record<string, readonly (string | null)[]>>;
    readonly gamma_by_strike: Readonly<Record<string, readonly (string | null)[]>>;
  };
  readonly legs: readonly OptionLegDto[];
}

/** @contract none — nested literal in `oipulse/marketstate/serialisation.py` */
export interface FutureDto {
  readonly instrument_id: number;
  readonly expiry_id: number | null;
  readonly ltp: string | null;
  readonly oi: number | null;
  readonly volume: number | null;
  readonly basis: string | null;
  readonly observed_at: string | null;
  readonly age_seconds: number | null;
  readonly stale: boolean;
}

/** @contract none — nested literal in `oipulse/marketstate/serialisation.py` */
export interface MarketStateDto {
  readonly underlying_id: number;
  readonly session_phase: string;
  readonly session_date: string | null;
  readonly spot: SpotDto;
  readonly futures: readonly FutureDto[];
  readonly expiries: readonly ExpiryDto[];
}

// ----------------------------------------------------------------- analytics

/** @contract model FeatureSpec */
export interface FeatureSpecDto {
  readonly identifier: string;
  readonly version: number;
  readonly definition: string;
  readonly formula: string | null;
  readonly units: string | null;
  readonly scope: string;
  readonly inputs: readonly string[];
  readonly depends_on: readonly string[];
  readonly parameters: Readonly<Record<string, unknown>>;
  readonly lookback: number | null;
  readonly availability_delay: number | null;
  readonly sampling_frequency: number | null;
  readonly normalization: string | null;
  readonly quality_requirements: Readonly<Record<string, unknown>>;
  readonly implementation_ref: string | null;
}

// ------------------------------------------------------------------- signals

/** @contract serializer evidence_to_dict */
export interface EvidenceDto {
  readonly kind: string;
  readonly statement: string;
  readonly weight: string | null;
  readonly metric_ref: string | null;
  readonly observation_refs: readonly string[];
  readonly observed_at: string | null;
}

/** @contract model SignalProvenance */
export interface SignalProvenanceDto {
  readonly rule_type: string;
  readonly rule_version: number;
  readonly build_context_id: string | null;
  readonly config_digest: string | null;
  readonly inputs_digest: string | null;
  readonly feature_versions: Readonly<Record<string, number>>;
  readonly state_checkpoint_ref: string | null;
  readonly strength_function: string | null;
  readonly strength_function_version: number | null;
}

/**
 * @contract serializer signal_to_dict
 *
 * `contradiction_assessment` is a union on the wire and stays one here. The backend
 * note is exact: "'assessed, found nothing' and 'has contradicting evidence' are
 * different claims and a client must be able to tell them apart." Flattening it to
 * an array would make the first indistinguishable from the second having been empty.
 */
export interface SignalDto {
  readonly signal_id: string;
  readonly type: string;
  readonly rule_version: number;
  readonly underlying_id: number;
  readonly expiry_id: number | null;
  readonly occurrence: number;
  readonly status: string;
  readonly strength: string | null;
  readonly horizon_seconds: number;
  readonly market_time: string | null;
  readonly knowledge_time: string | null;
  readonly created_at: string | null;
  readonly updated_at: string | null;
  readonly available_at: string | null;
  readonly expires_at: string | null;
  readonly invalidation_condition: string | null;
  readonly quality_status: string;
  readonly contradiction_assessment: "NONE_OBSERVED" | readonly EvidenceDto[];
  readonly provenance: SignalProvenanceDto;
  readonly content_digest: string;
}

/** @contract model SignalRuleSpec */
export interface SignalRuleSpecDto {
  readonly signal_type: string;
  readonly version: number;
  readonly definition: string;
  readonly horizon: number | null;
  readonly requires_features: readonly string[];
  readonly quality_requirements: Readonly<Record<string, unknown>>;
  readonly evaluation_interval: number | null;
  readonly persistence_evaluations: number | null;
  readonly strength_function: string | null;
  readonly default_config: Readonly<Record<string, unknown>>;
  readonly implementation_ref: string | null;
}

// -------------------------------------------------------------------- alerts

/** @contract serializer rule_to_dict */
export interface AlertRuleDto {
  readonly id: string;
  readonly signal_type: string;
  readonly channel: string;
  readonly severity: string;
  readonly on_statuses: readonly string[];
  readonly min_strength: string;
  readonly cooldown_seconds: number;
  readonly dedup_window_seconds: number;
  readonly enabled: boolean;
  readonly underlying_id: number | null;
  /** Names a destination configuration resolves. Never a credential. */
  readonly destination: string | null;
  readonly config_digest: string;
}

/** @contract serializer occurrence_to_dict */
export interface AlertOccurrenceDto {
  readonly occurrence_id: string;
  readonly rule_id: string;
  readonly rule_config_digest: string;
  readonly signal_id: string;
  readonly signal_type: string;
  readonly underlying_id: number | null;
  readonly status: string;
  readonly severity: string;
  readonly channel: string;
  readonly observed_at: string | null;
  readonly available_at: string | null;
  readonly triggered_at: string | null;
  readonly dedup_key: string;
  readonly delivered: boolean;
  readonly attempts: readonly {
    readonly attempt: number;
    readonly attempted_at: string | null;
    readonly status: string;
    readonly channel: string;
    readonly detail: string | null;
  }[];
  readonly acknowledged_at: string | null;
  readonly acknowledged_by: string | null;
}

// ------------------------------------------------------------------ research

/** @contract model SamplingResult */
export interface SamplingResultDto {
  readonly policy: Readonly<Record<string, unknown>>;
  readonly raw_events: number;
  /** After clustering. `13` §5 requires this beside the raw count, not instead of it. */
  readonly effective_sample: number;
  readonly clusters: number;
  readonly dropped_overlapping: number;
  readonly dropped_separation: number;
  readonly excluded_quality: number;
  readonly mean_overlap: number | null;
}

/** @contract model EventStudy */
export interface EventStudyDto {
  readonly study_id: string;
  readonly version: number;
  readonly question: string;
  readonly universe: Readonly<Record<string, unknown>>;
  readonly period: Readonly<Record<string, unknown>>;
  readonly event_definition: Readonly<Record<string, unknown>>;
  readonly horizons_seconds: readonly number[];
  readonly controls: Readonly<Record<string, unknown>>;
  readonly sampling: Readonly<Record<string, unknown>>;
  readonly minimum_sample: number | null;
  readonly comparison_count: number | null;
  readonly query_mode: string;
  readonly is_hindsight: boolean;
  readonly feature_versions: Readonly<Record<string, number>>;
  readonly signal_versions: Readonly<Record<string, number>>;
  readonly content_digest: string;
}

/** @contract model StudyResult */
export interface StudyResultDto {
  readonly study_id: string;
  readonly study_version: number;
  readonly study_digest: string;
  readonly content_hash: string;
  readonly dataset_content_hash: string | null;
  readonly event_definition_digest: string | null;
  readonly status: string;
  readonly status_detail: string | null;
  readonly sample: SamplingResultDto;
  readonly horizons: readonly Readonly<Record<string, unknown>>[];
  readonly query_mode: string;
  readonly knowledge_horizon: string | null;
  readonly is_hindsight: boolean;
  readonly build_context_id: string | null;
  readonly builder_version: string | null;
  readonly feature_versions: Readonly<Record<string, number>>;
  readonly signal_versions: Readonly<Record<string, number>>;
  readonly random_seed: number | null;
  readonly execution: Readonly<Record<string, unknown>>;
}

/** @contract model Dataset */
export interface DatasetDto {
  readonly name: string;
  readonly content_hash: string;
  readonly row_count: number;
  readonly period: Readonly<Record<string, unknown>>;
  readonly universe: Readonly<Record<string, unknown>>;
  readonly query_mode: string;
  readonly knowledge_horizon: string | null;
  readonly quality_summary: Readonly<Record<string, unknown>>;
  readonly build_context_id: string | null;
  readonly builder_version: string | null;
  readonly feature_versions: Readonly<Record<string, number>>;
  readonly created_at: string | null;
}

// -------------------------------------------------------------------- replay

/** @contract model ReplayContext */
export interface ReplayContextDto {
  readonly run_id: string;
  readonly underlying_ids: readonly number[];
  readonly expiry_ids: readonly number[];
  readonly period: Readonly<Record<string, unknown>>;
  readonly step_mode: string;
  readonly interval_seconds: number | null;
  readonly speed: number;
  readonly knowledge_mode: string;
  readonly pinned_knowledge_horizon: string | null;
  readonly is_hindsight: boolean;
  readonly build_context_id: string | null;
  readonly feature_versions: Readonly<Record<string, number>>;
  readonly rule_versions: Readonly<Record<string, number>>;
  readonly content_digest: string;
}

/** @contract serializer step_to_dict */
export interface ReplayStepDto {
  readonly index: number;
  readonly market_time: string;
  readonly knowledge_time: string;
  readonly is_lockstep: boolean;
}

/** @contract model ReplayProgress */
export interface ReplayProgressDto {
  readonly steps_completed: number;
  readonly states_built: number;
  readonly observations_visible: number;
  readonly checkpoint_hits: number;
  readonly checkpoint_misses: number;
}

// ------------------------------------------------------------------ backtest

/** @contract model BacktestStatistics */
export interface BacktestStatisticsDto {
  readonly intents_generated: number;
  readonly intents_rejected_by_risk: number;
  readonly orders_submitted: number;
  readonly fills: number;
  readonly partial_fills: number;
  readonly fill_rate: string | null;
  readonly assumption_based_fills: number;
  readonly rejections: number;
  readonly rejection_reasons: Readonly<Record<string, number>>;
  readonly gross_pnl: string;
  readonly net_pnl: string;
  readonly realized_pnl: string;
  readonly unrealized_pnl: string;
  readonly fees: string;
  readonly slippage_cost: string;
  readonly turnover: string;
  readonly max_drawdown: string | null;
}

/** @contract model BacktestResult */
export interface BacktestResultDto {
  readonly run_id: string;
  readonly content_hash: string;
  readonly strategy: Readonly<Record<string, unknown>>;
  readonly strategy_digest: string;
  readonly replay_digest: string;
  readonly fill_model_digest: string;
  readonly statistics: BacktestStatisticsDto;
  /** Shown beside the headline number, never in a footnote (`13` §5). */
  readonly assumptions: Readonly<Record<string, unknown>>;
  readonly assumption_based: boolean;
  readonly caveats: readonly string[];
  readonly risk_evaluated: boolean;
  readonly fills: readonly Readonly<Record<string, unknown>>[];
  readonly final_ledger: Readonly<Record<string, unknown>>;
  readonly build_context_id: string | null;
  readonly execution: Readonly<Record<string, unknown>>;
}

// ------------------------------------------------------------- paper trading

/** @contract model PaperAccount */
export interface PaperAccountDto {
  readonly account_id: string;
  readonly label: string;
  readonly owner: string | null;
  /** Always `PAPER` in this build. The badge refuses to assume it. */
  readonly mode: string;
  readonly status: string;
  readonly config: Readonly<Record<string, unknown>>;
  readonly config_digest: string;
  readonly opened_at: string | null;
  readonly closed_at: string | null;
}

/** @contract model OrderEvent */
export interface OrderEventDto {
  readonly order_id: string;
  readonly sequence: number;
  readonly from_state: string | null;
  readonly to_state: string;
  readonly trigger: string;
  readonly occurred_at: string;
  readonly payload: Readonly<Record<string, unknown>>;
}

/** @contract model PaperOrder */
export interface OrderDto {
  readonly order_id: string;
  readonly account_id: string;
  readonly intent_id: string;
  readonly instrument_id: number;
  readonly side: string;
  readonly quantity: number;
  readonly order_type: string;
  readonly limit_price: string | null;
  readonly state: string;
  readonly venue: string;
  readonly mode: string;
  readonly filled_quantity: number;
  readonly remaining_quantity: number;
  readonly average_fill_price: string | null;
  readonly is_terminal: boolean;
  readonly is_unresolved: boolean;
  readonly provider_order_id: string | null;
  readonly provider_status: string | null;
  readonly provider_event_time: string | null;
  readonly reject_reason: string | null;
  readonly reject_detail: string | null;
  readonly authorizing_risk_decision_id: string | null;
  readonly authorizing_decision_sequence: number | null;
  readonly signal_id: string | null;
  readonly signal_version: number | null;
  readonly strategy_id: string | null;
  readonly strategy_version: number | null;
  readonly build_context_id: string | null;
  readonly state_checkpoint_ref: string | null;
  readonly config_digest: string | null;
  readonly client_order_attempt_id: string | null;
  // No `market_time`: `PaperOrder.as_dict()` carries `knowledge_time` and
  // `decision_time` but not a market time, and the contract check rejected the
  // field when it was assumed here. An order is an action, and the action's time
  // is its decision time.
  readonly knowledge_time: string | null;
  readonly decision_time: string | null;
  readonly created_at: string | null;
  readonly received_at: string | null;
  readonly events: readonly OrderEventDto[];
}

/** @contract model Fill */
export interface FillDto {
  readonly intent_id: string;
  readonly instrument_id: number;
  readonly side: string;
  readonly quantity: number;
  readonly requested_quantity: number;
  readonly is_partial: boolean;
  readonly price: string;
  readonly reference_price: string | null;
  readonly price_source: string;
  readonly slippage_model: string | null;
  readonly slippage_per_unit: string | null;
  /** True when the fill was priced against an assumed spread (`10` §6). */
  readonly assumption_based: boolean;
  readonly costs: Readonly<Record<string, unknown>>;
  readonly filled_at: string;
}

/** @contract model AuditChain */
export interface AuditChainDto {
  readonly intent: Readonly<Record<string, unknown>>;
  readonly risk_decisions: readonly Readonly<Record<string, unknown>>[];
  readonly order: Readonly<Record<string, unknown>>;
  readonly fills: readonly Readonly<Record<string, unknown>>[];
  /** The eleven questions, answered, with `not recorded` where a link is absent. */
  readonly answers: Readonly<Record<string, unknown>>;
}

// ---------------------------------------------------------------------- risk

/** @contract model RiskDecisionStub */
export interface RiskDecisionStubDto {
  readonly intent_id: string;
  readonly verdict: string;
  readonly evaluated: boolean;
  readonly reason: string | null;
}

// --------------------------------------------------------------- OMS / recon

/** @contract model Discrepancy */
export interface DiscrepancyDto {
  readonly kind: string;
  readonly resolution: string;
  readonly order_id: string | null;
  readonly provider_order_id: string | null;
  readonly local_state: string | null;
  readonly provider_status: string | null;
  readonly local_value: string | null;
  readonly provider_value: string | null;
  readonly detail: string;
  readonly needs_attention: boolean;
}

// ----------------------------------------------------------------- portfolio

/** @contract model PortfolioPosition */
export interface PortfolioPositionDto {
  readonly quantity: number;
  readonly average_price: string | null;
  readonly cost_basis: string | null;
  readonly cost_basis_method: string;
  readonly realized_pnl: string;
  readonly fees: string;
  readonly status: string;
  readonly economics: Readonly<Record<string, unknown>> | null;
  readonly underlying_id: number | null;
  readonly expiry_id: number | null;
  readonly opened_at: string | null;
  readonly last_fill_at: string | null;
  readonly last_fill_key: string | null;
  readonly fills_applied: number;
}

/** @contract model ValuationResult */
export interface ValuationResultDto {
  readonly market_time: string;
  readonly knowledge_time: string;
  readonly positions: readonly Readonly<Record<string, unknown>>[];
  readonly total_market_value: string;
  readonly total_unrealized_pnl: string;
  readonly gross_exposure: string;
  readonly net_exposure: string;
  /** The honesty flag, beside every total it qualifies. */
  readonly is_complete: boolean;
  readonly unvalued_instruments: readonly number[];
  readonly state_quality: string | null;
  readonly market_state_ref: string | null;
  readonly build_context_id: string | null;
}

/** @contract model PortfolioGreeks */
export interface PortfolioGreeksDto {
  readonly delta: string | null;
  readonly gamma: string | null;
  readonly vega: string | null;
  readonly theta: string | null;
  readonly is_complete: boolean;
  readonly positions_included: number;
  readonly positions_total: number;
}

/** @contract model ReturnInputs */
export interface ReturnInputsDto {
  readonly starting_capital: string | null;
  readonly ending_equity: string | null;
  readonly net_cash_flows: string | null;
  readonly gross_pnl: string | null;
  readonly net_pnl: string | null;
  readonly realized_pnl: string | null;
  readonly unrealized_pnl: string | null;
  readonly fees: string | null;
  readonly simple_period_return: string | null;
  /** Labelled `SIMPLE_PERIOD`. Neither TWR nor MWR is implemented. */
  readonly return_methodology: string;
}

/** @contract model PortfolioSnapshot */
export interface PortfolioSnapshotDto {
  readonly account_id: string;
  readonly portfolio_id: string | null;
  readonly market_time: string;
  readonly knowledge_time: string;
  readonly cash: string;
  readonly equity: string;
  readonly market_value: string;
  readonly realized_pnl: string;
  readonly unrealized_pnl: string;
  readonly fees: string;
  readonly gross_exposure: string;
  readonly net_exposure: string;
  readonly concentration: Readonly<Record<string, unknown>> | null;
  readonly greeks: PortfolioGreeksDto;
  readonly returns: ReturnInputsDto;
  readonly drawdown: string | null;
  readonly peak_equity: string | null;
  /** `null` rather than `0` when unknown; the basis says which it is. */
  readonly margin_utilisation: string | null;
  readonly margin_basis: string | null;
  readonly valuation: ValuationResultDto;
  readonly is_complete: boolean;
  readonly unvalued_instruments: readonly number[];
  readonly build_context_id: string | null;
}

/** @contract model AttributionResult */
export interface AttributionResultDto {
  readonly bucket: string;
  readonly bucket_id: string | null;
  readonly total_pnl: string;
  readonly explained: string;
  /** Never absent, never folded into a component. */
  readonly residual: string;
  readonly residual_fraction: string | null;
  readonly reconciles: boolean;
  readonly method: string;
  readonly method_version: number;
  readonly components: readonly {
    readonly component: string;
    readonly amount: string;
    readonly inputs?: Readonly<Record<string, unknown>>;
    readonly uncomputed?: boolean;
  }[];
  readonly uncomputed_components: readonly string[];
}

/** @contract model AttributionSlice */
export interface AttributionSliceDto {
  readonly bucket: string;
  readonly bucket_id: string | null;
  readonly attribution: AttributionResultDto;
  readonly children: readonly AttributionSliceDto[];
  readonly is_unattributed: boolean;
}

/** @contract model PositionDiscrepancy */
export interface PositionDiscrepancyDto {
  readonly kind: string;
  readonly resolution: string;
  readonly instrument_id: number;
  readonly position_id: string | null;
  readonly local_quantity: number | null;
  readonly provider_quantity: number | null;
  readonly provider_average_price: string | null;
  readonly detail: string;
  readonly needs_attention: boolean;
}

/** @contract model PositionReconciliationRun */
export interface PositionReconciliationRunDto {
  readonly run_id: string;
  readonly account_id: string;
  readonly portfolio_id: string | null;
  readonly as_of: string;
  readonly started_at: string | null;
  readonly completed_at: string | null;
  readonly local_snapshot: Readonly<Record<string, unknown>>;
  readonly provider_snapshot: Readonly<Record<string, unknown>>;
  readonly discrepancies: readonly PositionDiscrepancyDto[];
  readonly matched: number;
  readonly mismatched: number;
  readonly corrections_applied: number;
  readonly is_clean: boolean;
  readonly needs_attention: boolean;
  readonly content_digest: string;
}

// ------------------------------------------------------------------- journal

/**
 * @contract model JournalEntry
 *
 * The accounting journal from Phase 8, not the reflective notebook
 * `13-FRONTEND_IA.md` §6 also describes. `order_id` and `fill_key` are the linkage
 * `12-API_SPEC.md` §3 requires: from an entry to the order, and from the order to
 * the intent, the risk decision and the signal.
 */
export interface JournalEntryDto {
  readonly entry_id: string;
  readonly account_id: string;
  readonly entry_type: string;
  readonly occurred_at: string;
  readonly cash_delta: string;
  readonly realized_pnl_delta: string;
  readonly fees_delta: string;
  readonly cash_after: string;
  readonly order_id: string | null;
  readonly fill_key: string | null;
  readonly source_event_key: string;
}
