# OI Pulse v2 — Implementation Roadmap

> **Deliverable X.** Twelve phases per the brief's §30. Each carries objective,
> dependencies, deliverables, modules, schema changes, tests, acceptance criteria and
> risks.

---

## Rebuild strategy

**Parallel build in the same repository, then cutover.** New code under `oipulse/`. The
legacy app keeps running and collecting until the new stack reaches parity on ingestion
and read paths, then legacy is removed. This avoids a big-bang rewrite while satisfying
"do not preserve the existing architecture" — no legacy module is refactored into the new
one; the old system is reference material and a stopgap data source, nothing more.

Legacy data is **not migrated into v2 schemas**. Its snapshots are user-scoped,
front-expiry-only and missing greeks and quotes; importing them would contaminate a
canonical store with data that cannot satisfy v2's invariants. They remain queryable in
place for as long as they are useful.

## Sequencing principle

**Ingestion is front-loaded.** Full historical intraday option-chain state cannot be
reconstructed for arbitrary past timestamps from the Upstox APIs. However, historical OI
can be obtained for supported dates and may be used for limited backfill and research.

Therefore every day Phase 2 is not running is a day of full-fidelity history that will
never exist. Phases 6–7 are gated on history depth. Getting greeks, quotes and
multi-expiry collection persisting early is worth more than any analytics built on them.

---

## Phase 1 — Foundation

**Objective** Skeleton with boundaries enforced, so later phases cannot erode the design.

**Dependencies** None.

**Deliverables** `core/` (Clock, ids, money, errors, config, time authority) · repository
base with the three mandatory query modes · migration framework · outbox skeleton ·
structured logging with correlation · health/readiness · CI pipeline.

**Modules** `core/`, `events/`, `observability/`, `api/` (health only).

**Schema** `sys_outbox`, `sys_event_inbox`, `sys_retention_locks`, migration
metadata.

**Tests** Import-linter contracts; clock-access AST scan; outbox idempotency; config
validation refuses placeholders.

**Acceptance** CI fails on a boundary violation or a `datetime.now()` call. A repository
method without a time mode does not compile past lint.

**Risks** Over-engineering the skeleton. Mitigation: only what Phase 2 needs.

---

## Phase 2 — Canonical market data

**Objective** Observations flowing, canonical, bitemporal, idempotent, multi-expiry.

**Dependencies** Phase 1.

**Deliverables** Instrument master (identity / version / vendor mapping) · expiry calendar
from the exchange · `InstrumentUniverse` · **`SubscriptionPlanner`** (capacity planning,
`ACCEPTED`/`DEGRADED`/`UNSATISFIABLE`) · `UpstoxMarketDataProvider` (REST + WS) · event
identity resolution with `identity_confidence` · **provider timestamp mapping and
feed-session identity verification (soak)** · append-only bitemporal observation store ·
rate-limit governor · historical **daily** OI backfill job · OHLC backfill.

**Modules** `instruments/`, `marketdata/*`.

**Schema** `instrument_*` (three tables + options/futures/expiries/universes),
`obs_quotes`, `obs_greeks`, `obs_depth`, `obs_ohlc`, `obs_index`, `obs_historical_oi`,
`chain_snapshots`, daily partitioning.

**Tests** Ingestion idempotency under replay; two distinct events at one timestamp yield
two rows; out-of-order arrival; reconnect creates a new feed session; backfill invisible
to earlier `knowledge_as_of`; instrument version resolution as-of.

**Acceptance** WS + REST ingesting for all configured underlyings across **multiple
expiries**, with greeks and bid/ask persisted. Replaying a day changes no row counts.
`knowledge_as_of` and `market_truth_at` return correctly divergent results on the
11:40/11:44 fixture.

**Risks** Subscription/connection limits constrain expiry breadth — mitigated by the
planner making the cost explicit **before** subscribing (A-5). WS behaviour may differ
from documentation — mitigated by a soak recording raw frames before any correctness
dependency on provider sequence or venue timestamps (A-1, A-3); until verified, identity
degrades to content hash with `identity_confidence = WEAK` and gap detection claims no
more than the identity supports.

> **Start collecting at the end of this phase and never stop.** Everything downstream is
> gated on history depth.

---

## Phase 3 — Market state

**Objective** `MarketState(T, K, BuildContext)` coherent, quality-assessed and
reconstructable.

**Dependencies** Phase 2.

**Deliverables** Data-quality detectors · `QualityAssessment` · staleness budgets ·
coherence modes · chain/WS reconciliation · `build_state()` · checkpointing · live state
in Redis · reconstruction API.

**Modules** `dataquality/`, `marketstate/`.

**Schema** `dq_issues`, `dq_assessments`, **`state_build_contexts`**, `state_checkpoints`
(keyed on `underlying, observed_at, knowledge_horizon, build_context_id`),
`state_checkpoint_legs`, `state_checkpoint_expiries`.

**Tests** Assembly under each coherence mode; each staleness breach escalates correctly;
WS gap → REST recovery; divergence detection; reconstruction with and without checkpoints
is identical; checkpoint dedup; **same `T` with different `K` yields different states**;
**a later-K checkpoint is never substituted**; a changed staleness policy yields a new
`build_context_id`.

**Acceptance** A state for any `(T, K, context)` with observation coverage, identical
whether served from a matching checkpoint or reconstructed. Checkpoint selection is exact
match on the full identity tuple. Deleting all checkpoints changes no reconstructed
result. Quality status visible via API.

**Risks** Staleness budgets set wrong — mitigated by deriving them from measured feed
cadence during this phase rather than guessing (A-6), and by carrying them in
`build_context_id` so a recalibration never silently conflates states.

---

## Phase 4 — Analytics

**Objective** Every metric documented, versioned, tested, with correct availability.

**Dependencies** Phase 3.

**Deliverables** Feature registry + decorator + registry API · the seven domains
(positioning, volatility, greeks, gamma, price, futures, structure) · `OIMigration`
tracking · deterministic regime classifier.

**Modules** `analytics/*`.

**Schema** `metric_values`, `interp_labels`, `metric_oi_migrations`.

**Tests** The mandatory six per feature; property tests (aggregation consistency, PCR
domain, migration bounds); **availability derives from input readiness — a late-arriving
raw input delays `available_at`, and a dependency's availability propagates**; features
refuse to compute when quality requirements are unmet.

**Acceptance** Every feature in the registry with definition, units, convention and
availability delay, served by `/features` and rendered in the UI. No feature computes over
a backfill-only period if it requires quotes or greeks.

**Risks** Feature sprawl — mitigated by the brief's rule that a metric needs a downstream
consumer. Lot-size correctness gates all exposure work: **re-verify against Upstox
contract data**, since the legacy seed values are stale.

---

## Phase 5 — Signals and alerts

**Objective** Explainable signals with a walkable evidence trail; reliable alert delivery.

**Dependencies** Phase 4.

**Deliverables** Signal framework · rule decorator with pinned feature versions ·
evidence model · lifecycle · strength derivation · alert rules, evaluation, dedup,
cooldown · SSE + one out-of-band channel.

**Modules** `signals/`, `alerts/`.

**Schema** `signal_signals`, `signal_evidence`, `alert_rules`, `alert_occurrences`.

**Tests** Rules produce contradicting evidence; no firing on `UNRELIABLE`; lifecycle
transitions; alert dedup and cooldown; delivery retry; the evidence trail resolves by join
from signal to raw observation.

**Acceptance** Every signal traceable `Signal → Evidence → MetricValue → MarketState →
Observation` in SQL. No signal carries an unexplained confidence number.

**Risks** Rules that never produce contradicting evidence — the framework flags them.
Alert fatigue — mitigated by cooldowns and dedup from the start.

---

## Phase 6 — Research and event studies

**Objective** Point-in-time-correct research over accumulated history.

**Dependencies** Phase 5, **plus sufficient history depth**.

**Deliverables** Event-study engine · dataset materialization with content hashing ·
statistics (distribution, MFE, MAE, excursion timing) · breakdowns · signal evaluation ·
evidence attribution.

**Modules** `research/`.

**Schema** `research_studies`, `research_datasets`, `research_results`,
`research_signal_evaluations`.

**Tests** Dataset reproducibility by content hash; minimum-sample enforcement; quality
exclusion counts reported; survivorship (universe resolved as-of); backfill and correction
leakage tests.

**Acceptance** A study re-runs to an identical content hash and identical statistics.
Results below minimum sample report insufficiency rather than a number.

**Risks** Insufficient history makes early results meaningless — mitigated by enforced
minimum samples and by having started collection in Phase 2.

---

## Phase 7 — Replay and backtesting

**Objective** Deterministic replay; backtests provably free of look-ahead.

**Dependencies** Phase 6.

**Deliverables** Replay engine with clock control and knowledge horizon ·
**knowledge-aware checkpoint selection** · **deterministic cross-session observation
ordering** (`observed_at, feed_session_ordinal, channel_sequence, id`) · stepping modes ·
speed control · strategy interface · fill model (latency, spread, slippage, partials,
fees) · backtest runner using the **real** risk engine and OMS.

**Modules** `replay/`, `backtest/`.

**Schema** `backtest_runs`, `backtest_results`, `backtest_trades`, replay-scoped event
namespace.

**Tests** **Every failing test in `15-TESTING.md` §2.** Replay reproduces the live event
stream; strategy context has no repository access; forward-window data unreachable;
backtest reproducibility.

**Acceptance** Deliberate look-ahead attempts fail the suite. A replay of a past session
reproduces the domain events the live run produced.

**Risks** Hidden non-determinism — the replay-vs-live event comparison is precisely the
detector. Fill-model realism — mitigated by printing assumptions beside every result and
flagging assumption-based runs.

---

## Phase 8 — Paper trading

**Objective** Trade end-to-end with no real money, over the production contracts.

**Dependencies** Phase 7.

**Deliverables** `TradeIntent` · `PaperBrokerAdapter` (full `BrokerAdapter`, including
simulated `UNKNOWN`) · OMS state machine · positions · P&L · journal.

**Modules** `trading/intents`, `trading/brokers`, `trading/oms`, `trading/portfolio`.

**Schema** `trade_accounts`, `trade_intents`, `trade_orders`, `trade_order_events`,
`trade_fills`, `portfolio_positions`, `journal_entries`.

**Tests** Order state machine permitted and forbidden transitions; partial fills;
duplicate callbacks idempotent; positions fold from fills; paper and live adapters satisfy
one shared suite.

**Acceptance** A signal can drive an intent to a filled paper position with full audit.
No component knows whether it is paper or live.

**Risks** Paper divergence from live — mitigated by one adapter protocol and one shared
test suite.

---

## Phase 9 — Risk

**Objective** A non-bypassable, independently testable gate.

**Dependencies** Phase 8.

**Deliverables** Risk engine · limit categories (position, order, capital, loss, exposure,
concentration, **stale data**, broker health, session) · immutable decision sequence ·
kill switch.

**Modules** `trading/risk`.

**Schema** `risk_profiles`, `risk_decisions` (PK `intent_id, sequence_no`, each carrying
`risk_state_ref`, `risk_evaluation_time`, `inputs_digest`, `approved_until`),
`trade_orders.authorizing_risk_decision_id`.

**Tests** Each limit at boundary; kill switch halts immediately; decisions append rather
than replace; each decision records the state **it** evaluated; an expired `approved_until`
is refused rather than silently re-approved; **no code path reaches a broker without an
approved decision from its own intent** (static + DB constraint + runtime).

**Acceptance** Risk is testable with no trading infrastructure present. An order cannot
be inserted without an approved decision **from its own intent**.

**Risks** Over-permissive defaults — mitigated by conservative defaults requiring explicit
relaxation, audited.

---

## Phase 10 — OMS, execution abstraction, reconciliation

**Objective** Production-grade order handling and reconciliation. **Live still disabled.**

**Dependencies** Phase 9.

**Deliverables** `UpstoxBrokerAdapter` (flagged off) · broker order-update WS ·
**reconciliation subsystem** · `UNKNOWN` / `PENDING_RECONCILIATION` handling · idempotency
keys · startup reconciliation gate.

**Modules** `trading/brokers`, `trading/reconciliation`.

**Schema** `trade_reconciliations`, `trade_reconciliation_discrepancies`.

**Tests** Acknowledgement loss → `UNKNOWN`, never a terminal state; no resubmit from
`UNKNOWN`; restart mid-submit; missed broker events; broker-side cancellation; manual
broker-side change; **wipe local state, reconcile, match the broker**.

**Acceptance** Reconciliation rebuilds local order and position state from broker truth.
`trader` is not ready until reconciliation is clean. Live trading remains off.

**Risks** Reconciliation bugs are the most dangerous class here — mitigated by treating it
as a first-class subsystem with its own suite, and by keeping live disabled until it is
exercised extensively against the paper adapter's fault injection.

---

## Phase 11 — Portfolio and attribution

**Objective** Answer "where did P&L come from?"

**Dependencies** Phase 10.

**Deliverables** Portfolio snapshots · exposure and greeks · margin · drawdown ·
attribution by direction / vol / theta / gamma / execution / slippage / costs · slicing by
strategy, signal, underlying, expiry, regime, time of day.

**Modules** `trading/portfolio`.

**Schema** `portfolio_snapshots`, `portfolio_attribution`.

**Tests** P&L arithmetic; greek aggregation using the instrument version valid at the
time; attribution components plus residual reconcile to total P&L.

**Acceptance** Attribution decomposes P&L with the residual **reported**, and slices by
regime.

**Risks** Attribution residual large enough to be meaningless — mitigated by reporting it
prominently; a large residual is information, not something to hide.

---

## Phase 12 — Professional terminal

**Objective** The twelve workflow screens.

**Dependencies** Phases 3–11 as each screen's backend lands.

**Deliverables** Command Center · Option Surface · Positioning · Volatility · Market
Structure · Signals · Alerts · Research · Replay · Backtest · Paper Trading · Portfolio ·
Risk · Journal.

**Modules** `frontend/`.

**Tests** Component and integration; traceability drill-through resolves; quality
indicator reflects API state; replay banner always present.

**Acceptance** Each screen answers its stated question. No screen ships ahead of its
backend — no "Coming soon" pages. Every displayed metric drills through to its definition
and inputs.

**Risks** UI ahead of backend — prevented by the no-stub rule. Over-dense presentation —
mitigated by the one-question-per-screen constraint.

---

## Cutover and legacy removal

After Phase 3 (v2 ingestion and read paths at parity), the legacy app is switched to
read-only and its collector stopped. After Phase 5, it is removed from deployment. Its
database is retained read-only until Phase 6 confirms nothing depends on it.

## Cross-phase, continuous

Testing (`15`), observability (`16`) and security (`17`) are not phases. Every phase ships
with its tests, its metrics and its audit records, and CI gates enforce it.

## Unresolved assumptions

Carried into `20-ARCHITECTURE_FREEZE.md` §10 rather than silently resolved.
