# Phase 1 / Phase 2 — Final Remediation Report

**Baseline verified externally:** commit `0a960df` (Phase 1 **FAIL**, Phase 2 **NOT VERIFIED**).
**Scope:** every defect named in that verification, plus the defects those fixes exposed.
**Phase 3 is not started.**

Read the scope statements literally. Where a check was executed, this report names the
command; where it was not, it says so and why. Nothing below is described as verified on
the strength of a string-presence check.

---

## Environment constraints (unchanged, and they bound every claim here)

No package index, no PostgreSQL, no Redis, no container runtime, no reachable
`api.upstox.com`, no broker credentials, no recorded provider fixtures. `ruff` and the
standard library are the only executables available.

Consequences, stated plainly:

* `mypy --strict oipulse` **was not run.** It cannot be installed. A stdlib
  approximation of its statically-checkable subset was written instead and is described
  honestly below; real mypy remains the authority and runs in CI.
* `alembic upgrade head` **was not run.** Integration tests that run it exist and are
  wired into a CI job with a `postgres:16` service; here they skip.
* No live WebSocket soak was performed. A-13 and A-1 remain open.

---

## P0-1 — Migration 0002 partitioned `obs_depth` before creating it

**Reported:** `alembic upgrade head` fails with `relation "obs_depth" does not exist`.

**Root cause.** `upgrade()` ran `for table in _PARTITIONED:` — which includes
`obs_depth` — *before* `op.create_table("obs_depth", ...)`, and then partitioned and
indexed `obs_depth` a second time explicitly afterwards. PostgreSQL requires, for every
table: parent → columns/constraints → partitions → indexes. The loop looked correct in
isolation; it was wrong only in relation to the statements around it, which is why
reading the loop never revealed it.

**Fix.** `oipulse/migrations/versions/0002_phase2_market_data.py` now creates all
sixteen tables first, including `obs_depth`, and runs a single partition-and-index loop
after every parent exists. The duplicate explicit calls are deleted.

**Gated against recurrence.**
`tools/check_migration_order.py` (new) walks `upgrade()` in **source order**, expands
`for table in _PARTITIONED:` by reading the module-level tuple, and asserts that no
operation names a table before it is created and that nothing is partitioned or indexed
twice.

**Evidence the gate works.** The defect was restored verbatim and the guard produced:

```
FAIL  migration operation ordering:
  0002_phase2_market_data.py:324: _create_daily_partitions() targets 'obs_depth' before it
      is created. PostgreSQL fails with 'relation "obs_depth" does not exist'.
  0002_phase2_market_data.py:325: _create_identity_indexes() targets 'obs_depth' before it
      is created. PostgreSQL fails with 'relation "obs_depth" does not exist'.
```

— the reported error, reproduced by static analysis. The defect was then reverted and
the guard passes. Two tests in `tests/phase2/test_migrations.py` failed under the same
mutation and pass after it.

**Not verified here:** that the corrected migration is *accepted* by PostgreSQL. That
requires a server and is covered by the integration suite below.

---

## P0-2 — `mypy --strict oipulse`: 12 errors across 7 files

**Constraint.** mypy cannot be installed. Rather than declare this blocked, the
underlying declarations were fixed and a stdlib gate was built so the contract is
enforced locally as well as in CI.

**What was fixed, by category.**

| Category | Fix |
|---|---|
| Bare generics (`type-arg`) | `observability/metrics.py` gained named aliases `MetricKey = tuple[str, tuple[tuple[str, str], ...]]` and `Labels = dict[str, str] \| None`; `store/schema.py` uses `sa.Column[object]`; `providers/upstox/provider.py` uses `dict[str, Any]` at the transport boundary |
| `object`-typed dependencies with suppressed attribute errors | Replaced with real structural types: `RestTransport`, `WsTransport` (provider); `ObservationSink`, `StreamingProvider`, `RecoveredChain` (collector); `ProbeRegistry` (readiness) |
| Blanket `Any` | `ResolvedBound.observed_at_max / ingested_at_max / available_at_max` are now `datetime` / `datetime \| None`; `TemporalRepository.fetch/_fetch(**criteria)` narrowed from `Any` to `object` |
| Implicit `Optional` | `InstrumentResolver.register(valid_from, valid_to)` now `datetime` / `datetime \| None` |
| Override incompatibility | `InMemoryObservationStore._fetch(**criteria: object)` now matches its base |

**Suppressions removed: 11 → 0.** No `# type: ignore` remains anywhere in `oipulse/`.
The two textual matches that remain are prose in docstrings explaining why the
suppression was wrong. One suppression remains in `tests/phase1/test_foundation.py`,
where the test deliberately makes a call mypy should reject.

**The gate.** `tools/check_typing_strict.py` (new) is a stdlib AST check of the
statically-checkable subset: annotation completeness, bare generics, implicit Optional,
`# type: ignore` without an error code, and override compatibility in parameter names,
arity and **awaitability**.

**Scope of this gate, stated rather than implied.** It is *not* mypy. It performs no
type inference, no assignment compatibility, no narrowing, no unreachability analysis.
Its override check is **nominal** — it compares a class against base classes named in
the same package — so it does not verify conformance to a structural `Protocol`. A clean
run means the declarations are well-formed, not that the program type-checks.
`mypy==1.13.0` stays pinned, `strict = true` stays on, and the CI `typecheck` job still
runs real mypy with no `continue-on-error`, no new excludes and no relaxed settings.

**Evidence the gate works.** Two mutations were injected and both were caught:
a bare `dict` annotation in `metrics.py`, and making `InMemoryObservationStore._fetch`
async against its synchronous base. Both were reverted.

**Residual risk, named:** because real mypy was not run, errors outside the checked
subset — chiefly inference and assignment compatibility — may remain. The CI
`typecheck` job is the check that settles this, and it has not run in this environment.

---

## P0-2a — Defect found *because* of the typing fix: the collector never wrote anything

Removing `store: object` and its `# type: ignore[attr-defined]` exposed a live bug.

`CanonicalCollector.persist()` was synchronous and called `self._store.append(...)`,
then read `result.inserted`. `InMemoryObservationStore.append` is synchronous, so every
offline test passed. `PostgresObservationRepository.append` — the production store — is
`async`. Against it, `append()` returned a coroutine that was never awaited, **nothing
was written**, and `result.inserted` raised `AttributeError` on a coroutine object.

This was exactly the error the suppression silenced.

**Fix.** `ObservationSink` declares `async def append(...)`. `persist()` is now `async`
and awaits. `AsyncSinkAdapter` (in `store/memory.py`) presents the synchronous in-memory
twin through the same contract, so the collector has one write path rather than
branching on whether a result happens to be awaitable.

**Tests.** `test_persist_writes_through_the_async_sink` and
`test_persist_is_idempotent_through_the_collector` assert on the *store's contents*, not
on `persist`'s return value — a return value can be right while nothing is durable.
`test_the_durable_store_satisfies_the_sink_contract` parses `store/postgres.py` and
asserts `append` is an `AsyncFunctionDef`, because SQLAlchemy cannot be imported here and
skipping would have removed the only check that catches this drift.

**Evidence.** Reverting `persist` to synchronous fails both tests; restoring it passes.

---

## P0-2b — Defect found the same way: the recovery path called a method that did not exist

`CanonicalCollector.recover_after_gap()` calls `self._provider.fetch_recovery_chain()`.
`UpstoxMarketDataProvider` **did not define it.** Because `provider` was typed `object`
with `# type: ignore[attr-defined]`, nothing failed until runtime — where the
`AttributeError` was caught by the collector's broad `except` and logged as a routine
`recovery_fetch_failed`. Recovery would have been permanently dead for every session
while reporting itself as an unlucky fetch.

**Fix.** `UpstoxMarketDataProvider.fetch_recovery_chain(expiry_id)` is implemented and
routes through the **same** `fetch_option_chain` path as routine polling — a separate
recovery path would be exercised only during incidents, which is the worst time to
discover it had diverged. `ExpiryBinding` maps a canonical `ExpiryId` back to the vendor
key and date REST needs; an unbound expiry raises `UnknownExpiry` rather than returning
an empty snapshot, because an empty chain is indistinguishable from a market with no
open interest.

**Tests** (`TestRecoveryPathWiring`, 5 tests): protocol conformance; a gap driving a real
REST re-fetch and persisting it; overlap with the resumed stream not duplicating; the gap
remaining a permanent record after recovery; an unbound expiry failing loudly.

---

## P0-3 — Migration tests

**Requirement:** fresh chain, upgrade from legacy `002`, Phase 1 sys tables, Phase 2
schema, partition creation, indexes and constraints, expected table inventory — and
*not* relying solely on unit tests that mock the database.

Two layers, and the split is the honest part.

**Layer 1 — `tests/phase2/test_migrations.py` (14 tests, all executed here).** Reads the
migration as source. Covers: the chain is linear `001 → 002 → 0001 → 0002` with one head;
a database stamped at `002` has a path to head; no revision touches a table before
creating it; each partitioned parent is partitioned exactly once and created before the
loop; Phase 1 creates exactly the three `sys_*` tables; Phase 2 creates exactly the
sixteen expected tables with no duplicates; downgrade mirrors upgrade in reverse creation
order; **no legacy table is dropped and no `TRUNCATE` / `DROP TABLE` / `DELETE FROM`
appears in either revision** (rules 19 and 20); every table declared in `store/schema.py`
is created by the migration; the three identity-tier indexes exist; tier-1 uniqueness is
scoped with `COALESCE(feed_session_id, '')`; `(instrument_id, observed_at, source)` is
not unique.

**Layer 2 — `tests/integration/test_migrations_postgres.py` (5 tests, all SKIPPED here).**
Applies the chain to a real PostgreSQL and introspects the result: `alembic upgrade head`
succeeds; every Phase 1 and Phase 2 table exists; every partitioned parent has
partitions in `pg_inherits`; the identity indexes exist in `pg_indexes` and tier-1 uses
`COALESCE`; downgrade to `002` removes everything and re-upgrade reproduces the schema
exactly.

Each test runs inside a **throwaway database** `oipulse_mig_<random>` created for the run
and dropped afterwards, so nothing touches the database named in `DATABASE_URL` and the
downgrade reverses only migrations the test applied.

**CI.** A new `migrations` job runs these against a `postgres:16` service and **fails if
they skip** — a skipped migration suite must not read as a pass, which is how the chain
reached external verification unapplied in the first place.

**Supporting change.** `oipulse/migrations/env.py` now runs online migrations through
`async_engine_from_config` + `run_sync`. `asyncpg` is the project's only PostgreSQL
driver, and the previous synchronous `engine_from_config` could not use it; the
alternative was adding a second driver, which is a new dependency and was not
authorized. Only the transport changed — the migration context is still synchronous.

**Not verified here:** everything in layer 2. No PostgreSQL is reachable.

---

## P1 — Ingestor runtime

**Reported:** `--role ingestor` exits 4; must be a real process entrypoint, not a
placeholder; must not implement Phase 3.

**Implemented** in `oipulse/marketdata/runtime.py`, dispatched from `oipulse/run.py`.
`ingestor` is removed from `_PHASE_OF_ROLE`, so it no longer reports "not implemented".

Sequence, each step present to make a specific failure loud and early:

1. **Preflight** — PostgreSQL, Redis and the role's local packages are probed before
   anything that uses them is constructed. All failures are reported together, not just
   the first.
2. **Credentials** — a missing `UPSTOX_ACCESS_TOKEN` stops startup. The token is read
   in-process, passed to the transports, and never logged or returned to a client
   (rule 18).
3. **Universe** — `OIPULSE_INGESTOR_UNIVERSE` (JSON) names the mode, instrument
   mappings, underlying ids and expiry bindings. **There is no default**: guessing one
   would start a process collecting something nobody asked for. A malformed document
   stops the process rather than being partially applied.
4. **Plan capacity** — `UNSATISFIABLE` refuses to start; `DEGRADED` starts and records a
   data-quality issue, so absent data stays attributable to a capacity decision rather
   than looking like a quiet market.
5. **Anchor with REST, then stream** — so the earliest states are snapshot-anchored. A
   failed anchor degrades to stream-only rather than refusing the session.
6. **Graceful shutdown** — SIGTERM/SIGINT stop the collector and **close the feed
   session**, which is what records the outage window. A process killed without it
   leaves a hole indistinguishable from a provider outage.

The trading calendar is **injected**, never hardcoded. `default_session_predicate` is
weekday 09:15–15:30 IST and states in its own docstring that it knows nothing about
holidays; a deployment with a real calendar supplies its own.

**Phase 3 is not implemented here.** The runtime persists observations and nothing else:
no MarketState, no features, no signals.

**Tests** (`tests/phase2/test_runtime.py`, 24 tests): universe parsing and every refusal
path; preflight failing and naming each dependency; the secret-bearing `database_url`
never appearing in a failure message; anchoring fetching and persisting every bound
expiry; a failed anchor degrading rather than refusing; shutdown closing the session and
being idempotent; the session predicate open, closed and replaceable; `--role ingestor`
no longer listed as unimplemented and exiting **2** (configuration) rather than 4
(not implemented) when no universe is configured.

**Not verified here:** the live loop. It needs `api.upstox.com`, credentials, PostgreSQL
and Redis.

---

## P1 — Readiness probes

**Implemented** in `oipulse/observability/readiness.py`, wired in `api/app.py`:

* **PostgreSQL** — `SELECT 1`. A connection that opens but cannot execute is not
  readiness; the round trip is the check.
* **Redis** — `PING`.
* **Local runtime dependencies** — resolved by import *spec*, not by importing, so a
  health check never executes third-party module-level code. An unknown role fails
  rather than passing silently: a typo in `--role` must not produce a process that
  reports itself ready while doing nothing.

Each is bounded by a 2-second timeout and converts failure into a reason string; a probe
that raised would make `/ops/ready` return 500 and lose the diagnostic. Failure details
never include the connection URL, which carries the password.

**Provider market-data availability is deliberately not a readiness dependency.** The
approved design does not require it for `api`, and if it did, every Upstox outage — and
every closed market, which is most of the day — would withdraw the whole API from
rotation. Feed health is surfaced through data-quality issues and metrics.

`ReadinessRegistry.reset()` was added because the registry is a module singleton and
repeated `create_app()` calls would otherwise stack duplicate probes.

`observability` must not import `api`, so the registry is typed structurally
(`ProbeRegistry` protocol) rather than imported or suppressed.

**Design docs updated** (`16-OBSERVABILITY.md` §5): the three implemented probes are
recorded, and the role-specific additions the design also requires — `ingestor` WS
authentication, `processor` recency, `trader` reconciliation — are marked **not yet
implemented** rather than silently treated as satisfied.

**Not verified here:** no PostgreSQL or Redis exists, so the probes were exercised
against injected failures and against the genuine "dependency absent" path, which is
this environment's real state.

---

## P1 — Recovery path re-verified

Chain: WS gap → recovery decision → out-of-band REST re-fetch → idempotent persistence →
stream continuation.

It had a broken link (P0-2b above) that no test touched. It is now implemented and
covered end to end by `TestRecoveryPathWiring`. `SessionManager.drain_new_gaps()` still
consumes each gap exactly once while `gaps` remains the permanent record, and
`test_the_gap_remains_a_permanent_record_after_recovery` asserts recovery does not erase
the hole it repaired.

**Not verified here:** behaviour against a real feed. Gap detection under live
conditions depends on A-1 and A-13.

---

## P1 — A-13 kept conservative and open

**No change, deliberately.** Tier-1 identity remains scoped by `feed_session_id` in
`marketdata/identity.py`, in `store/schema.py`, and in the migration's
`COALESCE(feed_session_id, '')` partial unique index. A-13 remains listed as
**unresolved** in `20-ARCHITECTURE_FREEZE.md` §10.

The asymmetry that justifies the scoping is unchanged: scoping a globally-unique id at
worst stores a genuine cross-session repeat twice, which is visible and reconcilable;
*not* scoping a session-scoped id silently discards the second session's live data with
no error and no gap recorded.

**A-13 is not closed, and cannot be closed from synthetic fixtures.** It needs two live
sessions' raw frames compared. `provider_event_id` is not claimed to be globally unique
anywhere in code or documentation.

---

## Files changed

**New**

| File | Purpose |
|---|---|
| `tools/check_migration_order.py` | PostgreSQL operation-ordering guard |
| `tools/check_typing_strict.py` | stdlib subset of `mypy --strict` |
| `oipulse/marketdata/runtime.py` | ingestor runtime |
| `oipulse/observability/readiness.py` | PostgreSQL / Redis / runtime-dependency probes |
| `tests/phase2/test_migrations.py` | structural migration tests (14) |
| `tests/phase2/test_runtime.py` | ingestor and readiness tests (24) |
| `tests/integration/__init__.py`, `tests/integration/test_migrations_postgres.py` | real-PostgreSQL migration tests (5, skipped here) |
| `docs/verification/PHASE-01-02-FINAL-REMEDIATION-REPORT.md` | this report |

**Modified**

| File | Change |
|---|---|
| `oipulse/migrations/versions/0002_phase2_market_data.py` | operation ordering corrected (P0-1) |
| `oipulse/migrations/env.py` | async engine, so `asyncpg` stays the only driver |
| `oipulse/marketdata/collector.py` | `ObservationSink` / `StreamingProvider` / `RecoveredChain` protocols; `persist` is async |
| `oipulse/marketdata/providers/upstox/provider.py` | `ExpiryBinding`, `fetch_recovery_chain`, `UnknownExpiry`; resolver types tightened |
| `oipulse/marketdata/store/memory.py` | `AsyncSinkAdapter` |
| `oipulse/marketdata/store/postgres.py`, `oipulse/persistence/repository.py` | `Any` narrowed to `datetime` / `object` |
| `oipulse/observability/metrics.py`, `oipulse/marketdata/store/schema.py` | named type aliases, parameterized generics |
| `oipulse/run.py` | dispatches `--role ingestor` |
| `oipulse/api/app.py`, `oipulse/api/health.py` | dependency probes registered; `reset()` |
| `.github/workflows/ci.yml` | two new gate steps; `migrations` job with a PostgreSQL service that fails on skip |
| `docs/design/14-DEPLOYMENT.md`, `docs/design/16-OBSERVABILITY.md` | ingestor universe configuration; implemented readiness state |
| `tests/phase2/test_marketdata.py` | collector write-path and recovery-wiring tests |

---

## Validation actually executed

| Check | Command | Result |
|---|---|---|
| Unit + structural suite | `python3 -m unittest discover -s tests -t .` | **203 passed, 5 skipped** |
| Lint | `ruff check oipulse tools tests` | pass |
| Format | `ruff format --check oipulse tools tests` | pass (72 files) |
| Byte-compile | `python3 -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, 4 contracts |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 4 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 6 kinds / 19 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 2 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 49 files |
| Mutation checks | defects re-injected by hand | 4 of 4 caught, all reverted |

**Not executed, and not claimable:** `mypy --strict`, `pytest`, `lint-imports`,
`alembic upgrade head`, any live Upstox call, any WebSocket soak. The packages and
services they need cannot be installed or reached here.

The 5 skips are the PostgreSQL integration tests. They are a reported gap, not a pass.

---

## Remaining external verification

1. `pip install -e ".[dev]"`, then `mypy --strict oipulse` — the authoritative type check.
2. `pytest -q` — the full suite under pytest rather than unittest.
3. `lint-imports` — import-linter contracts.
4. `pytest tests/integration/test_migrations_postgres.py` with `DATABASE_URL` set —
   applies the chain to a real server. **This is the check that settles P0-1.**
5. `alembic upgrade head` against a copy of the live database, confirming the legacy
   `002` stamp upgrades cleanly and no legacy table is touched.
6. Start `--role ingestor` with a real universe and credentials; confirm preflight,
   anchoring, streaming, gap recovery and SIGTERM session closure.
7. Poll `/ops/ready` with PostgreSQL and Redis alternately stopped; confirm 503 and the
   correct failing check name.
8. Two-session WebSocket soak capturing raw frames — the only thing that can settle
   **A-13** (event-id scope), **A-1** (sequence semantics) and **A-3** (venue timestamps).

---

## Status

```
PHASE 1: IMPLEMENTATION READY FOR EXTERNAL RE-VERIFICATION
PHASE 2: IMPLEMENTATION READY / EXTERNAL VERIFICATION PENDING
PHASE 3: NOT AUTHORIZED, NOT STARTED
```

Neither phase is claimed as PASS. A-13 remains open. Live trading remains disabled.
