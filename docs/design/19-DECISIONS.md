# OI Pulse v2 — Architectural Decisions and Trade-offs

> **Deliverable Y.** Each decision records what was chosen, what was rejected, and what
> the choice costs. A decision without a stated cost has not been thought through.

---

### AD-01 — Retain the technology stack; rebuild the architecture
**Decision** Python 3.12 / FastAPI / PostgreSQL / Redis / Next.js.
**Rejected** Rust or Go ingestion; JVM stack; Node backend.
**Why** The brief names Postgres and Redis, forbids Kafka and microservices, and says
nothing about language. The problems in the legacy system are architectural — user-scoped
market data, discarded greeks, no point-in-time model — and none is caused by Python.
**Cost** Python's throughput ceiling on the ingestion path is lower than a compiled
language's. Accepted: at three underlyings × several expiries the volume is modest, and
`ingestor` is isolated so it can be rewritten independently if measurement demands it.

### AD-02 — Modular monolith, not microservices
**Decision** One codebase, role-selected processes, enforced internal boundaries.
**Rejected** Service-per-domain.
**Why** Brief §31 forbids it. Boundaries are enforced by import-linter contracts, which
gives most of the discipline of separate services without distributed-systems cost.
**Cost** Boundaries can be violated by someone disabling a lint rule — mitigated by CI
gating. Scaling is coarser-grained than per-service.

### AD-03 — Market data is canonical and un-owned
**Decision** No `user_id` on any observation, state, metric or signal table.
**Rejected** The legacy per-user snapshot model.
**Why** Brief §4. One observation must not be duplicated because two users view NIFTY.
**Cost** Per-user data-access policy must be enforced at the API layer rather than falling
out of row ownership. Worth it — the legacy model blocks a shared research corpus entirely.

### AD-04 — Bitemporal, four-dimensional time
**Decision** `observed_at`, `ingested_at`, `computed_at`, `available_at`; three enforced
query modes.
**Rejected** A single timestamp; two timestamps without `available_at`.
**Why** A single timestamp permits a backtest to consume data that arrived late. Two
permit consuming a feature before its window completed.
**Cost** Every query is more complex; every table is wider; developers must think about
which mode they want. This is the single largest complexity cost in the design and the
one most worth paying — it is what makes results reproducible.

### AD-05 — Observation identity is provider-derived, not timestamp-derived
**Decision** `provider_event_id` → `(feed_session, channel, channel_sequence)` →
`(instrument, observed_at, source, content_hash)`.
**Rejected** `UNIQUE (instrument_id, observed_at, source)`.
**Why** WS feeds legitimately carry multiple distinct events at one timestamp resolution;
a timestamp key would silently discard real data.
**Cost** Three partial unique indexes instead of one; identity resolution logic in the
adapter. Accepted — silent data loss is not recoverable.

### AD-06 — Instrument identity, version and vendor mapping are three tables
**Decision** Stable identity separate from historised metadata and historised vendor keys.
**Rejected** One row with `valid_from`/`valid_to`; mutation in place (legacy).
**Why** Overloading identity with version means a lot-size revision rewrites history.
Binding identity to `instrument_key` makes identity a vendor concern.
**Cost** Every instrument read is a temporal join. Mitigated by caching resolved
instruments per `as_of` within a request.

### AD-07 — Event frequency decoupled from state checkpoint frequency
**Decision** Observations at full fidelity; checkpoints at a configurable cadence;
reconstruction on demand.
**Rejected** Persisting a `MarketState` per WS message; holding state only in memory.
**Why** Per-message persistence would exceed the observation volume while adding no
information. Memory-only would lose the ability to serve historical states cheaply.
**Cost** Two representations of state (checkpoint and reconstruction) that must agree —
addressed by using one `build_state()` function and testing equality.

### AD-08 — Observations are the sole source of historical truth
**Decision** Checkpoints and metrics are prunable materialization artifacts.
**Rejected** Treating checkpoints as the record of history.
**Why** Keeps the irreplaceable asset small and clearly identified.
**Cost** Reconstruction is slower than reading a checkpoint. Mitigated by checkpointing at
the cadence that matters and caching reconstructions.

### AD-09 — Retention locks plus embedded provenance
**Decision** Decision artifacts pin the rows they reference **and** embed a provenance
descriptor inline.
**Rejected** References alone (pruning breaks the chain); embedding alone (duplicates
heavily); never pruning anything (unbounded growth).
**Why** Brief correction 7 — pruning must not silently destroy auditability.
**Cost** Some duplication between descriptor and referenced rows; pruning jobs must join
the lock table. Accepted as the price of an audit chain that cannot be broken.

### AD-10 — Analytics are pure functions with a mandatory registry
**Decision** No I/O, no clock, no DB in `analytics/*`; every metric declared with units,
conventions, availability delay and quality requirements; registration requires tests.
**Rejected** Ad hoc calculation in services or routes (legacy).
**Why** Purity is what allows one implementation to serve live, replay, research and
backtest — removing the classic backtest/live divergence.
**Cost** Ceremony per feature. Deliberate: it is what stops indicator sprawl.

### AD-11 — `available_at` enforced by the engine, not by convention
**Decision** Strategy context exposes only available features; requesting an unavailable
one raises.
**Rejected** Documentation and code review.
**Why** Brief §10 — "do not rely on researcher discipline."
**Cost** The strategy API is more restrictive; some legitimate analyses need the explicit
market-truth mode. Accepted: the default must be the safe one.

### AD-12 — `TradeIntent` is the universal seam; risk is non-bypassable
**Decision** One intent type for paper and live; risk enforced by import contract, DB
constraint and test.
**Rejected** Separate paper and live paths; risk as a library strategies call.
**Why** Brief §20. A gate that can be skipped is not a gate.
**Cost** Paper trading carries production ceremony. That is the point — it makes paper
results meaningful.

### AD-13 — Risk decisions are an immutable sequence
**Decision** `PRIMARY KEY (intent_id, sequence_no)`; orders reference the exact approving
decision.
**Rejected** One decision per intent (an earlier draft of this design).
**Why** Correction 11 — risk is legitimately re-evaluated on modification, retry and
changed conditions.
**Cost** Slightly more complex FK. Necessary for correctness.

### AD-14 — `UNKNOWN` is a first-class order state
**Decision** A lost acknowledgement produces `UNKNOWN` → `PENDING_RECONCILIATION`;
broker state is authoritative; no resubmission from `UNKNOWN`.
**Rejected** Treating a failed submit as rejected; relying on an internal idempotency key
to make resubmission safe.
**Why** Correction 5 and 12. An internal key does not bind the broker. Assuming rejection
can double a position; assuming acceptance can orphan one.
**Cost** Orders can sit ambiguous pending reconciliation, blocking further intents on that
instrument. Correct behaviour, and far cheaper than a duplicated position.

### AD-15 — Reconciliation is a subsystem, not error handling
**Decision** Own scheduler, own records, own tests; must rebuild local state from broker
truth; `trader` is not ready until reconciliation is clean.
**Rejected** Reconciliation as a recovery routine.
**Why** Correction 6. Every ambiguity path terminates here, so it must be the most
trustworthy component in the trading stack.
**Cost** Significant implementation effort before live trading is possible. Appropriate.

### AD-16 — PostgreSQL only; no Kafka, no ClickHouse, no TSDB
**Decision** Partitioned Postgres with BRIN; Redis for cache, pub/sub and locks;
transactional outbox for events.
**Rejected** Kafka; a dedicated time-series store.
**Why** Brief §24 and §31 — optimize after measurement.
**Cost** Postgres will need partition management and eventually archival.
**Revisit when measured:** sustained ingestion above ~50k observations/second; observation
tables beyond a few TB with degraded query latency after tuning; a genuine need for
multi-consumer replayable streams beyond the outbox; outbox lag that cannot be resolved by
batching or concurrency.

### AD-17 — Deterministic regime and signals; no ML initially
**Decision** Rule-based, evidence-carrying, with `UNKNOWN` a valid answer.
**Rejected** ML classification of regime or signal strength.
**Why** Brief §31 — no ML before deterministic analytics are validated. An unexplainable
regime label would violate the explainability requirement.
**Cost** Lower ceiling on subtlety. Accepted: an explainable system that can be validated
is worth more than an opaque one that cannot.

### AD-18 — Server-side revocable sessions; permissions separated from day one
**Decision** Session records in the database; `MARKET_DATA_READ` / `RESEARCH` /
`PAPER_TRADE` / `LIVE_TRADE` / `ADMIN` separated even with one user.
**Rejected** The legacy signed `user:{id}` cookie with no session store.
**Why** Brief §27. Logout must revoke; live trading must be separately grantable.
**Cost** A session lookup per request. Trivial against the risk.

### AD-19 — Live trading behind three independent gates, built last
**Decision** Feature flag + second confirmation variable + `LIVE_TRADE` permission;
`UpstoxBrokerAdapter` implemented in Phase 10 and left disabled.
**Rejected** Building execution alongside analytics.
**Why** Brief §21 and §31 — paper trading, research and risk come first.
**Cost** Live trading is far out. Correct ordering for a system that will handle real money.

### AD-20 — Parallel build with cutover; no legacy data migration
**Decision** New code under `oipulse/`; legacy runs until parity, then is removed. Legacy
snapshots are not imported into v2 schemas.
**Rejected** In-place refactor; big-bang rewrite; migrating legacy history.
**Why** In-place refactor contradicts the brief. Legacy data is user-scoped,
front-expiry-only and lacks greeks and quotes — importing it would contaminate a canonical
store with rows that cannot satisfy v2 invariants.
**Cost** Two systems running concurrently for several phases; legacy history is not
available to v2 research. Mitigated by keeping the legacy database queryable read-only,
and by historical OI backfill partially covering the positioning dimension.

### AD-21 — MarketState identity includes the knowledge horizon and build context
**Decision** Identity is `(underlying_id, market_time, knowledge_horizon, build_context_id)`.
`BuildContext` is immutable and content-addressable, subsuming builder, staleness-policy and
feature-set versions.
**Rejected** `(underlying, observed_at, builder_version)` — the original key.
**Why** `MarketState` is defined as a function of `K`; a key omitting `K` collapses two
legitimately different states (`11:42` as known at `11:42` vs. as known at `11:50`). It also
let a later-K checkpoint silently satisfy an earlier-K request — look-ahead arriving through
a cache.
**Cost** Wider keys, more checkpoint rows, and exact-match-only selection means more
reconstruction in hindsight modes. Accepted: the alternative is a correctness hole.

### AD-22 — Feature availability derives from input readiness, not market time
**Decision** `available_at = max(lookback_end, latest_input_available_at, computed_at) +
availability_delay`, where a raw input's availability is its `ingested_at`.
**Rejected** `max(lookback_end, max(observed_at of inputs)) + delay` — the original formula.
**Why** An input observed 11:40 but ingested 11:44 would have yielded availability at
11:40:02 — look-ahead inside the mechanism built to prevent look-ahead, and invisible because
the number looks plausible.
**Cost** Availability computation must resolve a dependency graph rather than read
timestamps off the inputs. Necessary.

### AD-23 — Transactional inbox for database-backed consumers
**Decision** Consumption marker and business mutation commit in one transaction;
`PRIMARY KEY (subscriber, event_id)`.
**Rejected** Marking consumption after the mutation, with the marker as a "safety net".
**Why** A crash between mutation and marker double-applies on redelivery — unacceptable for
positions, portfolio, risk and orders.
**Cost** Consumers must own their transaction boundary rather than being pure handlers.
**Claim discipline:** this gives *exactly-once database application per
`(subscriber, event_id)`*, **not** global exactly-once — Postgres cannot make an external
side effect idempotent, and saying otherwise would license unsafe assumptions downstream.

### AD-24 — Explicit aggregate sequence for domain-event ordering
**Decision** Events carry `aggregate_type`/`aggregate_id`/`aggregate_sequence`; dispatch is
serialized per aggregate; a consumer defers sequence *n* while *n−1* is unprocessed.
**Rejected** Claiming per-aggregate ordering as a property of the outbox.
**Why** `FOR UPDATE SKIP LOCKED` with multiple dispatchers lets worker B deliver event #2
while worker A holds #1. The original document asserted an ordering guarantee the
implementation did not provide.
**Cost** Reduced dispatch parallelism within an aggregate (across aggregates is unaffected).
The correctness invariant lives in the sequence check, **not** in the Redis lock — so losing
the lock degrades throughput, never correctness.

### AD-25 — SubscriptionPlanner governs feed capacity
**Decision** Capacity is planned before subscribing, returning
`ACCEPTED` / `DEGRADED` / `UNSATISFIABLE`, with a recorded degradation ladder.
**Rejected** Discovering capacity limits through runtime WebSocket failures.
**Why** Connection and subscription limits are finite and the expiry dimension multiplies
demand fast. A capacity problem should be a deterministic planning result, not an outage.
**Cost** A universe change requires a planning step. Budget numbers are **configuration
verified in Phase 2 (A-5)**, never hard-coded constants — the architecture must not encode
a vendor limit as a truth.

### AD-26 — Historical OI is a distinct temporal granularity
**Decision** `observation_kind = HISTORICAL_DAILY_OI` with `observation_date` and an explicit
`valid_from`/`valid_to` session interval.
**Rejected** Storing daily OI as an observation at a synthetic intraday timestamp.
**Why** The Upstox OI endpoint is date-based. Presenting a daily figure as an instant is a
misrepresentation someone reads as truth later; making granularity explicit means a daily
aggregate can never be served as live intraday state.
**Cost** A second temporal shape in the observation store, and consumers must handle it.
Worth it — the alternative silently corrupts any research that touches backfilled periods.

### AD-27 — RiskDecision records its own evaluation context
**Decision** Each decision carries `risk_state_ref`, `risk_evaluation_time`, `inputs_digest`
and `approved_until`.
**Rejected** Relying on the intent's `state_checkpoint_ref` for the whole decision sequence.
**Why** Decision #2 at 11:47 evaluated a different market than the intent saw at 11:45. The
audit trail must answer "what did risk see when it approved the order that actually went
out", not only "what did the strategy see".
**Cost** More per-decision storage, and approvals that expire must be re-evaluated rather
than reused. Both are the point.

### AD-28 — Root package is `oipulse`, not `platform`
**Decision** The implementation root package is `oipulse/`. Documents 00, 18, 19 and 20
previously wrote it as `platform/` and have been updated.
**Rejected** Keeping `platform/` as the importable package name.
**Why** `platform` is a **Python standard-library module**. A top-level package of that
name shadows it for the whole process: `platform.system()` and
`platform.freedesktop_os_release()` disappear, which breaks any dependency that inspects
the host — including, as observed during Phase 1, Python's own crash handler. This was a
naming choice in prose that does not survive contact with the interpreter.
**Cost** A documentation rename. No module boundary, layer or dependency rule changes —
the architecture is identical, only the root package identifier differs. Recorded rather
than applied silently, per the documentation-discipline rule.

### AD-29 — `oipulse.core` is stdlib-only
**Decision** The innermost layer carries **zero third-party dependencies**.
`core.config` therefore implements environment loading and validation with the stdlib
rather than `pydantic-settings`, which `14-DEPLOYMENT.md` §3 names.
**Rejected** pydantic-settings in `core`.
**Why** `core` is imported by every layer including `analytics/*`, which the boundary
contract forbids from importing a DB driver, an HTTP client or a settings library
(`07-ANALYTICS.md` §1). A third-party dependency in `core` leaks transitively into
exactly the layer that must not have one, turning a structural guarantee into a
convention. The validation semantics `14` §3 requires — env-sourced, validated at
startup, refuses placeholders and short keys, refuses to start on error — are preserved
exactly and are covered by tests.
**Cost** Hand-written parsing and validation instead of a declarative schema, and a
second validation idiom if pydantic is later used at the API edge for request models
(where it is appropriate, because `api/` has no such constraint). The
`core-is-dependency-free` contract in `tools/check_import_boundaries.py` enforces this
rather than leaving it to review.

---

## Trade-offs accepted, summarized

| We accept | To get |
|---|---|
| Four timestamps and three query modes on every read | Reproducible research and honest backtests |
| Temporal joins for instrument resolution | History that is not rewritten by metadata changes |
| Ceremony per analytic feature | No indicator sprawl; documented conventions; testable purity |
| Two state representations that must agree | Cheap historical reads without losing reconstructability |
| Orders that can sit ambiguous | Never a duplicated or orphaned real position |
| Production ceremony in paper trading | Paper results that actually predict live behaviour |
| Slower path to live execution | Risk, research and reconciliation proven first |
| Python's throughput ceiling | Team familiarity; isolated `ingestor` if it ever matters |
| Running two systems during cutover | No big-bang rewrite risk |
| No legacy history in v2 | A canonical store whose invariants actually hold |
| Wider state keys and more reconstruction | States at different knowledge horizons never collide |
| Availability resolves a dependency graph | No look-ahead through late-arriving inputs |
| Consumers own a transaction boundary | A crash cannot double-apply a position or fill |
| Serialized dispatch within an aggregate | Order events never applied out of sequence |
| A planning step before subscribing | Capacity limits surface as decisions, not outages |
| A second temporal shape for daily OI | Backfilled data can never pose as intraday state |
| Risk approvals expire | No order submitted on a stale approval |
| Hand-written config validation in `core` | The innermost layer stays dependency-free, so `analytics` genuinely cannot reach a DB or HTTP client |
