# Phase 9 — Risk: Implementation Report

**Status: PHASE 9 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION**

This report does not claim Phase 9 PASS. It states what was built, what was executed,
and what was not.

| | |
|---|---|
| Branch | `phase9-risk` |
| Base commit | `4117531fe5b826e034bed4bec6c950939f52881f` (current `origin/main`) |
| Implementation commit | recorded in the completion message and in `git log`; a commit cannot contain its own hash |
| Design source | `docs/design/11-TRADING.md` §3, `02-DATA_MODEL.md` §6/§11, `12-API_SPEC.md` §204, `18-ROADMAP.md` Phase 9 |

## Checkpoint discrepancy (recurring; flagged again)

The brief names the verified Phase 8 checkpoint as
`43bbd5cf2823819e10812baddcabc478efb8b237`, tag `oi-pulse-v2-phase8`. That commit
exists and its **tree is identical to current `origin/main`** (`4117531`), verified
with `git diff --stat`, but it is **not an ancestor** of `origin/main`
(`git merge-base --is-ancestor` → false), and the tag is not present locally. As
instructed, the branch was created from current `origin/main`. This has now occurred
at six consecutive phases.

Two environment notes: `git fetch` could not lock credential storage (read-only file
system) but fetched successfully, and `git checkout -b` could not write upstream
branch configuration because a stale `.git/config.lock` exists. The branch was created
at the correct SHA regardless. I have not removed the lock file — that is the user's
repository state, not mine to clear.

---

## 1. The safety invariant

```
TradeIntent → RiskEvaluation → approved RiskDecision → execution authorization
```

`18-ROADMAP.md` Phase 9 requires this to hold three ways — **static, database
constraint and runtime** — and all three are implemented:

| Mechanism | Where |
|---|---|
| Static | `tools/check_risk_authorization.py` — six checks, all mutation-tested |
| Database | Migration `0009`: composite FK on `(intent_id, authorizing_decision_sequence)` → `risk_decisions(intent_id, sequence_no)`, plus a trigger asserting the referenced decision approves |
| Runtime | `PaperTradingRuntime.submit` refuses before constructing an order; the guard verifies the check *precedes* construction positionally in the AST |

The FK and the trigger are both needed. The FK alone would accept a `REJECTED`
decision; keying it on the order's **own** `intent_id` is what makes another intent's
approval unreferenceable, and only the trigger can express "must approve".

---

## 2. Modules

`oipulse/trading/risk.py` became `oipulse/trading/risk/` — the roadmap names the
module `trading/risk`, and the engine needs more than one file. Every Phase 8 public
name is preserved and re-exported.

| File | Contents |
|---|---|
| `risk/policy.py` | `RiskPolicy`, `RiskLimits`, `SessionWindow`, `LimitCategory`, `CONSERVATIVE_LIMITS` |
| `risk/state.py` | `RiskState`, `PositionSnapshot`, `ExposureSnapshot`, `KillSwitchState`, `StateQualityInput`, `VenueHealth`, `build_exposure` |
| `risk/decision.py` | `RiskDecisionRecord`, `LimitEvaluation`, `LimitStatus`, `RiskVerdict`, `AuthorizationStatus`, `inputs_digest_for` |
| `risk/limits.py` | 27 pure limit checks and `evaluate_all` |
| `risk/engine.py` | `RiskEngine` |
| `risk/gate.py` | `RiskGate` protocol, `UnevaluatedRiskGate` |
| `risk/serialisation.py` | Pure API envelopes |

Also: `oipulse/persistence/risk_tables.py`, `oipulse/migrations/versions/0009_phase9_risk.py`,
`oipulse/api/risk.py`, `tools/check_risk_authorization.py`, `tests/phase9/`.

---

## 3. RiskState

An **immutable value**, not a view onto a live ledger. Built once, hashed, and the
hash recorded on the decision as `risk_state_ref`.

Every collection is sorted on construction — positions, exposure buckets, pending
intents, halted strategies, marks, instrument grouping. Brief §14's cases are tested:
reordered positions, reordered intents, reordered buckets and a different process all
produce an identical ref, while a changed position or balance produces a different one.

**Nothing is computed here.** Brief §15 forbids a second analytics engine, so exposure
and greeks arrive as inputs. A missing input is carried as `None` and evaluated as
`NOT_EVALUABLE` — never substituted with a stale, latest, zero, previous or estimated
value. A test asserts the substitution message explicitly.

`RiskState` also carries `marks` and `instrument_underlying` for instruments the
evaluation may need to price or group but does not yet hold. Both were added after
discovering that deriving them from the position book made **every opening trade**
unpriceable and ungroupable, and therefore fail-closed.

---

## 4. RiskPolicy and versioning

Content-addressed over every limit, the description and the scope. A test walks
fifteen limit fields and asserts each one changes `policy_digest`; a version bump and
a description change do too.

`18` Phase 9 names over-permissive defaults as the risk and requires "conservative
defaults requiring explicit relaxation, audited". Three things implement that:

* **No default policy object.** `RiskPolicy` requires an explicit `RiskLimits`.
* **`CONSERVATIVE_LIMITS`** configures every category; a test asserts it leaves no
  limit `NOT_CONFIGURED`.
* **An unset limit is recorded, not skipped.** It evaluates to `NOT_CONFIGURED` and
  appears in `limits_evaluated`, so relaxation is visible in every decision made
  afterwards rather than invisible.

---

## 5. Evaluation

All 27 checks run in a declared order and **nothing short-circuits** (`11` §3). A test
asserts two breaches are both reported and that all 27 evaluations are present.

Four statuses, deliberately distinct:

| Status | Meaning | Blocks? |
|---|---|---|
| `PASSED` | configured, evaluated, within | no |
| `BREACHED` | configured, evaluated, exceeded | yes |
| `NOT_CONFIGURED` | the policy declares no such limit — an audited relaxation | no |
| `NOT_EVALUABLE` | configured but an input was missing — a data problem | **yes** |

Collapsing the last two into "passed" is the specific way a risk report overstates
what it checked, so they are separate values and both are stored. Every category in
`11` §3's table is covered; a test asserts that by enumerating `LimitCategory`.

**Fail-closed** (brief §22), three ways: a breach rejects, an unevaluable configured
limit rejects, and a knowledge-horizon violation rejects before any limit runs.

---

## 6. PIT and knowledge semantics

Brief §13's worked case is tested: a state assembled at K2 read by an evaluation at
K1 < K2 is refused, the horizon is checked before any limit, and a state exactly at
the horizon is permitted.

**I got this backwards in the first draft** and the Phase 8 suite caught it. My engine
compared the state's horizon against the *intent's*, which would have refused every
re-evaluation. `11` §3 is explicit that a later risk evaluation is *supposed* to see a
later state than the strategy did — that contrast is the reason `risk_state_ref` exists
separately from `state_checkpoint_ref`. The horizon under test is the **evaluation's
own**, which now defaults to `at` and can be passed explicitly. A test asserts a later
evaluation may read a later state.

Nothing reads a clock. A test walks the AST of every risk module and asserts no call
to `now`, `utcnow`, `today`, `time` or `monotonic`.

---

## 7. Decisions

`RiskDecisionRecord` carries the four mandatory fields with single defined meanings:
`risk_state_ref`, `risk_evaluation_time`, `inputs_digest`, `approved_until`, plus
policy identity, requested vs approved quantity, and structured `LimitEvaluation`
evidence.

`inputs_digest` hashes exactly three things — intent digest, policy digest, state ref.
The evaluation *time* is excluded, because including it would make the digest unable
to demonstrate reproducibility, which is its only purpose. A test re-derives it
independently.

**`EXPIRED` is not a stored verdict.** `11` §3 defines `APPROVED | REJECTED |
MODIFIED`, and a decision is immutable — it does not change when time passes, it stops
being *actionable*. `authorization_status(at)` renders `UNEVALUATED | APPROVED |
MODIFIED | REJECTED | EXPIRED` for a reader. Storing `EXPIRED` would mean mutating an
append-only record.

Expiry boundary is **inclusive** and tested at T−ε, T and T+ε.

Intent scoping: `authorizes(intent_id, at=...)` compares the id. Two intents identical
in symbol, quantity, strategy and timestamp have different content-addressed ids, and
a test asserts the second is refused.

---

## 8. Resizing

Off by default — silently trading less than a strategy asked for is a behaviour an
operator should opt into. When enabled, only genuinely size-based limits are resizable;
a kill switch, a stale state or a closed session cannot be satisfied by trading less,
and a test asserts a kill-switch breach is never resized away.

**The intent is never mutated.** A test captures `content_digest` before and after and
asserts both the digest and the quantity are unchanged. Multi-leg intents reduce
pro-rata by largest-remainder so a spread keeps its shape.

---

## 9. Paper-trading integration — an intentional behaviour change

Brief §18 requires integration without a bypass, and §17/§22 require that no approved
decision means no authorization. Together these change verified Phase 8 behaviour:

> **An account with no risk policy can no longer place an order.**

`UnevaluatedRiskGate` still exists and still approves, but its decisions carry
`evaluated=False`, and `is_actionable_at` returns False for any unevaluated decision.
The runtime therefore refuses. This is the fail-closed default and it is the barrier
the phase exists to build.

Consequences, all handled explicitly rather than papered over:

* `tests/phase8/_fixtures.py` now attaches a **real** `RiskEngine` with wide but
  genuinely evaluated limits. The data, session and kill-switch checks stay on, so a
  Phase 8 test cannot pass because risk was switched off. `unevaluated_risk=True`
  returns the pass-through for tests that assert the barrier.
* Four Phase 8 failure tests moved layer: insufficient cash, oversize quantity and an
  unknown instrument are now refused at the gate, producing **no order at all**
  rather than a rejected one. Each was rewritten to assert the property at the layer
  that now owns it, with the reasoning in its docstring. `all_or_none` was funded so
  it still reaches execution, where it belongs.
* Five Phase 8 tests whose premise Phase 9 changed (`no RiskEngine exists`, `risk is a
  single module`, `the chain reports no evaluation`, `the unrisked caveat`, `orders
  envelope counts rejections`) were replaced with the stronger property each was
  protecting, not deleted.

**No test was weakened to obtain a green result.** Every change is recorded above and
in the test docstrings.

---

## 10. Persistence

`risk_profiles` and `risk_decisions`, plus two columns, a CHECK, a composite FK, an
index and a trigger on `trade_orders`.

* `PRIMARY KEY (intent_id, sequence_no)` with **no** unique-per-intent constraint —
  `02` §6 says so explicitly, because re-evaluation is normal. A test asserts the
  absence as well as the presence.
* `uq_risk_profiles_identity` and `uq_risk_profiles_digest` make a policy version
  immutable.
* CHECKs: a rejection approves nothing, an approval carries a validity horizon,
  approved never exceeds requested, and the authorization key is whole or absent.

---

## 11. API

`/risk/profiles` (GET/PUT), `/risk/state/{account}`, `/risk/status/{account}`,
`/risk/decisions`, `/risk/decisions/{intent}/{seq}`, `/risk/kill-switch` (POST/DELETE),
and `POST /risk/evaluate`.

**No endpoint approves an intent** (brief §25). `/risk/evaluate` triggers a
server-side evaluation; it does not accept a verdict. The six forgeries brief §26
requires to be impossible are prevented structurally rather than by validation: there
is **no `dict → RiskDecisionRecord` direction anywhere in the codebase**, which a test
asserts by scanning for any decision deserialiser.

---

## 12. Tests

`python3 -m unittest discover -s tests -t .` on the development sandbox.

| | Baseline (Phase 8, measured before any Phase 9 edit) | After Phase 9 |
|---|---|---|
| Tests run | 981 | 1132 |
| Passed | 921 | 1069 |
| Failures | 0 | 0 |
| Errors | 49 | 49 |
| Skipped | 11 | 14 |

**The 49 errors are the same set, with the same two root causes and no error of any
other kind**: 39 × `No module named 'fastapi'`, 10 × `No module named 'google'`. The
three additional skips are the new PostgreSQL tests, which CI fails on skip.

Phase 9 added 151 tests: 141 in `tests/phase9/`, 7 in `tests/phase2/test_migrations.py`
and 3 in `tests/integration/test_migrations_postgres.py`.

| Module | Tests | Covers |
|---|---|---|
| `test_state_policy_and_determinism.py` | 34 | state identity, row-order independence, policy versioning, digest reproducibility, conservative defaults |
| `test_limits_and_decisions.py` | 56 | every limit at its boundary, data/session/kill-switch, fail-closed, knowledge horizon, expiry, intent scoping, resizing |
| `test_integration_and_guards.py` | 51 | paper integration, no-execution-without-approval (all three mechanisms), idempotency, concurrency, audit, API safety, persistence, guards, observability |

---

## 13. Gate results

| Check | Result |
|---|---|
| `tools/check_risk_authorization.py` | PASS — new; all six checks mutation-tested |
| `tools/check_paper_trading_safety.py` | PASS |
| `tools/check_import_boundaries.py` | PASS — 15 contracts |
| `tools/check_clock_access.py` | PASS |
| `tools/check_migration_chain.py` | PASS — single chain, 11 revisions, one head |
| `tools/check_migration_order.py` | PASS — 9 revisions, now chain-aware |
| `tools/check_schema_parity.py` | PASS — 47 tables |
| `tools/check_strategy_surface.py` | PASS |
| `tools/check_alert_purity.py` | PASS |
| `tools/check_temporal_repository.py` | PASS |
| `tools/check_typing_strict.py` | PASS — 161 files (subset only) |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS — 282 files |
| `python -m compileall -q oipulse tools tests` | PASS |

### Guard mutation tests

All six checks in `check_risk_authorization.py` were mutated and each produced a
finding and exit 1: removing an authorization field, constructing a `PaperOrder`
outside the runtime, importing `trading.brokers` from risk, assigning to an intent
attribute, dropping the `intent_id` comparison in `authorizes`, and removing the
approval check from `submit`.

**One mutation exposed a real weakness in my own guard.** The `authorizes` check
originally searched the unparsed source for the string `intent_id`, which always
succeeds because that is the parameter's name — so the check could never fail. It now
looks for an AST `Compare` between the parameter and `self.intent_id`, and the
mutation bites.

### `check_migration_order.py` was extended

It previously checked each revision in isolation, which flagged Phase 9's legitimate
`ALTER` of the Phase 8 `trade_orders`. It now walks the **revision chain** and seeds
each revision with the tables earlier ones created. Chain order is followed rather
than filename order, because `0001_` sorts before `001_`. A new test asserts the
relaxation did not disarm it: a use-before-create *within* one revision is still
caught, verified both by that test and by mutation.

---

## 14. What was NOT executed

Stated explicitly, because a gate claim must be no broader than the checks run.

- **`pytest -v` was not run.** pytest is not installed and there is no package index
  (DNS for `pypi.org` fails). The suite ran under `unittest`, which discovers the same
  tests.
- **`mypy --strict oipulse --show-error-codes` was not run.** mypy is not installed.
  `tools/check_typing_strict.py` is a stdlib-AST subset with **no inference and no
  assignment checking**. `mypy --strict` remains authoritative and is unverified for
  Phase 9 code. This is the largest gap.
- **`lint-imports` was not run.** import-linter is not installed. The AST guard did run.
- **Migration `0009` was never applied.** Alembic and PostgreSQL are both absent. The
  three PostgreSQL tests **skipped** — including the two that matter most, which
  insert an order authorized by a `REJECTED` decision and by another intent's decision
  and require the database to refuse both. **The trigger and the composite FK have
  never actually executed.** A skipped test is a reported gap, not a pass.
- **The `/risk` router was not exercised over HTTP.** fastapi is not installed. The
  pure envelopes were tested directly and the route surface by AST parse.
- **Concurrency was not tested under real concurrency.** Brief §20 asks for competing
  intents; the paper runtime evaluates at submission, so the test asserts the
  *sequential* semantics the design actually provides — the second intent sees the
  first's cash outflow. True concurrent safety rests on the database constraints,
  which are in the skipped tests.
- **No run against real Upstox data.** Every fixture is synthetic and says so.

---

## 15. Known limitations

1. **Greeks are never populated by the runtime.** Brief §15 forbids a second analytics
   engine, and wiring the Phase 4 greek features into `RiskState` is not done. The
   consequence is honest but inconvenient: a policy configuring a greek limit
   fails closed on every evaluation. The Phase 8 and Phase 9 runtime fixtures
   therefore leave greek caps **unset**, which the audit records as a relaxation. A
   production account wanting greek limits needs the feature wiring first.
2. **`orders_in_interval` is the account's total order count**, not a windowed count.
   The rate limit therefore tightens over an account's life instead of measuring the
   policy's interval. The interval is declared and stored but not yet applied.
3. **`pending_intent_ids` is populated but not consumed by any limit.** In-flight
   intents do not yet reserve headroom, so two intents submitted before either fills
   could each pass a check their sum would fail. The paper runtime fills
   synchronously, so this is latent rather than live — but it is the concurrency gap
   brief §20 points at.
4. **The unevaluated gate is still reachable.** An account constructed without a
   policy gets `UNEVALUATED_RISK` and cannot trade. That is fail-closed and correct,
   but it means a misconfiguration presents as "every intent rejected" rather than as
   a startup error.
5. **`risk_profiles` and `risk_decisions` are schema-only.** No repository writes
   them; the runtime holds decisions in memory. The durable half of decision
   idempotency is declared but not exercised.
6. **Broker health is `PAPER_SIMULATED` always.** There is no venue to check. The
   category is implemented and `UNKNOWN` fails closed, but nothing yet produces a real
   health signal — that is Phase 10.
7. **No `approved_until` enforcement at the storage layer.** The database requires an
   approval to *have* one; it does not check it at order-insert time, because the
   trigger has no market time to compare against. The runtime enforces it.

---

## 16. Remaining risks

1. **Unverified typing.** 161 files pass a subset check; none through `mypy --strict`.
2. **The database invariant has never run.** Item 4 in §14. The composite FK and
   trigger are the strongest expression of the phase's safety property and they are
   the least verified part of it.
3. **Untested HTTP surface.** ~230 lines of router exercised only by AST parse.
4. **The Phase 8 behaviour change is broad.** Five tests replaced and four moved
   layer. Each is documented, but a verifier should confirm that none of them lost
   coverage the original was providing.
5. **Limit arithmetic is tested at boundaries, not exhaustively.** Each limit has an
   at-the-limit and one-beyond case; interactions between limits are tested only
   where they arise naturally.

---

## 17. Git

One commit on `phase9-risk`, containing only Phase 9 work. No tag was created. No
merge into `main`. No force-push, reset or history rewrite.
