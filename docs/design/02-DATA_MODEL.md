# OI Pulse v2 — Data Model and ERD

> **Deliverable D.** PostgreSQL schema. Conceptual schemas are namespaced by table prefix
> rather than Postgres `SCHEMA` objects, keeping migrations and cross-domain queries simple
> while preserving the logical separation the brief's §24 asks for.
>
> **Revision 4** — correction passes 1–3 applied. Instrument identity/version/vendor split
> into three tables; observation identity no longer timestamp-based; `UNIQUE (intent_id)`
> on risk decisions removed; retention locks added; checkpoints distinguished from state
> truth; `available_at` added; **checkpoint and metric identity now include
> `knowledge_horizon` and `build_context_id`**; `state_build_contexts` introduced.

---

## 1. Schema map

```
instrument_*   identity · versions · vendor mappings · expiries · universes
obs_*          raw observations — append-only, partitioned, bitemporal
chain_*        REST option-chain consistency sets
dq_*           quality issues and assessments
state_*        MarketState checkpoints (materialization, not truth)
metric_*       derived values with provenance and availability
interp_*       interpretations
signal_*       signals and evidence
alert_*        rules and occurrences
research_*     studies, datasets, backtests
trade_*        intents, orders, fills, reconciliation
risk_*         profiles, limits, decision sequences
portfolio_*    positions, snapshots, attribution
identity_*     users, sessions, credentials, permissions
audit_*        immutable audit trail
sys_*          outbox, jobs, retention locks, partitions
```

---

## 2. Instruments — identity, version, vendor

```
┌──────────────────────────────┐
│ instrument_instruments       │   STABLE IDENTITY ONLY
│ id (PK)                      │   never mutated
│ instrument_type              │
│ underlying_id (FK, nullable) │
│ created_at                   │
└──────────┬───────────────────┘
           │
     ┌─────┴──────────────────────────┬──────────────────────────────┐
     ▼                                ▼                              ▼
┌─────────────────────────┐ ┌──────────────────────────┐ ┌──────────────────────┐
│ instrument_versions     │ │ instrument_vendor_       │ │ instrument_options   │
│ instrument_id FK        │ │   mappings               │ │ instrument_id FK     │
│ valid_from, valid_to    │ │ instrument_id FK         │ │ underlying_id FK     │
│ symbol, exchange        │ │ vendor ('upstox')        │ │ expiry_id FK         │
│ lot_size, tick_size     │ │ vendor_key               │ │ strike               │
│ contract_attributes     │ │ valid_from, valid_to     │ │ option_type CE|PE    │
│ EXCLUDE overlapping     │ │ EXCLUDE overlapping      │ └──────────────────────┘
│   (instrument_id,range) │ │   (instrument_id,vendor, │ ┌──────────────────────┐
└─────────────────────────┘ │    range)                │ │ instrument_futures   │
                            └──────────────────────────┘ │ contract_month       │
                                                         └──────────────────────┘
┌──────────────────────────┐   ┌───────────────────────────┐
│ instrument_expiries      │   │ instrument_universes      │
│ underlying_id FK         │   │ expiry_selector           │
│ expiry_date              │   │ strike_selector           │
│ expiry_type              │   │ (system-level, no user_id)│
│ settlement_type          │   └───────────────────────────┘
└──────────────────────────┘
```

Identity is permanent; metadata and vendor keys are historised. Resolving an instrument
for a past `as_of` selects the version and mapping valid at that time, so March's exposure
uses March's lot size and March's vendor key.

No `sequence` or `is_front_expiry` column: both are time-relative and are derived at query
time from `expiry_date` and the `as_of`.

---

## 3. Observations — bitemporal, identity-keyed

```
┌──────────────────────────────────────────────────────────────────────┐
│ obs_quotes                        PARTITION BY RANGE (observed_at)    │
├──────────────────────────────────────────────────────────────────────┤
│ id                                                                    │
│ instrument_id           FK                                            │
│ observed_at             venue truth time                              │
│ ingested_at             knowledge time                                │
│ source                  WS | REST_CHAIN | REST_QUOTE | REST_HIST_OI   │
│ provider_event_id       nullable — strongest identity                 │
│ feed_session_id         nullable                                      │
│ channel, channel_sequence  nullable                                   │
│ content_hash            fallback identity                             │
│ received_seq            local monotonic arrival counter               │
│ supersedes_observation_id  nullable — corrections                     │
│ ltp, bid, ask, bid_qty, ask_qty, volume, oi,                          │
│ provider_prev_oi, prev_close                                          │
└──────────────────────────────────────────────────────────────────────┘
```

**Identity constraints** — one partial unique index per identity tier, evaluated in
priority order rather than a single composite key:

```sql
CREATE UNIQUE INDEX uq_obs_quotes_provider_event
  ON obs_quotes (provider_event_id)
  WHERE provider_event_id IS NOT NULL;

CREATE UNIQUE INDEX uq_obs_quotes_feed_seq
  ON obs_quotes (feed_session_id, channel, channel_sequence)
  WHERE feed_session_id IS NOT NULL AND channel_sequence IS NOT NULL;

CREATE UNIQUE INDEX uq_obs_quotes_content
  ON obs_quotes (instrument_id, observed_at, source, content_hash)
  WHERE provider_event_id IS NULL AND feed_session_id IS NULL;
```

**`(instrument_id, observed_at, source)` is deliberately NOT unique.** A WS feed may
legitimately deliver several distinct events for one instrument at the same timestamp
resolution; collapsing them would destroy real information.

Sibling tables with identical identity columns and partitioning: `obs_greeks`
(`iv, delta, gamma, theta, vega, rho`), `obs_depth` (jsonb), `obs_ohlc`, `obs_index`,
`obs_historical_oi` (`oi`, `observation_date`, `valid_from`, `valid_to`,
kind `HISTORICAL_DAILY_OI`, source `REST_HIST_OI` — see below).

### Corrections
Never an `UPDATE`. A correction is a new row with `supersedes_observation_id` set. Both
rows persist, each with its own `ingested_at`, so a query at the original knowledge time
still returns the original value.

### Backfilled historical OI — date-granular, not an instant

The Upstox OI endpoint returns OI across strikes for an underlying, expiry and **date**.
Storing that as an observation at a precise intraday timestamp would misrepresent it, so
`obs_historical_oi` carries an explicit granularity:

```
observation_kind   HISTORICAL_DAILY_OI
observation_date   date      the trade date the figure describes
valid_from         timestamptz  session open of that date
valid_to           timestamptz  session close of that date
observed_at        timestamptz  = valid_to, for ordering only — NOT an instant claim
ingested_at        timestamptz  when the backfill ran
source             REST_HIST_OI
```

Two consequences, both automatic:

1. **Invisible to earlier knowledge queries.** `ingested_at` is the backfill run time, so
   `knowledge_at(T)` for any T before the backfill correctly returns nothing — it falls out
   of the bitemporal model with no special handling.
2. **Cannot masquerade as intraday state.** A query for an instant inside the day at tick
   resolution does not receive a daily aggregate; the `observation_kind` and
   `valid_from`/`valid_to` interval make the granularity explicit to every consumer. See
   `05-DATA_LIFECYCLE_PIT.md` §6 and `06-UPSTOX_INTEGRATION.md` §7.

---

## 4. Chain snapshots and quality

```
┌────────────────────────────────┐      ┌──────────────────────────────┐
│ chain_snapshots                │      │ dq_assessments               │
│ id, underlying_id, expiry_id   │      │ subject_kind, subject_id     │
│ observed_at, ingested_at       │      │ status OK|DEGRADED|UNRELIABLE│
│ request_id, leg_count          │      │ coverage_ratio               │
│ is_complete                    │      │ staleness_p95                │
│ CROSS-SECTIONAL CONSISTENCY SET│      │ cross_sectional_coherence    │
└───────────┬────────────────────┘      └──────────┬───────────────────┘
            │                                      ▼
            │                          ┌──────────────────────────────┐
            └─────────────────────────►│ dq_issues                    │
                                       │ type, severity, window, detail│
                                       └──────────────────────────────┘
```

---

## 5. State checkpoints — materialization, not truth

```
┌──────────────────────────────────────────────────────────────────┐
│ state_build_contexts              immutable, content-addressable  │
│ id  ◄── deterministic id derived from the configuration content   │
│ builder_version                                                   │
│ staleness_policy_version                                          │
│ feature_set_version                                               │
│ configuration_digest                                              │
│ created_at                                                        │
│ UNIQUE (configuration_digest)                                     │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────┐
│ state_checkpoints                 PARTITION BY RANGE (observed_at)│
│ id, underlying_id FK                                              │
│ observed_at              market time                              │
│ knowledge_horizon        ◄── part of identity, NOT metadata       │
│ build_context_id FK      ◄── subsumes builder_version             │
│ built_at                                                          │
│ session_phase, spot, futures_ref                                  │
│ quality_status, coherence_mode                                    │
│ trigger  (CADENCE | CHAIN_SNAPSHOT | SESSION_BOUNDARY |           │
│           QUALITY_TRANSITION | MANUAL)                            │
│ UNIQUE (underlying_id, observed_at, knowledge_horizon,            │
│         build_context_id)                                         │
└──────────────┬───────────────────────────────────────────────────┘
               ├──────────────────────┐
               ▼                      ▼
┌──────────────────────────┐ ┌────────────────────────────┐
│ state_checkpoint_legs    │ │ state_checkpoint_expiries  │
│ per contract             │ │ per-expiry aggregates      │
└──────────────────────────┘ └────────────────────────────┘
```

These are **prunable**. Deleting every checkpoint loses no history — only recomputation
time — because a `MarketState` is reconstructable from `obs_*`. The exception is
retention-locked rows (§8).

`knowledge_horizon` and `build_context_id` are **identity, not metadata**
(`04-MARKETSTATE.md` §1). `MarketState(NIFTY, 11:42, K=11:42)` and
`MarketState(NIFTY, 11:42, K=11:50)` are different, equally valid states; a key omitting
`K` would collapse them. Checkpoint lookup is **exact match on the full tuple** — a
checkpoint whose `knowledge_horizon` is later than requested must never be substituted,
because it may incorporate observations that had not arrived by the requested `K`.

The same reasoning applies to `metric_values` in §6: two metrics computed for the same
`observed_at` under different knowledge horizons are legitimately different values.

---

## 6. Metrics, interpretations, signals

```
┌──────────────────────────────────────────────────────────────────┐
│ metric_values                      PARTITION BY RANGE (observed_at)│
│ id, state_checkpoint_id FK (nullable — may be computed ad hoc)    │
│ feature_id, feature_version        ◄── exact registry version      │
│ scope_kind, scope_ref              underlying|expiry|strike|contract│
│ value, unit                                                        │
│ inputs_digest                      hash of input values            │
│ knowledge_horizon, build_context_id FK   ◄── part of identity      │
│ observed_at, computed_at, available_at   ◄── four-time model       │
│ quality_status                                                     │
│ UNIQUE (feature_id, feature_version, scope_kind, scope_ref,        │
│         observed_at, knowledge_horizon, build_context_id)          │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
┌──────────────────────────┐   ┌────────────────────────────────────┐
│ interp_labels            │   │ metric_oi_migrations               │
│ metric_refs[], label     │   │ origin_strike, destination_strike  │
│ convention_version       │   │ first_observed_at, last_observed_at│
│ available_at             │   │ status FORMING|CONFIRMED|FADED     │
└──────────┬───────────────┘   └────────────┬───────────────────────┘
           │                                ▼
           ▼
┌──────────────────────────────────────────────────────────────────┐
│ signal_signals                                                    │
│ id, type, underlying_id, expiry_id, horizon                       │
│ created_at, updated_at, available_at, expires_at                  │
│ strength, status, invalidation_condition                          │
│ contradiction_assessment  NONE_OBSERVED | (see signal_evidence)   │
│ state_checkpoint_id FK, provenance jsonb  ◄── embedded descriptor  │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────┐
│ signal_evidence                                                   │
│ signal_id FK, kind SUPPORTING|CONTRADICTING                       │
│   (signal_signals.contradiction_assessment = NONE_OBSERVED        │
│    when assessed with no material contradiction found)            │
│ metric_value_id FK, observation_refs jsonb                        │
│ statement, weight, observed_at                                    │
└──────────────────────────────────────────────────────────────────┘
```

**The audit chain is a foreign-key path**: `signal_evidence.metric_value_id` →
`metric_values.state_checkpoint_id` → `state_checkpoints.observed_at` → `obs_*`.
Explainability is a join, not a log search.

`provenance` is additionally embedded inline on every decision artifact — feature version,
implementation version, inputs digest, state version, observation refs — so the artifact
remains self-describing even in the limit case.

---

## 7. Trading

```
┌───────────────────┐
│ identity_users    │
└─────────┬─────────┘
          ▼
┌──────────────────────────┐      ┌──────────────────────┐
│ trade_accounts           │─────►│ risk_profiles        │
│ mode PAPER | LIVE        │      │ limits jsonb         │
│ broker_credential_id FK  │      └──────────────────────┘
└─────────┬────────────────┘
          ▼
┌─────────────────────────────────────────────────────────┐
│ trade_intents                                           │
│ id, account_id FK, source, source_ref                   │
│ client_order_intent_id UNIQUE                           │
│ state_checkpoint_id FK   ◄── what the system knew        │
│ legs jsonb, constraints jsonb, rationale_ref            │
└─────────┬───────────────────────────────────────────────┘
          ▼
┌─────────────────────────────────────────────────────────┐
│ risk_decisions              IMMUTABLE SEQUENCE           │
│ intent_id FK, sequence_no                               │
│ decision APPROVED|REJECTED|MODIFIED                     │
│ reasons jsonb, limits_evaluated jsonb, decided_at       │
│ PRIMARY KEY (intent_id, sequence_no)                    │
│ NO unique-per-intent constraint — re-evaluation is normal│
└─────────┬───────────────────────────────────────────────┘
          ▼
┌─────────────────────────────────────────────────────────┐
│ trade_orders                                            │
│ id, intent_id FK                                        │
│ authorizing_risk_decision_id  ◄── the EXACT approval     │
│   FK (intent_id, sequence_no) → risk_decisions          │
│ instrument_id FK, side, qty, order_type                 │
│ state, broker_order_id                                  │
│ client_order_attempt_id UNIQUE, idempotency_key         │
└─────────┬───────────────────────────────────────────────┘
          ├──────────────┬───────────────────┐
          ▼              ▼                   ▼
┌──────────────┐ ┌──────────────────┐ ┌────────────────────────┐
│ trade_order_ │ │ trade_fills      │ │ trade_reconciliations  │
│   events     │ │ broker_fill_id U │ │ run_id, scope          │
│ state machine│ │ qty, price, fees │ │ discrepancies jsonb    │
│ transitions  │ │                  │ │ resolution, broker_snap│
└──────────────┘ └────────┬─────────┘ └────────────────────────┘
                          ▼
              ┌────────────────────────┐
              │ portfolio_positions    │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────────────┐
              │ portfolio_snapshots            │
              │ exposure, greeks, margin, pnl  │
              └────────────┬───────────────────┘
                           ▼
              ┌────────────────────────────────┐
              │ portfolio_attribution          │
              │ bucket, component, amount      │
              └────────────────────────────────┘
```

`trade_orders.state` includes `UNKNOWN` and `PENDING_RECONCILIATION`. A lost
acknowledgement is recorded as fact, never resolved by inference.
`trade_reconciliations` stores the broker snapshot used and every discrepancy found, so
reconciliation is itself auditable.

---

## 8. Retention locks

```sql
CREATE TABLE sys_retention_locks (
  subject_kind  text NOT NULL,   -- 'state_checkpoint' | 'metric_value' | 'observation'
  subject_id    bigint NOT NULL,
  locked_by_kind text NOT NULL,  -- 'signal' | 'trade_intent' | 'research_result' | ...
  locked_by_id  bigint NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (subject_kind, subject_id, locked_by_kind, locked_by_id)
);
```

Any checkpoint, metric or observation referenced by a decision artifact is pinned when
that artifact is created. Pruning jobs must join against this table. Combined with the
embedded provenance descriptor, pruning cannot silently destroy the audit chain.

---

## 9. Partitioning and retention

| Table | Strategy |
|---|---|
| `obs_quotes`, `obs_greeks`, `obs_depth` | RANGE by `observed_at`, **daily**, created a month ahead by a job |
| `obs_ohlc`, `obs_index`, `obs_historical_oi` | RANGE by `observed_at`, monthly |
| `state_checkpoints`, `state_checkpoint_legs` | RANGE by `observed_at`, monthly |
| `metric_values` | RANGE by `observed_at`, monthly |
| `sys_outbox` | published rows pruned after 7 days |
| `sys_event_inbox` | `(subscriber, event_id)` PK; pruned with the corresponding outbox rows |
| Everything else | unpartitioned |

Retention by persistence tier (`00-OVERVIEW.md` §7):

| Tier | Policy |
|---|---|
| Immutable source (`obs_*`, `chain_*`) | forever. `obs_depth` is the exception: 90 days, high volume, low research value |
| Decision artifacts (`signal_*`, `trade_*`, `risk_*`, `research_*`) | forever, immutable |
| Recomputable (`state_*`, `metric_*`) | prunable by value/cost, **unless retention-locked** |
| Transient | never persisted |

---

## 10. Indexing

```sql
-- point-in-time: latest observation per instrument under a knowledge bound
CREATE INDEX ON obs_quotes (instrument_id, observed_at DESC, ingested_at);
CREATE INDEX ON obs_quotes USING BRIN (observed_at);
CREATE INDEX ON obs_quotes (ingested_at);          -- knowledge_as_of scans

-- state reconstruction and checkpoint lookup (exact-match on the identity tuple)
CREATE INDEX ON state_checkpoints
  (underlying_id, observed_at DESC, knowledge_horizon DESC, build_context_id);

-- metric retrieval and research
CREATE INDEX ON metric_values (state_checkpoint_id, feature_id);
CREATE INDEX ON metric_values
  (feature_id, feature_version, scope_ref, observed_at, knowledge_horizon);
CREATE INDEX ON metric_values (available_at);      -- tradable_information_at

-- signals
CREATE INDEX ON signal_signals (underlying_id, status, created_at DESC);
CREATE INDEX ON signal_signals (type, created_at DESC);

-- outbox dispatcher: claim an aggregate, deliver in sequence
CREATE INDEX ON sys_outbox (aggregate_type, aggregate_id, aggregate_sequence)
  WHERE status = 'pending';
CREATE UNIQUE INDEX ON sys_outbox (aggregate_type, aggregate_id, aggregate_sequence);
CREATE INDEX ON sys_outbox (status, created_at) WHERE status = 'pending';

-- reconciliation
CREATE UNIQUE INDEX ON trade_orders (broker_order_id) WHERE broker_order_id IS NOT NULL;
CREATE INDEX ON trade_orders (state) WHERE state IN ('UNKNOWN','PENDING_RECONCILIATION');
```

BRIN on `observed_at` is deliberate: within a daily partition rows correlate almost
perfectly with insertion order, giving most of a B-tree's benefit at a fraction of the size.

---

## 11. Invariants enforced in the database

| Invariant | Mechanism |
|---|---|
| Observations are never overwritten | No UPDATE/DELETE grant on `obs_*`; trigger raises |
| Distinct events are not collapsed | Tiered partial unique indexes (§3), **not** a timestamp key |
| Corrections preserve history | `supersedes_observation_id`; no mutation |
| `ingested_at >= observed_at` is not assumed | No constraint — clock skew is real and is *detected* as a `dq_issue` |
| OI is never negative | `CHECK (oi >= 0)` |
| Strike is positive | `CHECK (strike > 0)` |
| Instrument versions never overlap | `EXCLUDE USING gist (instrument_id WITH =, tstzrange(valid_from, valid_to) WITH &&)` |
| Vendor mappings never overlap | same, keyed additionally on `vendor` |
| Checkpoint identity includes knowledge horizon and build context | `UNIQUE (underlying_id, observed_at, knowledge_horizon, build_context_id)` |
| Metric identity likewise | `UNIQUE (feature_id, feature_version, scope_kind, scope_ref, observed_at, knowledge_horizon, build_context_id)` |
| A build context is immutable and content-addressable | `UNIQUE (configuration_digest)`; no UPDATE grant on `state_build_contexts` |
| Risk decisions are an append-only sequence | `PRIMARY KEY (intent_id, sequence_no)`; no UPDATE grant |
| **No order without an approved risk decision from its own intent** | Composite FK to `risk_decisions(intent_id, sequence_no)` + trigger asserting that decision's `decision = 'APPROVED'` and `intent_id` matches the order's |
| Fills cannot exceed order quantity | Trigger on `SUM(fills.qty) <= orders.qty` |
| A fill is never double-counted | `UNIQUE (broker_fill_id)` |
| Audit chain survives pruning | `sys_retention_locks` joined by every pruning job |
| An event is applied at most once per subscriber | `sys_event_inbox` PK `(subscriber, event_id)`, inserted in the **same transaction** as the business mutation |
| Aggregate events are totally ordered | `UNIQUE (aggregate_type, aggregate_id, aggregate_sequence)` on `sys_outbox` |

The order/risk constraint is the database-level expression of the brief's §20 — *a
strategy must never bypass the risk engine*. It is not possible to insert an order row
that skipped the gate, or one authorized by a different intent's approval.

---

## 12. What is deliberately absent

- **No `user_id` on any `obs_*`, `chain_*`, `state_*`, `metric_*`, `interp_*` or
  `signal_*` table.** Market data and its derivatives are canonical. The legacy
  `oi_snapshots.user_id` is the single modelling error most responsible for that system's
  limits.
- **No `is_front_expiry` / `sequence`** — time-relative, derived at query time.
- **No denormalized `our_previous_*` columns** — reconstructed from history.
  `provider_prev_oi` is retained because it is a provider observation, not our derivation.
- **No `UNIQUE (intent_id)` on risk decisions** — re-evaluation is normal and expected.
- **No nullable "computed later" columns on observations** — derived values live in
  `metric_values`, never backfilled onto raw rows.
