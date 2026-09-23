# OI Pulse v2 — Architecture Freeze

> **The implementation gate.** Architecture changes stop here. Subsequent work is
> implementation against this model. No new features during implementation unless they fit
> the frozen domain model and architecture.
>
> Frozen after **three** correction passes. Full detail lives in documents `00`–`19`; this
> is the authoritative summary and the record of what remains unresolved.

---

## 1. Final module map

```
oipulse/
├── core/                  Clock · ids · money · errors · config · time authority
├── instruments/           identity · versions · vendor mappings · expiry calendar · universes
├── marketdata/
│   ├── providers/         MarketDataProvider · BrokerAdapter protocols · Upstox impls
│   ├── ingestion/         WS consumer · REST poller · backfill · event identity
│   └── store/             append-only bitemporal observation repositories
├── dataquality/           detectors · assessments · gap registry
├── marketstate/           coherence policy · build_state() · checkpoints · reconstruction
├── analytics/
│   ├── registry/          FeatureDefinition registry + versioning
│   ├── positioning/  volatility/  greeks/  gamma/  price/  futures/  structure/
├── signals/               framework · rules · evidence · lifecycle
├── alerts/                evaluation · dedup · delivery sinks
├── research/              event studies · datasets · statistics
├── replay/                state sequence reconstruction · clock control
├── backtest/              deterministic engine · fill model · availability gate
├── trading/
│   ├── intents/  risk/  oms/  brokers/  reconciliation/  portfolio/
├── events/                domain events · outbox · dispatcher
├── observability/         logging · metrics · correlation · health
├── identity/              users · sessions · permissions · credentials
└── api/                   thin HTTP surface — no business logic
```

**Boundary contracts, CI-enforced:** `analytics/*` imports only `marketstate` and `core`;
nothing imports `api/`; `trading/risk` imports neither `trading/oms` nor any broker
adapter; nothing calls `datetime.now()` outside `core.clock`.

**Process roles:** `api` · `ingestor` · `processor` · `trader` (flagged off) · `jobs`.

---

## 2. Final domain model

```
Instrument (identity) ──< InstrumentVersion        historised metadata
                      ──< InstrumentVendorMapping  historised vendor keys
                      ──< Expiry, InstrumentUniverse

MarketObservation (immutable, canonical, un-owned, identity-keyed, supersedable)
   └─ Quote · Greeks · Depth · OHLC · Index · HistoricalOI
OptionChainSnapshot   cross-sectional consistency set

QualityIssue → QualityAssessment

MarketState  ─ assembled under a staleness budget, coherence-tagged
   └─ MarketStateCheckpoint   materialization artifact, prunable

FeatureDefinition (versioned) → MetricValue → Interpretation
                                            → OIMigration (lifecycle-tracked)

Signal (lifecycle) ──< Evidence (SUPPORTING | CONTRADICTING, row-referencing)
AlertRule → AlertOccurrence

EventStudy → Dataset → StudyResult
StrategyDefinition → BacktestRun → BacktestResult

TradeIntent → RiskDecision[sequence] → Order → Fill → Position → Portfolio → Attribution
Reconciliation (broker truth authoritative)

User · Session · Permission · BrokerCredential · AuditRecord
```

**The four-layer separation is never collapsed:** Observation → Metric → Interpretation →
Signal → Trade intent.

---

## 3. Final ERD summary

Detail in `02-DATA_MODEL.md`. Structural commitments:

| Commitment | Form |
|---|---|
| Instrument identity ≠ version ≠ vendor key | three tables, non-overlapping temporal ranges |
| Observation identity | tiered partial unique indexes; **not** `(instrument, observed_at, source)` |
| Corrections | new row + `supersedes_observation_id`; never mutation |
| No user scoping on market data | no `user_id` on `obs_*`, `state_*`, `metric_*`, `signal_*` |
| State & metric identity | includes `knowledge_horizon` and `build_context_id` — **not** `builder_version` alone |
| `BuildContext` | immutable, content-addressable; subsumes builder / staleness-policy / feature-set versions |
| Event ordering | `UNIQUE (aggregate_type, aggregate_id, aggregate_sequence)` on the outbox |
| Event consumption | `sys_event_inbox` PK `(subscriber, event_id)`, committed with the mutation |
| Historical OI | `HISTORICAL_DAILY_OI` with `observation_date` + valid interval — date granularity, never an instant |
| Risk decisions | `PRIMARY KEY (intent_id, sequence_no)`, each with `risk_state_ref`, `inputs_digest`, `approved_until`; no unique-per-intent |
| Order authorization | composite FK to the exact approving decision + trigger asserting APPROVED and matching intent |
| Audit survives pruning | `sys_retention_locks` + inline provenance descriptors |
| Volume management | daily partitions on `obs_*`, BRIN on `observed_at` |

---

## 4. Final event lifecycle

**Market events** (external facts) and **domain events** (our conclusions) are distinct.
Replay re-derives domain events; it never replays stored ones as input.

```
observation persisted
  → [batched] MarketObservationReceived
  → quality checks → MarketDataGapDetected?
  → state assembled → MarketStateBuilt (at checkpoint cadence, not per tick)
  → analytics → MetricsComputed
  → rules → SignalCreated/Updated/Confirmed/Invalidated
  → alerts → AlertTriggered → AlertDelivered
  → strategy → TradeIntentCreated
  → risk → RiskCheckPassed | RiskCheckRejected
  → OMS → OrderSubmitted → OrderAcknowledged | OrderStateUnknown
        → OrderReconciliationRequired → ReconciliationCompleted
        → OrderPartiallyFilled → OrderFilled
  → PositionChanged → PnLUpdated → PortfolioSnapshotTaken
```

Delivery: transactional outbox (written in the same transaction as the state change).
Ordering is **explicit, not incidental** — every event carries
`aggregate_type`/`aggregate_id`/`aggregate_sequence`, dispatch is serialized per aggregate,
and a consumer defers sequence *n* while *n−1* is unprocessed. Consumption uses a
**transactional inbox**: the marker and the business mutation commit together, giving
*exactly-once database application per `(subscriber, event_id)`* — **not** global
exactly-once, which Postgres cannot provide for external side effects. Every event carries
`correlation_id` and `causation_id`.

**One domain event per WS tick is an architectural error** — observations are the
high-frequency record; domain events mark decisions and transitions.

---

## 5. Final time model

| Field | Meaning |
|---|---|
| `observed_at` | when the fact was true, per the venue |
| `ingested_at` | when OI Pulse received and persisted it |
| `computed_at` | when a derived value was calculated |
| `available_at` | when it became consumable by the strategy layer |

| Query mode | Predicate | Default for |
|---|---|---|
| `market_truth_at(valid_time=T, knowledge_as_of=K)` | `observed_at <= T AND ingested_at <= K` | market-truth analysis — **explicit opt-in, recorded** |
| `knowledge_at(T)` | `+ ingested_at <= T` | research, replay, reconstruction |
| `tradable_information_at(T)` | `+ available_at <= T` | strategy, backtest |

Canonical fixture:
```
observed_at 11:40:00 · ingested_at 11:40:01 · computed_at 11:40:02 · available_at 11:40:03
A decision at 11:40:02 must NOT consume that feature.
```

`decision_time` — when a strategy acts — is a **consumer/action parameter, not a fifth
stored field**. Four stored dimensions, one action parameter.

Late arrivals, corrections, backfills and delayed feeds are all handled by the bitemporal
model without special-case code. Replay pins both a market clock and a knowledge horizon.

---

## 6. Final MarketState model

`MarketState(underlying, market_time T, knowledge_horizon K, build_context B)` —
immutable, deterministically assembled, quality-assessed, reconstructable.

**Identity is `(underlying_id, market_time, knowledge_horizon, build_context_id)`.** All
four. `MarketState(NIFTY, 11:42, K=11:42)` and `K=11:50` are different, equally valid
states. `BuildContext` is immutable and content-addressable, subsuming builder,
staleness-policy and feature-set versions, and yielding the determinism property:

> same observations + same K + same build context = same MarketState

**Checkpoint selection is exact match on the full tuple.** A checkpoint whose
`knowledge_horizon` is later than requested is **never** substituted — that would be
look-ahead arriving through a cache. An earlier-K checkpoint is equally unusable. Mismatch
means reconstruct, never approximate.

**Not** "latest observation ≤ T per field." Coherence is explicit:

- **Staleness budget per category** (initial: spot 5 s · futures 5 s · option quotes 30 s ·
  OI 60 s · greeks 60 s · depth 10 s), to be validated against measured feed cadence in
  Phase 2. Budgets are configuration carried in `staleness_policy_version`, therefore part
  of `build_context_id` — a recalibration never silently conflates states.
- **Escalation:** in-budget and coverage ≥ 98% → `OK`; non-spot breach or coverage 80–98%
  → `DEGRADED`; spot breach or coverage < 80% → `UNRELIABLE`.
- **Coherence modes:** `SNAPSHOT_ANCHORED` · `STREAM_ONLY` · `SNAPSHOT_STALE` ·
  `RECOVERING`. A REST chain snapshot is a cross-sectional consistency set and wins on
  divergence; WS ticks merge forward from it.
- **After a WS gap:** gap recorded, state marked `RECOVERING`, out-of-band REST recovery.
  Observations are never fabricated or interpolated.

**Three cadences, decoupled:** raw observations (every event, durable, the source of
historical truth) · checkpoints (configurable + `CHAIN_SNAPSHOT`, `SESSION_BOUNDARY`,
`QUALITY_TRANSITION` triggers; prunable) · reconstruction on demand.

**One `build_state()` serves live, reconstruction, replay and backtest.**

---

## 7. Final research leakage model

Every feature declares five window fields:
`lookback_start` · `lookback_end` · `availability_time` · `forward_start` · `forward_end`.

**Availability rule (input readiness, not market time):**
```
available_at = max(lookback_end, latest_input_available_at, computed_at)
               + availability_delay
```
Raw input availability is `ingested_at`; a derived dependency's is its own `available_at`.
Invariants: `available_at >= lookback_end`, `>= every required input's availability`,
`>= computed_at`.

```
15-minute OI migration · window 11:30:00 → 11:45:00 · available 11:45:02
Not consumable at 11:42, even though component observations existed then.
```

**Enforced in the engine, not by discipline.** The strategy context exposes only features
passing `tradable_information_at(decision_time)`; requesting another raises
`FeatureAccessError`. Strategies have no repository access, and forward-window data is in
a namespace they cannot reach.

Bias controls: `knowledge_as_of` default · as-of universe resolution (survivorship) ·
append-only corrections · backfill `ingested_at` at load time · minimum sample enforced ·
quality exclusions reported · comparison count recorded · feature versions pinned per study.

---

## 8. Final trading lifecycle

```
Signal/Strategy/Manual → TradeIntent → RiskEngine → RiskDecision[seq]
   → Order (state machine) → BrokerAdapter → Fill → Position → Portfolio → Attribution
                                   ↕
                            Reconciliation (broker authoritative)
```

```
CREATED → VALIDATING → RISK_CHECK → SUBMITTED
   → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
   → UNKNOWN → PENDING_RECONCILIATION → (resolved)
terminal: REJECTED · CANCELLED · EXPIRED · FAILED
```

Commitments:
- `TradeIntent` is the universal seam; paper and live differ only by account mode.
- Risk decisions append; an order binds to the **exact** approving decision. Each decision
  records the state **it** evaluated (`risk_state_ref`, `inputs_digest`) and an
  `approved_until` window, so a stale approval cannot be submitted later.
- Risk is non-bypassable — import contract + DB constraint + test.
- Stale data is a risk limit.
- A lost acknowledgement yields `UNKNOWN`, never an inferred terminal state, and **never a
  blind resubmission**.
- Internal idempotency keys give local dedup and audit; they do **not** guarantee
  broker-side duplicate prevention.
- Reconciliation is a first-class subsystem and must rebuild local state from broker truth.
- Live execution behind three independent gates, default off, built last.

---

## 9. Final implementation phases

| # | Phase | Gate |
|---|---|---|
| 1 | Foundation | boundaries + clock enforced in CI |
| 2 | Canonical market data | multi-expiry WS+REST ingesting with greeks and quotes; idempotent; bitemporal fixture passes; `SubscriptionPlanner` reports capacity before subscribing |
| 3 | Market state | `MarketState(T, K, BuildContext)` identical from a matching checkpoint or reconstruction; later-K checkpoints never substituted |
| 4 | Analytics | every feature documented, versioned, six tests each; availability derives from input readiness |
| 5 | Signals + alerts | evidence trail resolves by join |
| 6 | Research | study re-runs to an identical content hash |
| 7 | Replay + backtest | **every failing test in `15-TESTING.md` §2 passes**; deterministic cross-session ordering; knowledge-aware checkpoint selection |
| 8 | Paper trading | signal → filled paper position, fully audited |
| 9 | Risk | no path reaches a broker without an approved decision |
| 10 | OMS + reconciliation | wipe local state, reconcile, match broker. Live still off |
| 11 | Portfolio + attribution | P&L decomposed, residual reported |
| 12 | Terminal | twelve screens, none ahead of its backend |

**Sequencing principle.** Full historical intraday option-chain state cannot be
reconstructed for arbitrary past timestamps from the Upstox APIs. However, historical OI
can be obtained for supported dates and may be used for limited backfill and research.
Ingestion is therefore front-loaded: begin collecting at the end of Phase 2 and never stop.

**Rebuild strategy.** Parallel build under `oipulse/`; legacy runs until parity, then is
removed. Legacy data is not migrated — it is user-scoped, front-expiry-only and lacks
greeks and quotes.

---

## 10. Unresolved assumptions

Recorded rather than silently resolved. Each needs confirmation or measurement, and none
blocks Phase 1.

| # | Assumption | Resolve by | Impact if wrong |
|---|---|---|---|
| A-1 | Upstox WS provides a usable per-channel sequence number or event id | Phase 2 soak, recording raw frames | **Handled, not assumed** (`06` §6): identity falls back to content hash with `identity_confidence = WEAK`, sequence-based gap detection is not claimed, and replay ordering falls back to `(observed_at, feed_session_ordinal, id)` — still deterministic |
| A-2 | Upstox WS supplies greeks live, not only via the REST chain | Phase 2 | Greeks would be chain-cadence only; staleness budget for greeks must widen |
| A-3 | Venue timestamps are present and reliable on WS messages | Phase 2 | `observed_at` would fall back to receipt time, weakening the bitemporal guarantee — would need explicit documentation |
| A-4 | Historical OI endpoint granularity (EOD vs intraday) and date range | Phase 2 | Determines how much positioning research predates live collection |
| A-5 | Rate-limit **and subscription/connection** budgets support the intended underlying × expiry breadth (review indicates ~2 connections, ~2,000 LTPC/Greeks, ~1,500 Full — configuration, not constants) | Phase 2, via the governor and `SubscriptionPlanner` | Planner returns `DEGRADED` or `UNSATISFIABLE` at planning time; fewer expiries or reduced data modes, recorded as a quality fact |
| A-6 | Staleness budgets (spot 5 s, options 30 s, OI/greeks 60 s) match real cadence | Phase 3 measurement | Budgets recalibrated; `build_context_id` changes, so old and new states stay distinguishable |
| A-7 | Current NSE/BSE lot sizes (legacy seeds are stale) | Phase 4, against Upstox contract data | Every exposure, GEX and P&L figure scales wrongly |
| A-8 | Upstox exposes an exchange holiday calendar; otherwise a maintained source is needed | Phase 2 | Collection on holidays produces junk rows, as in the legacy system |
| A-9 | Broker order-history endpoints are sufficient for full reconciliation | Phase 10 | Reconciliation would need position-level inference; live trading delayed |
| A-10 | Single `processor` handles all underlyings within checkpoint cadence | Phase 3 load test | Shard earlier than planned |
| A-11 | Postgres partitioning suffices at target volume | Phase 3–6 measurement | Revisit AD-16 against its stated thresholds |
| A-12 | One user for the foreseeable future | Product decision | Multi-tenant isolation moves forward; `/offline-session` stays deleted regardless |

---

## 11. Correction traceability

Every correction from both review passes, and where it is applied.

### Pass 1
| # | Correction | Applied in |
|---|---|---|
| 1 | Bitemporal time model, `market_truth_at` / `knowledge_as_of` | `05` §2–3, §6–7 · `01` §2 · `02` §3 · `00` §2 |
| 2 | Observation identity beyond `(instrument, observed_at, source)` | `01` §4 · `02` §3 · `03` §2 |
| 3 | `provider_prev_oi` vs `our_previous_oi` | `01` §4 · `02` §12 · `04` §6 |
| 4 | MarketState coherence and staleness policy | `04` §3–4 · `20` §6 |
| 5 | Option-chain consistency and reconciliation | `04` §4 · `06` §2, §5 · `01` §4 |
| 6 | Instrument identity vs version vs vendor mapping | `01` §3 · `02` §2 · `19` AD-06 |
| 7 | Provenance and retention guarantees | `02` §8–9 · `05` §8–9 · `00` §7 · `19` AD-09 |
| 8 | Persistence policy tiers | `00` §7 · `02` §9 · `19` AD-08 |
| 9 | Feature/metric registry generalizing FormulaDefinition | `07` §2 · `01` §7 · `12` `/features` |
| 10 | Research leakage model | `09` §2 · `05` §5 · `15` §2.2–2.3 |
| 11 | Risk decision versioning (sequence, not one) | `11` §3 · `02` §7, §11 · `19` AD-13 |
| 12 | Broker/OMS UNKNOWN states | `11` §4–5 · `06` §8–9 · `15` §2.5 · `19` AD-14 |
| 13 | Cross-document consistency | this section · §12 |

### Pass 2
| # | Correction | Applied in |
|---|---|---|
| 1 | `available_at` as a fourth time dimension | `05` §2 · `01` §2 · `07` §3 · `00` §2 · `20` §5 |
| 2 | Event frequency vs state checkpoint frequency | `04` §5 · `03` §5 · `00` §3 · `19` AD-07 |
| 3 | Five-field research window availability | `09` §2 · `07` §2 · `15` §2.3 · `20` §7 |
| 4 | Corrected Upstox historical-data claim | `06` §6 · `18` sequencing · `00` §10 · `20` §9 |
| 5 | Corrected broker idempotency claim | `11` §5 · `06` §9 · `19` AD-14 · `17` §11 |
| 6 | Reconciliation as a first-class subsystem | `11` §6 · `18` Phase 10 · `19` AD-15 · `16` §6 |
| 7 | Final consistency gate | §12 |
| 8 | Implementation gate and freeze document | this document |

## 12. Pass 3 corrections

| # | Correction | Applied in |
|---|---|---|
| B1 | `available_at` from input readiness, not `observed_at` | `07` §3 · `05` §5 · `09` §2 · `20` §7 |
| B2 | Identity includes `knowledge_horizon` + `build_context_id`; `BuildContext` immutable and content-addressable | `04` §1, §5 · `02` §2, §5, §6, §11 · `20` §3, §6 |
| B3 | Aggregate-sequence ordering + transactional inbox; no global exactly-once claim | `03` §4 · `02` §9, §11 · `20` §4 |
| B4 | `market_truth_at` / `knowledge_at` / `tradable_information_at`; `decision_time` not stored; daily-OI granularity; cross-session replay order; API two-axis time | `05` §2–3, §6 · `10` §3–4 · `12` §2 · `02` §3 · `06` §7 |
| B5 | `SubscriptionPlanner`; provider sequence/timestamp verified not assumed | `06` §5–6 · `20` §10 (A-1, A-3, A-5) |
| B6 | Freeze regenerated from corrected documents | this document |
| R-08 | `contradiction_assessment: NONE_OBSERVED \| Evidence[]`; normative transition table | `08` §2–3 |
| R-09 | Event sampling / overlap policy; effective sample reported | `09` §3 |
| R-11 | `RiskDecision` carries `risk_state_ref`, `inputs_digest`, `approved_until` | `11` §3 |
| R-13 | Two-axis time control on research screens; three live gates | `13` §1, §4, §6 |
| R-14 | "API never computes" reworded; reconstruction location decided | `14` §1 |
| R-15 | Ten new tests (§2.8–2.16) | `15` §2, §4 |
| R-16 | Availability-lag, subscription, inbox, reconstruction metrics | `16` §3 |
| R-17 | Fernet described accurately (not AEAD); CSRF defence added | `17` §2, §7 |
| R-18 | Corrections propagated into phases 2, 3, 4, 7, 9 | `18` |
| R-19 | AD-21…AD-27 (review proposed AD-19/20, already taken) | `19` |

Passes 1 and 2 remain applied; their traceability is in §11.

---

## 13. Consistency gate — scope of what was checked

> **Scope statement.** An earlier revision of this document asserted "consistency gate —
> verified" on the strength of string-presence greps. Those cannot detect semantic
> contradiction, and real ones survived. What follows states exactly what was checked and
> by what means, so the reader can judge the coverage rather than take the word.

### Mechanical — automated, whole-set greps

| Check | Result |
|---|---|
| No `UNIQUE (underlying_id, observed_at, builder_version)` anywhere | clean |
| No metric uniqueness lacking `knowledge_horizon` / `build_context_id` | clean |
| No `observed_at of inputs` in any availability formula | clean |
| No bare `builder_version` where `build_context_id` subsumes it | clean — remaining mentions are inside the `BuildContext` definition and the statement that it is subsumed |
| Checkpoint trigger enum identical in `02` and `04` (set comparison) | clean — 5 values each |
| `channel_sequence` never called a total order without session qualification | clean |
| No unqualified "exactly once" claim | clean — the one match is the explicit negation |
| `decision_time` never listed as a stored column | clean |
| `AEAD` absent as a description of Fernet; `CSRF` present in `17` | clean |
| Live-trading gate count identical in `13` and `17` | clean — three in both |
| No ADR number used twice | clean — AD-01…AD-27, no gaps or repeats |
| Old query-mode names (`market_as_of`, `feature_available_as_of`) absent | clean |
| Two-part Upstox historical statement verbatim; no blanket "no history" claim | clean — present in `00`, `06`, `18`, `20`, including the legacy audit docs |
| Cross-references to renumbered `06` sections updated | clean |

### Semantic — reviewed by reading, not by grep

| Check | Means |
|---|---|
| `MarketState` identity tuple stated identically in `01`, `02`, `04`, `10`, `20` | read and compared |
| Query-mode names and predicates consistent across `01`, `05`, `09`, `10`, `12`, `20` | read and compared |
| Availability invariant chain identical in `05`, `07`, `09`, `15`, `20` | read and compared |
| Time model consistent wherever discussed: **four stored** dimensions (`observed_at`, `ingested_at`, `computed_at`, `available_at`) plus `decision_time` as an action parameter | read and compared |
| Every A-n assumption cross-checked against whether any document makes it a hard correctness dependency | read; A-1 was such a case and now has an explicit fallback (`06` §6) |
| Each blocking fix and refinement traceable to a named section | §12 above |

### Required failing tests specified
`15-TESTING.md` §2.1–2.16 — look-ahead; availability before `available_at`; window
completion; order without approved risk decision; order using another intent's approval;
terminal state assumed after lost ack; resubmit from `UNKNOWN`; trading on unreliable
data; backfill contamination; correction rewriting history; market-truth without an
explicit horizon; same-T-different-K identity; later-K checkpoint substitution; earlier-K
substitution; build-context conflation; late-input availability; dependency availability
propagation; out-of-order aggregate application; deferred-not-dropped events; inbox
double-application under crash; replay order across reconnect; ordering without a provider
sequence; daily OI posing as intraday state; quote-dependent feature over backfill.

### Findings from this pass's gate

Two semantic inconsistencies were found by read-and-compare that the mechanical checks did
not detect, and both were corrected before this gate was recorded as passing:

| Finding | Where | Fix |
|---|---|---|
| `00-OVERVIEW.md` still asserted "api — thin, never computes" (§6 diagram) and "`api` never computes" (§6 bullet), contradicting `14-DEPLOYMENT.md` §1, which had been corrected | `00` §6 | reworded to "performs no business logic", with reconstruction called out explicitly |
| This document described the time model as "three stored + `decision_time`", contradicting the four stored dimensions in `05` §2 | `20` §13 | corrected to four stored plus one action parameter |

Both are recorded rather than silently fixed, because they are evidence of what the
mechanical layer cannot catch: a phrase corrected in one document and left standing in
another reads as clean to every grep.

### Not verified here
Numeric budget values (A-5, A-6), lot sizes (A-7), and provider behaviour (A-1, A-2, A-3,
A-4) are **assumptions carried into implementation**, not facts checked by this gate. They
are listed in §10 with their resolution phase and consequence.

---

## 14. Freeze conditions

**FROZEN** as of this revision, after three correction passes. Changes from here require
an explicit unfreeze and a recorded decision in `19-DECISIONS.md`.

Permitted without unfreezing: calibrating the values in §10 (that is what they are for);
adding a feature to the registry that fits the existing model; adding a signal rule;
adding a screen whose backend exists.

Requires unfreezing: changing the time model, observation identity, the instrument
identity/version split, the persistence tiers, the risk-decision model, the order state
machine, or module boundaries.
