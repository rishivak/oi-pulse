# Phase 2 independent verification

**Gate status: `PARTIAL`**

**`PHASE 2: NOT VERIFIED`**

Mandatory live runtime evidence is missing. Offline tests and a disposable-database migration passed. The stored Upstox access token is rejected by the provider (`401`, `UDAPI100050`). No WebSocket session, no recorded provider fixture, and no market-window soak were produced.

Claude's implementation was not modified. No fix branch was created. Phase 3 was not started. The live database `oi_pulse` was not dropped, truncated, or migrated.

## Commit verified

| Item | Value |
|---|---|
| SHA | `f6ec506682450b79c4301fe2aaed78be25eb4b2e` |
| Subject | Phase 2 — Canonical market data (implementation; external verification pending) |
| Branch | `main` (up to date with `origin/main`) |
| Working tree | clean at the start of verification |
| Parent | `c04aa33` (Phase 1 merge) |

This report file is the only repository change made by the verifier.

## Environment

| Item | Value |
|---|---|
| OS | macOS 26.6.2 (Darwin 25.6.0, arm64) |
| Python | 3.12.14 (`/opt/homebrew/bin/python3.12`) |
| PostgreSQL | 16.14, container `oi-pulse-postgres`, volume `oi_pulse_postgres_data` |
| Redis | 7.4.10, container `oi-pulse-redis` |
| Live app database | `oi_pulse`, Alembic revision `002` (legacy). Left unchanged. |
| Disposable database | `oipulse_phase2_verify` on the same server. Created, migrated, downgraded, and migrated again. |
| Upstox SDK | none. Phase 2 uses a hand-written client. |
| Stored token | `upstox_accounts.id=1`, ciphertext 504 bytes, `updated_at` 2026-08-14. Decrypts with `TOKEN_ENCRYPTION_KEY`. Provider rejects it. |

Verification-only packages installed in `/tmp/oipulse-phase1-venv`, not added to the project:

| Package | Why |
|---|---|
| `psycopg==3.2.3` | Alembic `env.py` opens a synchronous engine. The project URL is `postgresql+asyncpg`, which does not complete a sync connect. |
| `greenlet==3.1.1` | Tried first with the asyncpg URL. SQLAlchemy still raised `MissingGreenlet`. |
| `httpx==0.27.2` | Not declared in `pyproject.toml`. `UpstoxRestClient` imports it lazily. Installed only to attempt the live probe. |
| `cryptography==43.0.1` | Not an `oipulse` dependency. Used only to decrypt the legacy stored token. The token value was not printed. |

## Dependencies (declared)

Installed with `pip install -e ".[dev]"` from `pyproject.toml`.

| Package | Version | Declared |
|---|---|---|
| fastapi | 0.115.0 | yes |
| uvicorn | 0.30.6 | yes |
| sqlalchemy | 2.0.35 | yes |
| asyncpg | 0.29.0 | yes |
| alembic | 1.13.3 | yes |
| redis | 5.0.8 | yes |
| hiredis | 3.4.2 | via `redis[hiredis]` |
| pydantic | 2.13.5 | transitive from FastAPI |
| pytest | 8.3.3 | yes |
| pytest-asyncio | 0.24.0 | yes |
| ruff | 0.16.5 | yes |
| mypy | 1.11.2 | yes |
| import-linter | 2.1 | yes |
| websockets | 17.1 | transitive from `uvicorn[standard]`, not a direct dependency |
| httpx | not declared | required by `oipulse/marketdata/providers/upstox/rest.py` |

## Commands and results

| Command | Result |
|---|---|
| `python -m unittest discover -s tests -t . -v` | **PASS.** 119 tests, 0.340s. |
| `pytest -q` | **PASS.** 119 passed. |
| `ruff check oipulse tools tests` | **PASS.** |
| `ruff format --check oipulse tools tests` | **PASS.** 58 files. |
| `mypy` | **FAIL.** 21 errors in 10 files. Includes the Phase 1 `TemporalRepository[T]` PEP 695 error under mypy 1.11.2, plus untyped metrics/lifecycle/collector/ws/schema code and `import-not-found` for `httpx`. |
| `lint-imports` | **FAIL.** `include_external_packages=True` is required because the core contract forbids external modules, and it is unset. Same failure as Phase 1. |
| `python tools/check_clock_access.py oipulse` | **PASS.** |
| `python tools/check_import_boundaries.py` | **PASS.** |
| `python tools/check_temporal_repository.py oipulse` | **PASS.** |
| `python -m compileall -q oipulse tools tests` | **PASS.** |

There is no separate integration-test suite. Phase 2 tests are unittest cases driven by synthetic fixtures and in-memory stores.

## Migration evidence

Policy: `0002_phase2_market_data.py` defines `downgrade()`, so downgrade was run on the disposable database only.

1. `CREATE DATABASE oipulse_phase2_verify`
2. `alembic current` on the empty database: no revision
3. `alembic upgrade head` → `0001_phase1_sys_tables` then `0002_phase2_market_data (head)`
4. Schema inspected
5. `alembic downgrade base` → public table count 1 (`alembic_version`)
6. `alembic upgrade head` again → `0002_phase2_market_data (head)`

`DATABASE_URL` for these commands used `postgresql+psycopg://…@127.0.0.1:5432/oipulse_phase2_verify` because the project's `postgresql+asyncpg` URL cannot drive the synchronous migrator.

### Tables present

`sys_outbox`, `sys_event_inbox`, `sys_retention_locks`, `instrument_instruments`, `instrument_expiries`, `instrument_versions`, `instrument_vendor_mappings`, `instrument_universes`, `obs_quotes` (120 daily partitions from 2026-01-01), `obs_greeks` (120 daily partitions), `obs_historical_oi`, `chain_snapshots`, `dq_issues`, `feed_sessions`.

No `user_id` column on any public table.

### Constraints that match the design

- Instrument version and vendor-mapping GiST exclusion constraints against overlapping validity ranges
- `UNIQUE (underlying_id, expiry_date)` on expiries
- Quote/Greeks/historical-OI partial unique indexes for provider event id, feed sequence, and content digest
- `CHECK (oi >= 0)` on quotes and historical OI
- Historical OI `CHECK (valid_to > valid_from)` plus `observation_date`, `valid_from`, `valid_to`
- Bitemporal columns `observed_at` and `ingested_at`, with `(instrument_id, observed_at, ingested_at)` and `ingested_at` indexes
- `(instrument_id, observed_at, source)` is not unique

### Schema divergences (implementation, not environment)

| Design (`02-DATA_MODEL.md`, roadmap) | At head |
|---|---|
| `instrument_options`, `instrument_futures` | absent. Strike and `CE`/`PE` sit on `instrument_instruments`. |
| `obs_depth`, `obs_ohlc`, `obs_index` | absent. Python types exist (`DepthObservation`, `OHLCObservation`, `IndexObservation`) and are not written by `PostgresObservationRepository`. |
| Unique provider-event index on `(provider_event_id)` | partitioned tables use `(provider_event_id, observed_at)`. Feed-sequence uniqueness likewise includes `observed_at`. The migration states this is required because PostgreSQL unique indexes on partitioned tables must include the partition key. |
| `obs_historical_oi` monthly partition | unpartitioned. |
| FK from observations to `instrument_instruments` | not declared. |

`obs_quotes` on the disposable database accepted a duplicate content-hash insert as `ON CONFLICT DO NOTHING` (`inserted=0`, `duplicates=1`).

## Requirement map

| Requirement | File/module | Implemented? | Test? | Runtime verified? |
|---|---|---|---|---|
| Instrument identity separate from version and vendor key | `oipulse/instruments/models.py`, migration | yes, in process and in SQL exclusion constraints | `TestInstrumentIdentity` | SQL constraints inspected. No live Upstox instrument master loaded. |
| Canonical identity cannot split or collide | `InstrumentResolver` in `provider.py` | in-memory registry only. No Postgres instrument repository. | unit tests for version-as-of and reissued keys | no |
| Multi-expiry universe, not front-only | `oipulse/instruments/universe.py` | yes | `TestMultiExpiry` | no live expiry calendar |
| NIFTY, BANKNIFTY, CE/PE, futures | models and synthetic fixtures | types and selectors exist | synthetic chains | no live metadata |
| SubscriptionPlanner ACCEPTED / DEGRADED / UNSATISFIABLE before subscribe | `oipulse/marketdata/subscription.py`, `collector.py` | yes. Limits are `SubscriptionBudget` defaults labelled `phase2-assumption:A-5 (unverified against live provider)` | `TestSubscriptionPlanner` | deterministic unit tests only. Limits not confirmed against Upstox. |
| REST client: chain, contracts, quotes, historical OI, OHLC, 401/429/5xx | `providers/upstox/rest.py` | yes, if `httpx` is installed | retry/governor unit tests; no HTTP server test | **one live call: 401.** Discovery, chain, Greeks, OI, futures not retrieved. |
| WebSocket connect, auth, subscribe, receive, reconnect, resubscribe | `providers/upstox/ws.py` | transport exists. Authorize URL is not fetched unless the caller injects `authorize`. Default `_resolve_socket_url` raises. | offline lifecycle and soak tests with a fake socket | **not run.** |
| Identity confidence and no global `channel_sequence` | `marketdata/identity.py`, `lifecycle.py` | yes. Absence of provider ids degrades to `WEAK` content hash. | `TestObservationIdentity`, `TestWebSocketLifecycle` | provider behaviour **not observed** |
| Gap detection and REST recovery | `lifecycle.py`, `recovery.py` | planning logic yes | `TestRecoveryAndCoherence` | no live gap, no recovered row from Upstox |
| Append-only bitemporal store | `store/memory.py`, `store/postgres.py` | quotes, greeks, historical OI | memory tests; this run also wrote Postgres | disposable DB: late row hidden from `knowledge_at(11:42)`, visible at `11:45` |
| Historical daily OI is not an intraday instant | `HistoricalDailyOI`, `normalize_historical_oi` | yes. `observed_at` is session close; interval is required. | `TestHistoricalDailyOI` | disposable DB: row's `observed_at` is session close, not 11:23:17 IST. `covers_instant` is true for that clock time. |
| Idempotent duplicate ingest | memory store and `ON CONFLICT DO NOTHING` | yes for quote/greek/historical OI | `TestIngestionIdempotency` | disposable DB duplicate insert suppressed |
| Process restart / subscription rebuild | `collector.py` comments and in-memory replay test | control flow exists; no process entrypoint was started | `test_restart_does_not_duplicate` rebuilds an in-memory store | **no process was started or killed** |
| Rate-limit governor | `marketdata/ratelimit.py` | yes | `TestRateLimitGovernance` | not exercised against Upstox 429 |
| Metrics names from `16` §3 | `observability/metrics.py` | in-process registry, not Prometheus | indirect via collector/rest/ws call sites | no live series |
| OHLC backfill job | REST method `get_historical_candles`; `OHLCObservation` | no table, no repository write, no job | no | no |
| Continuous collection during NSE hours | `CanonicalCollector` | class exists, not a runnable service | offline soak script | **not run.** Token invalid. |
| Recorded Upstox fixtures | `tests/fixtures/synthetic/` only | synthetic README states no capture | n/a | `tests/fixtures/upstox_recorded/` does not exist. No `RECORDED_UPSTOX_FIXTURE` was captured, because the only live response was a 401 error body. |

## Real Upstox evidence

```
decrypt=ok
GET https://api.upstox.com/v2/user/profile
  status 401
  errorCode UDAPI100050
  message Invalid token used to access API
GET https://api.upstox.com/v2/option/contract?instrument_key=NSE_INDEX|Nifty 50
  status 401
```

The token and profile body were not printed. Client id and client secret were not printed. No chain, expiry list, quote, Greek, OI, or futures payload was returned.

**Classification: ENVIRONMENT FAILURE** for live REST, WebSocket, recovery, and soak. The ciphertext decrypts; the provider rejects the token. The row's `updated_at` is 2026-08-14.

## WebSocket and identity confidence

No socket was opened.

`UpstoxWebSocketClient._resolve_socket_url` does not call `/feed/market-data-feed/authorize`. It raises unless an `authorize` callable is injected. That is an **IMPLEMENTATION** gap in the unattended connect path, separate from the expired token.

Offline tests show:

- with synthetic event id and sequence, confidence is `STRONG`
- without them, confidence is `WEAK` and sequence gaps are not claimed
- a sequence restart after reconnect is not treated as a gap

**Strongest identity level verified against the live provider: none.**

`VERIFIED ONLY: instrument/timestamp/content-based identity` is what the code does when provider hints are absent. That behaviour is unit-tested. It is not verified against a live frame.

Do not record `feed_session_id + channel + channel_sequence` as verified. Assumption A-1 remains unresolved. Documentation was not changed.

## Bitemporal evidence (disposable Postgres)

Quote for instrument `7`:

- `observed_at` = 2026-03-03 11:40:00Z
- `ingested_at` = 2026-03-03 11:44:00Z

| Query | Rows |
|---|---|
| `knowledge_at(2026-03-03 11:42Z)` | 0 |
| `knowledge_at(2026-03-03 11:45Z)` | 1 |
| `market_truth_at(valid_time=11:40Z, knowledge_as_of=11:45Z)` | 1 |
| second insert of the same observation | inserted 0, duplicates 1 |

Historical daily OI for the same instrument uses `observed_at` = session close `10:00Z`, interval `03:45Z`–`10:00Z`, `ingested_at` = 2026-03-04 02:00Z. A knowledge query at 11:23:17 IST (`05:53:17Z`) returned 0 historical rows and 0 quote rows. `covers_instant` for that clock time is true, and `observed_at` is not that clock time.

`computed_at` and `available_at` are not columns on raw observations. The repository rejects availability semantics. That matches the Phase 2 raw-store rule.

## SubscriptionPlanner evidence

Unit tests cover ACCEPTED, DEGRADED (with a recorded degradation step), and UNSATISFIABLE (protected instruments cannot be dropped to make an impossible universe fit). The same request is deterministic. Budget numbers are fields on `SubscriptionBudget`, and the default source string says they are unverified against the live provider.

No live subscription was attempted, so capacity was not confirmed.

## Fixture inventory

| Path | Kind |
|---|---|
| `tests/fixtures/synthetic/` | synthetic. README dated as hand-built from the legacy schema shape. Values invented. |
| `tests/fixtures/upstox_recorded/` | absent |

No `RECORDED_UPSTOX_FIXTURE` was written. A 401 body is not a market payload.

## Observability evidence

Metric constants in `observability/metrics.py` match the Phase 2 names in `16-OBSERVABILITY.md` §3: ingestion latency, observations ingested, duplicates, out-of-order, WebSocket state and reconnects, gap seconds, REST duration and errors, rate-limit budget and events, subscription capacity and rejections, identity confidence, chain coverage, data-quality issues.

They are an in-process counter/histogram registry. Nothing was scraped during a live run.

`observability/logging.py` has no redaction filter. The REST and WebSocket modules do not log the access token in the paths read. A WebSocket URL returned by Upstox authorize often embeds the feed token; this run never reached that URL, so leakage was not observed and is not cleared.

## Architecture compliance

| Decision | Result |
|---|---|
| AD-03 canonical, un-owned market data | no `user_id` on Phase 2 tables |
| AD-04 / AD-05 bitemporal identity, not timestamp identity | implemented and exercised on the disposable database for quotes |
| AD-06 three-way instrument identity | versions and vendor mappings are separate tables with exclusion constraints. Option/future subtype tables from the ERD are not separate tables. |
| AD-20 no legacy data migration | v2 migration was not applied to `oi_pulse` |
| AD-25 SubscriptionPlanner | implemented; live limits unverified |
| AD-26 daily OI granularity | model and table match; not filled from the provider |
| AD-28 / AD-29 | unchanged from Phase 1. `docs/design/14-DEPLOYMENT.md` still shows `python -m platform.run` and `pydantic-settings`. |
| No provider-sequence overclaim | code and tests refuse sequence gaps under `WEAK` identity |
| BuildContext / MarketState | not implemented. Appropriate for Phase 2. |

## Scope leakage

Not present as implementations: MarketState assembly, analytics registry, signals, research, replay engine, paper trading, risk engine, OMS, live order submission, portfolio attribution, terminal redesign.

Present and acceptable as Phase 2 support: `dataquality/issues.py` (issue records used by recovery), `dq_issues` table, domain-event type names that include `SIGNAL` and `ORDER` as identifiers only.

No live order was submitted.

## Failure classification

| Item | Class |
|---|---|
| Unit tests, Ruff, format, architecture guards, compile | pass |
| mypy strict | **TEST/TOOLING FAILURE** (21 errors; CI already marks mypy `continue-on-error`) |
| import-linter | **TEST/TOOLING FAILURE** (configuration does not satisfy import-linter 2.1) |
| Disposable migration up/down/up | pass, with the schema gaps listed above |
| Live `oi_pulse` still at legacy revision `002` | expected. v2 Alembic cannot share that version table. Not modified. |
| `httpx` absent from declared dependencies | **IMPLEMENTATION/PACKAGING** gap for the supported environment |
| WebSocket authorize not implemented unless injected | **IMPLEMENTATION** gap for an unattended live feed |
| Missing `obs_depth`, `obs_ohlc`, `obs_index`, `instrument_options`, `instrument_futures` | **IMPLEMENTATION** gap versus `02` and the Phase 2 roadmap |
| OHLC backfill not persisted | **IMPLEMENTATION** gap versus the roadmap deliverable |
| No Postgres instrument repository | **IMPLEMENTATION** gap for durable canonical identity |
| Upstox 401 `UDAPI100050` | **ENVIRONMENT FAILURE** |
| Live REST discovery, WebSocket, reconnect, recovery, soak, recorded fixtures, provider identity | **UNVERIFIED REQUIREMENT** |

## Final gate

| Requirement | PASS/FAIL | Evidence |
|---|---|---|
| Tests pass | PASS | 119 unittest / pytest |
| Migration passes on a disposable database | PASS | head `0002_phase2_market_data`, downgrade, re-upgrade |
| Schema matches the full Phase 2 ERD | FAIL | depth, OHLC, index, option, and future tables absent |
| Real API works | FAIL | 401 invalid token. No chain or expiry payload. |
| Real WebSocket works | NOT VERIFIED | no session. Authorize is not wired by default. |
| Real provider identity verified | NOT VERIFIED | no frames |
| Reconnect tested on the provider | NOT VERIFIED | offline test only |
| Recovery tested against REST | NOT VERIFIED | planner unit test only |
| Durable observations | PARTIAL | disposable Postgres quote and historical OI; not provider data |
| Bitemporal 11:40/11:44 fixture | PASS | disposable Postgres |
| SubscriptionPlanner | PASS offline | ACCEPTED, DEGRADED, UNSATISFIABLE. Live limits unverified. |
| Idempotency | PASS | memory tests and one Postgres duplicate insert |
| Soak test | NOT VERIFIED | collector was not started |
| No Phase 3+ leakage | PASS | no MarketState, signals, research, replay, trading, or OMS |
| Static analysis | FAIL | mypy 21 errors; import-linter exits 1 |

**Status: `PARTIAL`.**

**`PHASE 2: NOT VERIFIED`.**
