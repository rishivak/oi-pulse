# OI Pulse — API Inventory (Phase 0 Audit)

All routes verified against `backend/app/api/routes/`. Every endpoint except
`/api/health` and the OAuth entry points requires the `session_id` cookie via
`CurrentUser`.

**Legend** — 🟢 in use by the frontend · 🟡 built but unconsumed · 🔴 built but broken/empty

---

## `/api/health`
| | |
|---|---|
| `GET /api/health` | 🟢 `{"status":"ok"}`. Used by the startup scripts to poll readiness. |

---

## `/api/auth` — `routes/auth.py`

| Route | | Behaviour |
|---|---|---|
| `GET /login` | 🟢 | Generates a 32-byte urlsafe CSRF `state`, persists an `OAuthState` row (10-min TTL, `session_key` = client IP), 302s to the Upstox dialog. |
| `GET /callback` | 🟢 | Validates `state` (unexpired + unused) → marks consumed → exchanges code for token → fetches profile → upserts `User` + `UserPreference` + `UpstoxAccount` (Fernet-encrypted) → writes `oauth_login` audit row → sets signed `session_id` cookie → 302 to app root. Errors redirect to `/login?auth_error=…` with mapped codes: `oauth_denied`, `invalid_request`, `invalid_state`, `upstox_inactive_segments` (from Upstox error `UDAPI100058`), `token_exchange_failed`. |
| `POST /offline-session` | 🟢 | "Stored-data mode". Issues a session **without** Upstox by selecting the user who owns the newest OI snapshot, falling back to the most recently updated active user. 404 if none. Audit-logs `offline_login`. |
| `POST /logout` | 🟡 | Audit-logs and deletes the cookie. `authApi.logout` exists in the client; **no UI calls it** — there is no logout button anywhere. |
| `GET /me` | 🟢 | User + `live_market_access` · `access_mode` (`live`\|`stored`) · `upstox_connected`. |

> **Security note on `/offline-session`:** it is unauthenticated and hands the caller a
> valid session for whichever user owns the most recent snapshot. Acceptable for a
> single-user self-hosted deployment; it is a full authentication bypass the moment a
> second user exists. Must be gated before any multi-user work.

---

## `/api/oi` — `routes/oi.py` (612 lines, the functional core)

| Route | | Behaviour |
|---|---|---|
| `GET /timeframes` | 🟡 | `1m,5m,15m,30m,1h`, default `30m`. Frontend hardcodes this list instead. |
| `GET /underlyings` | 🟡 | NIFTY, BANKNIFTY, SENSEX. **Frontend hardcodes the same list in four separate places.** |
| `GET /expiries` | 🟢 | Live path calls Upstox `/option/contract` and syncs new dates into `option_expiries`. On `UpstoxError` or missing token, falls back to stored expiries (union of `option_expiries` and expiries referenced by the user's snapshots). |
| `GET /latest` | 🟢🔴 | Most recent snapshot + all strike rows. **Bug:** `expiry_date` is a required query parameter and is never applied to the query (`oi.py:116-126`). Harmless today because only the front expiry is ever collected; returns the wrong expiry the moment multi-expiry lands. |
| `GET /history` | 🟡 | Last N (≤500) snapshot summaries ascending. Correctly filters by expiry. |
| `GET /trending` | 🟢 | Rows for the Trending-OI table; ΔOI computed at read time by diffing consecutive snapshots in Python. |
| `GET /strikes` | 🟢 | Latest snapshot's strike rows, optionally windowed to ±`atm_range` around the strike nearest spot. |
| `GET /heatmap` | 🟡 | Thin wrapper over `strike_oi(atm_range=0)` reshaped to `{strike, call_oi_change, put_oi_change}`. **Fully functional; the page is a 10-line stub.** |
| `GET /history-bars` | 🟢 | Aggregates snapshots into timeframe bars at read time: open/close LTP, LTP change, OI OHLC, ΔOI (with prev-bucket-close fallback), `interpretation`, plus `total_call_oi`, `total_put_oi`, `call_oi_change`, `put_oi_change`, `pcr`, `oi_change_pcr`. Accepts `for_date` — **the existing hook for market replay**. |
| `GET /futures` | 🔴 | Reads `oi_time_bars WHERE instrument_type='FUTURES'`. **Always returns empty — no pipeline ever writes FUTURES rows.** |
| `GET /options/summary` | 🟡 | CE/PE aggregate for a given or latest bucket: totals, ΔOI both sides, `pcr`, `oi_change_pcr`. |

Serializers at the file's end expose per-strike `call_oi`/`put_oi`/`ltp`/`volume`/`iv`/`oi_change`.

---

## `/api/collector` — `routes/collector.py`

| Route | | Behaviour |
|---|---|---|
| `POST /start` | 🟢 | Validates underlying ∈ `SUPPORTED_UNDERLYINGS` and interval ∈ `BucketInterval` {1,3,5,10,15,30}; **403 unless live Upstox access**; upserts `collector_jobs` on `uq_collector_job` with `is_running=true`. |
| `POST /stop` | 🟢 | Sets `is_running=false`. |
| `GET /status` | 🟢 | All of the user's jobs with `last_run_at` / `last_status`. Polled every 10 s by Settings. |

> Intervals **3** and **10** are accepted here but map to `None` in
> `_interval_to_timeframe()`, so selecting them silently disables all Phase-2
> (`market_data_events` / `oi_time_bars`) persistence. See TECHNICAL_DEBT.md D-03.

---

## `/api/settings` — `routes/settings.py`

| Route | | Behaviour |
|---|---|---|
| `GET ""` | 🟡 | User preferences; lazily creates the row. Settings page fetches it into an **unused variable**. |
| `PATCH ""` | 🟡 | Updates preferences. **No UI calls it.** |

---

## `/api/stream` — `routes/stream.py`

| Route | | Behaviour |
|---|---|---|
| `GET /events` | 🟢 | SSE `StreamingResponse` over `subscribe_sse(user.id)` with `X-Accel-Buffering: no`. |

Event types the client registers vs. what the backend emits:

| Event | Emitted? | Handled? |
|---|---|---|
| `connected` | ✅ | ✅ |
| `snapshot_created` | ✅ | ✅ |
| `auth_required` | ✅ | ❌ registered, no-op — should force re-login |
| `collector_status_changed` | ❌ never | ❌ — would replace the 10 s Settings poll |
| `market_price_updated` | ❌ never | ❌ |
| `oi_alert` | ❌ never | ❌ — waiting on the alert engine |

---

## Summary

**19 endpoints. 11 consumed by the frontend, 7 built and unconsumed, 1 structurally empty.**

The highest-leverage observation for Phase 1: `/oi/heatmap`, `/oi/history-bars`,
`/oi/options/summary`, `/oi/underlyings`, `/oi/timeframes` and `PATCH /settings` are all
working backend capability behind "Coming soon" stubs or hardcoded frontend constants.
**Activate before building new endpoints** — per the brief's §18 and §37.

## Missing for the target architecture

| Domain | Endpoints needed |
|---|---|
| Market state | `GET /market-state/latest`, `GET /market-state/history`, `GET /market-state/{ts}` |
| Positioning | `GET /positioning/walls`, `/migration`, `/velocity`, `/concentration`, `/top-changes` |
| Greeks | `GET /greeks/gex`, `/gex/profile`, `/exposure` |
| Volatility | `GET /volatility/skew`, `/term-structure`, `/atm-iv`, `/rank` |
| Futures | `GET /futures/bars`, `/futures/oi` (plus the ingestion that fills them) |
| Regime | `GET /regime/current`, `/regime/history` |
| Signals | `GET /signals`, `GET /signals/{id}` (with evidence), `GET /signals/types` |
| Alerts | full CRUD + `POST /alerts/{id}/test`, `GET /alerts/history` |
| Research | `POST /research/experiments`, `GET /research/experiments/{id}/results` |
| Backtesting | `POST /backtest/runs`, `GET /backtest/runs/{id}` |
| Replay | `GET /replay/session`, `GET /replay/state-at` (point-in-time, no look-ahead) |
| Paper trading | `POST /paper/intents`, `GET /paper/orders`, `/positions`, `/pnl` |
| Risk | `GET /risk/limits`, `PUT /risk/limits`, `GET /risk/status`, `POST /risk/kill-switch` |
| Portfolio | `GET /portfolio`, `/pnl`, `/attribution`, `/greeks` |
| Journal | CRUD |
| Data quality | `GET /data-quality/status`, `/gaps` |
| Ops | `GET /metrics` |
