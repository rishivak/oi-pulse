# OI Pulse — Technical Debt Register (Phase 0 Audit)

Every item was verified against source. Each carries a file reference, an impact
assessment, and a phase.

**Severity** — 🔴 correctness/security · 🟠 blocks target architecture · 🟡 quality/hygiene

---

## Correctness bugs

### D-01 🟠 Greeks are fetched and discarded
`integrations/upstox/schemas.py:20-25` defines `delta`, `gamma`, `theta`, `vega`, `iv`.
`services/snapshot_service.py:168-169` persists **only `iv`**.
**Impact:** GEX, delta/gamma/vega exposure and all dealer-positioning analytics are blocked
— not by a data source, but by four missing columns. The vendor data already arrives in
every response we pay for.
**Fix:** migration adding 8 columns + ~4 lines in `StrikeInput` construction. Backfill is
impossible for historical rows; treat them as nullable.
**Phase 2.**

### D-02 🔴 `market_data_events` is not idempotent
`snapshot_service.py:362` — plain `pg_insert(...).values(event_rows)` with no
`ON CONFLICT`, and the table has no unique constraint.
**Impact:** the one non-idempotent write in a pipeline whose central claim is idempotency.
Any replay, retry, or catch-up duplicates every event row, silently corrupting any future
analytics built on this table.
**Fix:** `UNIQUE (instrument_key, event_ts, source)` + `ON CONFLICT DO NOTHING`.
**Phase 1.**

### D-03 🔴 3-minute and 10-minute collectors silently write no Phase-2 data
`snapshot_service.py:41-52` — `_interval_to_timeframe` handles 1, 5, 15, 30, 60 and returns
`None` otherwise. `config.py:11-17` — `BucketInterval` permits **3 and 10**. The frontend
`IntervalSelector` offers both.
**Impact:** a user selecting 3m or 10m gets Phase-1 snapshots but **zero**
`market_data_events` / `oi_time_bars` rows, with no error or warning. `/oi/history-bars`,
`/oi/futures` and `/oi/options/summary` are all empty for those users and nothing says why.
Also: the `60 → "1h"` branch is **unreachable**, since 60 is not a valid `BucketInterval`.
**Fix:** reconcile the two enumerations; fail loudly on an unmapped interval.
**Phase 1.**

### D-04 🔴 `/oi/latest` ignores its `expiry_date` parameter
`routes/oi.py:108-130` — `expiry_date` is a **required** parameter; the query filters on
`user_id`, `underlying`, `interval_min` only.
**Impact:** latent. Harmless while only the front expiry is collected; returns the wrong
expiry's snapshot the moment multi-expiry collection lands — which is a Phase-1 goal.
**Fix:** join `option_expiries` and filter, as `/oi/history` already does correctly.
**Phase 1.**

### D-05 🔴 PCR semantics are self-contradictory
- `KeyMetricsGrid.tsx:28` sets `pcrSentiment = "bull"` for PCR > 1.2; line 63 then renders
  the **text "Bearish"** inside the green `bull` badge. Line 44 adds a third reading:
  `pcr > 1 ? "Bearish tilt" : "Bullish tilt"`.
- `TrendingOITable` uses the **opposite** convention (PCR > 1.2 → red ▼ Bearish).
- Backend: `_safe_pcr(call_oi, put_oi)` and `safe_pcr(put_oi, call_oi)` take arguments in
  **opposite orders** (`analytics_service.py:83` vs `:146`). Both are internally consistent
  at their call sites, but it is a live footgun for any new caller.
**Impact:** two screens visibly disagree; a green badge reads "Bearish". This is a
credibility bug in a product whose value proposition is explainability.
**Fix:** write down the convention (high PCR = more puts open = conventionally bullish/
support-heavy, but **state it explicitly** rather than picking silently), define it once in
a shared helper, use it everywhere, and unit-test it. Standardize the backend argument order.
**Phase 1** — the brief calls this out directly in §19.

### D-06 🟠 Two incompatible bucket-flooring rules
`market_session.get_bucket_timestamp` floors in **IST** then converts to UTC.
`timeframe_service.bucket_start` floors in **UTC** directly.
**Impact:** the two agree for 1m/5m/15m/30m (which divide evenly into the 05:30 IST offset)
and **disagree at 1h**. Any code path mixing them at hourly resolution mis-buckets. This is
exactly the class of bug that silently corrupts backtests.
**Fix:** one canonical bucketing function, session-anchored (buckets should align to the
09:15 open, not to the wall clock). Unit-test across all timeframes and both conventions.
**Phase 1** — must be settled before Phase 4 research.

### D-07 🟠 Worker catch-up does not actually back-fill
`worker/scheduler.py:79-98` — `_run_catch_up` computes `missed_buckets(...)` and then calls
`_safe_run_job(...)`, which calls `run_snapshot_job`, which computes
`bucket_ts = get_bucket_timestamp(now, ...)`. The missed bucket timestamp is **never passed
through**.
**Impact:** the loop re-collects the *current* bucket up to 3 times. Missed buckets stay
missing. Restart gaps are permanent and undetected.
**Fix:** thread an explicit `bucket_ts` parameter into `run_snapshot_job`. Note that full
historical intraday option-chain state cannot be reconstructed for arbitrary past
timestamps from the Upstox APIs — the chain endpoint returns current state only — so the
honest fix is to record the gap in a data-quality table rather than pretend to fill it.
(Historical OI *can* be obtained for supported dates via the Upstox OI endpoint and is
useful for limited positioning backfill; it does not recover LTP, bid/ask, greeks or
depth for the missed bucket. See `docs/design/06-UPSTOX_INTEGRATION.md` §6.)
**Phase 1.**

### D-08 🟡 Collector does not stop on auth failure
`worker/jobs/snapshot_job.py:103-113` — on `UpstoxAuthError` the job is marked failed and
`auth_required` is emitted, but `is_running` stays true.
**Impact:** a failed Upstox call every interval until the user re-authenticates, burning
rate-limit budget and filling logs. The frontend registers an `auth_required` listener that
does nothing.
**Fix:** set `is_running=false` (or a `suspended` state) and handle `auth_required` in the UI.
**Phase 1.**

---

## Security

### D-09 🔴 `/api/auth/offline-session` is an unauthenticated session grant
`routes/auth.py:204` — unauthenticated; selects the user owning the newest snapshot (or the
most recently updated active user) and issues them a valid session.
**Impact:** acceptable for single-user localhost; a **complete authentication bypass** the
moment a second user exists or the app is exposed.
**Fix:** gate behind an explicit config flag defaulting to off, and hard-disable when
`app_env == "production"`.
**Phase 1** (flag) / **blocking for multi-user.**

### D-10 🟠 No session store, therefore no revocation
The cookie payload is literally `user:{id}`, signed (`auth.py:71`). `generate_session_id()`
in `security.py:48` is **dead code**.
**Impact:** logout only clears the client cookie — the signed value remains valid until TTL
expiry. No revocation, no "log out all devices", no server-side invalidation. The brief's
§36 permission separation (read / paper trade / live trade) has nothing to attach to.
**Fix:** real session records in Redis or Postgres. Required before live execution.
**Phase 6/7 prerequisite.**

### D-11 🟡 Rate limiter is in-process and unbounded
`main.py:39-58` — a `defaultdict` sliding window, self-described as a dev fallback. Never
evicts idle IPs (slow memory leak) and does not hold across replicas.
**Fix:** move to Redis. **Phase 2.**

### D-12 🟡 No cleanup for `oauth_states` or `outbox_events`
Neither expired CSRF tokens nor published events are ever deleted. Unbounded growth.
**Fix:** a periodic cleanup job on the existing scheduler. **Phase 1.**

---

## Discarded data and dead code

### D-13 🟠 ~70% of computed analytics are thrown away
`compute_snapshot_analytics` (`analytics_service.py:179`) returns per-strike
`call_oi_change_pct` / `put_oi_change_pct`, `call_ltp_change` / `put_ltp_change`,
`ce_signal` / `pe_signal`, `strike_pcr`, and four top-N ranked lists.
**Only `call_oi_change` and `put_oi_change` are persisted** (`snapshot_service.py:211-229`).
**Impact:** the work is done every cycle and discarded. More importantly, **you cannot
backtest a signal whose inputs were never stored** — this directly blocks Phase 4. Indexes
`ix_strikes_call_oi_change` / `ix_strikes_put_oi_change` exist to serve top-N queries that
no endpoint issues.
**Fix:** persist the full analytics output. **Phase 1–2.**

### D-14 🟡 Dead code and dead infrastructure
| Item | Location |
|---|---|
| `market_ticks` table | never read or written |
| `event_bus.publish_event()` | defined, called from nowhere |
| `generate_session_id()` | defined, unused (see D-10) |
| `access_token_iv` / `refresh_token_iv` | reserved for GCM, unused |
| `source='WS'` enum value | no WebSocket implementation |
| `instrument_type='FUTURES'` | nothing writes it — `/oi/futures` always empty |
| `lightweight-charts` | installed, **zero imports** |
| 6 Radix packages | dialog, dropdown-menu, separator, switch, tabs, tooltip — zero imports |
| `oiApi.heatmap/futures/optionsSummary/history/underlyings/timeframes`, `settingsApi.update`, `authApi.logout` | defined in `lib/api/client.ts`, called by nothing |
| `DashboardHeader` `priceChange`/`priceChangePct` props | implemented, never passed |
| `DataTable` column-filter state | wired, no UI exposes it |

### D-15 🟡 Docstrings contradict the implementation
- `security.py:3-8` claims "AES-256-GCM" — **Fernet is AES-128-CBC + HMAC-SHA256**. Also
  claims "all state lives in Redis" — there is no session store at all.
- `README.md:350` repeats the AES-256 claim.
- `stream.py`, `market_session.py`, `snapshot_job.py` docstrings all reference
  `collector_status_changed` events that are **never published**.
**Impact:** a future reader will make security decisions on false premises.
**Fix:** correct the text. Fernet is a perfectly good choice — only the description is wrong.
**Phase 1.**

---

## Frontend

### D-16 🔴 Expiry selectors are inert on every page
`app/(dashboard)/page.tsx`, `option-chain/page.tsx`, `trending-oi/page.tsx`,
`oi-history/page.tsx` all do `const selectedExpiry = expiries[0] ?? null` and render a
`<Select>` with **no `onValueChange`**.
**Impact:** back expiries are unreachable. Note this must be fixed **together with**
multi-expiry collection — today the control has nothing to select into.
**Fix:** shared expiry state (URL param or context) propagating through every query key.
**Phase 1** — brief §19.

### D-17 🟡 Underlyings hardcoded in four places
`["NIFTY","BANKNIFTY","SENSEX"]` is duplicated across four pages while `/oi/underlyings`
exists and is unconsumed.
**Phase 1.**

### D-18 🟡 Three stub pages over working backends
`/oi-heatmap`, `/oi-charts`, `/alerts` are 10-line placeholders. `/oi/heatmap` and
`/oi/history-bars` are **fully functional**.
**Phase 1** — brief §18.

### D-19 🟡 No error boundaries, no auth middleware, no tests
No `middleware.ts`, `loading.tsx`, `error.tsx`, or error boundary anywhere. Auth gating is
purely the axios 401 interceptor. Zero frontend tests.
**Phase 1–2.**

### D-20 🟡 `setInterval` shadowing
Three pages do `const [interval, setInterval] = useState(...)`, shadowing the global.
Harmless today, a trap later. **Phase 1.**

### D-21 🟡 Settings page fetches preferences and renders none
`prefs` is assigned and unused; no UI exists for `default_underlying`,
`default_interval_min`, `theme`, or `strike_window`. **Phase 1.**

---

## Architecture

### D-22 🟠 `save_snapshot` is a 330-line monolith
Owns locking, prev-snapshot lookup, analytics, three table writes, Phase-2 derivation, and
outbox emission — with two near-identical 40-line blocks for CE and PE.
**Impact:** the default destination for every new analytic. Refactor before Phase 2, not after.
**Fix:** split into `SnapshotWriter` / `BarDeriver` / `EventEmitter`; collapse the CE/PE
duplication by iterating legs (which the `OptionContract` entity makes natural).
**Phase 2.**

### D-23 🟠 Market data is user-scoped
`oi_snapshots.user_id` is part of `uq_snapshot`.
**Impact:** N users collecting NIFTY store N identical copies. Blocks a shared research
corpus and multiplies storage. **Phase 2** — decide before the dataset grows.

### D-24 🟠 Read-time aggregation duplicates the materialized store
`/oi/history-bars` re-aggregates raw snapshots in Python on every request while
`oi_time_bars` already materializes the same concept. Two code paths, one truth, able to
disagree. **Phase 2.**

### D-25 🟠 Front expiry only
`_resolve_active_expiry` returns the nearest expiry ≥ today. Blocks term structure,
cross-expiry GEX, calendar analysis — and makes D-16 unfixable in isolation.
**Fix:** subscription model for (underlying, expiry) pairs; cost against Upstox rate limits.
**Phase 2.**

### D-26 🟡 Expiry list re-fetched every cycle
`snapshot_job.py:94` calls `_resolve_active_expiry` — an Upstox round trip — on **every**
collection. Expiry lists change at most daily.
**Fix:** cache in Redis with a daily TTL. Frees rate-limit budget for D-25. **Phase 1.**

### D-27 🟡 Mixed logging, no metrics
`structlog` is configured but every service and route uses stdlib `logging`. No metrics, no
tracing, none of the observability the brief's §32 asks for. **Phase 2.**

### D-28 🟡 Dev servers in containers, no CI
`run_api.py` hardcodes `reload=True`; the frontend Dockerfile runs `next dev`. No CI, no
production build, no deployment config despite the README's Vercel/Railway diagram.
**Phase 2+**, blocking for anything beyond localhost.

---

## Testing

### D-29 🟠 Two test files total
`test_phase2_interpretation.py` and `test_phase2_timeframe.py` cover the pure timeframe and
interpretation helpers. **Not covered:** `compute_snapshot_analytics`, every route, the
snapshot writer, the outbox, SSE, the Upstox client, market-session logic, and the entire
frontend.
**Impact:** the brief's §35 requires tests for ΔOI, PCR, buildup, migration, velocity, GEX,
IV, regime, signals, risk rules, the order state machine and P&L — plus integration tests
and explicit look-ahead-bias tests. Essentially all of it is greenfield.
**Phase 1 onward, continuously.**

---

## Suggested ordering

**Phase 1 (correctness + activation — no architectural change):**
D-05 → D-03 → D-02 → D-04 → D-06 → D-16 → D-17 → D-18 → D-13(partial) → D-07 → D-08 →
D-26 → D-12 → D-09(flag) → D-15 → D-29(start)

**Phase 2 (market intelligence):** D-01 → D-22 → D-23 → D-25 → D-24 → D-11 → D-27 → D-28

**Later:** D-10 and D-19 before any live execution; D-14 cleanup opportunistically.
