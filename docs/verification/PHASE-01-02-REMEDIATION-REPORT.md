# Phase 1 / Phase 2 Remediation Report

**Baseline:** `f6ec506` (Phase 2 implementation)
**Scope:** remediate the defects found by independent verification. No architecture redesign.

| | Status |
|---|---|
| **PHASE 1** | **READY FOR EXTERNAL RE-VERIFICATION** |
| **PHASE 2** | **IMPLEMENTATION READY / EXTERNAL VERIFICATION PENDING** |
| Phase 3 | NOT AUTHORIZED — not begun |

Phase 2 is **not** marked PASS. Live provider behaviour, database behaviour, `mypy` and
`lint-imports` were not executed in this environment and remain external requirements.

---

## P0-1 — Alembic migration integration

**Original failure.** The live database carries `alembic_version = '002'` from the legacy
`backend/alembic` chain. The v2 chain began at `down_revision = None`, so two roots shared
one `alembic_version` table. `alembic upgrade head` could not locate `'002'` and every
upgrade failed.

**Root cause.** The v2 chain was authored as if the database were empty. Nothing examined
the revision DAG, so the defect was invisible to the test suite.

**Fix.** One linear chain, no parallel lineage, no hand-edited version table:

```
001 → 002 → 0001_phase1_sys_tables → 0002_phase2_market_data
└── backend/alembic/versions ──┘  └── oipulse/migrations/versions ──┘
```

* `alembic.ini` declares both `version_locations`; `script_location` stays
  `oipulse/migrations`, so the v2 `env.py` runs. Legacy migrations import only
  `alembic`/`sqlalchemy`, so they execute cleanly under it — verified by inspection.
* `0001_phase1_sys_tables.down_revision = "002"`.
* Existing database: resumes at `002` and applies the two new revisions.
* Fresh database: walks `001 → 0002`, producing a schema identical to production.
* No legacy table is dropped or truncated — AD-20 keeps the legacy application running
  until cutover, so its schema must survive. After cutover, removal is a forward
  migration, never a deletion of history.

**Evidence.** `tools/check_migration_chain.py` parses every revision across both
locations and asserts one root, one head, resolvable parents, no duplicates, no cycles.
Reproduced against the original defect: it reports *"expected exactly 1 root, found 2"*.

---

## P0-2 — API process entrypoint

**Original failure.** `/ops/health` and `/ops/ready` existed with no supported way to
start them; `14-DEPLOYMENT.md` documented `python -m platform.run`, a module that does
not exist under either name.

**Fix.** `oipulse/run.py` (entrypoint) and `oipulse/api/app.py` (factory).

* `python -m oipulse.run --role api`
* `python -m oipulse.run --role api --check` — validates configuration and exits.
* Configuration loads through the **dependency-free** `oipulse.core.config` (AD-29).
* FastAPI and uvicorn are imported **lazily**, inside the `api` branch only, so `--help`
  and `--check` work on an interpreter without the web stack — which is exactly where an
  operator most wants to check a deployment.
* A role whose runtime belongs to a later phase exits non-zero naming the phase, rather
  than starting a process that silently does nothing.
* Invalid configuration exits 2 before any socket, pool or provider connection exists.

Documented and actual commands are now identical, asserted by
`test_documented_command_matches_the_real_one`.

---

## P0-3 — CI type/lint enforcement

**Original failure.** mypy errors; import-linter configuration failure; the typecheck job
carried `continue-on-error`, so neither blocked a merge.

**Root causes and fixes.**

| Cause | Fix |
|---|---|
| mypy 1.11.2 cannot parse PEP 695 type parameters (`class TemporalRepository[T]`), which ruff UP046 requires on py312 | pinned **mypy 1.13.0** |
| typecheck was advisory | `continue-on-error` removed from the whole workflow |
| mypy ran without the runtime dependencies, so every third-party import errored | job installs `-e ".[dev]"` |
| import-linter contract named `oipulse.analytics`, which does not exist until Phase 4 — it resolves every module against the real graph and errors on an absent one | contract removed; the analytics purity rule is armed **now** in the AST guard, which does not require the package to exist |
| `forbidden_modules` listed external packages (`sqlalchemy`, `pydantic`), which import-linter must import and walk | external bans moved wholly to `tools/check_import_boundaries.py`; import-linter handles internal layering only |
| layers contract omitted the Phase 2 packages | layers rewritten from the **measured** import graph |

Two guard bugs were found while fixing this, both real:

* a package importing itself (`api.app` → `api.health`) was flagged under the wildcard
  subject, because self-imports were only exempted for a named subject;
* the composition root (`run.py`) legitimately imports `api` and had no exemption.

Both fixed; the exemption is a single named module and is verified not to be a blanket
hole (`events` importing `api` is still rejected).

**Not executed here.** `mypy` and `lint-imports` are not installed and PyPI is blocked. All
3 untyped definitions were found and fixed by AST scan (approximating
`disallow_untyped_defs`), and every module named in the import-linter config is asserted
to exist. **Neither tool was run. Both remain external verification items.**

---

## P0-4 — AD-28 / AD-29 document inconsistency

**Original failure.** Authoritative v2 documents still required the pre-rename package and
the rejected configuration library, and `config.py` cited the wrong ADR.

| Location | Was | Now |
|---|---|---|
| `14-DEPLOYMENT.md` §1 | `python -m platform.run` ×6 | `python -m oipulse.run`, with AD-28 stated |
| `14-DEPLOYMENT.md` §3 | "`pydantic-settings`, environment-sourced" | stdlib-only, AD-29 stated with its reason |
| `oipulse/core/config.py` | "Recorded as AD-28" | **AD-29**, noting AD-28 is the rename |

`19-DECISIONS.md` still contains both strings inside AD-28 and AD-29 themselves, where
they name the *rejected* option — correct and retained. `docs/ARCHITECTURE.md` and
`backend/` describe the legacy implementation and were **not** altered.

---

## P0-5 — Phase 2 schema mismatch

**Original failure.** Five entities required by the design were absent from schema and
migration: `obs_depth`, `obs_ohlc`, `obs_index`, `instrument_options`,
`instrument_futures`.

**Determination.** Compared against `02-DATA_MODEL.md` (§2 ERD lists the two instrument
subtype tables; §3 lists the three observation tables as siblings of `obs_quotes`; §9
gives them distinct retention). **All five are genuinely required in Phase 2** — the
normalizer already produced `DepthObservation`, `OHLCObservation` and `IndexObservation`
with nowhere to land. Nothing was deferred and no requirement was removed.

**Fix.**

* `obs_depth` — daily-partitioned like quotes and greeks; JSONB levels.
* `obs_ohlc`, `obs_index` — unpartitioned in Phase 2; partitioning is a forward migration
  when volume justifies it rather than a guess made now.
* `instrument_options`, `instrument_futures` — subtype tables per the §2 ERD.
  `instrument_instruments` is now **identity only**: attributes that *define* an
  instrument (strike, option type) belong to the subtype, not as nullable columns on the
  one table that must be uniform.
* Upgrade and downgrade mirror exactly: 16 tables each.

**Evidence.** `tools/check_schema_parity.py` cross-checks observation dataclasses against
`schema.py` and the migrations, and asserts upgrade/downgrade symmetry. Reproduced against
the original defect: it reports all five as missing.

---

## P1 — A-13 provider event identity

**Change.** Tier-1 identity is now scoped by `feed_session_id`. `provider_event_id` is
preserved in full on every row; only the *uniqueness key* includes the session.

**Why, and why not the other way.** Whether an Upstox event id is globally unique or
restarts per session is still unverified. The failure modes are asymmetric:

* Scoping a globally-unique id — at worst a genuine cross-session repeat is stored twice.
  Visible, recoverable by reconciliation.
* **Not** scoping a session-scoped id — the second session's events collide with the
  first's and are **silently discarded as live data loss**, with no error and no gap.

The offline soak measured exactly this: before the fix, 12 observations vanished across
the reconnect. After it, both identity branches write 34 observations and genuine
duplicates are still suppressed.

The database index uses `COALESCE(feed_session_id, '')` because Postgres treats NULLs as
distinct in a unique index — without it, REST rows (which carry no session) would not
deduplicate at all.

**A-13 remains open.** The assumption is retained in `20-ARCHITECTURE_FREEZE.md` §10 with
the resolution path: if ids prove globally unique, the session component can be dropped —
a narrowing change, safe to make later.

---

## P1 — Implementation completeness

The named gap was the **recovery path**: `plan_recovery` existed but the collector never
called it, so a detected gap produced no recovery.

* `SessionManager.drain_new_gaps()` — gaps are consumed exactly once, so one
  discontinuity triggers one recovery. `gaps` remains the full permanent record;
  draining marks a gap *handled*, never forgotten.
* `CanonicalCollector.recover_after_gap()` now records the quality issue, emits the
  metric, and performs the out-of-band re-fetch.
* The streaming loop drains and recovers per frame.
* `CollectorPlan` carries `underlying_ids` / `expiry_ids`, the scope recovery needs.

REST client, WS client, Postgres repository and migration 0002 were reviewed and are
implementation-complete for Phase 2 scope. None of them has been executed.

---

## P1 — Temporal semantics preserved

Unchanged and re-verified: `observed_at` / `ingested_at` / `computed_at` / `available_at`
remain four distinct fields; `decision_time` is **not** a persisted column (asserted);
historical daily OI remains date-granular with an explicit validity interval and its own
`REST_HIST_OI` source.

---

## P1 — Phase 1 foundation guarantees

All still pass: clock guard, boundary guard, temporal repository guard, explicit knowledge
horizon, `K < T` rejection, raw-store restriction, event sequence behaviour, inbox
idempotency, live trading disabled by default.

---

## Changed files

**New:** `oipulse/run.py`, `oipulse/api/app.py`, `tools/check_migration_chain.py`,
`tools/check_schema_parity.py`, `tests/phase1/test_remediation.py`, this report.

**Modified:** `alembic.ini`, `oipulse/migrations/versions/0001_phase1_sys_tables.py`,
`oipulse/migrations/versions/0002_phase2_market_data.py`,
`oipulse/marketdata/store/schema.py`, `oipulse/marketdata/identity.py`,
`oipulse/marketdata/lifecycle.py`, `oipulse/marketdata/collector.py`,
`oipulse/core/config.py`, `tools/check_import_boundaries.py`, `pyproject.toml`,
`.github/workflows/ci.yml`, `docs/design/14-DEPLOYMENT.md`,
`docs/design/20-ARCHITECTURE_FREEZE.md`, `tests/phase2/test_marketdata.py`,
`tests/phase2/test_ws_soak_offline.py`.

**Deliberately untouched:** `backend/` (legacy application and its migrations),
`docs/ARCHITECTURE.md` and the other legacy audit documents, `frontend/`, `infra/`.

---

## Validation performed

| # | Check | Result |
|---|---|---|
| 1 | Unit + locally-available integration tests | **PASS** — 152 tests |
| 2 | Integration against Postgres / provider | **NOT RUN** — no database; `api.upstox.com` blocked |
| 3 | Ruff lint | **PASS** |
| 4 | Ruff format | **PASS** |
| 5 | mypy | **NOT RUN** — not installed, PyPI blocked |
| 6 | lint-imports | **NOT RUN** — not installed, PyPI blocked |
| 7 | Compile checks | **PASS** |
| 8 | Architecture guards (clock, boundaries, temporal repository) | **PASS** |
| 9 | Migration chain + schema parity | **PASS** |
| 10 | Semantic document consistency | **PASS** — 7 checks; 3 apparent hits classified as quotations (gate table and ADR naming a rejected form), 0 live assertions |

Guards were each reproduced against the defect they exist to catch, so a passing result
means the check can actually fail.

---

## Remaining external verification

| # | Command | Expected | Requires |
|---|---|---|---|
| 1 | `pip install -e ".[dev]" && pytest -q` | 152 pass | PyPI |
| 2 | `mypy` | clean under `strict` | PyPI |
| 3 | `lint-imports` | both contracts pass | PyPI |
| 4 | `alembic current` on the live database | `002` | Postgres |
| 5 | `alembic upgrade head` on the live database | → `0002_phase2_market_data`; legacy tables intact and row counts unchanged | Postgres |
| 6 | `alembic upgrade head` on an empty database | full chain from `001`; schema matches production | Postgres |
| 7 | `alembic downgrade 0001_phase1_sys_tables && alembic upgrade head` | clean round-trip | Postgres |
| 8 | `\d+ obs_quotes`, `\d+ obs_depth` | partitioned; three partial unique indexes; `COALESCE(feed_session_id,'')` present | Postgres |
| 9 | Insert the same observation twice; then from two concurrent writers | one row each time | Postgres |
| 10 | `python -m oipulse.run --role api` then `curl /ops/health`, `/ops/ready` | 200 / 200 | PyPI |
| 11 | Capture **two** WS sessions' raw frames | settles **A-13** (event-id scope), **A-1** (sequence), **A-3** (venue timestamps) | credentials |
| 12 | Force a reconnect mid-stream | `RECONNECT_GAP` recorded; no false `WEBSOCKET_GAP`; no data lost across the boundary | credentials |
| 13 | Subscribe beyond budget | `UNSATISFIABLE` **before** the socket errors (**A-5**) | credentials |
| 14 | One market session of collection | rows accumulating; `ingestion_latency_seconds` p99 | all |

---

## Provider assumptions still requiring live verification

| ID | Assumption | If wrong |
|---|---|---|
| **A-1** | WS supplies a per-event id or channel sequence | identity degrades to content hash, `WEAK` confidence, no sequence-gap claim — already handled and tested |
| **A-3** | WS frames carry venue timestamps | `observed_at` falls back to receipt time; the substitution is recorded per row |
| **A-5** | ~2 connections, ~2,000 LTPC/Greeks, ~1,500 Full | configuration change only; the planner reports the numbers it used |
| **A-7** | Current NSE/BSE lot sizes (legacy seeds are stale) | every exposure, GEX and P&L figure scales wrongly |
| **A-8** | Exchange holiday calendar is obtainable | collection on holidays produces junk rows |
| **A-13** | Event-id scope: global or per-session | currently scoped by session, the safe direction; narrowing is safe later |

No Upstox response has been recorded. Every fixture under `tests/fixtures/synthetic/` is
labelled synthetic; structure is transcribed from the legacy integration that ran against
live Upstox, values are invented.

**Not claimed:** live REST or WS success, a live soak, verified provider identity
semantics, or an applied migration.
