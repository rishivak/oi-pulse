# OI Pulse v2 — Observability Strategy

> **Deliverable V.** Built in from the start, not retrofitted. Every critical operation
> carries a correlation identifier (brief §23).

---

## 1. Principles

1. **One logging system.** `structlog`, structured JSON, everywhere. The legacy codebase
   configures structlog and then uses stdlib `logging` in every service — output is
   mixed-format and unparseable.
2. **Correlation is mandatory.** A tick, the state it produced, the metric, the signal,
   the alert and the order share a `correlation_id`.
3. **Trading-specific signals matter more than generic ones.** Ingestion lag and outbox
   lag say more about this system's health than CPU does.
4. **Data quality is observability.** The system must know when its inputs are
   untrustworthy (`04-MARKETSTATE.md` §3).
5. **No secrets, ever.** No tokens, no credentials, no PII in logs.

---

## 2. Structured logging

Every record carries:

```json
{
  "ts": "2026-03-03T11:42:15.123Z",
  "level": "info",
  "event": "market_state_built",
  "role": "processor",
  "instance_id": "processor-a1",
  "correlation_id": "01HQ...",
  "causation_id": "01HQ...",
  "underlying": "NIFTY",
  "observed_at": "2026-03-03T11:42:15Z",
  "quality_status": "OK",
  "coherence_mode": "SNAPSHOT_ANCHORED",
  "duration_ms": 12
}
```

`correlation_id` originates at ingestion (or at an API request, or a job run) and
propagates through every derived operation via context-local storage — never threaded
manually through function signatures.

| Level | Use |
|---|---|
| `error` | requires human attention |
| `warning` | degraded but handled: gaps, staleness, retries, divergence |
| `info` | state transitions, lifecycle, decisions |
| `debug` | development only; off in production |

Logging is **sampled** on the ingestion hot path — per-observation logging would exceed
the observation volume itself. Ingestion logs at batch granularity with counts, and
individual events only on anomaly.

---

## 3. Metrics

Prometheus-compatible, exposed at `/ops/metrics`.

### Ingestion
| Metric | Type | Why |
|---|---|---|
| `ingestion_latency_seconds` (`ingested_at − observed_at`) | histogram | **the single most important health metric** |
| `observations_ingested_total` by source, instrument type | counter | |
| `observation_duplicates_total` | counter | identity resolution working |
| `observation_out_of_order_total` | counter | feed behaviour |
| `ws_connection_state` by feed | gauge | |
| `ws_reconnects_total`, `ws_gap_seconds` | counter, histogram | |
| `rest_request_duration_seconds` by endpoint | histogram | |
| `rest_errors_total` by endpoint, status | counter | |
| `rate_limit_budget_remaining` by endpoint class | gauge | capacity planning for expiry expansion |
| `rate_limit_events_total` | counter | |

### State and analytics
`state_build_duration_seconds` · `state_checkpoints_written_total` by trigger ·
`state_quality_status` (gauge per underlying) · `state_component_age_seconds` by category
(against the staleness budget) · `analytics_compute_duration_seconds` by feature ·
`analytics_skipped_total` by feature, reason · `feature_unavailable_total`.

**Proving the correctness model is actually working** — these are the metrics that turn
the architecture's guarantees into observable facts rather than claims:

| Metric | Type | What it proves |
|---|---|---|
| `feature_availability_lag_seconds` (`available_at − lookback_end`) | histogram | features are not becoming available before their window closes |
| `feature_input_readiness_lag_seconds` (`available_at − last_input_available_at`) | histogram | availability tracks **input readiness**, not market time (`07-ANALYTICS.md` §3) |
| `marketstate_reconstruction_duration_seconds` | histogram | reconstruction cost, and whether it needs its own role |
| `marketstate_checkpoint_reuse_total` | counter | how often a checkpoint matched the full identity tuple |
| `marketstate_checkpoint_rebuild_total` by reason (`no_match` \| `knowledge_mismatch` \| `context_mismatch`) | counter | **`knowledge_mismatch` rising means hindsight queries are common** — expected during research, suspicious in live serving |
| `subscription_capacity_used` / `_remaining` by mode | gauge | headroom before a universe change becomes `UNSATISFIABLE` |
| `subscription_rejections_total` by verdict | counter | planner refusals and degradations (`06-UPSTOX_INTEGRATION.md` §5) |
| `subscription_degradation_active` | gauge | a reduced plan is currently in force — data gaps are by design, not fault |
| `consumer_inbox_duplicate_total` by subscriber | counter | redelivery is being caught by the transactional inbox |
| `consumer_inbox_failure_total` by subscriber | counter | handlers failing after claiming an event |
| `aggregate_ordering_deferrals_total` | counter | out-of-sequence events correctly deferred (`03-EVENT_MODEL.md` §4) |
| `observation_identity_confidence` by level | gauge | how much of the feed is `STRONG` vs `WEAK` identity (A-1) |

`state_component_age_seconds` versus its budget is the early-warning signal for a
degrading feed — it moves before quality flips to `DEGRADED`.

### Events
`outbox_pending_count` (gauge) · **`outbox_lag_seconds`** (oldest pending age) ·
`outbox_published_total` · `outbox_failed_total` · `event_processing_duration_seconds` by
type · `event_redelivery_total`.

`outbox_lag_seconds` is the health metric for the whole derived pipeline; when it grows,
everything downstream is stale.

### Signals and alerts
`signals_created_total` by type · `signal_lifecycle_transitions_total` ·
`alerts_triggered_total` · `alerts_delivered_total` / `_failed_total` by channel ·
`alert_delivery_duration_seconds`.

### Trading
`trade_intents_total` by source · `risk_decisions_total` by decision · `orders_total` by
state · **`orders_unknown_count`** (gauge) · `order_submit_duration_seconds` ·
`reconciliation_runs_total` · `reconciliation_discrepancies_total` by kind ·
`broker_api_errors_total` · `position_count`, `portfolio_pnl`.

`orders_unknown_count > 0` for longer than the reconciliation interval is a page-worthy
condition — it means real money is in an ambiguous state.

### Infrastructure
`db_query_duration_seconds` by operation class · `db_pool_utilization` ·
`db_partition_lead_days` · `redis_operation_duration_seconds` · `sse_connections_active` ·
`http_request_duration_seconds` by route.

`db_partition_lead_days` falling below a week must alert — a missing partition should
never be discovered by a failed insert.

---

## 4. Data quality as a first-class surface

Beyond metrics, quality is queryable (`/data-quality/status`) and visible in the UI header.

```
DATA QUALITY   DEGRADED

  ● option chain incomplete        NIFTY 2026-03-05   coverage 0.87
  ● websocket gap                  11:32:04 – 11:32:09
  ● 3 contracts stale              > 60s

  Analytics computed on this state are flagged.
  IV_RANK unavailable: quality requirements unmet.
```

Quality issues are **persisted** (`dq_issues`), so research can exclude affected windows
and an operator can review history rather than only the present. This is what makes
"was this result computed on good data?" answerable after the fact.

---

## 5. Health checks

| Endpoint | Semantics |
|---|---|
| `/ops/health` | liveness — process responsive |
| `/ops/ready` | readiness — DB reachable and migrated, Redis reachable, role-specific deps |

Role-specific readiness: `ingestor` requires an authenticated WS; `processor` requires
recent observations; `trader` requires a healthy broker connection **and a clean
reconciliation**; `api` requires DB and Redis.

A `trader` that cannot reconcile is **not ready** and must not accept intents.

---

## 6. Alerting

| Condition | Severity |
|---|---|
| `orders_unknown_count > 0` beyond the reconciliation interval | **page** |
| Reconciliation discrepancies unresolved | **page** |
| Broker connectivity lost while positions are open | **page** |
| Ingestion stopped during market hours | **page** |
| Database unreachable | **page** |
| Ingestion latency p99 breach | warn |
| Outbox lag beyond threshold | warn |
| Quality `UNRELIABLE` for an underlying during market hours | warn |
| WS reconnect rate elevated | warn |
| Rate-limit budget below reserve | warn |
| Partition lead time below a week | warn |
| Checkpoint cadence slipping | warn |

Alerts route to the operator, and are deliberately distinct from user-facing market
alerts (`08-SIGNALS.md`) — conflating "the system is broken" with "the market moved" is a
category error that trains people to ignore both.

---

## 7. Tracing

OpenTelemetry spans on request and pipeline paths: `ingest → quality → state → analytics →
signals → alerts`, and `intent → risk → order → fill`. Sampled, since full tracing on the
ingestion path would exceed the data volume.

`correlation_id` and trace id are cross-referenced so a log line leads to a trace and back.

---

## 8. Audit versus observability

Distinct concerns, deliberately not merged:

| | Audit (`audit_*`, decision artifacts) | Observability (logs, metrics, traces) |
|---|---|---|
| Purpose | what happened, defensibly | how the system is behaving |
| Retention | forever, immutable | days to weeks |
| Store | PostgreSQL | log/metric backend |
| Queried by | researchers, compliance | operators |

**Audit must never depend on the log pipeline.** A dropped log line must not lose a risk
decision. Everything needed to reconstruct a decision lives in the database, not in logs —
which is why `11-TRADING.md` §10 can promise an eleven-question chain answerable by joins.
