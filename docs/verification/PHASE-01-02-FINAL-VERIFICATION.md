# Phase 1 and Phase 2 final external verification

**Status: `FAIL`**

Commit `0a960df` was verified on this machine. Implementation code was not modified. Phase 3 was not started. No `DROP` or `TRUNCATE` was issued against `oi_pulse`. `alembic_version` was not edited by hand.

## 1. Revision

| Command | Result |
|---|---|
| `git rev-parse HEAD` | `0a960df349f195c4dd556745db388bb4ae906a2b` |
| `git branch --show-current` | `main` |
| `git log -1 --oneline` | `0a960df Remediate Phase 1/2 verification failures` |
| `git status` | `main` matches `origin/main`. Untracked verification reports only. |

Expected commit `0a960df` matches.

## 2. Environment

| Item | Value |
|---|---|
| OS | macOS 26.6.2, Darwin 25.6.0, arm64 |
| Python | 3.12.14 |
| pip | 26.2.1 |
| PostgreSQL | 16.14 (`oi-pulse-postgres`) |
| Redis | 7.4.10 (`oi-pulse-redis`) |
| fastapi | 0.115.0 |
| uvicorn | 0.30.6 |
| sqlalchemy | 2.0.35 |
| asyncpg | 0.29.0 |
| alembic | 1.13.3 |
| redis | 5.0.8 |
| hiredis | 3.4.2 |
| pydantic | 2.13.5 (transitive from FastAPI) |
| pytest | 8.3.3 |
| pytest-asyncio | 0.24.0 |
| ruff | 0.16.5 |
| mypy | 1.13.0 |
| import-linter | 2.1 |
| websockets | 17.1 (via `uvicorn[standard]`, not a direct dependency) |
| httpx | 0.27.2 in the verifier venv only. **Not declared** in `pyproject.toml`. |
| psycopg | 3.2.3 in the verifier venv only. **Not declared.** Alembic's environment opens a synchronous engine, so the project `postgresql+asyncpg` URL cannot drive `alembic upgrade`. |

No secrets are included in this report.

## 3. Phase 1 quality gates

| Command | Result |
|---|---|
| `pytest -q` | **PASS.** 152 passed. |
| `ruff check oipulse tools tests` | **PASS.** |
| `ruff format --check oipulse tools tests` | **PASS.** 64 files. |
| `mypy --strict oipulse` | **FAIL.** 12 errors in 7 files. |
| `lint-imports` | **PASS.** 2 contracts kept, 0 broken. |
| `python -m compileall -q oipulse tools tests` | **PASS.** |
| `python tools/check_clock_access.py oipulse` | **PASS.** |
| `python tools/check_import_boundaries.py` | **PASS.** |
| `python tools/check_temporal_repository.py oipulse` | **PASS.** |

mypy errors:

- `oipulse/observability/metrics.py`: missing type parameters on `tuple` and `dict` (5)
- `oipulse/marketdata/providers/upstox/provider.py`: unparameterized `dict`
- `oipulse/marketdata/collector.py`: `Any` returned as `int`
- `oipulse/marketdata/providers/upstox/ws.py`: `Any` returned as `str`; unreachable statement
- `oipulse/marketdata/store/schema.py`: unparameterized `Column`
- `oipulse/marketdata/store/postgres.py`: `ReturningInsert` assigned to `Insert`
- `oipulse/marketdata/providers/upstox/rest.py`: `Any` returned as `dict[str, Any]`

The required mypy check was not skipped. The gate is not PASS.

## 4. API entrypoint

`python -m oipulse.run --help` exit 0. Help matches `oipulse/run.py`: `--role`, `--host`, `--port`, `--check`, `--reload`.

`python -m oipulse.run --check` exit 0. Log event `configuration_valid`, `role=api`, `app_env=development`, `live_trading_env_gates_open=false`. No URL, token, or secret was logged.

`python -m oipulse.run --role api --host 127.0.0.1 --port 8011` started and was stopped after the probes. The code default host is `0.0.0.0`; loopback was used for this check.

| Request | Result |
|---|---|
| `GET /ops/health` | `200 {"status":"ok"}` |
| `GET /ops/ready` | `200 {"ready":true,"checks":{}}` |

Readiness is true because the check registry is empty. This process did not probe PostgreSQL or Redis. Startup logs did not contain tokens or database URLs. The logged API role field was `all` (the settings default) while the process was started with `--role api`.

`python -m oipulse.run --role ingestor` exit **4**: `role 'ingestor' has no runtime yet`. There is no supported command that starts the collector.

## 5. Clean-database migration

Disposable database `oipulse_final_verify` on the same PostgreSQL 16 server. It was dropped and recreated for this pass. `oi_pulse` was not used.

`alembic history`:

```text
<base> -> 001
001 -> 002
002 -> 0001_phase1_sys_tables
0001_phase1_sys_tables -> 0002_phase2_market_data (head)
```

`alembic upgrade head` ran `001`, `002`, and `0001_phase1_sys_tables`, then failed inside `0002_phase2_market_data` at line 307:

```text
psycopg.errors.UndefinedTable: relation "obs_depth" does not exist
SQL: CREATE TABLE IF NOT EXISTS obs_depth_20260101 PARTITION OF obs_depth
     FOR VALUES FROM ('2026-01-01') TO ('2026-01-02')
```

Root cause: `_PARTITIONED` is `("obs_quotes", "obs_greeks", "obs_depth")`. The upgrade loop creates daily partitions for every name in that tuple **before** `op.create_table("obs_depth", ...)`. `obs_quotes` and `obs_greeks` already exist at that point. `obs_depth` does not. A second partition call exists after `create_table("obs_depth")` and is never reached.

`oipulse/migrations/env.py` wraps `run_migrations()` in one transaction. After the failure, `alembic current` is empty and `alembic_version`, `obs_depth`, `obs_quotes`, `sys_outbox`, and `instrument_options` are all absent.

**Final revision: none. Head was not reached.**

## 6. Legacy database

Exact `COUNT(*)` before `alembic upgrade head` on `oi_pulse`:

| Table | Count |
|---|---|
| `alembic_version` | `002` |
| `users` | 1 |
| `upstox_accounts` | 1 |
| `instruments` | 3 |
| `option_expiries` | 43 |
| `oi_snapshots` | 147 |
| `oi_strike_snapshots` | 19894 |
| `oi_time_bars` | 39788 |
| `market_data_events` | 39788 |
| `outbox_events` | 147 |
| `audit_logs` | 2 |
| `collector_jobs` | 5 |

The upgrade started at `002`, entered `0001_phase1_sys_tables`, then failed on the same `obs_depth` partition statement. The transaction rolled back.

After:

| Item | Value |
|---|---|
| `alembic_version` | `002` |
| `users` | 1 |
| `oi_snapshots` | 147 |
| `oi_strike_snapshots` | 19894 |
| `oi_time_bars` | 39788 |
| `market_data_events` | 39788 |
| `outbox_events` | 147 |
| `audit_logs` | 2 |
| `sys_outbox`, `obs_quotes`, `obs_depth`, `instrument_options` | absent |

No manual edit of `alembic_version`. No `DROP`. No `TRUNCATE`. The chain did not stay at `0001` or reach `0002`.

## 7. Migration round-trip

Not run. There is no committed head on the disposable database to downgrade from. `alembic current` there is empty after the rolled-back upgrade.

## 8. Phase 2 schema parity

Not inspectable as a migrated schema. The migration source declares the previously missing objects, and PostgreSQL never created them:

- `instrument_options`, `instrument_futures`
- `obs_depth` (the statement that fails)
- `obs_ohlc`, `obs_index` (created after the failing loop)
- GiST exclusion constraints on instrument versions and vendor mappings
- provider-event uniqueness on `(provider_event_id, COALESCE(feed_session_id, ''))` plus the partition key

Those constraints were not created, so their semantics were not verified in PostgreSQL.

## 9–11. Identity, universe, planner

`pytest` (152 passed) covers in-process canonical identity, multi-expiry selectors, and SubscriptionPlanner.

Planner results reproduced in this pass with the default `SubscriptionBudget` (`source=phase2-assumption:A-5 (unverified against live provider)`):

| Input | Result |
|---|---|
| 500 greeks | `ACCEPTED: 500/500` across 1 connection. Subscribe allowed. |
| 3000 greeks, 5 protected | `DEGRADED: 1691/3000` after `narrow_strike_band`, `drop_back_expiries`. |
| 2000 full + 100 greeks, 3 protected | `DEGRADED: 1576/2100` across 2 connections after `drop_depth_far_strikes`, `narrow_strike_band`. |
| 100 full + 100 greeks | `ACCEPTED: 200/200` across 2 connections. |
| 50000 greeks, all protected | `UNSATISFIABLE: 0/50000`. Subscribe refused. |
| Configured budget of 100 LTPC/greeks, 50 instruments | `ACCEPTED`. Same budget, 5000 greeks with 2 protected | `UNSATISFIABLE`. |

Planning is a pure function and does not open a socket. The collector that would subscribe afterward cannot be started (`--role ingestor` exits 4). Live Upstox limits were not confirmed.

No real instrument metadata was loaded. The stored ciphertext in `upstox_accounts` (updated `2026-08-14`) does not decrypt with the current `TOKEN_ENCRYPTION_KEY` (`InvalidToken`). There is no separate access-token variable in the environment. No contract, expiry, chain, quote, Greek, OI, or futures payload was retrieved. No `RECORDED_UPSTOX` fixture was written.

## 12–17 and 19–21. Live feed, identity, recovery, soak, PostgreSQL idempotency

Not executed.

- WebSocket authorize, subscribe, receive, reconnect, and recovery were not run.
- Whether `provider_event_id` is global or session-scoped was not observed on the provider.
- Whether `channel_sequence` exists on the live feed was not observed.
- The collector process cannot be started.
- No market-session row counts were captured. NSE was in session at the time of this pass; the blocker is credentials and the missing ingestor runtime, plus the migration never creating the observation tables.
- PostgreSQL duplicate-insert and concurrent-writer tests were not run, because `0002` never committed. The 11:40/11:44 late-arrival case was not replayed in PostgreSQL. The same behaviour is covered by passing in-memory unit tests inside the 152.

The migration source scopes tier-1 uniqueness with `COALESCE(feed_session_id, '')`. That index was not created, and no live replay compared two sessions, so the A-13 silent-loss fix is not runtime-verified.

## 18. Bitemporal and historical OI

Not exercised on PostgreSQL. In-memory tests inside `pytest` passed, including knowledge-time filtering and the requirement that historical daily OI carry a validity interval. That is not a substitute for a committed schema.

## 22. Observability

API `--check` and startup logs did not contain tokens, keys, or database URLs.

Phase 2 ingestion, WebSocket, gap, recovery, duplicate, and rate-limit metrics were not produced by a running collector.

## 23. Scope leakage

`oipulse/` packages: `api`, `core`, `dataquality`, `events`, `instruments`, `marketdata`, `migrations`, `observability`, `persistence`.

No MarketState, analytics registry, signals, research engine, replay, backtest, paper trading, risk engine, OMS, live order path, portfolio attribution, or terminal package.

## 24. Result table

| Requirement | Result | Evidence |
|---|---|---|
| Phase 1 tests | PASS | `pytest -q`: 152 passed |
| mypy | FAIL | `mypy --strict oipulse`: 12 errors in 7 files |
| import-linter | PASS | 2 kept, 0 broken |
| API entrypoint | PASS | `--help`, `--check`, `/ops/health` 200, `/ops/ready` 200 with empty checks. Ingestor exits 4. |
| clean migration | FAIL | `0002` dies on `PARTITION OF obs_depth` before the parent exists. Revision does not reach head. |
| legacy migration | FAIL | Same error. Database remains `002`. Exact counts unchanged. |
| Phase 2 schema | FAIL | `obs_depth`, `obs_ohlc`, `obs_index`, `instrument_options`, `instrument_futures` were not created. |
| canonical identity | FAIL | Unit tests pass. Stored token does not decrypt with the current key. No live metadata. |
| multi-expiry | FAIL | Selector unit tests pass. No live expiry calendar. |
| SubscriptionPlanner | PASS | ACCEPTED, DEGRADED, and UNSATISFIABLE reproduced. Live budgets not confirmed. |
| REST | FAIL | No authenticated call. Ciphertext decrypt failed. |
| WebSocket | FAIL | Not connected. Ingestor role has no runtime (exit 4). |
| A-13 identity | FAIL | Session-scoped unique index is in source only. No two live sessions compared. |
| sequence semantics | FAIL | No live frames. |
| reconnect/recovery | FAIL | Collector was not started. |
| bitemporal behavior | FAIL | Not exercised on PostgreSQL. In-memory tests passed inside the 152. |
| PostgreSQL idempotency | FAIL | Schema never committed. |
| restart | FAIL | No collector process. |
| market-session collection | FAIL | Not started. |
| observability | FAIL | API logs are free of secrets. Phase 2 metrics were not emitted by a collector. |
| scope leakage | PASS | No Phase 3+ modules or live orders. |

**Status: `FAIL`.**
