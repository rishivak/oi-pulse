# OI Pulse v2 — MarketState Model

> **Deliverable F.** The keystone abstraction. Defines what a MarketState is, how it is
> assembled coherently, how often it is checkpointed, and how it is reconstructed for an
> arbitrary past timestamp.

---

## 1. Definition

```
MarketState(underlying, market_time T, knowledge_horizon K, build_context B)
```

An immutable, deterministically-assembled representation of one underlying's complete
market picture at time `T`, using only observations with `observed_at <= T` **and**
`ingested_at <= K`, assembled under an explicit staleness budget, carrying its own quality
assessment, coherence descriptor and provenance.

**Critically, a MarketState is not "the latest observation ≤ T for every field".** That
definition silently produces a precise-looking object whose components may be seconds or
minutes apart. Coherence is a first-class property, not an accident of query order.

### Identity

```
(underlying_id, market_time, knowledge_horizon, build_context_id)
```

(`market_time` is the concept; its stored column is `observed_at` — `05` §2.)

All four are part of identity. `K` in particular: these are **different, equally valid
states**, because late data may have arrived between them —

```
MarketState(NIFTY, 11:42, K=11:42)    what we knew at 11:42
MarketState(NIFTY, 11:42, K=11:50)    what we now know about 11:42
```

A key omitting `K` would collapse them. In live assembly `K` tracks the ingestion frontier;
in research and replay it is pinned (`05-DATA_LIFECYCLE_PIT.md` §7). `decision_time` — when
a strategy acts — is a **consumer/action parameter, not part of state identity and not a
stored field** (`05` §2).

### `BuildContext` — immutable and content-addressable

```
BuildContext
├── id                        deterministic identifier of the assembly configuration
├── builder_version           assembly logic
├── staleness_policy_version  the budgets in §3
├── feature_set_version       which surfaces are materialized
└── configuration_digest      hash of all other assembly-affecting configuration
```

`build_context_id` **subsumes** those versions; nothing carries `builder_version` separately
alongside it. Because the id is derived from the configuration's content, it is stable and
comparable across processes and deployments.

This yields the determinism property the whole architecture rests on:

> **same observations + same K + same build context = same MarketState**

which is exactly what the replay determinism test asserts (`15-TESTING.md` §7).

---

## 2. Composition

```
MarketState
├── identity        underlying_id · market_time · knowledge_horizon · build_context_id
├── session         phase · session_date · open_at · close_at
├── spot            ltp · change · open/high/low · observed_at · age
├── futures[]       per contract: ltp · oi · volume · basis · observed_at · age
├── expiries[]      per expiry:
│   ├── legs[]        per contract: ltp · bid · ask · volume · oi ·
│   │                 provider_prev_oi · iv · delta · gamma · theta · vega · age
│   ├── surfaces      oi_by_strike · iv_by_strike · gamma_by_strike
│   └── aggregates    total_call_oi · total_put_oi · pcr · atm_strike
├── coherence       mode · max_component_age · chain_snapshot_ref · ws_merge_count
├── quality         status · issues[] · coverage_ratio · staleness_p95
└── provenance      build_context_id · observation_refs · assembled_at
```

Every leaf carries its own `observed_at` and derived `age = T - observed_at`. A consumer
can always ask how fresh any individual number is — there is no flattening that hides it.

---

## 3. Coherence policy — the staleness budget

Each data category has a maximum acceptable age. Exceeding it degrades the state rather
than silently producing a stale-but-precise-looking value.

### Starting budgets

> These are **initial values to be validated against observed feed cadence during Phase 2**,
> not fixed constants. They are configuration, carried in `staleness_policy_version` and
> therefore part of `build_context_id` (§1) — changing a budget yields a new build context,
> so states built under different budgets never collide.

| Category | Max age | On breach | Fallback |
|---|---|---|---|
| Spot | 5 s | `UNRELIABLE` | none — spot is required |
| Futures | 5 s | `DEGRADED` | mark contract stale, retain last value |
| Option quotes (LTP/bid/ask) | 30 s | `DEGRADED` | retain last, flag leg |
| Option OI | 60 s | `DEGRADED` | retain last, flag leg |
| Greeks / IV | 60 s | `DEGRADED` | retain last, flag leg; or recompute from spot + IV if policy allows |
| Depth | 10 s | `WARNING` | drop depth from the state rather than serve stale depth |
| Index OHLC | 60 s | `WARNING` | retain last |

OI and greeks carry looser budgets than spot because Upstox updates them less frequently —
the budget must reflect the feed's real cadence, not an aspiration.

### Escalation rules

| Condition | Resulting status |
|---|---|
| All categories within budget, chain coverage ≥ 98% | `OK` |
| Any non-spot category over budget, or coverage 80–98% | `DEGRADED` |
| Spot over budget, or coverage < 80%, or no chain data for a subscribed expiry | `UNRELIABLE` |

An `UNRELIABLE` state is still built and stored — suppressing it would hide the outage.
But signal evaluation is skipped, and any metric computed from it inherits
`quality_status = UNRELIABLE` and is excluded from research datasets by default.

---

## 4. Assembly under two consistency regimes

REST chain responses and WS ticks have genuinely different consistency properties, and the
state must not paper over the difference.

| Source | Guarantee |
|---|---|
| `OptionChainSnapshot` (REST) | **cross-sectional** — all legs as the venue reported them in one response, mutually consistent |
| WS tick stream | **per-instrument only** — no guarantee that two legs are from the same instant |

### Coherence modes

Every state records which regime produced it:

| Mode | Meaning |
|---|---|
| `SNAPSHOT_ANCHORED` | built on a recent chain snapshot, with WS ticks merged forward |
| `STREAM_ONLY` | built purely from WS ticks; no cross-sectional guarantee |
| `SNAPSHOT_STALE` | anchored on a snapshot now older than the anchor budget |
| `RECOVERING` | a gap was detected; REST recovery is in flight |

A consumer can therefore distinguish "these legs were mutually consistent" from "these
legs are each individually fresh but were never observed together". An apparently precise
state can never hide cross-sectional inconsistency.

### Assembly algorithm

```
build_state(underlying, T, K, B):
  1. resolve instrument universe as-of T
     → instrument versions and vendor mappings valid at T
  2. session = calendar.phase_at(T)
  3. anchor = latest chain_snapshot where observed_at <= T and ingested_at <= K
              and (T - anchor.observed_at) <= ANCHOR_MAX_AGE
  4. if anchor exists:
         legs = anchor.legs                          # cross-sectionally consistent
         merge forward: for each leg, latest WS observation in (anchor.observed_at, T]
         mode = SNAPSHOT_ANCHORED
     else:
         legs = for each instrument, latest observation <= T (and <= K)
         mode = STREAM_ONLY
  5. spot, futures = latest observations <= T (and <= K)
  6. compute age per component; apply staleness budget
  7. detect divergence (§5); raise issues
  8. quality = assess(coverage, ages, issues, mode)
  9. build surfaces and aggregates from legs
 10. return immutable MarketState with provenance (observation ids, build_context_id)
```

`ANCHOR_MAX_AGE` defaults to 2× the chain poll interval. Beyond it the anchor is no longer
trustworthy as a cross-sectional reference and the mode becomes `SNAPSHOT_STALE`.

### Divergence detection
When a chain snapshot arrives, each leg is compared against the WS-derived value that was
current for it. Material divergence beyond a per-field tolerance raises
`REST_WS_DIVERGENCE`, and **the snapshot wins** — it is the venue's own consistent view.
Persistent divergence indicates a WS handling bug and escalates operationally.

### After a WebSocket gap
1. `WEBSOCKET_GAP` raised with the missing sequence range.
2. Affected states are marked `RECOVERING`.
3. A REST chain fetch is triggered immediately (out of band from the normal poll).
4. On arrival it becomes the new anchor; the gap window is recorded permanently in
   `dq_issues` so research can exclude or flag it.
5. Observations inside the gap are **not** fabricated. The gap is recorded as absent data.

---

## 5. Checkpointing — event frequency is not state frequency

A `MarketState` is **not** persisted per WebSocket message.

| Concept | Cadence | Storage |
|---|---|---|
| Raw observations | every event | durable, append-only — **the source of historical truth** |
| Live state | continuously updated | in-memory in `processor`, mirrored to Redis for the API |
| **Checkpoints** | configurable + boundaries | `state_checkpoints` — a materialization artifact |
| Reconstruction | on demand for any T | computed from observations |

### Checkpoint triggers

| Trigger | When |
|---|---|
| `CADENCE` | every N seconds (default 5 s; 1 s/3 s configurable per underlying) |
| `CHAIN_SNAPSHOT` | whenever a REST chain snapshot is stored — always checkpoint an anchored state |
| `SESSION_BOUNDARY` | pre-open, open, close, post-close |
| `QUALITY_TRANSITION` | whenever quality status changes — so degradation windows are always captured |
| `MANUAL` | operator or backfill job |

Checkpointing on quality transitions matters: it guarantees the record shows exactly when
the data went bad, even if that fell between cadence ticks.

### Deduplication
`UNIQUE (underlying_id, observed_at, knowledge_horizon, build_context_id)` — the full
identity tuple from §1. A checkpoint whose content is byte-identical to its predecessor
(same observation refs, same quality, same context) is skipped entirely; during a quiet
period this collapses redundant rows without losing information, since reconstruction
would produce the same state anyway.

### Recovery
On `processor` restart, live state is rebuilt by loading the most recent checkpoint and
replaying observations forward from its `observed_at`. If no checkpoint exists, it rebuilds
from the last chain snapshot, and failing that from the session open.

### Reconstruction when no checkpoint exists
Full reconstruction runs the same `build_state` function against the observation store:

```
GET /market/state?underlying=NIFTY&market_time=...&knowledge_time=K
  1. look for a checkpoint matching the FULL identity tuple
     (underlying, T, K, current build_context_id)
  2. if absent → build_state(underlying, T, K, B) from observations
  3. optionally persist the result as a MANUAL checkpoint (cache)
```

### Checkpoint selection rule

> **A checkpoint whose `knowledge_horizon` is later than the requested `K` must never be
> used.** It may incorporate observations that had not yet arrived at `K`.

An *earlier*-K checkpoint is likewise not a substitute — it is a different state, missing
data that had arrived by `K`. Selection is therefore exact-match on the identity tuple, not
nearest-match. A mismatch means reconstruct, never approximate. Replay depends on this
(`10-REPLAY.md` §4).

**The same function serves live assembly, reconstruction, replay and backtest.** There is
no separate "historical" code path that could drift from the live one — that divergence is
a classic source of backtest/live mismatch, and the single-implementation rule removes it.

### Build-context versioning
`build_context_id` is stored on every checkpoint and subsumes `builder_version`,
`staleness_policy_version` and `feature_set_version` (§1). Changing assembly logic, a
staleness budget, or the materialized feature set produces a **new context id**, so states
built under different configurations are distinguishable rather than silently conflated.
Old checkpoints remain valid records of what the system built at the time; new
reconstructions use the current context; both coexist. Research records the context id its
dataset used.

---

## 6. Derived surfaces

Computed as part of assembly, from the legs:

| Surface | Content |
|---|---|
| `oi_by_strike` | call/put OI per strike, per expiry |
| `iv_by_strike` | call/put IV per strike — the input to skew |
| `gamma_by_strike` | per-strike gamma, the input to GEX |
| `net_oi_change` | vs. a reference point resolved by point-in-time lookup, never a stored `prev_*` |

Surfaces are **part of the state**, not analytics. The distinction: a surface is a
reorganisation of observed values with no formula applied; anything requiring a formula is
a `MetricValue` produced by L4 with a `feature_version` and an `available_at`.

---

## 7. What MarketState is not

- **Not a cache of the latest tick.** Redis holds that; it has no coherence guarantee, no
  quality assessment and no provenance.
- **Not user-scoped.** One state per `(underlying, market_time, knowledge_horizon,
  build_context_id)`, shared.
- **Not the source of historical truth.** Observations are. Checkpoints are an
  optimization and may be pruned (subject to retention locks in `02-DATA_MODEL.md` §8).
- **Not analytics.** It carries observed values and their reorganisations, not derived
  metrics.
- **Not guaranteed complete.** A state with `UNRELIABLE` quality is a valid, useful record
  that the market data was bad at that moment. Hiding it would be the error.
