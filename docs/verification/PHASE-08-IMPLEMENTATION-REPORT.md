# Phase 8 — Paper Trading: Implementation Report

**Status: PHASE 8 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION**

This report does not claim Phase 8 PASS. It states what was built, what was executed,
and what was not.

| | |
|---|---|
| Branch | `phase8-paper-trading` |
| Base commit | `980822abec5ac001e934b42f375c47de8f967e95` (current `origin/main`) |
| Implementation commit | recorded in the completion message and in `git log`; a commit cannot contain its own hash, so writing one here would be stale the moment it was written |
| Design source | `docs/design/11-TRADING.md`, `12-API_SPEC.md` §199, `18-ROADMAP.md` Phase 8 |

## Checkpoint discrepancy (recurring; flagged again)

The brief names the verified Phase 7 checkpoint as `cb7a7df`, tag
`oi-pulse-v2-phase7`. `cb7a7df` exists and its **tree is identical to current
`origin/main`** (`980822a`), but it is **not an ancestor** of `origin/main`, and the
tag is not present locally. As instructed, the branch was created from current
`origin/main`. Tree equality was checked with `git diff --stat`; ancestry was checked
with `git merge-base --is-ancestor` and is absent. This has now occurred at five
consecutive phases and is recorded rather than worked around.

---

## 1. Paper architecture

```
Signal / Strategy ──► TradeIntent ──► [risk seam] ──► PaperOrder ──► PaperFill
                                                            │
                                                            ▼
                                        position · cash · P&L · journal · audit
```

Each arrow is a separate recorded step with its own identity, and each can fail
without the next happening. A signal is not an order; an intent is not a fill.

**One implementation, not two.** `11` §7 requires paper fills to come from the same
`FillModel` the backtester uses. `oipulse/trading/execution.py` contains **no pricing
arithmetic** — it delegates to `oipulse.backtest.fills.simulate_fill`. The ledger is
the verified Phase 7 `Ledger`, wrapped for account scoping. A test asserts a paper
fill and a backtest fill produce the identical price and cost for the same inputs.

### Modules

| File | Contents |
|---|---|
| `trading/accounts.py` | `AccountMode`, `AccountStatus`, `PaperAccountConfig`, `PaperAccount`, `resolve_execution_mode`, `LiveExecutionUnavailable` |
| `trading/intents.py` | `TradeIntent` (multi-leg), `IntentLeg`, `IntentConstraints`, `TimeInForce`, `from_strategy_intent` |
| `trading/orders.py` | `OrderState`, the declared transition table, `OrderEvent`, `PaperOrder`, `RejectReason`, `InvalidTransition` |
| `trading/risk.py` | `RiskGate` protocol, `RiskDecisionRecord`, `UNEVALUATED_RISK` |
| `trading/execution.py` | `PaperExecutionModel`, `ExecutionOutcome` |
| `trading/brokers.py` | `BrokerAdapter` protocol, `PaperBrokerAdapter`, `adapter_for` |
| `trading/ledger.py` | `PaperLedger`, `PaperLedgerSnapshot`, `replay_fills`, `InsufficientCash` |
| `trading/events.py` | `TransactionalInbox`, `AggregateWatermarks`, event factories |
| `trading/runtime.py` | `PaperTradingRuntime`, `SubmissionResult` |
| `trading/audit.py` | `AuditChain`, `build_audit_chain` |
| `trading/serialisation.py` | Pure API envelopes |

Also: `persistence/trading_tables.py`, `migrations/versions/0008_phase8_paper_trading.py`,
`api/paper_trading.py`, `tools/check_paper_trading_safety.py`, `tests/phase8/`.

---

## 2. Paper-only safeguards

`11` §9 disables live trading by feature flag. Phase 8 goes further: **the
implementation does not exist.** A disabled feature is one configuration change from
being enabled; an absent implementation is not, and a guard can verify absence.

| Control | Mechanism |
|---|---|
| No live adapter | Exactly one `BrokerAdapter` implementation (`PaperBrokerAdapter`). No `UpstoxBrokerAdapter` anywhere in `oipulse/`. |
| No broker reachable | Import contract forbids `marketdata.providers`, `marketdata.upstox` |
| No credential reachable | Import contract forbids `marketdata.auth`, `core.config`, `core.secrets` |
| No network | Guard rejects any `post`/`put`/`request`/`send`/`urlopen`/`connect` call under `trading/` |
| No mode switch | `resolve_execution_mode` is the one function turning mode into a path, and it raises for anything but `PAPER`; a runtime cannot be constructed for a non-paper account |
| Database refuses it | `ck_trade_accounts_paper_only` CHECKs `mode = 'PAPER'` |
| No API escape hatch | No `mode` parameter on any endpoint; `POST /accounts` with a non-paper mode is 422, refused rather than coerced |
| Mode always stated | Every envelope carries `mode: "PAPER"` and `live_execution_available: false` from one constant |

`tools/check_paper_trading_safety.py` enforces five of these mechanically and was
**mutation-tested**: introducing a second adapter, an outbound `client.post(...)`, a
non-raising `resolve_execution_mode`, and an envelope claiming `LIVE` each produced a
finding and exit 1; the guard returned to PASS when each was reverted.

One mutation initially appeared not to bite. Investigation showed the mutation string
had not matched (ruff had reformatted the target onto one line), so the check had
never run. Re-applied correctly, it bit. Recorded because a mutation test that silently
fails to mutate is indistinguishable from a guard that does not work.

---

## 3. Account, order and execution models

**Account** (`11` §7). Identity, owner, mode, versioned content-addressed config
(starting cash, currency, fill model, cost model, reservation policy, order caps),
and a lifecycle `INITIALISED → ACTIVE ⇄ SUSPENDED → CLOSED` with a declared transition
table. **The account holds no balance** — `11` §8 requires positions to be a fold over
fills, and an account carrying a mutable total is exactly the drifting total the design
forbids. A test asserts no balance field exists.

**Order state machine** (`11` §4). A declared table; invalid transitions raise. Phase 8
implements `CREATED → ACCEPTED → OPEN → PARTIALLY_FILLED → FILLED` plus `CANCELLED`,
`REJECTED`, `EXPIRED`. Every permitted transition and **every** forbidden one are
tested by walking the table itself, so a transition added later is covered
automatically.

`SUBMITTED`, `UNKNOWN` and `PENDING_RECONCILIATION` from `11` §4 are **not**
implemented. They exist because a network sits between us and a broker and an
acknowledgement can be lost. A paper order crosses no network; simulating that
ambiguity would model a failure mode that cannot occur. Phase 10 owns them. Recorded
as a limitation below.

**Execution model.** Delegates pricing to the Phase 7 fill model; adds expiry against
market time, the `max_slippage` constraint, `all_or_none`, account order caps, cash
checks and unreliable-state refusal. Every assumption is identified by
`FillModel.content_digest()` and the account's `config_digest`.

---

## 4. PIT and knowledge-horizon semantics

Two clocks, kept apart. The runtime is driven **entirely** by supplied market time and
never reads a wall clock — `check_clock_access.py` enforces that no module can, and the
import contract additionally forbids `trading` from importing `core.clock` at all.

`submit()` takes `decision_state` and `execution_state` as **separate parameters**. The
strategy saw the first; the fill is priced against the second. A test asserts the fill
price comes from the later state (106.40 at minute 1, not the 101 the decision saw).

Phase 5 knowledge-horizon semantics are reused unweakened. The brief's worked case —
`market_time = T`, `feature available_at = T+2s`, `decision K = T` — is tested: the
read raises `FeatureAccessError` with "look-ahead refused", and the same feature is
consumable at `T+2s`, so the refusal is not blanket.

Execution timestamps do not overwrite decision times: a test submits at minute 2 for a
decision made at minute 0 and asserts `decision_time` and `knowledge_time` are
unchanged while `created_at` is the execution instant.

---

## 5. Ledger, reconciliation and recovery

Built on the verified Phase 7 `Ledger`. Phase 8 adds reserved cash (per-order, so a
cancel releases exactly what that order committed) and event-sourced reconstruction.

`replay_fills` is the **only** reconstruction path — the same `apply` loop the live
fold uses, so a rebuilt account and a continuously-run one cannot disagree. Tested:
event stream → state equals the live state; replaying the same stream twice is
identical; replaying a stream containing triplicate fills produces the same state and
reports the duplicates it ignored.

One additive change was made to Phase 7: `Ledger.has_applied(fill)`, a read-only
accessor. Phase 8 needs to know whether a redelivered fill would be a no-op *without*
applying it. The alternative was for `PaperLedger` to mirror the applied-set, creating
a second bookkeeping system for the two to disagree about. No Phase 7 behaviour
changed and all 29 Phase 7 ledger tests still pass.

---

## 6. Idempotency and the event model

**What is guaranteed, stated precisely** (`03` §4): *exactly-once database application
per `(subscriber, event_id)` transaction*. This is **not** global exactly-once
processing and nothing in the implementation claims it is.

Reuses the verified Phase 1 event layer rather than reimplementing it: `DomainEvent`,
`check_sequence`, and the transactional inbox.

A design error was caught during implementation and fixed. The first version of
`apply_ordered` called `inbox.claim` directly; a mutation that raised would leave the
claim in place, suppressing the retry and losing the write — precisely the failure the
transactional inbox exists to prevent. It now uses `apply_once`, which owns both
halves, and advances the watermark **inside** the mutation so a rolled-back event does
not permanently defer its successor. Both properties are tested.

A second bug was caught by the sequence check itself during the first end-to-end run:
only the last order transition was emitted as a domain event, leaving sequence 1
missing and the fill event deferred forever. Every appended transition is now emitted.

Tested: duplicate intent, duplicate fill, duplicate order event, retry after failure,
restart, reordering (deferred, not dropped), and events behind the watermark (absorbed,
not alerted). Deterministic identities: `intent_id` content-addressed,
`order_id = f(intent_id, leg_index, account_id)`, `fill_key` from Phase 7.

---

## 7. Audit chain

All thirteen questions the brief §14 requires are answered by reference traversal.
Missing references read `not recorded` — never a reconstruction, because a plausible
guess in an audit trail is worse than a visible gap. Assembling a chain across a
mismatched order and intent is refused.

---

## 8. Risk seam

`RiskGate` protocol plus `UNEVALUATED_RISK`, which approves everything and says so.
`RiskDecisionRecord` carries `sequence_no`, `risk_state_ref`, `risk_evaluation_time`,
`inputs_digest` and `approved_until` from the start, so Phase 9 fills them rather than
migrating the shape. No risk engine was built; `risk.py` does not import the modules it
constrains, and a test asserts that.

---

## 9. Tests

`python3 -m unittest discover -s tests -t .` on the development sandbox.

| | Baseline (Phase 7, measured before any Phase 8 edit) | After Phase 8 |
|---|---|---|
| Tests run | 836 | 976 |
| Passed | 783 | 921 |
| Failures | 0 | 0 |
| Errors | 44 | 44 |
| Skipped | 9 | 11 |

**The 44 errors are the same set, with the same two root causes and no error of any
other kind**, verified by grouping every error message: 34 × `No module named
'fastapi'`, 10 × `No module named 'google'`. The two additional skips are the new
PostgreSQL tests, which skip without a reachable database and which CI fails on skip.

Phase 8 added 140 tests: 133 in `tests/phase8/`, 5 in `tests/phase2/test_migrations.py`
and 2 in `tests/integration/test_migrations_postgres.py`.

| Module | Tests | Covers |
|---|---|---|
| `test_accounts_and_orders.py` | 34 | account lifecycle, paper-only boundary, config identity, every state transition, fills, order identity |
| `test_execution_and_failures.py` | 28 | decision/execution separation, knowledge horizon, every failure path, assumption-based fills, shared fill model |
| `test_idempotency_and_recovery.py` | 32 | intent/fill idempotency, inbox and ordering, reservation, ledger determinism, restart recovery |
| `test_audit_api_and_guards.py` | 39 | audit chain, envelopes, API surface, persistence shape, guards, observability |

### One existing test was changed

`tests/phase7/test_result_api_and_guards.py::test_no_phase_8_or_later_package_was_created`
asserted that `oipulse/trading` does not exist. That was correct while Phase 7 was the
frontier and is now false for an authorised reason.

It was **not** deleted or weakened. It was replaced with the stronger property it was
actually protecting —
`test_phase_7_does_not_depend_on_any_later_phase` — which parses `replay/` and
`backtest/` and asserts neither imports `trading`, `paper`, `portfolio`, `terminal` or
`oms`. Existence was only ever a proxy for non-dependence, and the dependency form keeps
biting as later phases land where the existence form would have to be relaxed at each
one. The reasoning is recorded in the test's docstring.

---

## 10. Gate results

| Check | Result |
|---|---|
| `tools/check_paper_trading_safety.py` | PASS — new; mutation-tested on 4 of its 5 checks |
| `tools/check_import_boundaries.py` | PASS — 15 contracts armed, 3 new |
| `tools/check_clock_access.py` | PASS |
| `tools/check_migration_chain.py` | PASS — single chain, 10 revisions, one head |
| `tools/check_migration_order.py` | PASS — 8 revisions |
| `tools/check_schema_parity.py` | PASS — 45 tables, upgrade/downgrade mirrored |
| `tools/check_strategy_surface.py` | PASS |
| `tools/check_alert_purity.py` | PASS |
| `tools/check_temporal_repository.py` | PASS |
| `tools/check_typing_strict.py` | PASS — 149 files (subset only) |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS — 265 files |
| `python -m compileall -q oipulse tools tests` | PASS |

New import contracts: `paper-trading-is-pure`, `paper-trading-cannot-reach-a-broker`,
`paper-trading-imports-no-later-phase`. `risk-is-independent` was extended to name
`trading.orders`, `trading.execution`, `trading.runtime` and `trading.ledger` — the
Phase 8 equivalents of the documented `trading.oms`, which the contract would otherwise
have passed over because the module layout differs from the name in the design.

---

## 11. What was NOT executed

Stated explicitly, because a gate claim must be no broader than the checks run.

- **`pytest -v` was not run.** pytest is not installed and there is no package index
  (DNS for `pypi.org` fails). The suite was executed with `unittest`, which discovers
  and runs the same tests.
- **`mypy --strict oipulse --show-error-codes` was not run.** mypy is not installed.
  `tools/check_typing_strict.py` is a stdlib-AST subset covering annotations, bare
  generics, implicit `Optional`, ignore codes and overrides. It performs **no inference
  and no assignment checking**. `mypy --strict` remains authoritative and is unverified
  for Phase 8 code. This is the largest gap.
- **`lint-imports` was not run.** import-linter is not installed. The `pyproject.toml`
  layer ordering was updated and parses, but was never executed against the real import
  graph. The AST guard did run and passes.
- **Migration `0008` was never applied.** Alembic and PostgreSQL are both absent. It is
  verified structurally (chain, ordering, table inventory, constraint text, downgrade
  mirroring) and by two PostgreSQL integration tests which **skipped**. A skipped test
  is a reported gap, not a pass. In particular, the test that inserts a `LIVE` account
  row and requires the database to refuse it has never actually run.
- **The FastAPI router was not exercised over HTTP.** fastapi is not installed. The pure
  envelopes were tested directly and the route surface by parsing the router source.
  Status codes, dependency wiring and request validation are unverified.
- **Concurrency was not tested under real concurrency.** Brief §20 asks for concurrent
  requests. The idempotency properties are tested by repeated sequential application,
  which is what the in-memory runtime can demonstrate honestly. True concurrent safety
  rests on the database constraints, and those are in the skipped PostgreSQL tests.
- **No run against real Upstox data.** Every fixture is synthetic and says so in its
  module docstring.

---

## 12. Known limitations

1. **`UNKNOWN` and `PENDING_RECONCILIATION` are not implemented.** They model network
   ambiguity, which a paper venue does not have. `11` §7 notes the paper adapter can be
   *instructed* to simulate `UNKNOWN` so the reconciliation path is exercised — that
   needs a reconciliation path, which is Phase 10.
2. **The risk engine is a declared seam, not an implementation.** `11` §1 makes risk
   non-bypassable and `02` §11 requires an order row to reference an approved decision.
   Phase 8 records the decision sequence but the database does not yet enforce the
   reference, because there is no engine to produce a real one. Phase 9 adds both.
3. **`GTC` is not supported.** A good-till-cancelled paper order would rest across
   sessions and simulating that needs data the observation store does not have. `DAY`
   and `IOC` only.
4. **Persistence is schema-only.** No repository writes `trade_orders` or `trade_fills`;
   the runtime holds an in-memory book. Persisting is the caller's job and the purity
   contract forbids `trading/*` from doing it. **The durable half of idempotency is
   therefore declared but not exercised.**
5. **No order resting.** Every order is submitted and resolved within one call. A
   genuinely resting `OPEN` order that fills on a later tick is reachable through the
   state machine but the runtime has no scheduler driving it; `expire_stale_orders`
   exists and is the only time-driven sweep.
6. **Two `TradeIntent` types exist.** `backtest.intents.TradeIntent` is single-leg and
   is the strategy's output; `trading.intents.TradeIntent` is the multi-leg
   account-scoped seam `11` §2 defines. `from_strategy_intent` converts explicitly.
   This is a deliberate separation, but it is duplication and a reviewer should confirm
   the split is the right one.
7. **The cost schedule is an assumption** (inherited from Phase 7): commonly published
   discount-broker and statutory rates, account-specific in reality, versioned so a
   result records which schedule produced it.
8. **Multi-leg is modelled but lightly exercised.** Legs are submitted independently and
   `all_or_none` is enforced per leg, not across the spread. A genuine spread whose legs
   must fill together is not yet expressible.

---

## 13. Remaining risks

1. **Unverified typing.** 149 files pass a subset check; none has been through
   `mypy --strict`. Verification should run it.
2. **Unapplied migration.** `0008` has never been executed by PostgreSQL. The Phase 2
   failure (`obs_depth` partitioned before it existed) was exactly this class of error
   and invisible to source reading.
3. **The paper-only claim rests partly on guards that only this repository runs.** The
   import contract and the safety guard are AST-based and pass, but `lint-imports` —
   which would check the *real* transitive graph — has not run. A transitive path to a
   credential through a module the AST guard does not model is the residual risk.
4. **Untested HTTP surface.** ~330 lines of router exercised only by AST parse.
5. **No concurrency testing.** Item 4 in §11.

---

## 14. Git

One commit on `phase8-paper-trading`, containing only Phase 8 work. No tag was created.
No merge into `main`. No force-push, reset or history rewrite.
