# Phase 6 — Research and Event Studies Implementation Report

**Base SHA:** `e4a1ace0c8ce463e524e99b9cd836ef26c71d3a0` (`origin/main`, "docs: add Phase 5
final verification report")
**Branch:** `phase6-research`
**Implementation SHA:** the tip of `phase6-research` — `git rev-parse phase6-research`.
A commit cannot contain its own hash.

```
PHASE 6 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION
```

Not claimed as PASS. §12 states exactly what was not executed.

---

## 0. Starting state — two discrepancies, both resolved

The brief names the verified Phase 5 checkpoint as `5326417` with tag
`oi-pulse-v2-phase5`.

* **`5326417` exists but is not an ancestor of `origin/main`.** Its tree is
  nonetheless **identical** to `origin/main` (`e4a1ace`) — `git diff --stat` between
  them is empty, and both carry the message "docs: add Phase 5 final verification
  report". Same content, different commit path, as happened in Phases 4 and 5.
* **The tag `oi-pulse-v2-phase5` does not exist** in this repository. `git tag -l`
  lists only `oi-pulse-v2-phase3` and `oi-pulse-v2-phase4`.

The brief also instructs the branch to start from the current `origin/main`, which is
unambiguous, so `phase6-research` was created from `e4a1ace`. The content is the
verified Phase 5 tree either way. Flagged rather than silently reconciled.

---

## 1. Clean Phase 5 baseline, measured before any change

Per §23 and the standing rule not to call a failure environmental without evidence.
Measured on the untouched tree at `e4a1ace`:

```
Ran 599 tests — 561 passed, 30 errors, 8 skipped

  20 × ModuleNotFoundError: No module named 'fastapi'
  10 × ModuleNotFoundError: No module named 'google'   (protobuf)
```

Every one of the 30 is a missing package; neither is installable (DNS fails for
`pypi.org`). **After Phase 6 the error set is byte-identical** — same 30, same two root
causes, and no error of any other kind.

---

## 2. Modules added

| Module | Purpose |
|---|---|
| `oipulse/research/windows.py` | the five window fields; `DecisionWindow` and `ForwardWindow` as separate types |
| `oipulse/research/access.py` | `PointInTimeAccessor`, `FeatureAccessError` — look-ahead refused |
| `oipulse/research/events.py` | versioned, content-addressed `EventDefinition`; two detectors |
| `oipulse/research/sampling.py` | sampling policy, overlap policy, separation, clustering |
| `oipulse/research/study.py` | `EventStudy`, `QueryMode`, `Universe`, `StudyPeriod` |
| `oipulse/research/statistics.py` | distribution, excursion, sample counts |
| `oipulse/research/dataset.py` | `Dataset` with `content_hash` and retention locks |
| `oipulse/research/result.py` | `StudyResult`, the content-addressed artifact |
| `oipulse/research/engine.py` | `EventStudyEngine`, `OutcomeSource` |
| `oipulse/research/signal_evaluation.py` | forward behaviour by transition; evidence attribution |
| `oipulse/research/serialisation.py` | API rendering, web-stack-free |
| `oipulse/persistence/research_tables.py` | the four Phase 6 tables |
| `oipulse/migrations/versions/0006_phase6_research.py` | the migration |
| `oipulse/api/research.py` | `/research` endpoints |

Modified: `oipulse/observability/metrics.py` (9 metric names + 4 emit helpers),
`oipulse/api/app.py` (router), `tools/check_import_boundaries.py` (2 contracts),
`pyproject.toml` (full layer ordering declared), and four test modules whose
migration-chain and contract-count assertions legitimately changed.

---

## 3. Research architecture

```
Historical Market Truth -> Knowledge-Horizon / PIT Selection
  -> Feature / Signal Evidence -> Event Detection
  -> Event Qualification / Sampling -> Forward / Backward Windows
  -> Outcome Computation -> Research Result -> Content-Addressed Artifact
```

The engine is pure: it reads a `Dataset` and an injected `OutcomeSource`, opens no
connection and reads no clock. Two new contracts enforce it:

* **`research-is-pure`** — `research/*` may import `signals`, `analytics`,
  `marketstate` and `core` only. Notably **not `alerts`**: whether a notification was
  delivered has no bearing on what history shows.
* **`research-never-recomputes-analytics`** — importing `analytics.domains`,
  `analytics.engine` or `signals.evaluation` is forbidden. Research consumes verified
  *results* pinned to exact versions; a second implementation would drift silently.

`pyproject.toml` now declares the full layer ordering, which had drifted: `analytics`,
`signals` and `alerts` were never added by Phases 4 and 5. Now
`run -> api -> {alerts, research} -> signals -> analytics -> marketstate -> marketdata
-> {low} -> core`, verified against the real import graph by the existing Phase 1 test.

---

## 4. Point-in-time correctness

**Look-ahead is refused, not warned about** (`09` §2). `PointInTimeAccessor.get_feature`
raises `FeatureAccessError` when a value's `available_at` is later than the request,
and the message is the design's own. Three failure modes are closed:

* asking before availability;
* receiving a *different version* than the one pinned — a v3 row is not a match for a
  v2 request;
* receiving a later correction as though it existed at the original horizon —
  `get_feature` returns the latest **available** value, not the latest value.

`try_feature` exists for callers that treat absence as a state, as a **separate method
rather than a flag**, so the refusing path stays the default and opting out is visible.

**Availability uses the identical formula as Phase 4.** `research.windows.availability_time`
and `analytics.availability.available_at` are asserted equal by a regression test: a
study must judge availability by exactly the rule that produced it, or a study and the
pipeline could disagree about what was knowable and nobody could tell which was right.
The 11:40/11:44 worked example reproduces as 11:44:02.

**Decision and outcome windows are different types.** `DecisionWindow` has no
`forward_start` or `forward_end` field, so a decision context physically cannot be
handed a forward window. A test asserts the absence of the fields, not just the
behaviour.

---

## 5. Corrections and bitemporal research

The brief's case is a test class. With an observation `observed_at = T`,
`ingested_at = K1`, corrected at `K2`:

* a study at `K1` sees `120`;
* a study at `K2` sees `180`;
* the two produce different values **and different input digests**;
* `build_dataset` filters at build time, so a `K1` extract does not physically contain
  the `K2` row — there is no path by which a study reaches it;
* the two horizons produce **different dataset content hashes**, so the artifacts are
  distinguishable rather than pooled.

Backfill leakage is covered the same way: a row loaded later carries a later
`knowledge_horizon` and is invisible to an earlier extract.

---

## 6. Events, sampling and windows

**Event definitions are versioned and content-addressed.** A threshold edit or a
version bump changes `content_digest`; re-registering a version raises. The two event
kinds are exactly those the specification names — signal transition and feature
threshold crossing — and `EventKind` is closed. The global registry starts **empty**:
definitions are supplied by a study, not preloaded, so no event type exists that the
authoritative list does not name.

Detection deduplicates by `occurrence_id` before any policy applies, and
`occurrence_id` **excludes `detected_at`** — when detection ran is execution metadata.
Scan order does not affect the result.

**Sampling implements the design's own example.** Four events five minutes apart with a
30-minute horizon: `raw_events = 4`, `clusters = 1`, `effective_sample = 1`,
`mean_overlap = 4`. All three policies are implemented, `FIRST_PER_CLUSTER` is the
conservative default, and `minimum_event_separation` defaults to the longest horizon so
forward windows cannot overlap unless a researcher opts in. **Both counts are reported
in every result**, including under `ALL`.

Boundary cases are tested: simultaneous events, events exactly one separation apart
(separate clusters), events inside the separation (one cluster), duplicates, and
reordered input.

**Incomplete windows are excluded and counted, never fabricated.** `ForwardWindow`
reports `COMPLETE`, `TRUNCATED` or `EMPTY`; `require_complete` raises `IncompleteWindow`.

---

## 7. Reproducibility — the Phase 6 gate

`Dataset.content_hash` covers the extract's sorted content and the parameters that
produced it. `StudyResult.content_hash` covers the study digest, the event-definition
digest, the dataset hash, the sample counts and every statistic.

**Execution metadata is structurally excluded.** `ExecutionMetadata` is a separate
object that the hash function never receives, so a field added to it later cannot leak
into semantic identity by accident — a test asserts the hash source does not mention it.

Tested in both directions: same request twice, reordered source rows, reordered
collections, different wall-clock time, different duration and host all hash
identically; changed source content, a changed threshold and a changed knowledge
horizon all produce a different artifact.

---

## 8. Honesty requirements (`09` §3)

* **Minimum sample is enforced on the effective count.** Four overlapping events do not
  satisfy a minimum of two. Below the minimum the result carries
  `INSUFFICIENT_SAMPLE`, no statistics, and a detail string — and **still has a content
  hash**, because a study that silently produced nothing is indistinguishable from one
  never run.
* **Quality-filtered by default**, exclusions counted and reported.
* **Comparison count recorded** on every result: horizons × (controls + 1).
* **No statistic is invented.** An empty sample has no mean; one observation has no
  standard deviation; profit factor with no losses is `None`, not infinity; missing
  instants are skipped and counted, never carried forward.
* Signal evaluations below their minimum return `reportable: false` with the count but
  **no distribution** — withholding the number while reporting that the question was
  asked.

---

## 9. Persistence and API

Four tables, **deliberately unpartitioned**: decision artifacts are immutable and
retained forever (`02` §9), so time partitions would imply a pruning story that must
not exist for this data. `research_datasets.content_hash` and
`research_results.content_hash` are `UNIQUE` — the reproducibility gate enforced by the
database, not only by the code that computes it. No Phase 7 backtest table is created,
asserted by a test over the tables actually created.

`/research` implements the specified surface. Two behaviours are enforced rather than
left to a client: **`knowledge_time` is required on a run** — silently answering a
historical question with today's knowledge would be invisible in the response — and a
missing artifact is 404 while an unconfigured process is 503, never an empty list.

Every result response carries the honesty numbers in `meta`: both event counts,
exclusion counts, comparison count, the hindsight flag and the content hash.

---

## 10. Observability

`research_runs_total`, `research_run_duration_seconds`, `research_failures_total`,
`research_events_detected_total`, `research_events_excluded_total` (labelled by reason),
`research_incomplete_windows_total`, `research_artifacts_created_total`,
`research_content_hashes_total`, `research_provenance_failures_total`. Emitted by
helpers taking primitives, because `research/` may not import the registry. Both event
counts are recorded, never just the raw one.

---

## 11. Verification actually executed

| Check | Command | Result |
|---|---|---|
| Full suite | `python3 -m unittest discover -s tests -t .` | **707 tests: 669 passed, 30 errors, 8 skipped** |
| Phase 6 only | `tests/phase6/` | **105 passed** |
| Lint | `ruff check .` | pass |
| Format | `ruff format --check .` | pass, 213 files |
| Byte-compile | `python -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, **8** contracts |
| Alert purity | `tools/check_alert_purity.py` | pass |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 8 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 34 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 6 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 119 files |

Phase 6 test breakdown: PIT and leakage 26, events and sampling 30, reproducibility,
API, statistics, signal evaluation, guards and regression 49.

**108 tests added, zero regressions.** The 30 errors are the same 30 measured on the
clean baseline, same two root causes, no error of any other kind.

### Not executed here

`pytest`, `mypy --strict oipulse --show-error-codes`, `lint-imports`,
`alembic upgrade head`, and any endpoint over HTTP. DNS resolution fails for
`pypi.org`, so nothing is installable; there is no PostgreSQL and no FastAPI. The
stdlib typing guard passes over 119 files and is explicitly **not** equivalent to mypy
— no inference, no assignment compatibility, nominal-only override checking.

The 8 skips are the PostgreSQL migration integration tests (extended to cover the
Phase 6 tables), the outstanding recorded-fixture set, and one Phase 2 soak item. Each
is a reported gap, not a pass.

---

## 12. Known limitations

1. **No research store.** Schema, migration and endpoint contracts exist; wiring
   durable persistence needs a database to test against. Endpoints answer 503.
2. **Datasets and outcome series are caller-supplied.** The pure layer cannot load
   history. `SeriesOutcomeSource` is an explicit in-memory source; a PostgreSQL-backed
   one implements the same `OutcomeSource` protocol.
3. **`REGIME` breakdowns report `unknown` without a caller-supplied classifier.** The
   bucket needs the Phase 4 classifier's output for the event's state, which the
   engine does not recompute. `control_of` is the injection point.
4. **No overlap-corrected statistics.** `09` §3 explicitly does not require block
   bootstrap or Newey–West in this phase; it requires that the engine *know* events
   overlap and say so, which it does.
5. **`describe` uses nearest-rank quantiles**, deliberately: interpolation would
   invent a value no event produced.
6. **Session boundaries are dataset-driven.** A weekend or holiday gap appears as
   missing instants and a truncated or empty window; the engine has no trading
   calendar and does not need one, because the absence of data is the signal.
7. **Evidence attribution reports separation, not significance.** A p-value over
   overlapping samples would be exactly the false precision `09` §3 warns against.
8. **`.env.example` is unreadable in this sandbox** (read-deny list), so git reports a
   phantom modification it cannot diff. Excluded from the commit.

---

## 13. Scope control

No replay engine, backtest execution, paper trading, risk, OMS, broker adapters,
portfolio attribution or terminal. Tests assert `research/*` imports none of those
packages and that migration `0006` creates only `research_*` tables. The layer is
usable by Phase 7 through `OutcomeSource`, `PointInTimeAccessor` and the content-hashed
artifacts, all of which are deterministic interfaces with no live-path dependency.

Phase 1–5 behaviour is unchanged: 54 features, 16 signal rules, `StateIdentity`
untouched, and feature availability semantics re-asserted by regression test.
