# Phase 1 and Phase 2 final external verification

Verified commit: `6e987ed97d4ed6dfdc4e1cdb4e2d17ef92b80229`
Branch: `phase1-2-final-remediation`
Parent: `245911211cace08f1772ce87fde0706add809b40`
Working tree at the start of this pass: clean
Date: 2026-09-24
Verifier: independent external pass. Implementation code was not modified.

`PHASE 1: NOT PASS`

`PHASE 2: NOT VERIFIED`

Phase 1 is not pass because `mypy --strict oipulse` fails (21 errors) and the test suite fails (2 failures, 201 passed). Those results were not skipped, ignored, or continued past.

Phase 2 is not verified because every mandatory live-provider requirement lacks evidence. The stored Upstox ciphertext does not decrypt with the current `TOKEN_ENCRYPTION_KEY`, and `UPSTOX_ACCESS_TOKEN` is unset. No REST call, WebSocket session, reconnect, recovery invocation, recorded fixture, or session soak was executed.

## Classification

| Requirement | Result |
| --- | --- |
| mypy --strict | FAIL |
| lint-imports | PASS |
| ruff check | PASS |
| ruff format | PASS |
| compileall | PASS |
| pytest / unittest | FAIL |
| architecture guards | PASS |
| fresh migration to head | PASS |
| legacy revision 002 clone upgraded to head | PASS |
| supported downgrade round-trip | PASS |
| schema (instruments, observations, partitions, keys) | PASS |
| PostgreSQL idempotency, including concurrent writers | PASS |
| API `/ops/health` and `/ops/ready` | PASS |
| readiness failure when Redis or PostgreSQL is down | PASS |
| ingestor refuses to start without universe or token | PASS |
| ingestor live sequence and SIGTERM during a feed | ENVIRONMENT BLOCKED |
| Upstox token usable by the application | FAIL |
| live REST | ENVIRONMENT BLOCKED |
| multi-expiry against live metadata | ENVIRONMENT BLOCKED |
| SubscriptionPlanner unit verdicts | PASS |
| SubscriptionPlanner versus live Upstox limits | ENVIRONMENT BLOCKED |
| WebSocket authorize through persist | ENVIRONMENT BLOCKED |
| A-13 provider event identity on the live feed | NOT VERIFIED |
| same `provider_event_id` in two feed sessions kept in PostgreSQL | PASS |
| reconnect | ENVIRONMENT BLOCKED |
| recovery method present (prior `AttributeError` defect) | PASS |
| recovery invoked against the live provider | ENVIRONMENT BLOCKED |
| bitemporal knowledge on raw quotes | PASS |
| derived `available_at` inequalities | UNVERIFIED |
| historical daily OI not treated as an intraday instant | PASS |
| recorded Upstox fixtures | FAIL |
| session soak | ENVIRONMENT BLOCKED |
| observability metrics exist in code; populated by a live session | UNVERIFIED |
| credentials absent from API and ingestor refusal logs | PASS |
| Phase 3+ scope leakage | PASS |
| live broker orders | PASS (none sent) |

## 1. Provenance

| Item | Value |
| --- | --- |
| Branch | `phase1-2-final-remediation` |
| HEAD | `6e987ed97d4ed6dfdc4e1cdb4e2d17ef92b80229` |
| Parent | `245911211cace08f1772ce87fde0706add809b40` |
| Subject | Remediate Phase 1/2 defects from external verification at 0a960df |
| Python | 3.12.14 |
| pip | isolated venv `/tmp/oipulse-phase1-venv` |
| PostgreSQL | 16.14 (container `oi-pulse-postgres`, host port 5432) |
| Redis | 7.4.10 (container `oi-pulse-redis`, host port 6379) |
| OS | macOS 26.6.2 (build 25G83), Darwin 25.6.0 arm64 |

Declared and installed versions used for the gates:

| Package | Version | Declared in `pyproject.toml` |
| --- | --- | --- |
| fastapi | 0.115.0 | yes |
| uvicorn | 0.30.6 | yes (`uvicorn[standard]`) |
| sqlalchemy | 2.0.35 | yes |
| asyncpg | 0.29.0 | yes |
| alembic | 1.13.3 | yes |
| redis | 5.0.8 | yes |
| hiredis | 3.4.2 | yes (extra) |
| pydantic | 2.13.5 | transitive |
| pytest | 8.3.3 | dev |
| pytest-asyncio | 0.24.0 | dev |
| ruff | 0.16.5 | dev |
| mypy | 1.13.0 | dev |
| import-linter | 2.1 | dev |
| websockets | 17.1 | transitive via uvicorn |
| httpx | 0.27.2 | no |
| psycopg | 3.2.3 | no |
| cryptography | 43.0.1 | no (present because the venv was reused) |

`httpx` is imported by the Upstox REST client and `psycopg` is what the sync Alembic env can actually run. Neither is in the project dependency list. Migration and any future REST run depend on packages a clean `pip install -e ".[dev]"` does not install.

## 2. Architecture contract

Read as authoritative: `CLAUDE.md` and design documents 01, 02, 03, 04, 05, 06, 08, 09, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20. This pass did not redesign them. Comparison is in the architecture audit below.

## 3. Quality gate

Commands, isolated venv, repository root, 2026-09-24:

```text
mypy --strict oipulse
  exit 1
  Found 21 errors in 6 files (checked 49 source files)

ruff check oipulse tools tests
  exit 0
  All checks passed!

ruff format --check oipulse tools tests
  exit 0
  72 files already formatted

lint-imports
  exit 0
  Analyzed 49 files, 98 dependencies.
  Dependencies point downward only KEPT
  core depends on nothing internal KEPT
  Contracts: 2 kept, 0 broken.

python -m compileall -q oipulse tools tests
  exit 0

pytest -q
  exit 1
  2 failed, 201 passed in 16.68s

python -m unittest discover -s tests -t . 
  exit 1
  Ran 203 tests in 13.727s
  FAILED (failures=2)
```

mypy errors, all in implementation files, none suppressed for this pass:

- `oipulse/marketdata/providers/upstox/ws.py:134` no-any-return
- `oipulse/marketdata/providers/upstox/ws.py:191` unreachable
- `oipulse/observability/readiness.py:135` untyped `redis.from_url`
- `oipulse/marketdata/store/schema.py:49-65` Column arg-type (14)
- `oipulse/marketdata/store/postgres.py:133` ReturningInsert assigned to Insert
- `oipulse/marketdata/providers/upstox/rest.py:159` no-any-return
- `oipulse/marketdata/runtime.py:215` range versus InstrumentId
- `oipulse/marketdata/runtime.py:313` cannot infer lambda

The two test failures are real assertions, not skips:

1. `TestPreflight.test_preflight_fails_when_a_dependency_is_unreachable`
   The probe reached the real PostgreSQL server. Message: `ingestor cannot start: postgres: InvalidPasswordError: password authentication failed for user "u"`. The test then required the word `redis` in that message.
2. `TestReadinessProbes.test_a_missing_package_fails_the_runtime_probe_by_name`
   `check_runtime_dependencies("ingestor")` returned `ok=True` because sqlalchemy, asyncpg, and redis are installed. The test expects them to be absent.

No `continue-on-error`, no new type ignores, no skipped tests, no disabled contracts.

Architecture guards, all exit 0:

```text
python tools/check_clock_access.py oipulse
  PASS  no wall-clock access outside oipulse/core/clock.py
python tools/check_import_boundaries.py
  PASS  layer boundaries clean (4 contracts armed)
python tools/check_temporal_repository.py oipulse
  PASS  every repository read accepts a temporal bound
python tools/check_schema_parity.py
  PASS  schema parity: 6 observation kinds, 19 tables created, upgrade/downgrade mirrored
python tools/check_migration_chain.py
  PASS  single alembic chain, 4 revisions, one head
     001 -> 002 -> 0001_phase1_sys_tables -> 0002_phase2_market_data
python tools/check_migration_order.py
  PASS  migration operation ordering across 2 revision(s)
python tools/check_typing_strict.py
  PASS  strict-typing subset across 49 file(s)
  The tool states that mypy --strict remains authoritative.
```

## 4. Fresh database migration

Disposable database `oipulse_final_verify` on the same PostgreSQL 16.14 instance. The primary `oi_pulse` database was not dropped, truncated, or downgraded.

Alembic was run with a sync URL (`postgresql+psycopg`) because `oipulse/migrations/env.py` uses a synchronous engine. `postgresql+asyncpg` cannot run that env (`MissingGreenlet`). `psycopg` is installed only in the verifier venv.

```text
alembic upgrade head
current: 0002_phase2_market_data (head)
```

Chain observed:

```text
001 -> 002 -> 0001_phase1_sys_tables -> 0002_phase2_market_data
```

Parents present include `obs_depth` (partitioned), `obs_greeks` (partitioned), `obs_quotes` (partitioned), `obs_ohlc`, `obs_index`, `obs_historical_oi`, `instrument_instruments`, `instrument_versions`, `instrument_vendor_mappings`, `instrument_expiries`, `instrument_universes`, `instrument_options`, `instrument_futures`, `chain_snapshots`, `dq_issues`, `feed_sessions`, `sys_outbox`, `sys_event_inbox`, `sys_retention_locks`, plus the legacy tables from revisions 001 and 002.

Partitions: `obs_quotes`, `obs_greeks`, and `obs_depth` each have 120 daily children (anchor 2026-01-01). Primary keys, BRIN, ingested indexes, point-in-time indexes, and the three tiered unique indexes are inherited on each child.

Constraints confirmed on the parent objects:

- Primary keys on `instrument_instruments`, `instrument_options`, `instrument_futures`, `obs_depth`, `obs_ohlc`, `obs_index`, and the partitioned observation parents.
- Foreign keys from options and futures to instrument, expiry, and underlying.
- `uq_instrument_option_contract`, `uq_instrument_future_contract`, `uq_instrument_expiry`.
- GiST exclusion constraints `ex_instrument_version_no_overlap` and `ex_vendor_mapping_no_overlap`.
- Checks: instrument type, option type, positive strike, non-negative OI and volume on quote partitions.
- Partial unique indexes on observations: `uq_*_provider_event` on `(provider_event_id, COALESCE(feed_session_id, ''), observed_at)` for partitioned tables; `uq_*_feed_seq` on `(feed_session_id, channel, channel_sequence, observed_at)` where session and sequence are present; `uq_*_content` where both provider event id and feed session are null.

`observed_at` and `ingested_at` exist on observation tables. Raw observation tables do not have `computed_at` or `available_at`.

The previous head failure (partitioning `obs_depth` before `CREATE TABLE`) is gone. Partition creation runs after the parents exist.

## 5. Legacy database upgrade

The live database `oi_pulse` was already at `0002_phase2_market_data` when this pass started. It was not downgraded and `alembic_version` was not edited.

Disposable clone `oipulse_legacy_clone` was created from that database, downgraded to revision `002` through Alembic (v2 tables removed, legacy tables kept), then upgraded with `alembic upgrade head` back to `0002_phase2_market_data`.

Row counts, live database and clone, identical before and after that path:

| Table | Rows |
| --- | --- |
| users | 1 |
| oi_snapshots | 147 |
| oi_strike_snapshots | 19894 |
| oi_time_bars | 39788 |
| market_data_events | 39788 |
| outbox_events | 147 |
| option_expiries | 43 |
| instruments | 3 |

Phase 1 `sys_*` tables and Phase 2 tables are present after the upgrade. No DROP or TRUNCATE was issued against `oi_pulse`.

## 6. Migration round-trip

Disposable database `oipulse_roundtrip`:

```text
upgrade head -> 0002_phase2_market_data
downgrade base -> public left with alembic_version only (1 table)
upgrade head -> 0002_phase2_market_data (head)
```

The repository defines downgrade on 0002, 0001, 002, and 001. This path exercises that code. Downgrade of 002 drops legacy market-data tables; downgrade of 001 drops the initial schema. That was done only on `oipulse_roundtrip`. Production was not downgraded.

## 7. PostgreSQL idempotency

Against `oipulse_final_verify` through `PostgresObservationRepository` (asyncpg), not the in-memory store.

| Case | Result |
| --- | --- |
| Same quote inserted twice | inserted 1, then 0 duplicates 1. One row. |
| Same REST content-hash snapshot twice | inserted 1, then 0 duplicates 1. One row. |
| Same `provider_event_id` with a different `feed_session_id` | second insert kept. Two rows (`evt1_rows=2`). |
| Two concurrent writers, same logical identity (`evt-race3`, session `sess-race`, sequence 99) | results `(1, 0)` and `(0, 1)`. `count(*)=1`. |
| Historical daily OI inserted once | inserted 1. |

An earlier concurrent pair both reported a duplicate and left zero rows. That pair reused `(feed_session_id, channel, channel_sequence, observed_at)` already stored for a different instrument. The unique index is channel-scoped, so the second instrument was discarded. The retest with a fresh session identity left exactly one row. No silent loss of the winning insert.

Duplicate WebSocket delivery and recovery overlap are the same database identity rules. A live duplicate frame and a live recovery overlap were not captured from Upstox.

## 8. API runtime

```text
python -m oipulse.run --role api --host 127.0.0.1 --port 8012
```

Process environment pointed `DATABASE_URL` and `REDIS_URL` at `127.0.0.1`. Settings were not written back to `.env`.

| Probe | Result |
| --- | --- |
| `GET /ops/health` | 200 `{"status":"ok"}` |
| `GET /ops/ready` | 200 `{"ready":true,"checks":{"postgres":true,"redis":true,"runtime_dependencies":true}}` |
| Redis on closed port 6399 | `/ops/health` 200; `/ops/ready` 503 `{"ready":false,"checks":{"postgres":true,"redis":false,"runtime_dependencies":true}}` |
| PostgreSQL on closed port 5999 | `/ops/ready` 503 (access log) |

The ready body names postgres, redis, and runtime package presence. It does not include an Upstox probe. Health stays ok when a dependency is down, which matches a liveness probe that is separate from readiness. The 503 body did not contain the password or a connection URL. Startup log line `live_trading_env_gates_open` was false. No access token appeared in the API log.

## 9. Ingestor runtime

```text
python -m oipulse.run --role ingestor
```

| Condition | Exit | Message |
| --- | --- | --- |
| `OIPULSE_INGESTOR_UNIVERSE` unset | 2 | names that variable and says the role will not subscribe |
| Universe set, `UPSTOX_ACCESS_TOKEN` empty | 2 | `UPSTOX_ACCESS_TOKEN is required for the ingestor role and was empty.` |

The credential check happens before preflight and before any subscribe call. The documented sequence `preflight → credentials → universe → capacity → anchor → stream → session closure` was not executed as a running process, because there is no usable token. SIGTERM during a live feed was not sent. No orders were sent. Roles other than api and ingestor still exit 4 (`processor`, `trader`, `jobs`).

## 10. Upstox authentication

`UPSTOX_ACCESS_TOKEN` is not in `.env` and was not exported.

`upstox_accounts` has one row. Ciphertext length 504 bytes. `token_expires_at` is null. `updated_at` is `2026-08-14 03:57:43+00`. `is_active` is true.

Fernet decrypt with the current `TOKEN_ENCRYPTION_KEY` raised `InvalidToken`. The v2 ingestor does not read this ciphertext. It reads only `UPSTOX_ACCESS_TOKEN`. The application cannot securely use a token in this environment.

The token value was not printed, logged, or written into this report.

## 11. Live REST

ENVIRONMENT BLOCKED. No authenticated request was made. No payload was captured. `tests/fixtures/recorded/` does not exist. Nothing was labeled `RECORDED_UPSTOX_FIXTURE`. No field was invented.

## 12. Multi-expiry

ENVIRONMENT BLOCKED against live provider metadata.

The schema can represent multiple expiries (`instrument_expiries`, `uq_instrument_expiry`) and both CE and PE (`instrument_options`, option-type check, unique contract key) plus futures (`instrument_futures`). That is a schema capability, not evidence that a live universe was resolved beyond the front expiry.

## 13. SubscriptionPlanner

Unit behavior is covered by the passing planner tests in the 201 passed results. The implementation returns `ACCEPTED`, `DEGRADED`, and `UNSATISFIABLE`, and `ok_to_subscribe` is false for `UNSATISFIABLE`. `Collector.plan` runs before `subscribe`.

Default budget source string:

```text
phase2-assumption:A-5 (unverified against live provider)
```

with `max_connections=2`. Those numbers were not compared with a live Upstox account. They stay assumptions.

## 14. WebSocket

ENVIRONMENT BLOCKED. No authorize, connect, subscribe, or message receive against Upstox.

## 15. A-13 provider_event_id

`NOT VERIFIED` against the provider.

No messages were captured from one feed session, and none from a second session. Global uniqueness, session scope, reuse, channel-sequence existence, monotonicity, and reset-on-reconnect are unknown for the live Upstox feed.

What PostgreSQL does with the identity the code writes is verified separately: the unique index includes `COALESCE(feed_session_id, '')`, and inserting the same `provider_event_id` under a second feed session kept both rows. That is the intended guard. It is not evidence of what Upstox actually sends.

## 16. Reconnect

ENVIRONMENT BLOCKED. No controlled disconnect of a live feed.

## 17. Recovery

`UpstoxMarketDataProvider.fetch_recovery_chain` exists and calls the same `fetch_option_chain` path as a routine snapshot. `Collector.recover_after_gap` calls that method and persists returned legs. The prior defect (missing method, `AttributeError` swallowed as `recovery_fetch_failed`) is fixed in source.

The collector still has a broad `except Exception` that logs `recovery_fetch_failed`. A live gap was not produced, so persistence of recovered rows and resumption of the stream were not observed. Live recovery is ENVIRONMENT BLOCKED.

## 18. Bitemporal

On `oipulse_final_verify`, one quote:

```text
observed_at = 2026-03-03 11:40:00Z
ingested_at = 2026-03-03 11:44:00Z
```

`knowledge_at(11:42Z)` returned 0 rows. `knowledge_at(11:45Z)` returned 1 row.

`PostgresObservationRepository.supports_availability` is false. `fetch` with an availability bound raises and tells the caller to use `knowledge_at()` or `market_truth_at()`. Raw queries filter on `ingested_at`, not on `observed_at` alone.

Derived `available_at >= lookback_end`, `>= latest input availability`, and `>= computed_at` was not executed. Phase 2 stores no derived feature rows and the raw tables have no `available_at`. That check is UNVERIFIED.

## 19. Historical OI

A historical daily OI row was inserted with `observation_kind` `historical_daily_oi`, `observed_at` at the session close (10:00Z), and an interval covering 03:45Z–10:00Z. `ingested_at` was the next day 02:00Z.

A knowledge query at 2026-03-03 11:23:17 IST (05:53:17Z) returned 0 historical rows. `covers_instant` was true for the session interval and `observed_at` was the close, not 11:23:17. The value was not visible as if it had been known at that intraday timestamp.

This used a controlled row, not a live historical-OI download.

## 20. Event identity under reconnect

Database evidence only. A repeated event id in a new feed session was kept. A repeated logical identity in the same session collapsed to one row. Out-of-order live frames and recovery overlap from a real socket were not simulated with recordings, because no recordings exist.

## 21. Collector session soak

ENVIRONMENT BLOCKED. NSE was in session on 2026-09-24, but the collector cannot authenticate. No observation counts, reconnects, gaps, or row growth from a live shard were captured.

## 22. Observability

Code defines ingestion latency, identity confidence, ingested counts, duplicate counts, and data-quality issue counters, and the collector increments them on `persist` and `recover_after_gap`. A live process did not populate them, so runtime population is UNVERIFIED.

API and ingestor logs from this pass did not contain an access token, the encryption key, or a connection password.

## 23. Phase 1 regression

Clock guard, import-boundary guard, and temporal-repository guard passed on this commit. Knowledge-horizon reads are required by the temporal guard. The raw store rejects availability queries. K < T rejection remains in `oipulse/core` and its tests are inside the 201 passed. Live trading flags on the API process were closed. The two failing tests are Phase 2 runtime tests, not a Phase 1 clock or boundary regression. Phase 1 quality is still not pass because mypy fails.

## 24. Semantic audit

| Invariant | Evidence |
| --- | --- |
| MarketState identity `(underlying_id, market_time, knowledge_horizon, build_context_id)` | Design only. No MarketState package or table. |
| Four stored times on derived data | Raw observations store `observed_at` and `ingested_at`. `computed_at` and `available_at` are not columns. `decision_time` is not persisted. |
| Availability depends on input readiness | Raw repository refuses availability mode. No derived row was checked. |
| `channel_sequence` is session-scoped | Unique index includes `feed_session_id`. It is not a global order key. |
| A-13 | NOT VERIFIED on the live feed. Database scope by feed session is implemented. |
| Planner before subscribe | `Collector.plan` and `CapacityResult.ok_to_subscribe` gate the start. Live limits unverified. |
| Historical OI | Kind `historical_daily_oi`. Controlled row was invisible at an intraday knowledge time before ingestion. |

## 25. Scope leakage

`oipulse/` packages are `api`, `core`, `dataquality`, `events`, `instruments`, `marketdata`, `migrations`, `observability`, `persistence`. There is no marketstate, analytics, signals, research, replay, backtest, trading, OMS, risk, or portfolio package. `dq_issues` and `dataquality` are Phase 2 support. `live_trading_enabled` defaults false and the API log showed the env gates closed. No broker order was sent.

## 26. Gate

Phase 1 pass requires mypy, lint-imports, migration, API, ingestor foundation, tests, and architecture guards.

Present: lint-imports, migration (fresh and legacy clone), API, ingestor refusal, architecture guards.
Absent: mypy, tests.

`PHASE 1: NOT PASS`

Phase 2 pass requires canonical identity, multi-expiry, REST, WebSocket, quotes, Greeks, durable observations, PostgreSQL migration, PostgreSQL idempotency, bitemporal correctness, SubscriptionPlanner, provider identity evidence, reconnect, recovery, recorded fixtures, session soak, observability, and no Phase 3 leakage.

Present: schema identity, migration, idempotency, raw bitemporal knowledge, historical-OI representation, planner verdicts in tests, recovery method in source, no Phase 3 leakage.
Absent: live REST, live multi-expiry, WebSocket, quotes and Greeks from the provider, A-13 live evidence, reconnect, live recovery, recorded fixtures, session soak, populated metrics.

`PHASE 2: NOT VERIFIED`

## 27. Unresolved

- `mypy --strict` reports 21 errors in six files.
- Two tests assume an offline sandbox and fail when PostgreSQL and the runtime packages are present.
- `httpx` and `psycopg` are required to exercise REST and Alembic and are not declared dependencies.
- Stored Upstox ciphertext does not decrypt with the current key (`InvalidToken`). No replacement token was available.
- A-13 is NOT VERIFIED. Default subscription capacities remain labeled `phase2-assumption:A-5 (unverified against live provider)`.
- Derived `available_at` inequalities have no rows to check.
- Disposable databases left in place, all at head: `oipulse_final_verify` (contains the idempotency rows), `oipulse_legacy_clone`, `oipulse_roundtrip`. Primary `oi_pulse` was not modified by this pass and was already at head.

No implementation change was made. Phase 3 was not started.

## 28. Later live pass, same commit

After this report was drafted, a browser login completed against the API process on port 8080. That process was pointed at disposable database `oipulse_roundtrip` and encrypted the new token with a different `TOKEN_ENCRYPTION_KEY` than `.env`. Decrypt with the server key succeeded. The browser then showed `{"detail":"Not Found"}` because the post-login redirect is `http://localhost:8080/`, and that API process has no route for `/`.

Live REST through `UpstoxRestClient` on 2026-09-24, about 12:25 IST:

- profile: success, `user_id` present
- `NSE_INDEX|Nifty 50` contracts: 1662 rows, 18 expiries, first `2026-09-29`, `2026-10-06`, `2026-10-13`
- chains for those three expiries included CE and PE, bid, ask, LTP, OI, previous OI, IV, and Greeks `delta`, `gamma`, `theta`, `vega`, `iv`, `pop`
- a quote carried `last_price`, `volume`, `oi`, and `depth`
- a closed local port raised `UpstoxRestError`

WebSocket:

- `GET /v2/feed/market-data-feed/authorize` returned 410 `UDAPI1153` (v2 discontinued)
- `GET /v3/feed/market-data-feed/authorize` returned 200 and a one-time socket URL
- two sessions each delivered one protobuf frame: `type = 2` (`market_info`) and `currentTs` `1790233082386` and `1790233086699`
- the published v3 `FeedResponse` has `type`, `feeds`, `currentTs`, and `marketInfo`. It has no `provider_event_id` and no channel sequence
- `UpstoxWebSocketClient` without an authorize callable does not connect
- with a v3 URL injected, the client parses protobuf as JSON, logs `ws_error`, and stops after one reconnect
- no live quote frame was decoded, so reconnect, recovery, and a session soak were not completed
- no recorded fixture was written

```text
PHASE 1
  tests                      FAIL
  mypy                       FAIL
  lint-imports               PASS
  migration from 002         PASS
  fresh migration            PASS
  API entrypoint             PASS
  guards                     PASS

PHASE 2
  schema                     PASS
  multi-expiry               PASS
  REST                       PASS
  WebSocket                  FAIL
  provider identity          VERIFIED
  reconnect                  FAIL
  recovery                   FAIL
  idempotency                PASS
  bitemporal                 PASS
  SubscriptionPlanner        PASS
  recorded fixtures          FAIL
  market-session soak        FAIL

Scope leakage                 PASS

PHASE 2 GATE                  NOT VERIFIED
```

Provider identity `VERIFIED` means the live v3 feed was observed and it does not carry a provider event id or a channel sequence. It does not mean those identifiers were shown to be globally unique.
