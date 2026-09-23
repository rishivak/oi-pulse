# OI Pulse v2 — Domain Model

> **Deliverable C.** The ubiquitous language. Every name here is used consistently across
> code, database, API and UI. Where the legacy system had a collision or ambiguity, the
> resolution is recorded in §14.
>
> **Revision 3** — correction passes 1 and 2 applied.

---

## 1. The four-layer separation

The brief's §7 is the organising principle. These are four distinct kinds of thing and
they are never collapsed:

| Layer | Question | Example | Mutability |
|---|---|---|---|
| **Observation** | What did the venue report? | `oi = 1,200,000` | Immutable, append-only |
| **Derived metric** | What does arithmetic give us? | `oi_change = +180,000` (+17.6%) | Recomputable, versioned |
| **Interpretation** | What do we call that? | `PUT_POSITIONING_INCREASED` | Recomputable, versioned |
| **Signal** | What is developing, with what evidence? | `SUPPORT_STRUCTURE_STRENGTHENING` | Lifecycle-tracked |
| **Trade intent** | What would we do? | defined-risk bullish structure | Lifecycle-tracked |

A metric is never stored as a signal. An interpretation never loses its underlying metric.
A signal always references the evidence records that produced it. This is what makes the
explainability requirement structural rather than cosmetic.

---

## 2. Time

### The four timestamps

| Field | Meaning | Applies to |
|---|---|---|
| `observed_at` | when the fact was true, per the venue | observations, derived records |
| `ingested_at` | when OI Pulse first received and persisted it | observations |
| `computed_at` | when the derived value was calculated | derived records |
| `available_at` | when the derived value became consumable by the strategy layer | derived records |

`available_at` is **not** `computed_at`. A feature over a window ending at 11:45 has an
`available_at` of at least `11:45 + availability_delay`, no matter when its component data
arrived. See `09-RESEARCH.md` §2.

### The three query modes

| Mode | Predicate | Default for |
|---|---|---|
| `market_truth_at(valid_time=T, knowledge_as_of=K)` | `observed_at <= T` | market-truth analysis (explicit opt-in) |
| `knowledge_at(T)` | `observed_at <= T AND ingested_at <= T` | research, replay |
| `tradable_information_at(T)` | above **and** `available_at <= T` | strategy, backtest |

Enforced at the repository layer. There is no unbounded query method for callers to
reach for.

### `MarketSession`
A value object, not hardcoded times: `session_date` · `phase`
(`PRE_OPEN` · `OPEN` · `CLOSED` · `POST_CLOSE` · `HOLIDAY`) · `open_at` · `close_at` ·
`exchange`. Sourced from a stored exchange calendar — the legacy hardcoded holiday list
with "indicative" entries is the failure mode being avoided.

### `Clock`
Injected interface, never the wall clock. `SystemClock` · `ReplayClock` · `FrozenClock`.
No module calls `datetime.now()`.

---

## 3. Instruments — identity, version, vendor mapping

Three separate concepts. Overloading them onto one row is an error the legacy system makes
and an earlier draft of this document repeated.

### `Instrument` — stable identity only
`id` · `instrument_type` · `underlying_ref` · `created_at`

Permanent. Never mutated. This is what a March observation and a September observation
both point at.

### `InstrumentVersion` — historised metadata
`instrument_id` · `valid_from` · `valid_to` · `symbol` · `exchange` · `currency` ·
`lot_size` · `tick_size` · `contract_attributes`

A lot-size revision creates a **new version**. Reconstructing March's exposure resolves
March's version and therefore March's lot size. Versions for one instrument may not
overlap in time.

### `InstrumentVendorMapping` — historised external identity
`instrument_id` · `vendor` · `vendor_key` · `valid_from` · `valid_to`

The Upstox `instrument_key` is an *external* identifier, not our identity. Vendor keys
change format and get reissued; binding the domain to `"NSE_INDEX|Nifty 50"` makes
instrument identity a vendor concern, which it is not.

### Concrete instrument types
| Type | Additional fields |
|---|---|
| `IndexInstrument` | — |
| `EquityInstrument` | `isin`, `sector` |
| `FutureInstrument` | `underlying_id`, `expiry_id`, `contract_month` |
| `OptionInstrument` | `underlying_id`, `expiry_id`, `strike`, `option_type` (CE\|PE) |

There is no "NIFTY special case" anywhere. An index is an instrument; NIFTY is a row.

### `Expiry` — first-class
`id` · `underlying_id` · `expiry_date` · `expiry_type` (`WEEKLY`\|`MONTHLY`\|`QUARTERLY`) ·
`settlement_type` · `is_active`

`days_to_expiry` and `sequence` (0 = front) are **derived at query time relative to the
`as_of`**, never stored. "Front expiry" means something different in March than today;
storing it would be a point-in-time violation.

### `InstrumentUniverse`
The subscription model — system-level, not user-level. The market is collected once.

`id` · `name` · `underlying_ids` · `expiry_selector` · `strike_selector` · `is_active`

- `expiry_selector`: `FRONT` · `FRONT_N(n)` · `ALL_WEEKLY` · `MONTHLY_N(n)` · `EXPLICIT(ids)`
- `strike_selector`: `ATM_RANGE(n)` · `ALL` · `MONEYNESS_BAND(lo, hi)`

Users have *watchlists*, which are views over what is collected.

---

## 4. Market data

### `MarketObservation` — the atom
Immutable, append-only, canonical and **un-owned**. One observation is not duplicated
because two users are viewing NIFTY.

Common fields: `id` · `instrument_id` · `observed_at` · `ingested_at` · `source` ·
`identity` (below) · `supersedes_observation_id`

### Observation identity
A timestamp is not an identity. WS feeds legitimately carry multiple events for one
instrument at the same timestamp resolution. Identity resolves in priority order:

```
1. provider_event_id
2. (feed_session_id, channel, channel_sequence)
3. (instrument_id, observed_at, source, content_hash)
```

`content_hash` is the last resort, never the primary rule. **Two distinct events sharing a
timestamp are two rows.** Persisted alongside: `feed_session_id`, `channel_sequence`,
`provider_event_id`, `content_hash`, `received_seq`.

This supports duplicate delivery (idempotent upsert on resolved identity), out-of-order
delivery (order by `channel_sequence`, not arrival), reconnects (new `feed_session_id`),
gap detection (sequence discontinuity → `QualityIssue`), and replay.

### Corrections
A venue correction is a **new row** with `supersedes_observation_id` pointing at the
prior one — never a mutation. The superseded value remains visible at its original
knowledge time, which is what makes "what did we believe at 11:42?" answerable even after
a 13:00 correction.

### Typed variants

| Variant | Fields |
|---|---|
| `QuoteObservation` | `ltp`, `bid`, `ask`, `bid_qty`, `ask_qty`, `volume`, `oi`, `provider_prev_oi`, `prev_close` |
| `GreeksObservation` | `iv`, `delta`, `gamma`, `theta`, `vega`, `rho` |
| `DepthObservation` | `bids[]`, `asks[]` (price, qty, orders) |
| `OHLCObservation` | `open`, `high`, `low`, `close`, `volume`, `oi`, `interval` |
| `IndexObservation` | `ltp`, `prev_close`, `open`, `high`, `low` |
| `HistoricalOIObservation` | `oi`, `trade_date` — from the Upstox historical OI endpoint; distinct `source` |

Every field Upstox provides is captured. The legacy system parses `delta`, `gamma`,
`theta`, `vega`, `bid`, `ask` and `prev_oi` and discards all of them — unrecoverable once
the moment passes.

### `provider_prev_oi` vs `our_previous_oi`

| Concept | Stored? | Why |
|---|---|---|
| `provider_prev_oi` | **Yes** | A provider-reported previous OI is itself a raw observation — a fact about what the provider asserted. |
| `our_previous_oi` | **Never** | Always reconstructed from canonical history under the active valid/knowledge-time semantics. |

The legacy `call_prev_oi` denormalization is the anti-pattern: it duplicates state, breaks
silently when a bucket is missed, and cannot answer "previous as of when?". A provider
field is not the same thing.

### `OptionChainSnapshot`
A REST-sourced **cross-sectional consistency set**: all legs of one `(underlying, expiry)`
as the venue reported them in a single response. Distinct from the tick stream, which
carries no cross-sectional guarantee. Used as an assembly baseline and for reconciliation
against WS (`04-MARKETSTATE.md` §4).

---

## 5. Data quality

### `QualityIssue`
`type` · `severity` (`INFO`·`WARNING`·`DEGRADED`·`CRITICAL`) · `instrument_id` ·
`window_start` · `window_end` · `detail` · `detected_at`

Types: `STALE_PRICE` · `MISSING_OBSERVATION` · `DUPLICATE_OBSERVATION` · `OUT_OF_ORDER` ·
`IMPOSSIBLE_VALUE` · `NEGATIVE_OI` · `INVALID_STRIKE` · `MISSING_EXPIRY` ·
`DISCONTINUITY` · `WEBSOCKET_GAP` · `RECONNECT_GAP` · `REST_WS_DIVERGENCE` ·
`INCOMPLETE_CHAIN` · `CLOCK_SKEW` · `STALENESS_BUDGET_EXCEEDED`

### `QualityAssessment`
Attached to **every** `MarketState`: `status` (`OK`·`DEGRADED`·`UNRELIABLE`) · `issues[]` ·
`coverage_ratio` · `staleness_p95` · `cross_sectional_coherence`.

Analytics read this and carry it forward, so a signal built on an incomplete chain says so.

---

## 6. Market state

### `MarketState`
An immutable, versioned, deterministically-assembled representation of one underlying's
market picture at one instant — assembled under an explicit **staleness budget**, carrying
its own quality assessment and provenance, and **reconstructable** from the observation
store for any T.

### `MarketStateCheckpoint`
A **materialization artifact**, not the truth. Written at a configurable cadence
(1 s / 3 s / 5 s) and at defined event boundaries. Prunable and rebuildable; deleting all
checkpoints loses no history, only recomputation time. Full treatment in `04`.

---

## 7. Analytics

### `FeatureDefinition` — the registry
Generalizes what an earlier draft called `FormulaDefinition`.

`identifier` · `version` · `definition` · `inputs` · `formula` · `units` ·
`sampling_frequency` · `lookback` · **`availability_delay`** · `normalization` ·
`quality_requirements` · `implementation_ref`

`PUT_OI_MIGRATION@v1` and `@v2` coexist and are independently referenceable. Every
computed value and every research result names the exact version used. The registry is
generated from code annotations and rendered in the UI, so hovering GEX shows the
convention in force.

### `MetricValue`
`id` · `market_state_ref` · `feature_id` · `feature_version` · `scope` · `value` · `unit` ·
`inputs_digest` · `observed_at` · `computed_at` · `available_at` · `quality`

`inputs_digest` hashes the input values: recomputation yielding a different digest for the
same `observed_at` is a bug, and CI checks for it.

### `Interpretation`
`metric_refs[]` · `label` · `convention_version` · `computed_at` · `available_at`

> **Naming resolution.** Legacy has two enums for this — `OISignal` (`long_buildup`) and
> `OIInterpretation` (`LONG_BUILDUP`) — differing only in casing, and calls them "signals".
> In v2 `BuildupClassification` is an *interpretation*; `Signal` is reserved for L6.

### `OIMigration` — first-class
A durable analytical observation of positioning moving between strikes.

`id` · `underlying_id` · `expiry_id` · `option_type` · `origin_strike` ·
`destination_strike` · `direction` · `magnitude` · `first_observed_at` ·
`last_observed_at` · `duration` · `confidence` · `evidence[]` ·
`status` (`FORMING`·`CONFIRMED`·`FADED`) · `available_at`

Migrations have duration and lifecycle — tracked entities, not per-tick calculations,
which is what makes them researchable.

---

## 8. Signals

### `Signal`
`id` · `type` · `underlying_id` · `expiry_id` · `horizon` · `created_at` · `updated_at` ·
`available_at` · `strength` · `status` · `evidence[]` · `contradiction_assessment`
(`NONE_OBSERVED | Evidence[]` — always present; a rule is never forced to manufacture one) ·
`invalidation_condition` · `expires_at` · `provenance`

Lifecycle: `FORMING → ACTIVE → {CONFIRMED | INVALIDATED | EXPIRED | FADED}`.

A signal is a tracked entity with a lifespan, not an instantaneous emission — which is what
makes "what happened after signals like this?" answerable.

### `Evidence`
`signal_id` · `kind` (`SUPPORTING`\|`CONTRADICTING`) · `metric_value_ref` ·
`observation_refs[]` · `statement` · `weight` · `observed_at`

Evidence **references** the metric and observation rows it came from; it is not a rendered
string. The chain `Signal → Evidence → MetricValue → MarketState → Observation` is
walkable in SQL.

`strength` derives from weighted evidence via a declared, versioned function. There is no
field in which to put an unexplained confidence number.

---

## 9. Alerts

`AlertRule` — `id` · `user_id` · `name` · `condition` (composable expression tree) ·
`scope` · `is_enabled` · `cooldown` · `delivery_channels[]`

`AlertOccurrence` — `rule_id` · `triggered_at` · `trigger_refs` · `dedup_key` ·
`delivery_attempts[]`

Persisted, deduplicated, delivered through the outbox: never lost, never double-sent.

---

## 10. Research

`ResearchQuestion` · `EventStudy` · `StudyResult` · `Dataset` (materialized,
point-in-time-correct, content-hashed) · `StrategyDefinition` · `BacktestRun` ·
`BacktestResult`.

Every study records which query mode it ran under and the feature versions it consumed.
Detail in `09-RESEARCH.md`.

---

## 11. Trading

### `TradeIntent` — the universal seam
`id` · `source` (`STRATEGY`\|`MANUAL`\|`SIGNAL`) · `source_ref` · `account_id` · `legs[]` ·
`time_in_force` · `constraints` · `rationale_ref` · `created_at` · `market_state_ref` ·
`client_order_intent_id`

One type, consumed identically by paper and live. `market_state_ref` links every trade to
exactly what the system knew when the decision was made, closing the research loop.

### `RiskDecision` — an immutable sequence
`intent_id` · `sequence_no` · `decision` (`APPROVED`·`REJECTED`·`MODIFIED`) · `reasons[]` ·
`limits_evaluated` · `decided_at`

**Not one decision per intent.** Risk is re-evaluated on modification, retry, changed
market conditions, changed quantity or amendment. Each evaluation appends a new, immutable
decision. An order references the **exact** decision that authorized it
(`authorizing_risk_decision_id`).

### `Order` and the state machine
`id` · `intent_id` · `authorizing_risk_decision_id` · `instrument_id` · `side` · `qty` ·
`order_type` · `state` · `broker_order_id` · `client_order_attempt_id` · `idempotency_key`

```
CREATED → VALIDATING → RISK_CHECK → SUBMITTED
   → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
   → UNKNOWN → PENDING_RECONCILIATION → (any resolved state)
terminal: REJECTED · CANCELLED · EXPIRED · FAILED
```

`UNKNOWN` is entered when a submission's outcome cannot be determined — typically a lost
acknowledgement. The system **never** infers accepted or rejected. Resolution is by
reconciliation against broker order history; **broker state is authoritative**. Retries
must not resubmit while the outcome is unknown.

### Internal idempotency vs broker-side duplicate prevention
Distinct concerns, and conflating them is dangerous:

- **Internal idempotency** — `client_order_intent_id`, `client_order_attempt_id`,
  `idempotency_key`. Ours; reliable; gives local dedup and a clean audit trail.
- **Broker-side duplicate prevention** — **not guaranteed** by any of the above. Upstox may
  or may not reject a duplicate.

Safety therefore comes from the `UNKNOWN` → reconciliation path, not from the key.

### `Reconciliation` — a first-class subsystem
Not an error handler. Reconciles local OMS ↔ broker order state ↔ broker fills ↔ local
portfolio, and can **rebuild local state from broker truth**.

Handles: acknowledgement loss · process restart · WS disconnect · missed broker events ·
partial fills · duplicate callbacks · broker-side cancellations · manual broker-side
changes.

### `Fill`, `Position`, `Portfolio`, `TradingAccount`
`TradingAccount` carries `mode` (`PAPER`\|`LIVE`), so the same code paths serve both.
A user may hold several.

---

## 12. Identity

`User` · `Session` (server-side, revocable — not a signed `user:{id}` cookie) ·
`Permission` (`MARKET_DATA_READ` · `RESEARCH` · `PAPER_TRADE` · `LIVE_TRADE` · `ADMIN`,
separated even with one user) · `BrokerCredential` (encrypted, server-side, never
serialized to any response) · `AuditRecord`.

---

## 13. Events

Domain events are enumerated in `03-EVENT_MODEL.md`. They are persisted through a
transactional outbox and are idempotent on their event identity.

---

## 14. Glossary of resolved ambiguities

| Term | v2 meaning |
|---|---|
| **Observation** | A raw reported fact. Immutable. |
| **Metric value** | A derived number with provenance, feature version, and `available_at`. |
| **Interpretation** | A label applied to metrics. |
| **Signal** | An evidence-backed, lifecycle-tracked inference. |
| **Buildup classification** | An interpretation. *Not* a signal. (Legacy `OISignal`.) |
| **Snapshot** | Only ever an `OptionChainSnapshot` — one REST consistency set. |
| **State** | Only ever a `MarketState`. |
| **Checkpoint** | A persisted materialization of a `MarketState`. Not the truth. |
| **Interval** | Collection cadence. |
| **Timeframe** | Aggregation window. |
| **Bucket** | A session-anchored aggregation boundary — one authority, one rule. |
| **PCR** | `total_put_oi / total_call_oi`. Reported as a number with its convention documented; the system attaches **no** bullish/bearish label. (Legacy contradicted itself across two screens.) |
| **Wall** | A strike with locally extreme OI, by a declared, versioned definition. |
| **Support / resistance** | Levels derived from *positioning*, explicitly not price-technical levels. |
| **Available** | Consumable by the strategy layer — `available_at`, not `computed_at`. |
| **Unknown (order)** | Outcome undetermined. Never a synonym for failed. |
