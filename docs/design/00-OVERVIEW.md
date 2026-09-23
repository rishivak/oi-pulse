# OI Pulse v2 — System Architecture Overview

> **Deliverables A (system architecture) and B (architecture diagram).**
> First-principles redesign. The existing application is reference material for domain
> concepts, Upstox integration details and lessons learned — see the Phase 0 audit in
> `docs/ARCHITECTURE.md`, `docs/DATA_FLOW.md`, `docs/DOMAIN_MODEL.md`,
> `docs/API_INVENTORY.md`, `docs/DATABASE_INVENTORY.md`, `docs/TECHNICAL_DEBT.md`.
> Its architecture is **not** carried forward.
>
> **Revision 3** — incorporates correction passes 1 and 2. Architecture freezes at
> `20-ARCHITECTURE_FREEZE.md`.

---

## Document set

| # | Document | Brief ref |
|---|---|---|
| 00 | Overview + architecture diagram (this) | A, B |
| 01 | Domain model | C |
| 02 | Data model + ERD | D |
| 03 | Event model | E |
| 04 | MarketState model + checkpointing | F |
| 05 | Data lifecycle + bitemporal point-in-time model | G, H |
| 06 | Upstox integration architecture | I |
| 07 | Analytics architecture + feature registry | J |
| 08 | Signal architecture | K |
| 09 | Research architecture + leakage model | L |
| 10 | Replay architecture | M |
| 11 | Paper trading, risk, execution, reconciliation, portfolio | N, O, P, Q |
| 12 | API specification | R |
| 13 | Frontend information architecture | S |
| 14 | Deployment architecture | T |
| 15 | Testing strategy | U |
| 16 | Observability strategy | V |
| 17 | Security model | W |
| 18 | Implementation roadmap | X |
| 19 | Architectural decisions and trade-offs | Y |
| 20 | **Architecture freeze** | gate |

---

## 1. The product, stated precisely

The product is **not** an options dashboard. It is a system whose primary asset is a
**trustworthy, historically reconstructable representation of the Indian derivatives
market**, and whose secondary assets are the analytics, signals, research results and
trading records derived from it.

### The governing question

Not *"what was the market doing?"* but:

> **What exactly did OI Pulse know at that moment, what could it legitimately infer, and
> what decision was made from that information?**

### The quality bar

For any historical decision, the system must answer this chain deterministically,
auditably and point-in-time correctly:

1. What was true?
2. What did OI Pulse know?
3. When did OI Pulse become *able to use* that information?
4. What did the model calculate?
5. What evidence existed?
6. What signal existed?
7. What trade intent was created?
8. What risk decision authorized or rejected it?
9. What order actually reached the broker?
10. What happened after execution?
11. What was the resulting portfolio and P&L?

Question 3 is the one most systems get wrong, and it is why the time model below has four
dimensions rather than one.

---

## 2. The time model — four dimensions

| Field | Meaning |
|---|---|
| `observed_at` | when the market fact was true, per the venue |
| `ingested_at` | when OI Pulse first received and persisted the fact |
| `computed_at` | when a derived value was calculated |
| `available_at` | when a derived value became **available to the consumer/strategy layer** |

Three query modes, all first-class in the repository layer — never left to caller
discipline:

| Mode | Question |
|---|---|
| `market_truth_at(valid_time=T, knowledge_as_of=K)` | What was true in the market at T? |
| `knowledge_at(T)` | What information did OI Pulse hold at T? |
| `tradable_information_at(T)` | What could a strategy legitimately consume at T? |

**Research and backtesting default to `knowledge_as_of`**, and the backtest engine further
restricts to `tradable_information_at`. Market-truth analysis requires an explicit opt-in
recorded on the study.

Worked fixture:

```
observed_at  = 11:40:00     the fact was true
ingested_at  = 11:40:01     we received it
computed_at  = 11:40:02     we calculated the feature
available_at = 11:40:03     the strategy layer could use it

A decision at 11:40:02 must NOT see that feature.
```

This fixture is specified as a test in `15-TESTING.md`. Look-ahead is an architectural
property, not a bug class to be reviewed for.

Full treatment — late arrivals, corrections, backfills, delayed data, replay semantics —
in `05-DATA_LIFECYCLE_PIT.md`.

---

## 3. Event frequency is not state frequency

Three distinct cadences, deliberately decoupled:

| Concept | Cadence | Durability |
|---|---|---|
| **Raw observations** | every received event | durable, append-only, **the source of historical truth** |
| **State checkpoints** | configurable (1 s / 3 s / 5 s) + defined event boundaries | materialization artifact — prunable and rebuildable |
| **Historical reconstruction** | on demand | computed from observations for any T |

A `MarketState` is **not** persisted per WebSocket message. Live state lives in memory and
Redis; checkpoints are an optimization. If every checkpoint were deleted, no historical
truth would be lost — only recomputation time. Detail in `04-MARKETSTATE.md`.

---

## 4. Layer model

```
┌───────────────────────────────────────────────────────────────────────────┐
│  UPSTOX     REST  discovery · option chain · historical OI · reconciliation│
│             WS    live ticks · order updates                               │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L1  INGESTION      provider adapters · normalization · event identity     │
│      → MarketObservation  (append-only, immutable, canonical, un-owned)    │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L2  DATA QUALITY   gaps · staleness · sanity · REST/WS divergence         │
│      → QualityAssessment attached to every state                           │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L3  MARKET STATE   coherent assembly under a staleness budget             │
│      → MarketState(T)   live in memory · checkpointed · reconstructable    │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L4  ANALYTICS      pure functions · versioned features · available_at     │
│      positioning · volatility · greeks · gamma · price · futures · structure│
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L5  INTERPRETATION labelled observations over metrics                     │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L6  SIGNALS        evidence · contradicting evidence · lifecycle          │
└───────────┬───────────────────────────────────────────┬───────────────────┘
            ▼                                           ▼
┌───────────────────────────┐          ┌────────────────────────────────────┐
│  L7  ALERTS               │          │  L8  RESEARCH                      │
│      rules · dedup ·      │          │      event studies · backtests ·   │
│      outbox delivery      │          │      replay                        │
└───────────────────────────┘          └────────────────┬───────────────────┘
                                                        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L9  STRATEGY       MarketState(T) + available features → TradeIntent      │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L10 RISK           the only gate · non-bypassable · versioned decisions   │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L11 EXECUTION      OMS state machine · BrokerAdapter · RECONCILIATION     │
│                     Paper first · Upstox later, feature-flagged off        │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  L12 PORTFOLIO      positions · P&L · exposure · attribution               │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
                         feeds back into L8 RESEARCH
```

Two rules make the layering real rather than decorative:

1. **Dependencies point downward only.** L4 may not import from L6. Enforced by an
   import-linter contract in CI (`15-TESTING.md`), not by convention.
2. **Each layer's output is addressable** — queryable for a past timestamp, and
   recomputable to verify it matches.

---

## 5. Module boundaries

A **modular monolith**. One codebase, several process roles, enforced internal boundaries.

```
oipulse/
├── core/                  time, clock, ids, money, errors, config
├── instruments/           identity · versions · vendor mappings · expiry calendar
├── marketdata/
│   ├── providers/         MarketDataProvider protocol + UpstoxMarketDataProvider
│   ├── ingestion/         WS consumer · REST poller · backfill · reconciliation
│   └── store/             append-only observation repositories (bitemporal)
├── dataquality/           detectors · assessments · gap registry
├── marketstate/           assembly · coherence policy · checkpoints · reconstruction
├── analytics/
│   ├── registry/          FeatureDefinition registry + versioning
│   ├── positioning/       OI change · concentration · walls · migration · buildup
│   ├── volatility/        IV · skew · term structure · realized · rank
│   ├── greeks/            per-contract and aggregated exposure
│   ├── gamma/             GEX · concentration · by strike · by expiry
│   ├── price/             returns · trend · momentum · range · VWAP
│   ├── futures/           basis · OI · confirmation / divergence
│   └── structure/         positioning-derived levels · regime
├── signals/               framework · rules · evidence · lifecycle
├── alerts/                evaluation · dedup · delivery sinks
├── research/              event studies · datasets · statistics
├── replay/                state sequence reconstruction · clock control
├── backtest/              deterministic engine · fills · costs · availability gate
├── trading/
│   ├── intents/           TradeIntent — the universal seam
│   ├── risk/              limit engine · decision sequence · kill switch
│   ├── oms/               order state machine · order manager
│   ├── brokers/           BrokerAdapter protocol · Paper · Upstox
│   ├── reconciliation/    first-class subsystem — broker truth is authoritative
│   └── portfolio/         positions · P&L · exposure · attribution
├── events/                domain events · outbox · dispatcher
├── observability/         logging · metrics · correlation · health
├── identity/              users · auth · sessions · permissions · credentials
└── api/                   thin HTTP surface — no business logic
```

**Boundary rules, enforced in CI:**
- `analytics/*` may import `marketstate` and `core`. Nothing else. No DB, no HTTP, no clock.
- `api/*` may call service modules but contains no calculations.
- `trading/risk` may not import `trading/oms` or any strategy module.
- Nothing imports `api/`.
- Nothing calls `datetime.now()` — only the injected `core.clock.Clock`.

The last rule is what makes replay and backtest determinism achievable rather than hoped for.

---

## 6. Process topology

```
┌──────────────┐  HTTPS  ┌─────────┐        ┌────────────────────────────┐
│   Browser    ├────────►│  nginx  ├───────►│  api — thin, no business logic│
└──────────────┘   SSE   └─────────┘        └─────────────┬──────────────┘
                                                          │
┌──────────────────────────────────────────┐              │
│  ingestor                                │              │
│  • Upstox WS (live ticks)                │              │
│  • REST poller (chain, discovery)        │              │
│  • normalize → MarketObservation         │              │
│  never blocked by analytics              │              │
└──────────────────┬───────────────────────┘              │
                   ▼                                      │
┌──────────────────────────────────────────┐              │
│  processor                               │              │
│  • data quality                          │              │
│  • MarketState assembly + checkpoints    │              │
│  • analytics (feature registry)          │              │
│  • signals → alerts                      │              │
└──────────────────┬───────────────────────┘              │
                   ▼                                      │
┌──────────────────────────────────────────┐              │
│  trader     feature-flagged, off by default              │
│  • strategy runners → TradeIntent        │              │
│  • risk → OMS → BrokerAdapter            │              │
│  • broker WS order updates               │              │
│  • reconciliation loop                   │              │
└──────────────────┬───────────────────────┘              │
                   ▼                                      │
┌──────────────────────────────────────────┐              │
│  jobs       scheduled                    │              │
│  • instrument / expiry / calendar refresh│              │
│  • historical OI backfill                │              │
│  • REST↔WS reconciliation sweeps         │              │
│  • research batch · retention · partitions│             │
└──────────────────┬───────────────────────┘              │
                   ▼                                      ▼
   ┌───────────────────────────┐        ┌──────────────────────────────┐
   │  PostgreSQL               │        │  Redis                       │
   │  durable source of truth  │        │  live state · cache          │
   │  partitioned observations │        │  pub/sub · locks             │
   └───────────────────────────┘        └──────────────────────────────┘
```

Five roles, deployable as fewer processes. In development all non-API roles run in one
worker; in production they split. Identical code — a deployment decision, not an
architectural one (`14-DEPLOYMENT.md`).

Why these seams:
- **`ingestor` is isolated** so a WS consumer is never blocked by an analytics computation.
- **`trader` is isolated** as the only holder of order authority; killable without
  affecting collection.
- **`api` performs no business logic** — no analytics, strategy evaluation or trading
  decisions — so a slow query cannot stall ingestion. It may serve a deterministic
  `MarketState` reconstruction, which is computation but is read-only and shares one
  implementation with the live path (`14-DEPLOYMENT.md` §1).

---

## 7. Persistence policy

Four tiers. "Persist everything" is not the rule; "know why each thing is persisted" is.

| Tier | Examples | Policy |
|---|---|---|
| **Immutable source** | observations, chain snapshots, broker callbacks | persist forever — irreplaceable |
| **Decision artifact** | signals, evidence, intents, risk decisions, orders, fills, research results | persist forever, immutable, **never pruned** |
| **Recomputable materialized** | state checkpoints, metric values | persist by value vs. query cost; prunable **unless retention-locked** |
| **Transient intermediate** | assembly scratch, per-tick working values | never persisted |

**Retention lock:** any checkpoint, metric or observation referenced by a decision
artifact is pinned and exempt from pruning. In addition, every decision artifact embeds a
provenance descriptor — `feature_version`, `implementation_version`, `inputs_digest`,
`market_state_version`, observation references — so that even in the limit case the audit
chain states exactly what produced it. Pruning can never silently break auditability.

---

## 8. Storage strategy

PostgreSQL is the durable source of truth. Redis holds only what may be lost: cache,
pub/sub fan-out, locks, live latest-state.

Conceptual namespacing (detail in `02-DATA_MODEL.md`):

```
instrument_*  obs_*  chain_*  dq_*  state_*  metric_*  interp_*
signal_*  alert_*  research_*  trade_*  risk_*  portfolio_*
identity_*  audit_*  sys_*
```

`obs_*` is the volume driver and is range-partitioned by day. No Kafka, no ClickHouse, no
separate TSDB — the brief forbids them and Postgres handles this volume with partitioning
and BRIN indexes. `19-DECISIONS.md` records the measured thresholds that would justify
revisiting.

---

## 9. What this buys over the legacy design

| Legacy | v2 |
|---|---|
| Market data keyed by `user_id`, duplicated per user | Canonical, un-owned observations |
| Front expiry only | Expiry is a first-class dimension |
| REST polling only | WS live + REST for discovery, backfill, reconciliation |
| Greeks, bid/ask, `prev_oi` parsed and discarded | Every provided field persisted |
| Raw / derived / interpretation collapsed | Four explicitly separated layers |
| 330-line `save_snapshot` god function | Ingestion → quality → state → analytics pipeline |
| Analytics computed then thrown away | Persisted, versioned, addressable |
| Two incompatible bucket-flooring rules | One session-anchored time authority |
| One timestamp | Four: observed / ingested / computed / available |
| No point-in-time guarantee | Three enforced query modes |
| No data-quality concept | First-class, attached to every state |
| No provenance | Every derived value carries source + feature version |
| Instrument metadata mutated in place | Identity, version and vendor mapping separated |
| Fill assumed on API acceptance | Explicit UNKNOWN → reconciliation, broker authoritative |
| Business logic in routes and models | Routes thin; models are data |

---

## 10. Scope honesty

**Upstox historical data.** Full historical intraday option-chain state cannot be
reconstructed for arbitrary past timestamps from the Upstox APIs. However, historical OI
can be obtained for supported dates and may be used for limited backfill and research.

Historical OI backfill must **not** be read as implying the availability of historical
LTP, bid/ask, intraday Greeks, depth, or a complete synchronized option surface.
Backfilled rows carry a distinct `source` and an `ingested_at` at backfill time, so they
remain correctly invisible to `knowledge_as_of` queries before that point.

**Statistical features need history.** IV rank and percentile report *insufficient
history* rather than computing from a short window. Withholding a statistic beats
fabricating one.

**Live automated execution is out of scope for the initial build**, per the brief. The
architecture reaches it; the roadmap does not enable it.

**Broker-side duplicate prevention is not ours to guarantee.** Internal idempotency keys
give local dedup and audit, not a promise that Upstox will reject a duplicate. See
`11-TRADING.md`.
