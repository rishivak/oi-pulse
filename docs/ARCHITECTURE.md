# OI Pulse — Architecture (Phase 0 Audit)

> **Status:** as-built description of the repository at commit `a0621dd`.
> Everything here was verified against source, not inferred from filenames.
> Aspirational/roadmap items are marked explicitly.

---

## 1. What the system is today

A self-hosted options analytics terminal. A background worker polls the Upstox
Option Chain REST API on a fixed interval during NSE market hours, stores per-strike
Open Interest snapshots in PostgreSQL, computes OI deltas / PCR / buildup classifications
at write time, and pushes change notifications to a Next.js dashboard over Server-Sent
Events fanned out through Redis pub/sub.

Scale: ~4.2k LOC backend (42 Python files), ~33 TS/TSX files frontend, 14 tables,
2 Alembic revisions, 2 test files.

---

## 2. Process topology

Five containers, orchestrated by scripts rather than compose (see §7):

| Container | Role | Port |
|---|---|---|
| `oi-pulse-nginx` | Reverse proxy; single browser entrypoint | 8080 |
| `oi-pulse-frontend` | Next.js dev server | 3000 |
| `oi-pulse-api` | FastAPI / uvicorn | 8000 |
| `oi-pulse-worker` | APScheduler collector — **no HTTP surface** | — |
| `oi-pulse-postgres` | PostgreSQL 16 | 5432 |
| `oi-pulse-redis` | Redis 7 | 6379 |

nginx proxies `/` → frontend and `/api/` → api with `proxy_buffering off` and
`proxy_read_timeout 3600s` specifically so SSE survives. Everything is same-origin on
`localhost:8080` so the OAuth callback and the session cookie agree on origin.

The API and the worker are **peers, not parent/child**. They share only PostgreSQL and
Redis. The worker never calls the API; the API never dispatches to the worker. Their sole
coupling is the `collector_jobs` table: the API writes `is_running`, the worker polls it.

`backend/run_worker.py` warns that only one worker instance may run per database —
job dispatch is not distributed-safe beyond the per-bucket Redis lock.

---

## 3. Backend module map

```
backend/
├── run_api.py              uvicorn entry — NOTE: reload=True hardcoded
├── run_worker.py           scheduler entry, structlog, SIGINT/SIGTERM handling
├── app/
│   ├── main.py             app factory: CORS, rate limiter, routers, error handler
│   ├── core/
│   │   ├── config.py       pydantic-settings; SUPPORTED_UNDERLYINGS, instrument keys
│   │   └── security.py     Fernet token crypto + itsdangerous cookie signing
│   ├── db/
│   │   ├── engine.py       async engine, Base, get_db
│   │   └── models/         user, instrument, snapshot, market_data, operational
│   ├── integrations/upstox/
│   │   ├── client.py       REST client: retry, backoff, 401/429 handling
│   │   └── schemas.py      Pydantic models of Upstox responses
│   ├── services/
│   │   ├── snapshot_service.py   persistence + idempotency  (the heart, 422 ln)
│   │   ├── analytics_service.py  pure analytics              (255 ln, tested)
│   │   ├── timeframe_service.py  pure bucketing/aggregation  (116 ln, tested)
│   │   ├── market_session.py     IST calendar                (109 ln)
│   │   └── event_bus.py          outbox flush + SSE subscribe (139 ln)
│   └── api/
│       ├── deps.py         CurrentUser / UpstoxToken / DB annotated dependencies
│       └── routes/         auth, oi, collector, settings, stream
├── worker/
│   ├── scheduler.py        AsyncIOScheduler — 2 jobs + startup catch-up
│   └── jobs/snapshot_job.py  one collection cycle
└── alembic/versions/       001_initial_schema, 002_phase2_market_data_schema
```

### Layering

The separation is genuinely clean in one important respect: **`analytics_service.py` and
`timeframe_service.py` are pure and IO-free by deliberate design**, which is why they are
the only two modules with tests. This is the correct foundation for the analytics
expansion and must be preserved.

The weak boundary is `snapshot_service.save_snapshot`: it owns locking, previous-snapshot
lookup, analytics invocation, three separate table writes, Phase-2 bar derivation, and
outbox emission in a single 330-line function. This is the main refactor target — not
because it is wrong, but because every new analytic will otherwise be appended to it.

---

## 4. Frontend module map

```
frontend/
├── next.config.mjs         rewrites /api/* → NEXT_PUBLIC_API_URL (same-origin cookies)
├── app/
│   ├── (auth)/login/       Upstox connect + "continue with stored data"
│   └── (dashboard)/
│       ├── page.tsx        Dashboard — KeyMetricsGrid
│       ├── trending-oi/    time-bucket table
│       ├── option-chain/   CE ‖ strike ‖ PE table
│       ├── oi-history/     timeframe bars table
│       ├── settings/       collector control + Upstox connection
│       ├── oi-heatmap/     STUB (10 lines)
│       ├── oi-charts/      STUB (10 lines)
│       └── alerts/         STUB (10 lines)
├── components/
│   ├── ui/                 shadcn-style: badge, button, card, select, table
│   ├── common/             DataTable, IntervalSelector, StrikeRangeSelector
│   ├── layout/             MainNav, DashboardHeader (+ ConnectionIndicator)
│   ├── dashboard/          KeyMetricsGrid
│   ├── option-chain/       OptionChainTable
│   └── trending-oi/        TrendingOITable
└── lib/
    ├── api/client.ts       axios, baseURL "/api", 401 → /login
    ├── api/queryKeys.ts    centralized query-key factory
    ├── realtime/sse-client.ts  singleton EventSource + market-hours-aware reconnect
    ├── types/index.ts      full domain typing
    └── utils/formatters.ts fmtLakh, fmtPCR, deltaArrow, oiSignalLabel, …
```

State: TanStack Query for server state, `useState` per page for local state. No global
store. Auth gating is **client-side only** — the axios 401 interceptor redirects to
`/login`. There is no `middleware.ts`, no `loading.tsx`, no `error.tsx`, no error boundary.

---

## 5. Request/response paths

**Read path (browser → data)**
```
Component → TanStack Query → axios (/api/...) → nginx → FastAPI route
  → get_current_user (unsign cookie) → SQLAlchemy → Postgres → serializer → JSON
```

**Live path (worker → browser)**
```
worker writes OutboxEvent in the snapshot transaction
  → scheduler flush_outbox (every 2s) → Redis PUBLISH events:{user_id}
  → API subscribe_sse generator → StreamingResponse → EventSource
  → SSE listener → queryClient.invalidateQueries → refetch over the read path
```

Note the live path carries a **notification**, not the full dataset. The `snapshot_created`
payload contains headline numbers (spot, totals, PCR, net ΔOI) which `KeyMetricsGrid` uses
directly, but per-strike data always comes from a follow-up REST fetch.

**Auth path**
```
GET /api/auth/login → create OAuthState row (10-min TTL) → 302 to Upstox dialog
  → user approves → GET /api/auth/callback?code&state
  → validate + consume state → exchange code for token → fetch profile
  → upsert User + UserPreference + UpstoxAccount (Fernet-encrypted token)
  → audit_log "oauth_login" → Set-Cookie session_id → 302 to app root
```

---

## 6. Cross-cutting concerns

### Security boundary
The Upstox access token never leaves the backend — enforced structurally, since the
browser only ever talks to OI Pulse's own API. Tokens are Fernet-encrypted at rest.
`audit_logs` records `oauth_login`, `offline_login`, `logout` and is explicitly token-free.

Two docstring inaccuracies worth correcting (see TECHNICAL_DEBT.md D-14, D-15):
`security.py` claims AES-256-GCM (Fernet is AES-128-CBC + HMAC-SHA256) and claims
"all state lives in Redis" (there is no session store at all — the cookie payload is
literally the string `user:{id}`, signed).

### Configuration
`pydantic-settings` reads `.env`. Required with no default: `DATABASE_URL`,
`TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET_KEY`, `UPSTOX_CLIENT_ID`,
`UPSTOX_CLIENT_SECRET`, `UPSTOX_REDIRECT_URI`. A validator rejects the literal
placeholder `REPLACE_WITH_FERNET_KEY`. `get_settings()` is `@lru_cache`'d.

### Error handling
Three layers: the Upstox client raises typed errors (`UpstoxAuthError`,
`UpstoxRateLimitError`, `UpstoxError`); `snapshot_job` catches per-cycle and records
status on `collector_jobs`; `_safe_run_job` is a catch-all so one bad job never kills the
scheduler. The API has a global 500 handler that logs and returns a generic body.

### Logging
`structlog` is configured in `run_worker.py` and imported in `main.py`, but every service
and route module uses **stdlib `logging`** instead. Log output is therefore mixed-format.
There are no metrics and no tracing.

### Rate limiting
An in-process `defaultdict` sliding window (120 req / 60 s per IP) in `main.py`. Explicitly
labelled a "Redis-free fallback for dev" — it does not hold across replicas and it grows
unboundedly (no eviction of idle IPs).

---

## 7. Deployment reality vs. the README

The README diagrams a Vercel + Railway + Supabase + Upstash topology. **No artifact in the
repository supports this** — there is no `vercel.json`, `railway.toml`, `Procfile`, or CI
workflow. That topology is aspirational.

What actually exists is local-only, and both containers run **development servers**:
`run_api.py` hardcodes `uvicorn.run(..., reload=True)`, and the frontend Dockerfile's CMD
is `next dev`. Migrations run automatically — the backend Dockerfile is
`alembic upgrade head && python run_api.py`.

Compose files exist (`infra/docker-compose.yml`, `infra/podman-compose.yml`) but are not
the blessed path; `infra/scripts/podman-up.sh` issues raw `podman run` calls instead,
because (per the README) `podman compose` on Windows may delegate to a non-Docker-free
provider.

---

## 8. Architectural assessment

### Keep — this is good work
- **Idempotent ingestion.** `uq_snapshot` + Redis `SET NX EX` lock + `ON CONFLICT DO UPDATE`
  means replaying a bucket is safe. This is the single most valuable property in the codebase
  and every future ingestion path must preserve it.
- **Transactional outbox.** The event row is written in the same transaction as the snapshot,
  then drained by a poller using `FOR UPDATE SKIP LOCKED`. No lost events on crash between
  commit and publish.
- **Pure analytics core.** IO-free, deterministic, unit-tested. Exactly the right shape for
  the Market State Engine to build on.
- **Server-side-only broker credentials.** Structurally enforced.
- **Market-session awareness on both ends** — the collector skips closed markets; the SSE
  client pauses reconnection outside hours.

### Change — structural limits on the target architecture
1. **Front-expiry only.** `_resolve_active_expiry` returns the nearest expiry ≥ today.
   Term structure, cross-expiry GEX, and calendar analysis are all blocked on this.
2. **REST polling only.** `source='WS'` is a reserved enum value with no implementation.
   Sub-minute state and true intraday replay need the WebSocket feed.
3. **Snapshots are user-scoped.** `oi_snapshots.user_id` means market data is stored per
   user. Market data is not user-specific; this will duplicate rows per user and complicates
   a shared research dataset. Needs resolution before multi-user.
4. **Derived analytics are discarded.** `compute_snapshot_analytics` computes per-strike
   signals, percentage changes, and top-N rankings every cycle; only totals, PCR, and two
   ΔOI columns are persisted.
5. **Greeks are discarded.** `delta`/`gamma`/`theta`/`vega` are parsed into
   `UpstoxOptionGreeks` and dropped. GEX is blocked on a migration, not on a data source.
6. **No market-state concept.** Analytics are computed per-request or at write time and
   never materialized as a first-class, queryable state object.
7. **`save_snapshot` is a monolith** — the natural place every new analytic gets appended.

### Target shape (detail in the migration plan)
A **modular monolith**, not microservices. Same two processes (API + worker), same
Postgres/Redis, but with internal package boundaries:

```
market_data/     provider abstraction, canonical models, REST + WS ingestion
market_state/    MarketState assembly and persistence
analytics/       positioning, volatility, price, greeks  (pure, tested)
signals/         signal model, evidence, deterministic rules
alerts/          evaluator + delivery (reuses the existing outbox)
research/        experiments, forward returns, backtests
replay/          point-in-time reconstruction (no look-ahead)
paper_trading/   PaperBrokerAdapter behind the same TradeIntent interface
risk/            independently testable limit engine
execution/       order manager + state machine  (last, behind a feature flag)
portfolio/       positions, P&L, attribution
```

The `MarketDataProvider` / `BrokerAdapter` interfaces are what make this tractable:
`UpstoxMarketDataProvider` and `PaperBrokerAdapter` first, `UpstoxBrokerAdapter` last and
feature-flagged.
