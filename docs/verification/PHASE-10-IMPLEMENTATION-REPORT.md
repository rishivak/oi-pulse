# Phase 10 — OMS + Reconciliation: Implementation Report

**Status: PHASE 10 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION**

This report does not claim Phase 10 PASS. It states what was built, what was
executed, and — kept deliberately separate, per brief §29 — what the environment
blocked.

| | |
|---|---|
| Branch | `phase10-oms-reconciliation` |
| Base commit | `d6180b39b288d494a9007f9d850bf752e57e487a` (current `origin/main`) |
| Implementation commit | recorded in the completion message and in `git log`; a commit cannot contain its own hash |
| Design source | `11-TRADING.md` §4–§6, `06-UPSTOX_INTEGRATION.md` §1 and §10, `02-DATA_MODEL.md` §6/§11, `12-API_SPEC.md` §211, `18-ROADMAP.md` Phase 10 |

## Checkpoint discrepancy (recurring; flagged again)

The brief names the verified Phase 9 checkpoint as `e0b2868…`, tag
`oi-pulse-v2-phase9`. That commit exists and its **tree is identical to current
`origin/main`** (`d6180b3`), verified with `git diff --stat`, but it is **not an
ancestor** (`git merge-base --is-ancestor` → false), and the tag is absent locally.
Branched from current `origin/main` as instructed. Seventh consecutive phase.

Two environment notes, neither affecting the commit: a stale `.git/config.lock`
prevented `git checkout -b` writing upstream branch configuration (the branch was
created at the correct SHA), and a temporary worktree used to measure the baseline
left a `prunable` admin entry that `git worktree prune` could not delete ("Device or
resource busy"). I did not delete either by hand — they are repository state, not
mine to clear.

---

## 1. The live-off barrier

Brief §4 requires structural protection, not just a flag. There are two layers and
the second is the one that matters.

**The flag.** `LIVE_EXECUTION_ENABLED` is a module constant assigned the literal
`False`, not a value read from the environment. Flipping it is a code change that
appears in a diff.

**The absence.** Even with the flag `True`, nothing could submit. `UpstoxBrokerAdapter`
holds no HTTP client, no credential, no base URL and no wire format, and every
venue-reaching method raises through the capability gate before doing anything.

### Why there is no Upstox wire format

`06-UPSTOX_INTEGRATION.md` established the Upstox **market-data** contracts —
instruments, option chain, quotes, historical OHLC and OI, and the V3 binary feed,
the last verified against recorded fixtures. It established **nothing** about the
order APIs: no request encoding, no response shape, no status vocabulary, no error
taxonomy, and no statement about whether Upstox deduplicates a repeated submission.

Brief §28 forbids inventing those. Writing `place_order` would mean inventing all of
it; the invented version would type-check, pass tests written against the same
invention, and be wrong in ways nobody could see until real money moved. So the
adapter is a **boundary without an implementation**, and that is recorded as a
limitation rather than presented as completeness.

| Control | Mechanism |
|---|---|
| Flag off | `LIVE_EXECUTION_ENABLED = False`, a literal constant |
| No capability | `UpstoxBrokerAdapter.capabilities` is empty — not even `QUERY_PROVIDER` |
| No implementation | every method gates then raises; no `await`, no client, no credential |
| No network | nothing under `trading/` imports httpx, requests, aiohttp, urllib, socket or websockets |
| No credential | nothing under `trading/` imports `core.config`, `core.secrets`, `marketdata.auth`, `marketdata.upstox` or `marketdata.providers` |
| No submit route | no API path contains a submitting verb; no endpoint takes a `provider_*` parameter or a target state |
| No risk bypass | `authorize_submission` precedes `place_order`, verified positionally in the AST |
| No resubmit | `UNKNOWN` has exactly one outgoing edge, parsed from the transition table |

`tools/check_live_execution_barrier.py` enforces all eight and was **mutation-tested
on all eight** (§10 below).

---

## 2. OMS architecture

```
approved RiskDecision ──► OMS order ──► adapter ──► ack | reject | SILENCE
                                                            │
                                                     UNKNOWN ──► PENDING_RECONCILIATION
```

### Identities kept apart (brief §5)

| Identity | Whose | Meaning |
|---|---|---|
| `intent_id` | ours, content-addressed | what a strategy asked for |
| `risk_decision_id` | ours, `(intent, sequence)` | which approval authorized it |
| `order_id` | ours, `(intent, leg, account)` | the OMS order |
| `client_order_attempt_id` | ours, `(order, attempt)` | one submission try |
| `provider_order_id` | the venue's, **or `None`** | the venue's order |

A test asserts all five are distinct on a single submitted order. The last pairing
matters most: an order can exist at the venue with no provider id known to us.

### Order state machine

Phase 8's subset plus the five states a network relationship needs: `SUBMITTING`,
`SUBMITTED`, `CANCEL_PENDING`, `UNKNOWN`, `PENDING_RECONCILIATION`. Terminal set
unchanged. `UNKNOWN → PENDING_RECONCILIATION` is the **only** edge out of `UNKNOWN`;
`11` §5's "never resubmit from UNKNOWN" is therefore a property of the graph, not a
policy. Every permitted and every forbidden transition is tested by walking the
table, so the coverage grew with the machine automatically — Phase 8's tests needed
no edit.

### Submission idempotency (brief §9)

`client_order_attempt_id` is content-addressed over `(order_id, attempt)`; a retry
after a restart recomputes the same value, and re-sending it is refused locally.
`uq_trade_orders_client_attempt` is the durable half.

**No claim is made about provider-side deduplication.** `06` §10 is explicit that our
key does not bind Upstox, and a test asserts the source contains no such overclaim.
Safety against duplicates comes from refusing to resubmit without reconciliation.

### Ambiguous submission (brief §10)

A timeout produces `UNKNOWN` — never a terminal state, never a retry. The paper venue
injects the realistic and dangerous case: the order **reaches the book** while the
acknowledgement is lost, so reconciliation has something genuinely there to find. A
dropped request produces the same local state and a different truth, which is the
entire reason only reconciliation can distinguish them. Both are tested.

An unresolved order blocks further intents for its instrument **from the same
strategy** (`11` §5), so ambiguity cannot compound. Scoped rather than global,
because halting an account over one ambiguous order is not obviously safer — a
strategy unable to exit a position has its own risk.

---

## 3. Reconciliation

`11` §6's seven steps, with steps 6 and 7 returned to the caller (the reconciler
holds no database and emits no events, for the same purity reason as every other
layer).

### One ordering decision worth reading

Fills are applied **before** order status. A fill is the *evidence* for a status;
applying `FILLED` first drives the order terminal, after which `apply_fill`
correctly refuses — leaving an order claiming `FILLED` with a filled quantity of
zero and the fill recorded as an unresolvable discrepancy. I had this the wrong way
round in the first draft and the smoke test caught it; there is now a test asserting
the coherent outcome.

### What reconciliation may and may not do

`11` §6 step 5 authorises three actions: append order events, insert missing fills,
correct positions. Brief §12 forbids inventing others. So:

* `MISSING_LOCALLY` — an order at the broker we have no record of — is **recorded,
  never cancelled**. That is the corrective action most likely to be invented and
  most likely to lose money.
* `OMS_AHEAD` — we show progress the broker does not — is recorded, not rolled back.
  It almost always means a bug on our side, and rewriting local state would destroy
  the evidence.
* A provider status of `UNKNOWN` maps to **no state**; the order stays unresolved.

### Identity recovery

Reconciliation matches an order whose acknowledgement was lost via our attempt id,
and **writes the discovered provider id back**. Without that the order could never be
cancelled (cancel addresses by provider id) and every later run would rediscover it.
`adopt_provider_identity` refuses to overwrite a *different* id — two provider ids for
one order means a duplicate submission or a bad match, and both need a human.

### Idempotency (brief §13)

Two mechanisms: fills are keyed on `BrokerFill.dedup_key` (the provider's execution id
where it exists, a content digest where it does not — and the weaker basis is stated
in the discrepancy detail), and order-state application is a no-op when already in the
target state. Tests assert that two runs over settled evidence produce an identical
`content_digest` and append **no** event.

Note precisely what is idempotent: the *resulting state* and the *run digest over
unchanged evidence*. The first run after a change legitimately reports the work it
did; the second reports none.

---

## 4. Provider semantics — what is and is not claimed

Brief §8 and §28. Every one of these is an absence, deliberately:

* **No provider sequence number** anywhere. `06` §6 verified Upstox V3 supplies no
  ordering for market data; nothing has been verified for order updates, and a
  sequence field would imply a guarantee nobody established.
* **No provider event id.** `BrokerOrderEvent` has none.
* **`provider_order_id` and `provider_fill_id` are both nullable**, and the `None`
  case is exercised by tests rather than treated as an edge.
* **Order-update events are a hint.** `subscribe_order_updates` may yield duplicates
  and promises no ordering, because no provider has promised either.
* **Simulated provider ids are prefixed `paper-`** so they cannot pass for a real
  broker's.
* **No recorded Upstox order payload exists**, so none is claimed. The recorded
  fixtures in `tests/fixtures/recorded/upstox_v3/` are market data and were not used
  here.

---

## 5. Temporal semantics (brief §16)

`market_time`, `knowledge_time` and `decision_time` are unchanged and uncollapsed.
Phase 10 adds two venue clocks stored separately: `provider_event_time` (when the
venue says it happened) and `received_at` (when we learned of it). Neither is market
time and neither is used as one. A test asserts an order's `decision_time` and
`knowledge_time` survive a submission made three minutes later, and another walks the
OMS AST asserting no call to `now`, `utcnow`, `today` or `monotonic`.

---

## 6. Risk authorization (brief §6, §17)

`authorize_submission` consumes the canonical `RiskDecisionRecord` and re-derives
nothing — a test asserts the module mentions no `RiskLimits`, `evaluate_all`,
`LimitEvaluation` or `RiskEngine`. Seven refusals are enumerated and each is tested:
no decision, rejected, expired, unevaluated, wrong intent, order/intent mismatch,
quantity exceeding the approval. A further test asserts that **no refused submission
reaches the venue**, by checking the venue's book is empty.

---

## 7. Persistence

`trade_reconciliations`, `trade_reconciliation_discrepancies`, and six columns plus
three constraints and two indexes on `trade_orders`.

Discrepancies are normalised into their own table rather than left as a JSON blob,
because `11` §6 requires a *pattern* of them to be visible and a blob makes that a
scan. `uq_trade_reconciliations_digest` makes a re-run over unchanged evidence collide.
`ck_trade_reconciliations_clean_means_nothing_outstanding` stops a row claiming
cleanliness while recording work left to do.

---

## 8. API

`/reconciliation/status`, `/runs`, `/runs/{id}`, `/trigger`, `/orders`,
`/orders/{id}`, `/orders/{id}/events`, `/orders/{id}/provider-state`,
`/orders/{id}/cancel`.

**No submit endpoint** (brief §24). **No parameter accepts provider truth** — the
trigger endpoint explicitly refuses `provider_orders`, `provider_fills`,
`provider_state` and `orders` in the body with a 422 (brief §25). **No endpoint takes
a target state**, so an arbitrary transition is not expressible. Every response
carries `live_execution_enabled: false`.

`/provider-state` returns 409 rather than an empty body when we hold no provider id —
the normal state after a lost acknowledgement, and an empty body would read as the
venue saying it has nothing.

---

## 9. Tests

`python3 -m unittest discover -s tests -t .`

| | Baseline (Phase 9, measured in a clean worktree at the base commit before any edit) | After Phase 10 |
|---|---|---|
| Tests run | 1144 | 1251 |
| Passed | 1075 | 1168 |
| Failures | 0 | 0 |
| Errors | 55 | 55 |
| Skipped | 14 | 28 |

**The 55 errors are the same set, same two root causes, no error of any other kind**:
45 × `No module named 'fastapi'`, 10 × `No module named 'google'`. The 14 additional
skips are the new real-HTTP tests, which `skipUnless(HAVE_FASTAPI)` rather than
erroring — deliberately, so an environment-blocked check reports as blocked instead of
adding to an error count that hides real failures.

Phase 10 added 107 tests: 101 in `tests/phase10/`, 6 in `tests/phase2/test_migrations.py`.

| Module | Tests | Covers |
|---|---|---|
| `test_oms_lifecycle.py` | 43 | risk gate (7 refusals), identity separation, submission idempotency, ambiguity, lifecycle, cancel, capability barrier, temporal |
| `test_reconciliation_and_guards.py` | 44 | provider mapping, every outcome kind, fill ingestion and dedup, idempotency, recovery, startup gate, envelopes, API shape, guards, observability |
| `test_api_runtime.py` | 14 | real HTTP behaviour — **skipped here**, runs in CI |

### One existing test replaced

`tests/phase8/…::test_no_live_broker_adapter_exists_anywhere_in_the_codebase`
asserted `UpstoxBrokerAdapter` must not exist, with the note "Phase 10 owns the live
adapter and it must not land without its gates". The gates now exist. It was
**replaced, not deleted**, by the property it was protecting: every adapter's
capability set must exclude `LIVE_SUBMIT`. That is strictly stronger — the old form
would have passed a live-capable adapter under any other class name.

### A guard weakness this phase exposed

`check_paper_trading_safety.py` matched adapter classes on `{submit, cancel, status}`
— Phase 8's method names. When Phase 10 added an adapter using the design's
`place_order`/`cancel_order` names, that guard **silently stopped covering the case it
was written for** and still reported PASS. It is left as-is (it still correctly
guards the paper surface) and the live-submission question now belongs to
`check_live_execution_barrier.py`, which checks capabilities at runtime rather than
matching method names. Recorded because a guard that quietly narrows its own scope is
worth knowing about.

---

## 10. Gate results

| Check | Result |
|---|---|
| `tools/check_live_execution_barrier.py` | PASS — new; **all 8 checks mutation-tested** |
| `tools/check_risk_authorization.py` | PASS |
| `tools/check_paper_trading_safety.py` | PASS (see the weakness noted in §9) |
| `tools/check_import_boundaries.py` | PASS — 15 contracts |
| `tools/check_clock_access.py` | PASS |
| `tools/check_migration_chain.py` | PASS — single chain, 12 revisions, one head |
| `tools/check_migration_order.py` | PASS — 10 revisions |
| `tools/check_schema_parity.py` | PASS — 49 tables |
| `tools/check_strategy_surface.py` | PASS |
| `tools/check_alert_purity.py` | PASS |
| `tools/check_temporal_repository.py` | PASS |
| `tools/check_typing_strict.py` | PASS — 175 files (subset only) |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS — 304 files |
| `python -m compileall -q oipulse tools tests` | PASS |

### Barrier guard mutation tests (brief §19)

Each mutation was applied, the guard run, and the file restored:

| Mutation | Caught |
|---|---|
| `LIVE_EXECUTION_ENABLED = os.getenv(...)` | yes — "not the literal False" |
| paper adapter granted `LIVE_SUBMIT` | yes — named the adapter |
| Upstox `place_order` awaits an HTTP call | yes — "awaits something" |
| Upstox `place_order` loses its capability gate | yes — "does not call require_capability" |
| `trading/` imports `httpx` | yes — named the file and module |
| `trading/` imports `core.config` | yes — "can reach a live credential" |
| an API route named `/orders/submit` | yes — named the route |
| `place_order` called before authorization | yes — reported both line numbers |
| `UNKNOWN` given an edge back to `CREATED` | yes — read from the transition table |

One mutation initially produced a misleading message: the runtime adapter import ran
before the static scan, so adding `httpx` reported "cannot import the broker adapters"
rather than naming the module. The check order was changed so static scans run first.

---

## 11. What was NOT executed — environment-blocked (brief §29)

Kept separate from §10 deliberately. **None of these is equivalent to the static check
that stands in for it.**

| Required | Status | What stood in, and why it is not the same |
|---|---|---|
| `pytest -v` | **blocked** — not installed, no package index | `unittest` discovered and ran the same tests |
| `mypy --strict oipulse --show-error-codes` | **blocked** — not installed | `check_typing_strict.py` is a stdlib-AST subset with **no inference and no assignment checking**. It cannot find an unreachable branch or a bad assignment, which is exactly what the Phase 9 verification pass found with real mypy. **Largest gap.** |
| `lint-imports` | **blocked** — not installed | The AST guard checks declared imports; it does not resolve the real transitive graph |
| Real PostgreSQL migration test | **blocked** — no `alembic`, no `sqlalchemy`, `DATABASE_URL` unset | Migration `0010` verified by source inspection only. **It has never been executed.** The 14 integration tests skipped |
| Real FastAPI tests | **blocked** — not installed | `test_api_runtime.py` written and skipped; the AST checks assert route *shape*, not status codes, refusals or wiring |
| Provider-adapter tests against recorded responses | **not possible** | No recorded Upstox **order** response exists. The recorded fixtures are market data. Tests run against the simulated venue with fault injection, which is what `18` Phase 10 asks for — but it is not evidence about Upstox |

---

## 12. Known limitations

1. **`UpstoxBrokerAdapter` has no wire format.** It is a boundary, not an
   implementation. This is deliberate (§1) but it means the roadmap deliverable is
   half-delivered: the seam exists, the mapping does not, and Phase 11+ must write it
   against verified provider documentation.
2. **No broker order-update WebSocket.** `18` Phase 10 lists it. The `BrokerOrderEvent`
   type and `subscribe_order_updates` exist and the paper venue emits events, but
   there is no live subscription, because there is no live adapter to subscribe with.
3. **Positions are not reconciled.** `11` §6 scopes reconciliation to orders, fills
   **and positions**; `get_positions` is implemented on the paper venue and the
   `POSITION_CORRECTED` resolution is declared, but the reconciler compares orders and
   fills only. Position reconciliation is not implemented.
4. **`modify_order` is refused, not implemented.** `11` §4 defines no amend path and
   brief §15 makes cancel/replace conditional on the specification requiring it.
   Inventing amend semantics would model a venue behaviour nobody verified.
5. **Nothing is persisted.** The OMS and reconciler hold state in memory; no
   repository writes `trade_reconciliations`. The durable halves of submission
   idempotency and run idempotency are declared but not exercised.
6. **Reconciliation has no scheduler.** `11` §6 calls it "a subsystem with its own
   scheduler". The six triggers are modelled as an enum and the reconciler runs on
   demand; nothing drives it periodically or at a session boundary.
7. **`PaperOrder` is now a misnomer.** It is the canonical OMS order and carries a
   `venue`; the class name predates the Phase 10 framing. Renaming it would touch
   every phase from 8 onward and was judged not worth the churn — flagged so a
   reviewer does not read the name as a scope claim.
8. **Order matching has two bases, not three.** Provider id, then our attempt id.
   There is deliberately no instrument-and-quantity fallback: it would pair our order
   with somebody else's identical one, and a wrong match attributes a stranger's fill
   to our ledger.

---

## 13. Remaining risks

1. **Unverified typing** — 175 files pass a subset check; none through `mypy --strict`.
2. **Migration `0010` has never run.** Six added columns, three constraints and two
   indexes on a table that already holds data in any real deployment.
3. **Reconciliation bugs are the most dangerous class here** (`18` Phase 10 says so).
   The subsystem is tested against a simulated venue only; every fault it handles is
   one I chose to inject, so the coverage is bounded by my imagination about failure
   modes rather than by observed provider behaviour.
4. **The startup gate is advisory.** `startup_gate` returns a verdict and the API
   surfaces it, but nothing prevents an intent being accepted while it is False —
   `18` Phase 10's "trader is not ready until reconciliation is clean" is reported,
   not enforced.
5. **The API surface is untested over HTTP here.** ~250 lines of router.

---

## 14. Git

One commit on `phase10-oms-reconciliation`, containing only Phase 10 work. No tag
created. No merge into `main`. No force-push, reset or history rewrite.
