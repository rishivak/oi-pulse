# Phase 5 — Signals and Alerts Implementation Report

**Base SHA:** `9a2cdb48b8bf6316ba2f7b86b8d3c22deedfe978` (`origin/main`, "verify: complete
independent Phase 4 analytics verification and quality gate")
**Branch:** `phase5-signals`
**Implementation SHA:** the tip of `phase5-signals` — `git rev-parse phase5-signals`.
A commit cannot contain its own hash.

```
PHASE 5 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION
```

Not claimed as PASS. §11 states exactly what was not executed.

---

## 0. A discrepancy in the starting state, resolved

The brief names the verified Phase 4 checkpoint as
`325544a68a528b9686880e0b6950d1cdec7f89e2`. **That commit does not exist in this
repository** — `git cat-file -t` reports "Not a valid commit name".

What does exist:

| Ref | SHA |
|---|---|
| `origin/main` | `9a2cdb4` — "verify: complete independent Phase 4 analytics verification and quality gate" |
| `oi-pulse-v2-phase4` (tag) | `de65eb1` |
| my Phase 4 implementation | `a64b99d` on `origin/main`, `2b5d54e` locally |

`git diff de65eb1 9a2cdb4` is **empty**: the tag and `origin/main` have identical trees,
reached by different commit paths. The brief also instructs that the branch start from
the *current* `origin/main`, which is unambiguous, so `phase5-signals` was created from
`9a2cdb4`. The content is identical to the tagged checkpoint either way. Flagged rather
than silently reconciled.

---

## 1. Clean Phase 4 baseline, measured before any change

The brief requires that environmental failures not be labelled pre-existing without
evidence. Measured on the untouched tree at `9a2cdb4`:

```
Ran 467 tests — 439 passed, 21 errors, 7 skipped

  11 × ModuleNotFoundError: No module named 'fastapi'
  10 × ModuleNotFoundError: No module named 'google'   (protobuf)
```

Every one of the 21 is a missing package. Neither is installable here: DNS resolution
fails for `pypi.org`. **After Phase 5 the error set is byte-identical** — same 21, same
two root causes, no error of any other kind.

---

## 2. Modules added

| Module | Purpose |
|---|---|
| `oipulse/signals/model.py` | `Signal`, `Evidence`, `MetricRef`, `SignalIdentity`, `SignalProvenance` |
| `oipulse/signals/lifecycle.py` | the normative transition table from `08` §3 |
| `oipulse/signals/strength.py` | declared, versioned strength derivation |
| `oipulse/signals/context.py` | `RuleContext`, `RuleConfig` — the pure input set |
| `oipulse/signals/rules.py` | `@signal_rule`, `SignalRuleSpec`, `RuleRegistry` |
| `oipulse/signals/evaluation.py` | PIT gating, quality gating, availability, lifecycle, idempotency |
| `oipulse/signals/catalogue/*` | the 16 signal types from `08` §6 |
| `oipulse/signals/serialisation.py` | API rendering, web-stack-free |
| `oipulse/alerts/model.py` | `AlertRule`, `AlertOccurrence`, `DeliveryAttempt` |
| `oipulse/alerts/routing.py` | dedup keys, cooldown, suppression |
| `oipulse/alerts/delivery.py` | SSE and webhook channels, bounded retry |
| `oipulse/alerts/serialisation.py` | API rendering |
| `oipulse/persistence/signal_tables.py` | `signal_signals`, `signal_evidence`, `alert_rules`, `alert_occurrences` |
| `oipulse/migrations/versions/0005_phase5_signals.py` | the migration |
| `oipulse/api/signals.py`, `oipulse/api/alerts.py` | the Phase 5 endpoints |
| `tools/check_alert_purity.py` | the alert/signal separation guard |

Modified: `oipulse/observability/metrics.py` (10 metric names + 7 emit helpers),
`oipulse/api/app.py` (two routers), `tools/check_import_boundaries.py` (two contracts),
`.github/workflows/ci.yml` (one gate step), and four test modules whose migration-chain
assertions legitimately changed.

---

## 3. Layer separation

```
MarketState -> Analytics Features -> Signal Evaluation
    -> Signal Evidence / Provenance -> Alert Generation / Delivery
```

Two new import contracts make the separation structural rather than conventional:

* **`signals-are-pure`** — `signals/*` may import `analytics`, `marketstate` and `core`
  only. No database, no HTTP, no clock. Same contract as analytics, same reason: one
  implementation across live, replay and backtest.
* **`alerts-never-import-signal-internals`** — `alerts/*` may import `signals` and
  `core`, but never `signals.evaluation`, `signals.lifecycle` or `signals.rules`.
  Importing any of those would let delivery re-evaluate or transition a signal.

`tools/check_alert_purity.py` adds three AST checks: alerts never call a transition or
evaluator, alerts hold no field typed `Signal` (you cannot mutate what you do not hold),
and signals never import alerts. **Verified by mutation** — adding a `signal: Signal`
field to `AlertOccurrence` makes it fail with the exact diagnosis, and reverting restores
the pass.

`test_signals_do_not_duplicate_analytics_calculations` asserts that `signals/*` imports
neither `analytics.domains` nor `analytics.engine`: signals consume feature *results*,
they do not recompute features.

---

## 4. Signal model and versioning

`SignalIdentity` is content-addressable over
`(signal_type, rule_version, underlying_id, expiry_id, market_time, knowledge_horizon,
build_context_id, config_digest, occurrence)`. Every element is load-bearing and each has
a test asserting a change to it produces a different `signal_id`.

* **`config_digest`** content-addresses the rule's thresholds, so a threshold edit
  produces a distinguishable population rather than silently redefining the old one.
* **`rule_version`** is pinned into identity, and `requires_features` pins exact feature
  versions — a feature bumping to v3 cannot change a rule's behaviour without a versioned
  change to the rule. A test asserts every pinned `(feature, version)` actually exists in
  the Phase 4 registry.
* **`occurrence`** implements `08` §3's rule that a recurrence after a terminal state is
  a *new* signal, so research counts two occurrences rather than one long-lived entity.
* **`stream_key`** deliberately excludes market time, so successive evaluations of one
  developing signal update one entity.

`Evidence` references the full `metric_values` identity tuple, never a rendered string.
Supporting weights are non-negative and contradicting weights non-positive, enforced in
`__post_init__` and again by a database `CHECK` — a sign error would silently invert an
item's contribution to strength.

`contradiction_assessment` is mandatory; `NONE_OBSERVED` is a positive claim and stays
distinguishable from an evidence list on the wire. The evaluation report accumulates
which rules returned `NONE_OBSERVED` so the caller can flag a rule that *always* does.

There is **no field for an unexplained confidence number**. A test asserts `Signal` has
no `confidence`, `score` or `probability` slot.

---

## 5. Point-in-time correctness

The gate is applied **once, before any rule runs**: metric values are filtered to
`available_at <= K`, so a rule cannot reach past its horizon because nothing beyond it is
in the context.

### One decision worth stating plainly

**The signal's `K` is its own, and it is not the state's.** A `MarketState` at market
time `T` yields features that only become available at `T + availability_delay`. A signal
evaluated at the *state's* horizon could therefore never consume any of them — the first
end-to-end run produced 28 features and zero signals, every rule skipped
`feature_unavailable`, which is how this surfaced.

So `SignalEvaluator.evaluate` takes an explicit `knowledge_horizon`, defaulting to
`evaluated_at`: the moment the evaluation ran, which is exactly what it knew. The design
says the same thing — a state for 11:45:00 yields a signal *created 11:45:02, available
11:45:02* (`08` §5).

Passing an **earlier** `K` is the research and replay case and is honoured exactly:
features not yet available at that instant are withheld, so a reconstruction at an
earlier horizon sees strictly less. A `K` before market time raises. Later-K data is
never substituted, and `test_a_later_horizon_never_backfills_an_earlier_one` asserts the
difference directly.

**Availability propagates.** A signal's `available_at` is
`max(market_time, latest_input_available_at, evaluated_at) + delay`, so it is never
available before the last feature it consumed. Tested per evidence item, plus an explicit
negative that `market_time + delay` alone is insufficient.

---

## 6. Quality gating

No rule fires on `UNRELIABLE`; every one of the 16 declares the gate and a test asserts
it. `DEGRADED` is usable and the signal records the status it was built under. An
unparseable quality requirement is treated as *refusing*, because a gate that passes when
it cannot check is not a gate.

A rule that raises is recorded as `RULE_ERROR` and skipped, not swallowed and not allowed
to take down the batch. Every non-firing rule carries a typed reason and a detail string;
a test asserts the counts reconcile against the registry so nothing can vanish.

---

## 7. Lifecycle

`PERMITTED_TRANSITIONS` transcribes `08` §3 exactly, and a test compares the whole table
against the design. The two rules the diagram left ambiguous are pinned individually:
`CONFIRMED -> INVALIDATED` is permitted, and terminal states are never left. An illegal
transition raises `IllegalTransition` rather than being coerced. Every transition appends
to history and overwrites nothing.

---

## 8. Idempotency

Signal identity is deterministic, so re-processing a source event yields the same
`signal_id` and advances one entity rather than creating a second. Tested for repeat,
retry and recurrence.

Alert occurrences carry a deterministic `dedup_key` built from the rule, the signal's
lifecycle stream and its status, bucketed into a fixed window against a pinned epoch —
so two processes compute the same key without coordination. `UNIQUE (dedup_key,
observed_at)` enforces it in the database as well as in the router.

`AlertRouter.restore` rebuilds the index from the store, so a **process restart does not
re-alert**; a test asserts it.

No exactly-once delivery is claimed anywhere. `03-EVENT_MODEL.md` is explicit that the
guarantee is exactly-once *database application* per `(subscriber, event_id)`, and the
delivery module says so in its own docstring.

---

## 9. Alerts

Dedup and cooldown answer different questions and both are implemented: dedup asks "is
this the same logical alert?", cooldown asks "have we alerted about this stream too
recently?". Suppression is **recorded** with a reason, never silent.

Delivery is bounded, every attempt is recorded rather than overwritten, and attempt
timestamps are supplied and advanced by backoff rather than read from a clock — so the
retry history is reproducible under replay. SSE plus one out-of-band channel (webhook),
per the roadmap; the webhook's HTTP transport is injected, so this layer performs no
network I/O itself and no third-party vendor is integrated.

`FORMING` is excluded from alerting by default: partially met entry conditions are not
worth waking anyone for.

---

## 10. Persistence, API, observability

**Migration `0005`** adds the four tables with: the full signal identity `UNIQUE`, the
alert `dedup_key` `UNIQUE`, `CHECK (available_at >= observed_at)`,
`CHECK (strength BETWEEN 0 AND 1)`, and the evidence weight-sign `CHECK`.
`contradiction_assessment` is `NOT NULL` because `NONE_OBSERVED` is a finding.
`signal_evidence` stores the **full** `metric_values` identity tuple, not a surrogate id,
because `metric_values` is partitioned and a bare id would not identify a row there —
that tuple is what makes the audit chain a join. `alert_rules` is deliberately
unpartitioned: it is configuration, not a time series.

**API**: `/signals`, `/signals/{id}`, `/signals/{id}/history`, `/signals/types` and full
alert-rule CRUD, `/alerts/rules/{id}/test`, `/alerts/occurrences`, acknowledge. The
dry-run uses a throwaway router so it cannot affect real dedup state, and reports
`delivered: false, persisted: false`. Endpoints without a configured reader answer **503
naming the dependency**, never an empty list — an empty list is indistinguishable from
"nothing fired", which is a different and more interesting fact.

**Observability**: `signals_created_total`, `signal_lifecycle_transitions_total`,
`signal_evaluations_skipped_total`, `signal_evaluation_duration_seconds`,
`signal_availability_lag_seconds`, `signal_idempotent_repeats_total`,
`alerts_triggered_total`, `alerts_suppressed_total`, `alerts_delivered_total` /
`alerts_failed_total`, `alert_delivery_duration_seconds`. Emitted by helpers taking
primitives, because `signals/` may not import the registry.

---

## 11. Verification actually executed

| Check | Command | Result |
|---|---|---|
| Full suite | `python3 -m unittest discover -s tests -t .` | **590 tests: 561 passed, 21 errors, 8 skipped** |
| Phase 5 only | `tests/phase5/` | **120 passed** |
| Lint | `ruff check .` | pass |
| Format | `ruff format --check .` | pass, 192 files |
| Byte-compile | `python -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, **6** contracts |
| Alert purity | `tools/check_alert_purity.py` | pass (mutation-verified) |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 7 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 30 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 5 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 105 files |

Phase 5 test breakdown: signal model 33, lifecycle and evaluation 34, alerts 30,
end-to-end and regression 23.

**124 tests added** (590 − 467 + 1 new skip). **Zero regressions**: the 21 errors are the
same 21 measured on the clean baseline, same two root causes, and no error of any other
kind exists.

### Not executed here

`pytest`, `mypy --strict oipulse --show-error-codes`, `lint-imports`, `alembic upgrade
head`, and any endpoint over HTTP. DNS resolution fails for `pypi.org`, so nothing is
installable; there is no PostgreSQL and no FastAPI. The stdlib typing guard passes over
105 files and is explicitly **not** equivalent to mypy — no inference, no assignment
compatibility, nominal-only override checking.

The 8 skips are the 6 PostgreSQL migration integration tests (now including the Phase 5
constraint check), the outstanding recorded-fixture set, and one Phase 2 soak item. Each
is a reported gap, not a pass.

---

## 12. Known limitations

1. **No signal or alert writer/reader.** Schema, migration and endpoint contracts exist;
   wiring durable persistence needs a database to test against. Endpoints answer 503.
2. **`prior_strengths` and `existing` are caller-supplied.** Persistence criteria and
   lifecycle continuity need prior evaluations, which the pure layer may not fetch. The
   live processor supplies them; the shape is fixed and tested.
3. **Rule thresholds are declared defaults, not calibrated.** Every one is a
   `default_config` entry that a deployment overrides, and any override changes
   `config_digest`. None has been validated against real market data — that is a
   research activity, and Phase 6 is not authorised.
4. **The SSE channel appends to an injected in-process buffer.** The HTTP streaming
   endpoint (`GET /stream/events`, `12` §227) is not implemented; it needs the web stack.
5. **The webhook transport is injected and no vendor integration exists**, deliberately —
   the specification does not require one.
6. **Delivery scheduling is the caller's concern.** `deliver_with_retry` does not sleep;
   a pure function that slept would make the layer untestable at speed.
7. **`AlertRouter`'s epoch defaults to a fixed date.** Replay should pin it explicitly to
   reproduce historical bucketing exactly.
8. **`.env.example` is unreadable in this sandbox** (read-deny list), so git reports a
   phantom modification it cannot diff. Excluded from the commit.

---

## 13. Scope control

No research, event studies, replay engine, backtesting, paper trading, risk engine, OMS,
broker execution, portfolio attribution or terminal. A test asserts `signals/*` and
`alerts/*` import none of those packages. Phase 1–4 behaviour is unchanged: the analytics
registry still holds 54 features, `StateIdentity` is untouched, and feature availability
semantics are re-asserted by regression test.
