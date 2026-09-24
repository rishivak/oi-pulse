# OI Pulse v2 — Event Model

> **Deliverable E.** Internal domain events, their identity, ordering, delivery and
> idempotency. Per the brief's §11: PostgreSQL + Redis + internal async processing. No
> Kafka.

---

## 1. Two distinct event concepts

These are routinely conflated and must not be:

| | **Market events** | **Domain events** |
|---|---|---|
| Origin | external — the venue/provider | internal — our own state transitions |
| Identity | provider-assigned (§2) | system-assigned UUID |
| Ordering | provider sequence | causal, per aggregate |
| Storage | `obs_*` (append-only observations) | `sys_outbox` + subscriber state |
| Replayable | yes — they *are* the history | yes, but as a consequence of replaying market events |
| Example | a tick on NIFTY 25000 CE | `SignalCreated` |

Market events are **facts we received**. Domain events are **things we decided or
concluded**. Replay re-derives domain events from market events; it never replays stored
domain events as though they were input.

---

## 2. Market event identity

Covered in `01-DOMAIN_MODEL.md` §4 and `02-DATA_MODEL.md` §3. Restated because it governs
ingestion idempotency:

```
1. provider_event_id
2. (feed_session_id, channel, channel_sequence)
3. (instrument_id, observed_at, source, content_hash)
```

A timestamp alone is never an identity. Two distinct events at the same timestamp
resolution are two rows.

**For Upstox V3 the resolution is always tier 3.** External verification of the live
feed observed, across two distinct feed sessions, that `provider_event_id` is **absent**
and `channel_sequence` is **absent** (AD-30, `20` §10 A-1/A-13). Tiers 1 and 2 remain
implemented for a provider that does supply them; they are never reached on this feed,
and neither field is ever synthesized.

### Four distinct concepts

Conflating any two of these is the failure this section exists to prevent.

| Concept | What it is | Upstox V3 |
|---|---|---|
| **Provider identity** | an id the provider itself assigns to an event | none |
| **OI Pulse-derived identity** | our deterministic digest of the decoded payload | the only identity available; `WEAK` confidence |
| **Local receive ordering** | `received_seq`, a local arrival counter | present, diagnostics only |
| **Provider ordering** | a sequence the provider supplies | none |

The derived digest deduplicates correctly and is **never labelled a provider event id**.
`received_seq` is **never promoted to an ordering authority**: arrival order is not
market order, and a local counter presented as a sequence would manufacture a guarantee
the feed does not give.

### Ordering
`channel_sequence` defines order within a feed session — **not** arrival order — *when a
provider supplies one*. Upstox V3 does not, so for that feed the ordering authority is
`observed_at` plus the stored session ordinal. `received_seq` is stored separately so
arrival order remains recoverable for diagnostics, but no processing logic depends on it.

Across feed sessions (i.e. across a reconnect), ordering falls back to `observed_at`, and
the boundary is recorded as a `RECONNECT_GAP` quality issue because cross-session ordering
cannot be guaranteed.

### Gap detection — two different kinds, only one of them detectable here

**Provider-sequence gap.** A discontinuity in `channel_sequence` within a session raises
`WEBSOCKET_GAP` with the missing range. This requires a provider sequence, so **it is
not detectable on Upstox V3** and is never claimed for it. Reporting "no gap" on a feed
that cannot express one would be a false guarantee.

**Connectivity / reconnect gap.** Detectable without any provider sequence, from
connection loss, reconnect, elapsed silence against the explicit heartbeat budget, and
expected observation continuity. This is what triggers REST recovery on Upstox V3:

```
connection interruption
  -> RECONNECT_GAP (or STALE_FEED against the heartbeat budget)
  -> recovery plan (out of band)
  -> REST recovery fetch
  -> canonical persistence, idempotent
  -> stream resumes
```

A gap is **not** emitted merely because time elapsed: `STALE_FEED` is raised against the
documented heartbeat budget and reports silence, not a count of lost messages. The gap
window is recorded permanently and never interpolated (`04-MARKETSTATE.md` §4).

### Out-of-order arrival
Accepted and stored with its true `observed_at`. Because state assembly queries by
`observed_at` rather than by arrival, a late event is automatically placed correctly in
market time — and, because `ingested_at` records when it actually arrived, it is
automatically *excluded* from knowledge-time queries before that point. The bitemporal
model handles late arrival without special-case code.

### Idempotent ingestion
`INSERT ... ON CONFLICT DO NOTHING` against whichever identity index applies. Duplicate
delivery is a no-op. Ingestion may be retried freely.

---

## 3. Domain event catalogue

| Event | Emitted when | Key payload |
|---|---|---|
| `MarketObservationReceived` | batch of observations persisted | instrument ids, observed range, source, count |
| `MarketDataGapDetected` | sequence discontinuity or staleness breach | instrument, window, gap kind |
| `OptionChainSnapshotStored` | REST chain response persisted | underlying, expiry, leg count, completeness |
| `MarketStateBuilt` | a checkpoint is written | underlying, observed_at, quality, coherence mode |
| `MarketStateDegraded` | quality status worsens | underlying, previous status, new status, issues |
| `MetricsComputed` | a feature batch completes | feature ids/versions, scope, observed_at, available_at |
| `InterpretationChanged` | a label transitions | scope, from, to |
| `OIMigrationDetected` / `OIMigrationConfirmed` / `OIMigrationFaded` | migration lifecycle | origin, destination, magnitude |
| `SignalCreated` / `SignalUpdated` / `SignalConfirmed` / `SignalInvalidated` / `SignalExpired` | signal lifecycle | signal id, type, strength, status |
| `AlertTriggered` | rule condition met and past cooldown | rule id, dedup key, trigger refs |
| `AlertDelivered` / `AlertDeliveryFailed` | per channel attempt | channel, attempt, error |
| `TradeIntentCreated` | strategy or user produces an intent | intent id, account, legs, state ref |
| `RiskCheckPassed` / `RiskCheckRejected` | a risk decision is appended | intent id, sequence_no, reasons |
| `OrderSubmitted` | request dispatched to a broker adapter | order id, attempt id |
| `OrderAcknowledged` | broker confirms receipt | broker order id |
| `OrderStateUnknown` | submission outcome undetermined | order id, attempt id, last known state |
| `OrderReconciliationRequired` | order enters PENDING_RECONCILIATION | order id, reason |
| `OrderPartiallyFilled` / `OrderFilled` / `OrderCancelled` / `OrderRejected` / `OrderExpired` / `OrderFailed` | terminal or progress transitions | qty, price, reason |
| `ReconciliationCompleted` | a reconciliation run finishes | run id, discrepancies, resolutions |
| `PositionChanged` | position quantity or average price moves | instrument, qty, avg price |
| `PnLUpdated` | portfolio revaluation | account, realized, unrealized |
| `PortfolioSnapshotTaken` | periodic or event-driven snapshot | account, exposure, greeks |
| `DataQualityStatusChanged` | overall status transitions | scope, from, to |
| `ResearchRunCompleted` / `BacktestRunCompleted` | batch job finishes | run id, result ref |

Every domain event carries: `event_id` (UUID) · `event_type` · `schema_version` ·
`occurred_at` · `correlation_id` · `causation_id` · `payload` ·
**`aggregate_type` · `aggregate_id` · `aggregate_sequence`** (§4 ordering).

`correlation_id` threads a whole causal chain — a tick that produces a state, a metric, a
signal, an alert and an order all share it. `causation_id` names the immediate parent
event. Together they make the brief's §23 correlation requirement queryable rather than
reconstructed from log text.

---

## 4. Delivery — transactional outbox

The one piece of the legacy architecture worth carrying forward conceptually, though
reimplemented.

```
┌────────────────────────────────────────────────────────────┐
│ BEGIN                                                      │
│   ... domain state writes (signal, order, position, ...)   │
│   INSERT INTO sys_outbox (...)     ← same transaction      │
│ COMMIT                                                     │
└───────────────────────┬────────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────────┐
│ dispatcher  (in `processor` / `trader` roles)              │
│   claim next unlocked AGGREGATE (not row):                 │
│   SELECT ... WHERE status='pending'                        │
│     ORDER BY aggregate_id, aggregate_sequence              │
│     FOR UPDATE SKIP LOCKED                                 │
│   → deliver that aggregate's events to subscribers,        │
│     strictly in aggregate_sequence order                   │
│   → status='published' | retry_count++ | 'failed'          │
└───────────────────────┬────────────────────────────────────┘
                        ▼
        ┌───────────────┴────────────────┐
        ▼                                ▼
┌────────────────────┐        ┌──────────────────────────┐
│ in-process handlers│        │ Redis pub/sub → SSE      │
│ (analytics, signals│        │ (browser fan-out)        │
│  alerts, portfolio)│        └──────────────────────────┘
└────────────────────┘
```

No event is lost between a commit and its publication, because the publication intent is
part of the commit.

### Transactional inbox — atomic, idempotent application

A consumption marker written *after* a business mutation is not enough: a crash in between
leaves the mutation applied and the marker absent, so redelivery applies it twice. For
positions, portfolio, risk and orders that is unacceptable.

Consumers therefore use a **transactional inbox** — the marker and the mutation commit
**together**:

```
BEGIN
  INSERT INTO sys_event_inbox (subscriber, event_id)      -- PK (subscriber, event_id)
    ON CONFLICT DO NOTHING
  IF inserted = 0:                                        -- already applied
      COMMIT and return                                   -- no-op
  ... perform the business mutation ...
COMMIT
```

**What this guarantees, stated precisely:** *exactly-once database application per
`(subscriber, event_id)` transaction*. It is **not** global exactly-once processing.
Postgres can make the marker and the mutation atomic; it cannot make an arbitrary external
side effect — an HTTP call, a Telegram message, a broker order — happen exactly once.
Consumers with external side effects must additionally be idempotent at the boundary
(idempotency keys, dedup on the receiving side), and `11-TRADING.md` §5 addresses the
broker case specifically.

### Ordering

Per-aggregate ordering is **not** a property the outbox provides for free.
`FOR UPDATE SKIP LOCKED` with several dispatchers lets worker B claim event #2 while
worker A still holds #1, and deliver it first.

Ordering is therefore made explicit rather than assumed:

- Every domain event carries `aggregate_type`, `aggregate_id`, `aggregate_sequence`
  (monotonic per aggregate, assigned in the producing transaction).
- **Dispatch is serialized per aggregate:** a dispatcher claims an aggregate, not an
  individual row, and delivers that aggregate's pending events in sequence order.
- A consumer rejects `aggregate_sequence = n` while `n-1` is unprocessed for that
  aggregate; the event returns to pending rather than being applied out of order.

The correctness invariant lives in the sequence check, **not** in a Redis lock. The lock is
an efficiency measure that avoids contention; if it is lost or expires, the sequence check
still prevents out-of-order application.

Global ordering across aggregates is **not** provided, and nothing in the domain requires
it.

### Retry and poison events
Exponential backoff; after N attempts the row moves to `failed` and raises an operational
alert. Failed events are never silently dropped — they remain queryable, and a replay tool
can re-dispatch them after a fix.

---

## 5. Cadence — what fires how often

This is where the brief's §11 and correction 2 intersect. Event volume and processing
cadence are deliberately decoupled:

| Stage | Cadence | Notes |
|---|---|---|
| Market observation ingestion | every event | no aggregation, no sampling, full fidelity |
| `MarketObservationReceived` | **batched** — per flush, not per tick | one event per persisted batch, or the outbox would exceed the tick rate |
| State checkpoint | configurable 1 s / 3 s / 5 s, plus boundaries | `MarketStateBuilt` |
| Analytics | per checkpoint, or per feature's own `sampling_frequency` | a 15-minute feature does not recompute every second |
| Signal evaluation | per checkpoint, throttled per signal type | rules declare their own minimum interval |
| Alert evaluation | on `SignalCreated`/`Updated` and on checkpoint | cooldown enforced per rule |
| Portfolio revaluation | on fill, and on a timer during market hours | |
| Reconciliation | on `OrderStateUnknown`, on restart, and on a timer | |

**Emitting one domain event per WebSocket tick would be an architectural error** — the
outbox would become the bottleneck and the event log would carry no more information than
the observation store already does. Observations are the high-frequency record; domain
events mark decisions and transitions.

---

## 6. Events and replay

Replay drives the pipeline from stored **market** observations under a pinned knowledge
horizon (`10-REPLAY.md`). Domain events are then **re-derived**, not re-played:

- Stored domain events from the original live run are *not* fed into handlers.
- Re-derived events are tagged with the replay run id and written to a separate namespace,
  never into the live outbox.
- Comparing re-derived events against the originals is a **determinism test**: for the same
  observations, knowledge horizon and code version, the derived event stream must match.
  Divergence indicates hidden non-determinism (wall-clock access, map iteration order,
  uninitialised ordering) and fails CI.

This is the strongest available check that the system is reproducible, and it is cheap
because it reuses the normal pipeline.

---

## 7. What is deliberately not built

- **No Kafka, no external broker.** Postgres outbox plus Redis pub/sub meets the
  durability and fan-out needs at this scale. `19-DECISIONS.md` records the thresholds
  that would justify revisiting.
- **No event sourcing for market data.** Observations are already an append-only log;
  layering an event store on top would duplicate it.
- **Event sourcing *is* used for the order aggregate** (`trade_order_events`), because
  order state genuinely requires an auditable transition history and the volume is tiny.
- **No global ordering guarantee.** Expensive, and nothing in the domain needs it.
