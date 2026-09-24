# Phase 7 — Replay + Backtesting: Implementation Report

**Status: PHASE 7 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION**

This report does not claim Phase 7 PASS. It states what was built, what was executed,
and what was not.

| | |
|---|---|
| Branch | `phase7-replay-backtest` |
| Base commit | `e30c056f3bd386af9d7d21f061ef07e42be52926` (current `origin/main`) |
| Design source | `docs/design/10-REPLAY.md`, `12-API_SPEC.md` §187/§193, `18-ROADMAP.md` Phase 7 |

## Checkpoint discrepancy (unchanged from prior phases, flagged again)

The brief names the verified Phase 6 checkpoint as
`5805f35abd944fc603d61ed1a9104560fd8a3a6f`, tag `oi-pulse-v2-phase6`. That commit is
present in the repository and its **tree is identical to current `origin/main`**, but
it is not an ancestor of `origin/main`, and the tag `oi-pulse-v2-phase6` is not present
locally. As instructed, the branch was created from current `origin/main`
(`e30c056`). Tree equality was checked; ancestry was not established. This has
occurred at each of the last four phases and is recorded rather than worked around.

---

## 1. Files changed

### New — replay (`oipulse/replay/`)

| File | Contents |
|---|---|
| `__init__.py` | Package exports |
| `ordering.py` | `SessionOrdinals`, `order_key`, `replay_order`, `MISSING_SEQUENCE` |
| `context.py` | `ReplayContext`, `ReplayPeriod`, `StepMode`, `KnowledgeMode`, `ReplaySpeed`, `IncoherentReplayHorizon` |
| `timeline.py` | `ReplayStep`, `ReplayTimeline`, `build_timeline`, `lockstep_horizons` |
| `engine.py` | `ReplayEngine`, `ReplayProgress`, `SteppedState`, `ResumePoint` |
| `serialisation.py` | Pure API envelopes |

### New — backtest (`oipulse/backtest/`)

| File | Contents |
|---|---|
| `__init__.py` | Package exports |
| `intents.py` | `TradeIntent`, `Side`, `OrderType` |
| `strategy.py` | `Strategy`, `StrategyContext`, `FeatureView`, `SignalView`, `AccountView`, `StrategySpec` |
| `fills.py` | `FillModel`, `Fill`, `FillOutcome`, `QuoteSnapshot`, `SlippageModel`, `PriceSource`, `RejectionReason`, `simulate_fill` |
| `costs.py` | `CostModel`, `CostBreakdown`, `INDIAN_OPTIONS_COSTS` |
| `ledger.py` | `Ledger`, `LedgerSnapshot`, `Position`, `fill_key` |
| `risk.py` | `RiskGate` protocol, `UnconstrainedRiskGate`, `RiskDecisionStub` |
| `runner.py` | `BacktestRunner`, `ScheduledIntent` |
| `result.py` | `BacktestResult`, `BacktestStatistics`, `BacktestExecutionMetadata` |
| `serialisation.py` | Pure API envelopes |

### New — other

- `oipulse/persistence/backtest_tables.py` — `replay_runs`, `backtest_results`, `backtest_trades`, `replay_events`
- `oipulse/migrations/versions/0007_phase7_replay_backtest.py`
- `oipulse/api/replay.py`, `oipulse/api/backtest.py`
- `tools/check_strategy_surface.py`
- `tests/phase7/` — `_fixtures.py` plus four test modules

### Modified

- `oipulse/api/app.py` — the two routers wired in
- `oipulse/observability/metrics.py` — Phase 7 metrics and three emit helpers
- `tools/check_import_boundaries.py` — four new contracts
- `pyproject.toml` — `backtest` and `replay` added to the layer ordering
- `tests/phase1/test_remediation.py` — chain length 8 → 9
- `tests/phase2/test_migrations.py` — chain, `PHASE7_TABLES`, five new tests
- `tests/integration/test_migrations_postgres.py` — Phase 7 tables and constraints

`.env.example` shows as modified in `git status`. Git cannot read it (sandbox
deny-list) and the entry is a phantom; it is excluded from the commit, as in every
prior phase.

---

## 2. Behaviour implemented

**Replay clock and ordering.** `ReplayClock` moves forward only. The cross-session key
is `(observed_at, feed_session_ordinal, channel_sequence, id)`; `channel_sequence`
resets per feed session, so the session ordinal is what makes the order total.
`MISSING_SEQUENCE = -1`, not 0, because a provider could legitimately emit sequence 0.
Ordinals are supplied, never re-derived per run.

**Timeline and visibility.** Three step modes (`CHECKPOINT`, `FIXED_INTERVAL`,
`EVENT`). `visible_observations` filters on **both** axes —
`observed_at <= T and ingested_at <= K` — so a late-arriving row cannot leak into a
lockstep replay.

**Knowledge-aware checkpoint selection.** Delegated to the verified Phase 3
`StateService`, which matches the full identity tuple. The engine asks the same
question first only so a run can *report* hits and misses.

**Decision / execution separation.** An intent decided at step `T` is filled against
the state at the first step at or after `T + latency`. An intent whose execution time
falls past the end of the period **expires unfilled** and is reported in
`coverage_warnings`. Within a step the order is: execute due fills → build context →
ask the strategy → risk-gate → schedule.

**Fill model.** No default constructor; every parameter is named at the call site and
the whole set is content-addressed onto the result. Where bid/ask is absent, a declared
spread is assumed around the last traded price and the fill is marked
`assumption_based`, which propagates to the run and into its caveats. Rejection and
partial sizing are deterministic functions of state and of the intent's content digest
— no `random` anywhere.

**Cost model.** `INDIAN_OPTIONS_COSTS` is named, versioned and states its basis in
prose. STT falls on the sell, stamp duty on the buy, GST on services but not on
statutory taxes. Gross P&L, each fee component, slippage and net P&L stay separate.

**Ledger.** Idempotent by content-addressed `fill_key`; duplicates are counted and
reported rather than silently ignored. An unmarked position is **named and excluded**
from unrealized P&L rather than marked at cost.

**Risk seam.** `RiskGate` protocol plus `UNCONSTRAINED_RISK`, which approves everything
and says so. A run using it is flagged `risk_evaluated=False` and carries a caveat
naming exactly what was not applied. No stand-in risk engine was built.

**Result identity.** `content_hash` covers the strategy digest, replay digest, build
context, fill-model digest, the full assumption set, statistics, ledger and fills; it
excludes `BacktestExecutionMetadata`, which the hash function never receives.

---

## 3. Tests run

Executed with `python3 -m unittest discover -s tests -t .` on the development sandbox.

| | Baseline (Phase 6, measured before any Phase 7 edit) | After Phase 7 |
|---|---|---|
| Tests run | 717 | 832 |
| Passed | 669 | 783 |
| Errors | 40 | 40 |
| Skipped | 8 | 9 |

**The 40 errors are the same set, with the same two root causes and no error of any
other kind**, verified by grouping every error message: 30 × `No module named
'fastapi'`, 10 × `No module named 'google'`. The additional skip is the new
PostgreSQL constraint test, which skips without a reachable database and which CI
fails on skip.

Phase 7 added 115 tests: 109 in `tests/phase7/`, 5 in `tests/phase2/test_migrations.py`
and 1 in `tests/integration/test_migrations_postgres.py`.

`tests/phase7/` breakdown:

| Module | Tests | Covers |
|---|---|---|
| `test_ordering_and_timeline.py` | 16 | clock, cross-session ordering, timeline, PIT reconstruction |
| `test_determinism_and_resume.py` | 14 | determinism, checkpoint selection, resume, corrections |
| `test_strategy_and_lookahead.py` | 16 | strategy surface, availability, decision/execution separation, intent identity |
| `test_execution_and_ledger.py` | 29 | price source, slippage, rejections, costs, ledger, fill determinism |
| `test_result_api_and_guards.py` | 34 | result identity, risk seam, envelopes, persistence, guards, observability, research compatibility, performance |

---

## 4. Gate results

| Check | Result |
|---|---|
| `tools/check_clock_access.py` | PASS |
| `tools/check_import_boundaries.py` | PASS — 12 contracts armed, 4 new |
| `tools/check_migration_chain.py` | PASS — single chain, 9 revisions, one head |
| `tools/check_migration_order.py` | PASS — 7 revisions |
| `tools/check_schema_parity.py` | PASS — 38 tables, upgrade/downgrade mirrored |
| `tools/check_alert_purity.py` | PASS |
| `tools/check_temporal_repository.py` | PASS |
| `tools/check_typing_strict.py` | PASS — 134 files (subset only) |
| `tools/check_strategy_surface.py` | PASS — new; mutation-tested |
| `ruff check .` | PASS |
| `ruff format --check` | PASS — 242 files |

The new `check_strategy_surface.py` was mutation-tested: adding
`store: PointInTimeAccessor` to `StrategyContext` produced two findings and exit 1, and
the guard returned to PASS when reverted. It is not merely printing PASS.

### New import contracts

- `replay-is-pure` — replay may import only `marketstate`, `marketdata`, `core`; no DB/HTTP.
- `replay-never-reaches-a-broker-or-a-provider` — no `trading`, `brokers`, `alerts`, `api`, no live provider.
- `backtest-is-pure-and-cannot-trade` — no `persistence`, `api`, `trading`, `alerts`, no wall clock.
- `no-phase-8-or-later-leakage` — no `paper`, `portfolio`, `oms`, `terminal`.

`core.clock` is deliberately **not** forbidden to `replay`: replay legitimately owns
`ReplayClock`, whose "now" is the replayed instant. The wall-clock rule is enforced for
every module by `check_clock_access.py`, which distinguishes the two; a blanket import
ban could not. My first draft of the contract did forbid it and correctly failed — the
contract was corrected, not the code.

---

## 5. Performance observations

Measured on the development sandbox against the synthetic fixture. These are
observations, not thresholds; no test asserts a timing target, because a hard threshold
would fail on slower CI hardware for no architectural reason.

| Measurement | Result |
|---|---|
| Replay throughput | 6 steps / 78 observations in ~2 ms (~31,000–45,000 obs/sec across runs) |
| Backtest end-to-end | ~3 ms |
| Ledger application | 10,000 fills in ~43–58 ms (~200,000 fills/sec) |
| Checkpoint reuse | 0 hits / 6 misses under lockstep with no pre-populated store, as expected |

The fixture is small by design. These figures characterise per-operation cost, not
behaviour at production volume, and should not be read as a capacity statement.

---

## 6. What was NOT executed

Stated explicitly, because a gate claim must be no broader than the checks run.

- **`mypy --strict` was not run.** mypy is not installed and there is no package index
  (DNS resolution for `pypi.org` fails). `tools/check_typing_strict.py` is a
  stdlib-AST subset covering annotations, bare generics, implicit `Optional`, ignore
  codes and overrides. It does **not** perform inference or assignment checking.
  `mypy --strict oipulse` remains authoritative and is unverified for Phase 7 code.
- **`lint-imports` was not run.** import-linter is not installed. The `pyproject.toml`
  layer ordering was updated and parses, but was not executed against the real import
  graph. The AST guard in `tools/check_import_boundaries.py` did run and passes.
- **The migration was not applied.** Alembic and PostgreSQL are both absent. Migration
  `0007` is verified structurally (chain, ordering, table inventory, constraint text,
  downgrade mirroring) and by the PostgreSQL integration test, which **skipped**.
  A skipped test is a reported gap, not a pass.
- **The FastAPI routers were not exercised over HTTP.** fastapi is not installed. The
  pure envelope functions in `replay/serialisation.py` and `backtest/serialisation.py`
  were tested directly, and the route paths were checked by parsing the router source.
  Request/response behaviour, status codes and dependency wiring are unverified.
- **No run against real Upstox data.** Every fixture is synthetic and says so in its
  module docstring.

---

## 7. Known limitations

1. **The risk engine is a declared seam, not an implementation.** `10` §7 specifies the
   production risk engine and OMS in the backtest path. Both are later phases. Runs are
   flagged `risk_evaluated=False` and carry a caveat naming what was not applied.
2. **Stop and bracket orders are absent**, not approximated. They need intra-bar path
   data a state sequence does not carry.
3. **Market impact is off by default**, per `10` §6 and §9. The parameter exists; the
   model does not.
4. **The cost schedule is an assumption.** `INDIAN_OPTIONS_COSTS` reflects commonly
   published discount-broker and statutory rates. Brokerage in particular is
   account-specific. The version travels onto every result.
5. **No SSE stream endpoint.** `12` §187 lists `GET /replay/sessions/{id}/stream`. The
   session/control/state endpoints are implemented; the stream is not, because the
   Phase 7 brief's API requirement does not name it and no consumer exists until the
   terminal (Phase 12). This is an omission from the API spec, stated rather than
   quietly dropped.
6. **`replay_events` is declared but not written.** The table and its namespace rule
   exist; no Phase 7 code emits a replay event, because nothing downstream consumes one
   yet. The structural guarantee (§8) is in place ahead of the producer.
7. **Persistence is schema-only.** No repository writes `backtest_results` or
   `backtest_trades`; the runner returns an in-memory artifact. Persisting it is the
   caller's job and the purity contract forbids `backtest/*` from doing it.

---

## 8. Remaining risks

1. **Unverified typing.** The largest gap. 134 files pass a subset check; none has been
   through `mypy --strict`. Verification should run it.
2. **Unapplied migration.** `0007` has never been executed by PostgreSQL. The Phase 2
   failure mode (`obs_depth` partitioned before it existed) was exactly this class of
   error and was invisible to source reading.
3. **Untested HTTP surface.** Two routers, roughly 250 lines, exercised only by AST.
4. **Fixture scale.** Determinism is demonstrated over 6 steps and 78 observations.
   Nothing here establishes behaviour over a full session, and the resume path in
   particular has been tested only across a single mid-timeline boundary.
5. **`SessionOrdinals` has no producer.** The ordering rule is correct and tested, but
   nothing in Phase 2 currently assigns and stores feed-session ordinals. Until it
   does, every real observation resolves through the unknown-session branch. This is a
   genuine gap between the design and the ingest path, and verification should confirm
   where the ordinals are meant to be assigned.

---

## 9. Git

One commit on `phase7-replay-backtest`, containing only Phase 7 work. No tag was
created. No merge into `main`. No force-push, reset or history rewrite.
