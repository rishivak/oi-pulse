# OI Pulse — Database Inventory (Phase 0 Audit)

PostgreSQL 16. 14 tables across 2 Alembic revisions:
`001_initial_schema` → `002_phase2_market_data_schema`.

Postgres-specific features in active use: `JSONB`, `INET`, `UUID`, `ON CONFLICT DO UPDATE`,
`SELECT … FOR UPDATE SKIP LOCKED`, partial indexes.

---

## 1. Entity relationships

```
users ─┬─1:1─ upstox_accounts        (Fernet-encrypted tokens)
       ├─1:1─ user_preferences
       ├─1:N─ collector_jobs
       ├─1:N─ alerts ····· FK expiry_id → option_expiries
       ├─1:N─ oi_snapshots ─1:N─ oi_strike_snapshots
       ├─0:N─ outbox_events          (SET NULL on user delete)
       └─0:N─ audit_logs             (SET NULL on user delete)

oauth_states        standalone, short-lived CSRF tokens
instruments         reference, seeded in migration 001
option_expiries ────FK target of oi_snapshots.expiry_id and alerts.expiry_id

market_ticks        standalone — ORPHANED, never read or written
market_data_events  standalone — no FKs, instrument-keyed
oi_time_bars        standalone — no FKs, instrument-keyed
```

Note the two data models are **not linked**: the Phase-1 chain-shaped tables
(`oi_snapshots`) and the Phase-2 instrument-shaped tables (`oi_time_bars`) share no foreign
key and are joined only by convention on `(underlying, expiry_date, strike, option_type)`.

---

## 2. Identity and auth

### `users`
`id` · `email` **UNIQUE** · `display_name` · `is_active` · timestamps.
Created implicitly on first OAuth callback; email falls back to
`{upstox_user_id}@upstox.user` when Upstox returns none.

### `upstox_accounts` — 1:1 with `users`
`upstox_user_id` · `access_token_enc` **bytea** · `access_token_iv` · `refresh_token_enc` ·
`refresh_token_iv` · `token_expires_at` · `is_active`.
The `*_iv` columns are reserved for a future manual GCM implementation and are unused today.

### `oauth_states`
`state_token` **UNIQUE** · `session_key` (the client IP) · `expires_at` · `used_at`.
10-minute TTL, one-time consumption. **No cleanup job** — expired rows accumulate forever.

### `user_preferences` — 1:1 with `users`
`default_underlying` (NIFTY) · `default_interval_min` (5) · `default_expiry_type` (current) ·
`theme` (dark) · `strike_window` (10).
Backend CRUD is complete; **the Settings page fetches this and renders none of it**.

---

## 3. Reference data

### `instruments`
`instrument_key` **UNIQUE** · `underlying_symbol` · `exchange` · `name` · `lot_size` ·
`is_active`. Index `ix_instruments_underlying (underlying_symbol, is_active)`.
Seeded in migration 001: NIFTY (lot 50), BANKNIFTY (15), SENSEX (10).

> **Lot sizes are stale.** NSE has revised index option lot sizes since these were written.
> Any notional, exposure, GEX, or P&L calculation multiplies by lot size, so this table must
> be re-verified before Phase 2 — a wrong lot size silently scales every exposure metric.

### `option_expiries`
`(underlying, expiry_date)` **UNIQUE** `uq_expiry` · `is_active`.
Index `ix_option_expiries_underlying_active`.
Populated lazily by `get_or_create_expiry()` and by `/oi/expiries` syncing from Upstox.

---

## 4. Phase-1 snapshot store (chain-shaped)

### `oi_snapshots`
```
UNIQUE uq_snapshot (user_id, underlying, expiry_id, interval_min, bucket_ts)
```
`spot_price` · `total_call_oi` · `total_put_oi` · `pcr` · `created_at`.
Indexes: `(user_id, underlying, expiry_id, bucket_ts)`, `(bucket_ts, underlying)`.

`uq_snapshot` **is** the idempotency guarantee — it is what makes `ON CONFLICT DO UPDATE`
safe on replay. Do not weaken it.

> **`user_id` in this key is an architectural problem.** Market data is not user-specific.
> Two users collecting NIFTY 5m produce two identical row sets. This blocks a shared
> research dataset and doubles storage per user. Resolving it is a prerequisite for
> multi-user and for treating history as a research corpus.

### `oi_strike_snapshots`
```
UNIQUE uq_strike (snapshot_id, strike)
```
Current: `call_oi` · `put_oi` · `call_ltp` · `put_ltp` · `call_volume` · `put_volume` ·
`call_iv` · `put_iv`.
Denormalized previous: `call_prev_oi` · `put_prev_oi`.
Precomputed: `call_oi_change` · `put_oi_change`.

Indexes: `(snapshot_id)`, `(snapshot_id, call_oi_change)`, `(snapshot_id, put_oi_change)` —
the latter two exist specifically to serve top-N ΔOI queries **that no endpoint issues**.

**Missing columns that Upstox already returns** (see TECHNICAL_DEBT.md D-01):
`call_delta`, `put_delta`, `call_gamma`, `put_gamma`, `call_theta`, `put_theta`,
`call_vega`, `put_vega`, `call_bid`, `put_bid`, `call_ask`, `put_ask`,
`call_bid_qty`, `put_bid_qty`, `call_ask_qty`, `put_ask_qty`, `call_prev_ltp`,
`put_prev_ltp` (computed in memory, never stored), `call_close`, `put_close`,
vendor `prev_oi`.

### `market_ticks`
`underlying` · `ltp` · `tick_ts`. Index `(underlying, tick_ts)`.
**Fully orphaned** — created and indexed in migration 001, never read or written by any
code. Either wire it to the planned WebSocket feed or drop it.

---

## 5. Phase-2 normalized store (instrument-shaped)

### `market_data_events`
`event_ts` · `instrument_type` (FUTURES|OPTIONS) · `instrument_key` · `trading_symbol` ·
`underlying` · `expiry_date` · `strike` · `option_type` · `ltp` · `oi` · `volume` ·
`source` (REST|WS).

Four indexes: `(event_ts)`, `(instrument_key, event_ts)`,
`(underlying, expiry_date, event_ts)`,
`(underlying, expiry_date, strike, option_type, event_ts)`.

> **No unique constraint, and inserted without `ON CONFLICT`.** This is the single
> non-idempotent write in an otherwise idempotent pipeline: replaying a bucket duplicates
> every row. Needs `UNIQUE (instrument_key, event_ts, source)` or equivalent.

`source='WS'` is defined for a WebSocket feed that does not exist. `instrument_type='FUTURES'`
is defined but nothing ever writes it.

### `oi_time_bars`
```
UNIQUE uq_oi_time_bar (instrument_key, timeframe, bucket_start_ts)
```
`timeframe` (1m|5m|15m|30m|1h) · `bucket_start_ts` · `bucket_end_ts` ·
`open_ltp` · `close_ltp` · `ltp_change` ·
`open_oi` · `close_oi` · `oi_change` · `oi_high` · `oi_low` · `volume` ·
`interpretation` · `points_count` · `source` (DERIVED).

Three indexes: `(underlying, timeframe, bucket_start_ts)`,
`(underlying, expiry_date, timeframe, bucket_start_ts)`,
`(underlying, expiry_date, strike, option_type, timeframe, bucket_start_ts)`.

Today every row is a degenerate single-point bar: `points_count=1`,
`open_ltp == close_ltp`, `oi_high == oi_low == close_oi`, and `open_oi` taken from the
previous *snapshot* rather than the previous bar. Rows are written only when
`_interval_to_timeframe()` maps the collector interval — **3-minute and 10-minute
collectors write nothing here at all**, and the `1h` branch is unreachable because 60 is
not a valid `BucketInterval`.

This table is the right foundation for the target architecture: instrument-keyed, not
user-scoped, supports futures. It should become primary rather than additive.

---

## 6. Operational

### `collector_jobs`
```
UNIQUE uq_collector_job (user_id, underlying, interval_min)
```
`is_running` · `last_run_at` · `last_status` (success|failed|skipped) · `last_error`.
The sole coupling between the API process and the worker process.

> Note the key omits `expiry_id` — a job is per (user, underlying, interval) and always
> collects the front expiry. Multi-expiry collection requires either a key change or a
> separate subscription table.

### `outbox_events`
`event_id` **UUID UNIQUE** · `user_id` · `event_type` · `schema_version` ·
`payload` **JSONB** · `status` (pending|published|failed) · `retry_count` · `last_error` ·
`processed_at`.
Partial index `ix_outbox_pending (status, created_at) WHERE status = 'pending'` — exactly
the right index for the poller's query.

**No retention policy.** Published rows accumulate indefinitely; at a 5-minute cadence
across three underlyings this grows without bound.

### `audit_logs`
`action` · `resource_type` · `resource_id` · `ip_address` **INET** · `status` ·
`details` **JSONB**. Index `(user_id, created_at)`.
Written for `oauth_login`, `offline_login`, `logout`. **No API exposes it** — DB inspection
only. Deliberately token-free.

### `alerts`
`user_id` · `underlying` · `expiry_id` · `alert_type` · `condition_json` **JSONB** ·
`is_enabled` · `last_triggered_at`. Index `(user_id, is_enabled)` — precisely the index an
evaluator loop would need.

> **Schema only.** No routes, no evaluator, no delivery, no UI. The frontend SSE client
> already listens for an `oi_alert` event that nothing publishes. The plumbing around this
> table is more complete than the table's own feature.

---

## 7. Gaps against the target architecture

Tables the target design needs that do not exist:

| Domain | Needed |
|---|---|
| Market state | `market_states` — materialized per (underlying, timestamp) |
| Volatility | IV history is implicit in `oi_strike_snapshots`; no IV rank/percentile store |
| Futures | no futures OHLC/OI table; `/oi/futures` reads `oi_time_bars` which never gets FUTURES rows |
| Price context | no spot OHLC/VWAP store (`market_ticks` is the orphaned stub) |
| Regime | `market_regimes` with evidence |
| Signals | `signals` + `signal_evidence` |
| Research | `research_experiments`, `strategy_definitions`, `backtest_runs`, `backtest_results`, `signal_evaluations` |
| Paper trading | `trade_intents`, `paper_orders`, `paper_fills`, `paper_positions` |
| Risk | `risk_limits`, `risk_checks` |
| Execution | `orders`, `order_events`, `fills`, `positions`, `portfolio_snapshots` |
| Journal | `journal_entries` |
| Data quality | `data_quality_checks`, `collection_gaps` |

## 8. Hygiene items

- No retention/partitioning anywhere. `oi_strike_snapshots` is the volume driver:
  ~70 strikes × 2 sides × 75 buckets/day ≈ 10k rows/day/underlying/interval. Partitioning
  by month on `bucket_ts` should be planned before the research corpus matters.
- No cleanup for expired `oauth_states` or published `outbox_events`.
- `market_ticks` is dead — drop it or wire it.
- `access_token_iv` / `refresh_token_iv` are dead — drop them or implement GCM.
- Index `ix_snapshots_ts_underlying (bucket_ts, underlying)` supports cross-user queries
  that no endpoint makes; keep it for the future research corpus.
