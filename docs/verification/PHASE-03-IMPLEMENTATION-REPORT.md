# Phase 3 — MarketState Implementation Report

**Base SHA:** `98720d5f825f5b953940d9097cb5fd8ac8aa8cdc` (`origin/main`, "Merge pull
request #2 from rishivak/phase2-v3-integration")
**Branch:** `phase3-marketstate`
**Implementation commit:** the tip of `phase3-marketstate` — `git rev-parse phase3-marketstate`.
Not written out here: a commit cannot contain its own hash, and amending to insert one
only changes it again.

```
PHASE 3 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION
```

Not claimed as PASS. Sections 12 and 13 state precisely what was not executed here.

---

## 1. Base selection

Local `main` was stale at `2459112`. `origin/main` is `98720d5`, one merge commit ahead
of `origin/phase2-v3-integration` (`6a17262`), and `git diff 98720d5 6a17262` is empty —
the merge brought the tested tree onto `main` unchanged. `phase3-marketstate` was
therefore branched from `98720d5`, the authoritative base, not from a Phase 2 SHA.

`git pull` could not update local `main`: the credential-storage lock is on a read-only
filesystem here. The remote-tracking ref `origin/main` was already present and was used
directly.

**`.env.example` reports as modified and cannot be read** — it is on this sandbox's
read-deny list, so git cannot diff it and reports a phantom modification. This also
explains the same phantom flagged in the previous two sessions; it is **not** an edit
anyone made. It blocked `git checkout main` and is excluded from the commit.

---

## 2. Modules added

| Module | Purpose |
|---|---|
| `oipulse/marketstate/context.py` | `BuildContext`, content-addressable `build_context_id` |
| `oipulse/marketstate/staleness.py` | documented budgets, categories, escalation table |
| `oipulse/marketstate/state.py` | the immutable `MarketState` composition |
| `oipulse/marketstate/universe.py` | instrument universe resolution as-of `T` |
| `oipulse/marketstate/builder.py` | **the single deterministic `build_state`** |
| `oipulse/marketstate/checkpoints.py` | checkpoint identity, exact-match selection, `StateService` |
| `oipulse/marketstate/serialisation.py` | the API response envelope, as a pure function |
| `oipulse/marketstate/store/schema.py` | `state_build_contexts`, `state_checkpoints`, legs, expiries |
| `oipulse/migrations/versions/0003_phase3_marketstate.py` | the migration for those four tables |
| `oipulse/api/market_state.py` | `GET /market/state` |

Modified: `oipulse/api/app.py` (router + docstring), `pyproject.toml` (layering
contract), and three test modules whose migration-chain assertions legitimately changed.

---

## 3. BuildContext

Immutable `frozen=True, slots=True`. `id` is derived from all four fields
(`builder_version`, `staleness_policy_version`, `feature_set_version`,
`configuration_digest`) via a sorted-key SHA-256, prefixed `bc_`.

* **Same configuration → same identity**, including across dict ordering, so two
  processes computing it independently agree without coordination.
* **Materially different configuration → different identity.** `StalenessPolicy.as_configuration()`
  feeds every budget, both coverage thresholds and the anchor max age into the digest,
  so changing a single budget yields a new `build_context_id` and states built under
  different budgets can never collide.
* `build_context_id` **subsumes** the three versions (AD-21). `StateIdentity` carries no
  separate `builder_version`; a test asserts its field set is exactly the four identity
  elements, because two sources of truth for one fact can disagree.

---

## 4. State identity

`StateIdentity(underlying_id, market_time, knowledge_horizon, build_context_id)`, with
`as_key()` returning a 4-tuple used for every lookup.

Tested: different `K`, different `T` and different `BuildContext` all produce
distinguishable identities while the other elements match. `decision_time` is absent
from identity and from persistence — it remains a query/action parameter (`05` §2).

---

## 5. Deterministic builder

`StateBuilder.build` is the only path that produces a `MarketState`, and is used by
live assembly, reconstruction, checkpoint materialization and checkpoint validation
alike. There is no second historical implementation to drift.

Determinism is enforced rather than assumed:

* **No wall clock in any value.** Every `age` is `market_time - observed_at`. The only
  clock read is `provenance.assembled_at`, which is excluded from the content digest —
  tested by building the same state under clocks 6000 minutes apart and asserting
  identical digests.
* **No future information.** Every read goes through one bound from `bound_for(T, K)`;
  there is no second query path.
* **No nondeterministic iteration.** Expiries, futures, legs, surfaces, observation refs
  and issue lists are all explicitly sorted. Tested by loading the same observations in
  reverse order and asserting an identical digest.
* **No mutable global state.** The builder holds its collaborators and nothing else;
  it is stateless between calls.
* Decimals are digested as strings, so float representation cannot cause drift.

The Phase 1 clock guard (`tools/check_clock_access.py`) passes over the new package.

---

## 6. Point-in-time behaviour

An observation participates only when `observed_at <= T` **and** `ingested_at <= K`.
The brief's worked example is a test: with `observed_at = 11:40, ingested_at = 11:44`,
`MarketState(T=11:45, K=11:42)` has zero legs and coverage 0.0, and
`MarketState(T=11:45, K=11:45)` has all six legs and coverage 1.0.

Also tested: an observation after `T` never participates even at a far-future `K`
(market-truth widens the knowledge axis, never valid time); a correction is invisible
before its own ingestion and visible after.

### One interaction that required a decision, stated explicitly

`K < T` is handled as `knowledge_at(K)`, **not** `market_truth_at(T, K)`.

Phase 1's `resolve_bound` rejects `MarketTruthAt` with `knowledge_as_of < valid_time`,
citing `10-REPLAY.md` §2. That approved decision was left untouched. It does not block
the request, because when `K < T` the knowledge constraint dominates: an observation
cannot be ingested before it was observed, so `ingested_at <= K` already implies
`observed_at <= K < T`, and the pair collapses to `knowledge_at(K)`.

The single input where the two readings differ is a row with recorded clock skew
(`observed_at > ingested_at`). There `knowledge_at(K)` is strictly narrower, which is
the correct default: it cannot manufacture look-ahead, and the skew is already recorded
as a `CLOCK_SKEW` quality issue rather than normalised away.

`12-API_SPEC.md` §2 rejects `knowledge_time < market_time` on `/market/state`, and that
rejection is implemented **at the endpoint** (422), not in the builder — the mechanism
is general, the HTTP edge is strict. The layer split is asserted by two tests.

---

## 7. Staleness

The budgets are the documented ones, reproduced exactly and asserted against the design
values by test: spot 5 s, futures 5 s, option quotes 30 s, option OI 60 s, greeks/IV
60 s, depth 10 s, index OHLC 60 s. None was invented. They are carried in
`staleness_policy_version` and feed `build_context_id`.

`QualityStatus` is `OK | WARNING | DEGRADED | UNRELIABLE`; the escalation table produces
only `OK`, `DEGRADED` and `UNRELIABLE` at state level, with `WARNING` used for
category-level breaches.

Quote, OI and greeks staleness are judged **separately** even though quote and OI arrive
on one row: a single budget for both would flag greeks that are perfectly current for
what they are.

### One reconciliation between two tables, stated explicitly

`04` §3 has a budget table whose "On breach" column gives a per-category severity, and
an escalation table that gives the state status. They interact for **depth**, whose
documented fallback is *"drop depth from the state rather than serve stale depth"*.

Implemented as: a dropped category leaves no stale value in the state, so it does not
count as "over budget" for escalation — the issue is still recorded, so the drop is
visible. Every other category over budget escalates per the literal table. This is an
interpretation of how the two tables compose and is flagged for verification.

`UNRELIABLE` states are still built and returned; only `is_usable_for_signals` goes
false. Suppressing them would hide the outage.

---

## 8. Coverage and missing data

Coverage is `present_legs / expected_legs`, where expected comes from the resolved
universe — so a leg with no visible observation is counted as missing rather than
silently omitted, which would make coverage a tautology over whatever happened to be
present.

**Missing stays missing.** Every observed value is `| None`; a sum over no observations
is `None`, not `0`; `pcr` is `None` when call OI is absent or zero. Tested explicitly,
including that a leg with greeks but no quote reports `oi=None` rather than `oi=0`. The
migration makes every observed column nullable for the same reason.

---

## 9. Coherence

All four modes implemented: `SNAPSHOT_ANCHORED`, `STREAM_ONLY`, `SNAPSHOT_STALE`,
`RECOVERING`, with `is_cross_sectional` true only for the first.

`ANCHOR_MAX_AGE` is 2× the chain poll interval per `04` §4 (30 s at the Phase 2 default
cadence) and is part of the build context. No anchor → `STREAM_ONLY`; anchor beyond the
budget → `SNAPSHOT_STALE`; a gap issue outranks everything → `RECOVERING`.

The architectural distinction is preserved in the type: an arbitrary collection of WS
messages reports `STREAM_ONLY` and `is_cross_sectional=False`, so it cannot be presented
as a synchronized snapshot.

---

## 10. Checkpoints and reconstruction

Selection is **exact match on the full identity tuple**. Tested prohibitions: a later-K
checkpoint does not satisfy an earlier-K request; an earlier-K checkpoint does not
satisfy a later-K request; different `market_time` and different `build_context_id` are
both rejected; and with three near-miss checkpoints stored, none is chosen. Nearest-match
is the subtle form of look-ahead and does not exist in the code.

Deduplication skips a byte-identical successor. The in-memory store is keyed by the same
tuple as the database's `UNIQUE` constraint, so tests exercise the real rule.

`test_a_checkpointed_state_equals_a_fresh_reconstruction` asserts identical
`content_digest()` **and** identical `as_comparable()` between materialization and full
reconstruction. Caching a reconstruction is opt-in, so a GET cannot grow
`state_checkpoints` unexpectedly.

---

## 11. Immutability, provenance, API

Every dataclass is `frozen=True, slots=True`; collections are tuples; surfaces are
`MappingProxyType`. Tested that the state, nested leaves and surfaces all reject
mutation.

Provenance exposes market time, knowledge horizon, the full `BuildContext` and its three
versions, sorted observation refs, quality, coherence and the content digest.

`GET /market/state` implements the `12-API_SPEC.md` §2 envelope: both times echoed,
`semantics` naming `knowledge_at` or `market_truth_at`, quality and provenance on every
response. Prices serialise as strings — a rupee price round-tripped through an IEEE
double is no longer the price the venue quoted. No `decision_time` parameter is offered,
because raw observations have no `available_at` and a state is their reorganisation. An
unconfigured process answers 503 rather than assembling from an invented default.

The four persisted time dimensions are unchanged; no new persistence column was added
for `decision_time`.

---

## 12. Tests and checks executed

| Check | Command | Result |
|---|---|---|
| Full suite | `python3 -m unittest discover -s tests -t .` | **342 tests: 332 pass, 10 errors, 6 skipped** |
| Phase 3 only | `tests/phase3/` | **86 pass** |
| Lint | `ruff check oipulse tools tests` | pass |
| Format | `ruff format --check oipulse tools tests` | pass, 96 files |
| Byte-compile | `python -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, 4 contracts |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 5 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 23 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 3 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 64 files |

**The 10 errors are pre-existing on the base and unrelated to Phase 3.** All are
`ModuleNotFoundError: No module named 'google'` from the Phase 2 V3 protobuf decoder
tests; `protobuf` cannot be installed here. The base commit produces the same 10 errors
before any Phase 3 code existed. **Zero regressions were introduced.**

Test counts by area: identity 13, point-in-time 9, checkpoints 13, quality/staleness/
coherence/determinism/immutability 33, API 16, plus 2 added to the Phase 2 migration
suite for the Phase 3 tables.

---

## 13. Known limitations and what was NOT executed

1. **`pytest` and `mypy --strict` were not run.** Neither is installable: DNS resolution
   fails for `pypi.org`. The stdlib approximation passes over all 64 files and is
   explicitly not equivalent to mypy — no inference, no assignment compatibility, and a
   nominal-only override check.
2. **`lint-imports` was not run** (import-linter not installable). The stdlib boundary
   guard passes, and `pyproject.toml` now declares `oipulse.marketstate` between `api`
   and `marketdata`.
3. **Migration 0003 was not applied to PostgreSQL.** No server is reachable. It is
   covered structurally and by the integration suite, which skips here and runs in CI.
4. **`/market/state` was not served over HTTP.** FastAPI is not installed. Routing is
   verified by parsing and the envelope by calling the pure `state_to_envelope`; that
   this is not an end-to-end HTTP test is the limitation.
5. **No PostgreSQL-backed `CheckpointStore` or `UniverseResolver`.** The Protocols and
   the schema exist; the in-memory implementations are what run. Wiring the durable ones
   needs a database to test against.
6. **`StaticUniverseResolver` applies only the expiry-activity temporal rule**, not full
   instrument-version history; that resolution lives in `instruments/` and needs the
   database.
7. **Two interpretations flagged for verification**, both argued above rather than made
   silently: the `K < T` bound (§6) and the depth-drop/escalation interaction (§7).
8. **One unrelated change was required by a gate.**
   `tests/integration/test_migrations_postgres.py` had an over-long line on the base and
   failed `ruff format --check`; it is reflowed with no behaviour change. Without it the
   required format gate cannot pass.
9. **`.env.example` is unreadable in this sandbox** and is excluded from the commit.

---

## 14. Phase boundary

No Phase 4+ code. No analytics, signals, research, replay, paper trading, risk, OMS,
portfolio, terminal or ML. Surfaces are reorganisations of observed values with **no
formula applied**; `spot.change`, `futures.basis` and `pcr` are differences and ratios
of observed values that `04` §2 lists as part of the state, not `MetricValue`s — none
carries a `feature_version` or an `available_at`, which is the line `04` §6 draws.

Phase 2 was not modified: no change to V3 decoding, REST, WebSocket ingestion,
normalization, PostgreSQL persistence, idempotency, `SubscriptionPlanner` or temporal
correctness. The only Phase 2 test edits update migration-chain assertions that
legitimately changed when revision 0003 was added.
