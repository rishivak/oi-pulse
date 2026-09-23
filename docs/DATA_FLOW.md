# OI Pulse — Data Flow (Phase 0 Audit)

> Traced end to end against source. Line references are to commit `a0621dd`.

---

## 1. The complete collection cycle

```
                        ┌─────────────────────────────────────────┐
                        │  worker/scheduler.py                    │
                        │  AsyncIOScheduler (UTC)                 │
                        │  • dispatch_active_jobs   every 60 s    │
                        │  • flush_outbox_task      every  2 s    │
                        │  • _run_catch_up()        on startup    │
                        └────────────────┬────────────────────────┘
                                         │ every 60 s
                                         ▼
              SELECT * FROM collector_jobs WHERE is_running = true
                                         │
                       for each job: is current bucket != bucket(last_run_at)?
                                         │ yes
                                         ▼
                        asyncio.create_task(_safe_run_job(...))
                                         │
                                         ▼
      ┌──────────────────────────────────────────────────────────────────┐
      │  worker/jobs/snapshot_job.py :: run_snapshot_job                 │
      ├──────────────────────────────────────────────────────────────────┤
      │ 1. is_market_open(now)?          → no  ⇒ return silently         │
      │ 2. bucket_ts = floor(now, interval) in IST, stored as UTC        │
      │ 3. load UpstoxAccount, decrypt_token()                           │
      │    → missing ⇒ job status "failed"                               │
      │ 4. _resolve_active_expiry()  ── Upstox GET /option/contract      │
      │    → nearest expiry ≥ today   → none ⇒ status "skipped"          │
      │ 5. get_option_chain()        ── Upstox GET /option/chain         │
      │    → UpstoxAuthError ⇒ status "failed" + emit `auth_required`    │
      │    → UpstoxError     ⇒ status "failed"                           │
      │    → empty chain     ⇒ status "skipped"                          │
      │ 6. spot = first row with underlying_spot_price                   │
      │ 7. save_snapshot(...)                                            │
      │ 8. _update_job_status(...)                                       │
      └────────────────────────────────┬─────────────────────────────────┘
                                       ▼
      ┌──────────────────────────────────────────────────────────────────┐
      │  app/services/snapshot_service.py :: save_snapshot               │
      ├──────────────────────────────────────────────────────────────────┤
      │  a. get_or_create_expiry() → option_expiries row                 │
      │  b. Redis SET NX EX 120                                          │
      │       lock:snapshot:{user}:{underlying}:{expiry}:{interval}:{ts} │
      │       not acquired ⇒ return existing row, else sleep 1 s and go  │
      │  c. SELECT previous snapshot (bucket_ts < this one, ORDER DESC)  │
      │       → build strike → (prev call/put OI, prev call/put LTP) map │
      │  d. build StrikeInput[] from the chain rows                      │
      │  e. compute_snapshot_analytics(strike_inputs)      ← PURE        │
      │  f. INSERT oi_snapshots        ON CONFLICT uq_snapshot DO UPDATE │
      │  g. INSERT oi_strike_snapshots ON CONFLICT uq_strike   DO UPDATE │
      │  h. INSERT market_data_events  ← plain insert, NOT idempotent    │
      │  i. INSERT oi_time_bars        ON CONFLICT uq_oi_time_bar        │
      │  j. session.add(OutboxEvent "snapshot_created")                  │
      │  k. COMMIT            ← f–j are one transaction                  │
      │  l. finally: release Redis lock                                  │
      └────────────────────────────────┬─────────────────────────────────┘
                                       │
                                       ▼ (≤ 2 s later, separate process tick)
      ┌──────────────────────────────────────────────────────────────────┐
      │  app/services/event_bus.py :: flush_outbox                       │
      ├──────────────────────────────────────────────────────────────────┤
      │  SELECT … WHERE status='pending' ORDER BY created_at LIMIT 100   │
      │         FOR UPDATE SKIP LOCKED                                   │
      │  for each: redis.PUBLISH events:{user_id}  → status='published'  │
      │  on error: retry_count++; ≥ 5 ⇒ status='failed'                  │
      │  COMMIT                                                          │
      └────────────────────────────────┬─────────────────────────────────┘
                                       ▼
                          Redis channel  events:{user_id}
                                       │
                                       ▼
      ┌──────────────────────────────────────────────────────────────────┐
      │  API process — app/api/routes/stream.py  GET /api/stream/events  │
      │  subscribe_sse(user.id): pubsub.subscribe → yield SSE frames     │
      │  StreamingResponse(media_type="text/event-stream",               │
      │                    headers={"X-Accel-Buffering": "no"})          │
      └────────────────────────────────┬─────────────────────────────────┘
                                       ▼ nginx (proxy_buffering off)
      ┌──────────────────────────────────────────────────────────────────┐
      │  lib/realtime/sse-client.ts — singleton EventSource              │
      │  on "snapshot_created" → queryClient.invalidateQueries(...)      │
      │                        → components refetch over the REST path   │
      └──────────────────────────────────────────────────────────────────┘
```

---

## 2. Timestamp handling

This is subtle and worth stating precisely, because every replay and backtest depends on it.

- `get_bucket_timestamp(dt, interval_min)` (`market_session.py`) converts to **IST**, floors
  to the interval boundary in IST, then converts back to **UTC**. The UTC value is the
  canonical `bucket_ts` stored in `oi_snapshots`.
- `bucket_start(ts, timeframe)` (`timeframe_service.py`) floors in **UTC directly**, with no
  IST conversion.

These two functions therefore **do not agree**. For intervals that divide evenly into the
IST offset (05:30 — i.e. 1m, 5m, 15m, 30m) the boundaries coincide. For 1h they do not:
IST-floored hours land on `:00` IST = `:30` UTC, while `bucket_start` floors to `:00` UTC.
Anything mixing the two at 1h resolution will mis-bucket. See TECHNICAL_DEBT.md D-06.

---

## 3. Where derived values are computed

| Value | Computed | Stored? | Exposed? |
|---|---|---|---|
| `call_oi_change` / `put_oi_change` per strike | write time | ✅ `oi_strike_snapshots` | ✅ |
| `total_call_oi` / `total_put_oi` / `pcr` | write time | ✅ `oi_snapshots` | ✅ |
| `call_oi_change_pct` / `put_oi_change_pct` | write time | ❌ | ❌ |
| `call_ltp_change` / `put_ltp_change` | write time | ❌ (only into bars) | ❌ |
| `ce_signal` / `pe_signal` per strike | write time | ❌ | ❌ |
| `strike_pcr` | write time | ❌ | computed again in the frontend |
| `top_call_oi_additions` and 3 siblings | write time | ❌ | ❌ |
| `interpretation` per leg | write time | ✅ `oi_time_bars` | ✅ |
| Timeframe bar aggregation | **read time** in `/oi/history-bars` | ❌ | ✅ |
| Trending ΔOI between buckets | **read time** in `/oi/trending` | ❌ | ✅ |
| ATM strike | **read time** in `/oi/strikes` | ❌ | implicit |

Two observations that matter for the target architecture:

1. **`compute_snapshot_analytics` already produces most of a positioning layer and throws
   ~70% of it away.** Persisting its full output is nearly free and is a prerequisite for
   signal backtesting (you cannot backtest a signal whose inputs were never stored).
2. **Read-time aggregation in `/oi/history-bars` re-derives bars on every request** from raw
   snapshots, in Python, while `oi_time_bars` already exists as a materialized store. The two
   paths compute overlapping things by different routes and can disagree.

---

## 4. The Phase-2 dual-write

`save_snapshot` writes the same cycle into **two independent models**:

- **Phase 1** — `oi_snapshots` + `oi_strike_snapshots`, keyed by
  `(user, underlying, expiry, interval, bucket_ts)`, one row per snapshot with children
  per strike. Chain-shaped.
- **Phase 2** — `market_data_events` + `oi_time_bars`, keyed by
  `(instrument_key, timeframe, bucket_start_ts)`, one row per **leg**. Instrument-shaped.

Phase 2 is the better foundation for the target architecture (instrument-centric, supports
futures, not user-scoped except by omission), but today it is a second-class citizen:

- It is **conditional** on `_interval_to_timeframe(interval_min)` returning non-`None`.
  That function handles 1, 5, 15, 30, 60 — but `BucketInterval` permits **3 and 10**, which
  map to `None`. Running a 3-minute or 10-minute collector **silently writes no Phase-2 data
  at all**. And 60 is not a valid `BucketInterval`, so the `1h` branch is unreachable.
- Each bar is a degenerate single-point bar: `points_count=1`, `open_ltp == close_ltp`,
  `oi_high == oi_low == close_oi`. `open_oi` comes from the previous *snapshot*, not the
  previous bar of that timeframe.
- `market_data_events` is inserted **without** `ON CONFLICT` and has **no unique constraint**,
  so it is the one write in an otherwise-idempotent pipeline that duplicates on replay.

---

## 5. Event lifecycle

| Event type | Emitted by | Emitted today? | Consumed by frontend? |
|---|---|---|---|
| `connected` | `subscribe_sse` on open | ✅ | ✅ status indicator |
| `snapshot_created` | `save_snapshot` via outbox | ✅ | ✅ invalidates queries |
| `auth_required` | `snapshot_job` on `UpstoxAuthError` | ✅ | ❌ listener registered, no handler |
| `collector_status_changed` | — referenced in 3 docstrings | ❌ **never published** | ❌ |
| `market_price_updated` | — | ❌ never published | ❌ |
| `oi_alert` | — | ❌ never published | ❌ |

`event_bus.publish_event()` exists as a direct (non-outbox) publish path and is **called
from nowhere**. All real events go through the outbox.

The frontend SSE client registers listeners for all six types. Three of them can never fire.
Notably, `collector_status_changed` would make the Settings page's 10-second poll
unnecessary, and `auth_required` should force a re-login but currently does nothing.

---

## 6. Failure modes traced

| Failure | Current behaviour | Gap |
|---|---|---|
| Market closed | `run_snapshot_job` returns before any work | — |
| Worker restarts mid-session | `_run_catch_up()` replays last 3 missed buckets | **Calls `run_snapshot_job`, which always collects *now*'s bucket** — it re-collects current data rather than backfilling the missed timestamps. The gap stays a gap. |
| Two workers race a bucket | Redis lock; loser returns the existing row | Correct |
| Crash between commit and publish | Outbox row survives, poller retries | Correct |
| Redis down | `flush_outbox` raises, caught and logged; rows stay pending | Events delayed, not lost. Correct. |
| Upstox 401 | Job marked failed, `auth_required` emitted | Collector does **not** set `is_running=false`, so it retries every interval until the user re-auths |
| Upstox 429 | Client honours `Retry-After`, retries in budget | Retries do not count against a circuit breaker |
| Upstox 5xx / timeout | 3 attempts, exponential backoff + jitter | — |
| Chain returns empty | status `skipped` | Indistinguishable from a real halt |
| Snapshot write fails | status `failed`, error recorded | Bucket permanently missing; no repair path |
| Holiday not in the hardcoded list | `is_market_open` returns true; collector runs | Collects garbage/stale rows on unlisted holidays |

There is **no data-quality layer** — nothing detects a missing bucket, a stale price, or a
duplicated `market_data_events` row after the fact.

---

## 7. Read paths in use

| Frontend surface | Endpoint | Notes |
|---|---|---|
| Dashboard metrics | `/oi/expiries`, `/oi/latest` | live payload preferred over REST |
| Trending OI | `/oi/trending` | ΔOI diffed at read time |
| Option Chain | `/oi/strikes` | ±N strikes around ATM |
| OI History | `/oi/history-bars` | bars aggregated at read time |
| Settings | `/collector/status`, `/auth/me` | 10 s poll |
| — | `/oi/heatmap` | **built, unused** |
| — | `/oi/futures` | **built, returns empty — nothing writes FUTURES bars** |
| — | `/oi/options/summary` | **built, unused** |
| — | `/oi/history` | **built, unused** |
| — | `/oi/underlyings`, `/oi/timeframes` | **built, unused** — frontend hardcodes both |
| — | `PATCH /settings` | **built, unused** — Settings fetches prefs and renders none |
