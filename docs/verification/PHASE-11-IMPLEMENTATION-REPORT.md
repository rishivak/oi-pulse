# Phase 11 — Portfolio + Attribution: Implementation Report

**Status: PHASE 11 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION**

This report does not claim Phase 11 PASS. It states what was built, what was
executed, and — kept separate — what the environment blocked.

| | |
|---|---|
| Branch | `phase11-portfolio-attribution` |
| Base commit | `28a97fe949dd972f240b11350def38ad89f036ba` (current `origin/main`) |
| Implementation commit | recorded in the completion message and in `git log`; a commit cannot contain its own hash |
| Design source | `11-TRADING.md` §8, `07-ANALYTICS.md` §4.3, `02-DATA_MODEL.md` §6, `12-API_SPEC.md` §208, `18-ROADMAP.md` Phase 11 |

## Checkpoint verification — a real difference this time

The brief names the verified Phase 10 checkpoint as `7a2f7fb…`, tag
`oi-pulse-v2-phase10`. Unlike the previous six phases, `git diff` between it and
`origin/main` is **not empty**:

```
backend/Dockerfile                 | 4 ++--
infra/scripts/podman-machine-up.sh | 0
infra/scripts/podman-reset-data.sh | 0
infra/scripts/podman-up.ps1        | 2 +-
infra/scripts/podman-up.sh         | 2 +-
scripts/start-local.sh             | 0
scripts/status-local.sh            | 0
```

`origin/main` carries an unrelated infra chore (`28a97fe`) on top of the verified
tree: a Dockerfile CMD change and file-mode changes on shell scripts. **Every
application path is byte-identical** — `git diff 7a2f7fb origin/main -- oipulse/
tools/ tests/ docs/` is empty. So `origin/main` does contain the verified Phase 10
code; it additionally contains infra changes that were never part of a phase gate.

As before, the commit is not an ancestor of `origin/main` and the tag is absent
locally. Branched from `origin/main` as instructed.

---

## 1. Portfolio architecture

```
canonical fills ──► PositionBook ──► valuation (from MarketState) ──► snapshot
                          │                                              │
                          └──► position reconciliation          attribution + RESIDUAL
```

Modules, all under `oipulse/trading/portfolio/`:

| File | Contents |
|---|---|
| `economics.py` | `ContractEconomics`, `resolve_economics`, `MultiplierSource` |
| `positions.py` | `PositionKey`, `PortfolioPosition`, `PositionBook`, `CostBasisMethod` |
| `valuation.py` | `value_positions`, `ValuationResult`, `UnvaluedReason`, `ValuationRefused` |
| `snapshot.py` | `PortfolioSnapshot`, `ReturnInputs`, `PortfolioGreeks`, margin/drawdown helpers |
| `attribution.py` | the seven components, `AttributionResult`, `roll_up`, `group_by` |
| `reconciliation.py` | `PositionReconciler`, the six discrepancy kinds |
| `serialisation.py` | Pure API envelopes |

Plus `persistence/portfolio_tables.py`, migration `0011`, `api/portfolio.py`,
`tools/check_portfolio_integrity.py`, `tests/phase11/`.

---

## 2. Position model and identity

`PositionKey` is `(account_id, portfolio_id, instrument_id)`. Four things are
deliberately excluded, each for a stated reason:

* **Side** — a position is signed; keying on side would let an account hold +10 and
  −10 simultaneously and be flat in neither.
* **Strategy** — ownership is *attribution*, not identity. Two strategies trading one
  instrument in one account hold one position the broker cannot split, and splitting
  the key would make reconciliation impossible.
* **Lot** — the cost-basis method has no lots (below).
* **Underlying** — derived from the instrument.

### Cost basis: weighted average, inherited not chosen

Brief §7 forbids silently picking a methodology. This one is **not chosen here**:
`11` §8 gives `Position` an `average_price`, and the verified Phase 7 `Ledger`
already implements weighted-average cost with realised P&L struck against the
existing basis on reduction. `PositionBook` **reuses that ledger** rather than
writing a second implementation, so a portfolio position and a backtest position
cannot disagree about what a trade cost.

The consequence is recorded rather than hidden: with average cost there are no tax
lots, so specific-identification questions are unanswerable. That is a limitation of
the inherited method, listed in §12.

### Determinism

`apply_all` folds fills in order of the content-addressed `fill_key`, not arrival or
row order — average-cost arithmetic is order-sensitive, so the order is imposed
rather than assumed. A duplicate fill is ignored and counted. Tested with reordered
fills, reordered positions and repeated application.

---

## 3. Derivative economics — the unit question, answered

The most dangerous available error here is applying `lot_size` twice. It is not, and
the reasoning is in the module rather than implied.

Throughout OI Pulse a quantity is **in units, not lots**: the Phase 7 cost model
demonstrates it, since a fill of 50 at 106.40 produces a turnover of 5,320 — one
NIFTY lot's premium, not fifty lots'. So 50 units *is* one lot of 50, and its notional
is `price * 50`. `UNITS_PER_QUANTITY` is therefore **1**, a named documented constant
that appears in the arithmetic so a reader can see the conversion was considered.
`07` §4.3's "× lot size" applies to per-contract greeks and open interest, which are
quoted per contract — not to a unit quantity that already counts them.

`lot_size` is still resolved and carried, for reporting a position in lots and
validating whole-lot quantities.

**The instrument version valid at the valuation time is used** (`07` §4.3). A test
resolves economics at minute 1 and minute 3 across a lot-size revision and asserts
each sees its own version — a revision does not retroactively rewrite exposure.

**Nothing is assumed.** An absent contract multiplier yields 1 with
`multiplier_source = DECLARED_DEFAULT`, so the default is on the record. Unresolvable
metadata returns `None`, and the position is reported **unvalued** rather than valued
on a guess. Overlapping versions raise rather than picking one, because choosing
silently would make the valuation depend on iteration order.

---

## 4. Valuation and point-in-time correctness

`value_positions` takes a `MarketState` — already scoped to `(T, K, BuildContext)` by
Phase 3 — and reads marks out of it. It fetches nothing, and its signature has no
parameter through which a later price could arrive, so brief §10's "never substitute
a future price" is structural. A test asserts the parameter set.

Five failure modes, none of which substitutes a value:

| Condition | Result |
|---|---|
| no price in the state | unvalued, named |
| state is `UNRELIABLE` | **whole valuation refused** unless explicitly allowed |
| quote marked stale | unvalued, reason recorded |
| economics unresolvable | unvalued |
| price ingested after K | cannot occur — the state never contains one |

An unvalued position is never zero. `is_complete` travels with every total, because a
sum that silently omitted a position reads as a smaller, safer book than the one that
exists.

PIT is tested with a quote observed at minute 1 but **ingested** at minute 3: unvalued
at K=1, valued at K=3. An earlier draft of that test used two competing observations
at the same `observed_at`; that is not a correction — the store handles corrections
via `supersedes_observation_id` — so the test was rewritten on an unambiguous case
rather than asserting on undefined behaviour.

---

## 5. P&L decomposition and the residual

The seven components are exactly `11` §8's list. The method is
`GREEK_EXPLAIN_SECOND_ORDER` version 1, and the version is part of every content hash.

```
dP ~= delta*dS + 0.5*gamma*dS^2 + vega*dSigma + theta*dt
```

**The residual is computed, never balanced.** `residual = total - sum(components)`, a
derived property with no settable field. No component is ever adjusted to make the sum
come out; a test proves it by decomposing the same greeks against two very different
totals and asserting every component is identical.

`reconciles` is true by construction — it is an identity, not evidence the model is
good. `residual_fraction` is the number that says how much was actually explained, and
it is promoted into `meta` on every API response, because `18` Phase 11 says report it
prominently and a value nested three levels down is not prominent.

A component whose inputs are missing reports `computed=False` with a detail naming
what was absent, so the residual it creates has a stated cause.

**The database enforces it too.** `ck_portfolio_attribution_reconciles` asserts
`total_pnl = explained + residual` with `residual NOT NULL`, so a row hiding an
unexplained amount cannot be written.

### Roll-up

Components sum across children and the parent's residual is the **sum of the
children's** — not recomputed against an independently measured total, which could
differ and would silently absorb the discrepancy. One uncomputed child component makes
the parent's partial too, so a gap does not vanish at every level above where it
happened. Order-independent, verified by digest.

### Unattributed performance

`UNATTRIBUTED` is a declared bucket id. A slice with no owner is labelled, never
assigned to the first strategy, and the count is surfaced in the envelope.

---

## 6. Return methodology

Brief §11 forbids inventing TWR or MWR where none is specified, and the design
specifies neither. `ReturnInputs` therefore reports the inputs a return is computed
*from* plus one field named `simple_period_return`, labelled `SIMPLE_PERIOD` in every
response. A test asserts the module claims no TWR, MWR or IRR.

---

## 7. Margin

Brief §22: use Phase 9 outputs, do not build a risk engine. Margin utilisation is
`deployed capital / the risk policy's declared capital limit`, both Phase 9 products,
and the **basis is reported alongside the number** because a utilisation with no
denominator is not interpretable. Absent either input it is `None` and
`NOT_AVAILABLE` — never 0, which would read as no margin used. No SPAN or exchange
margin model was implemented.

---

## 8. Position reconciliation — the Phase 10 deferral, discharged

Phase 10 implemented order and fill reconciliation and explicitly deferred positions;
that was limitation #3 of its report. This closes it.

| Kind | Resolution | Why |
|---|---|---|
| `MATCH` | none | they agree |
| `QUANTITY_MISMATCH` | **corrected** | unambiguous; broker authoritative (`11` §6 step 5) |
| `SIDE_MISMATCH` | recorded | not a rounding difference; overwriting destroys the evidence |
| `MISSING_AT_PROVIDER` | recorded | zeroing it would make an untracked real position invisible |
| `MISSING_LOCALLY` | recorded | adopting it needs a cost basis, and the broker's average price is not ours |
| `UNKNOWN` | recorded | insufficient evidence |

Corrections are **returned, not written** — the reconciler holds no position book — so
a caller can run report-only, and `apply_corrections=False` records that choice in the
audit rather than leaving it implicit. Idempotent: keyed on `(position, observed
quantity)`, so a repeat applies nothing and the run digest is unchanged.

---

## 9. Tests

`python3 -m unittest discover -s tests -t .`

| | Baseline (Phase 10, at the base commit before any edit) | After Phase 11 |
|---|---|---|
| Tests run | 1261 | 1383 |
| Passed | 1178 | 1284 |
| Failures | 0 | 0 |
| Errors | 55 | 55 |
| Skipped | 28 | 44 |

**The 55 errors are the same set, same two root causes, no error of any other kind**:
45 × `No module named 'fastapi'`, 10 × `No module named 'google'`. The 16 additional
skips are the 13 new real-HTTP tests (which `skipUnless` rather than erroring) and 3
new PostgreSQL tests.

Phase 11 added 122 tests: 112 in `tests/phase11/`, 7 in `tests/phase2/test_migrations.py`
and 3 in `tests/integration/test_migrations_postgres.py`.

| Module | Tests | Covers |
|---|---|---|
| `test_positions_and_valuation.py` | 39 | identity, the fold, cost basis, contract economics, lot-size revisions, valuation, PIT, determinism |
| `test_attribution_and_reconciliation.py` | 60 | the seven components, residual, roll-up, unattributed, costs, returns, margin, snapshot identity, position reconciliation, API shape, guards, observability |
| `test_api_runtime.py` | 13 | real HTTP behaviour — **skipped here**, runs in CI |

### Two bugs my own tests caught

**Closed positions were not retained.** The Phase 7 `Ledger.positions()` filters out
zero-quantity positions, so `include_closed=True` could never return one — my
docstring claimed retention the code could not deliver. Fixed with an additive
`Ledger.all_positions()` accessor (no behaviour change; all 29 Phase 7 ledger tests
still pass).

**A misconceived PIT test.** Described in §4.

---

## 10. Gate results

| Check | Result |
|---|---|
| `tools/check_portfolio_integrity.py` | PASS — new; **all 8 checks mutation-tested** |
| `tools/check_live_execution_barrier.py` | PASS |
| `tools/check_risk_authorization.py` | PASS |
| `tools/check_paper_trading_safety.py` | PASS |
| `tools/check_import_boundaries.py` | PASS — 15 contracts |
| `tools/check_clock_access.py` | PASS |
| `tools/check_migration_chain.py` | PASS — single chain, 13 revisions, one head |
| `tools/check_migration_order.py` | PASS — 11 revisions |
| `tools/check_schema_parity.py` | PASS — 52 tables |
| `tools/check_strategy_surface.py` | PASS |
| `tools/check_alert_purity.py` | PASS |
| `tools/check_temporal_repository.py` | PASS |
| `tools/check_typing_strict.py` | PASS — 185 files (subset only) |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS — 322 files |
| `python -m compileall -q oipulse tools tests` | PASS |

### Guard mutation tests (brief §29)

Each mutation applied, guard run, file restored:

| Mutation | Caught |
|---|---|
| `residual` becomes a settable field | yes — "must be a derived property" |
| a component's `amount` is assigned | yes — "assigns to a component's amount" |
| `computed_at` enters the snapshot hash | yes — named the runtime field |
| valuation calls `fetch_quotes` | yes — "would create a second price source" |
| portfolio imports the OMS | yes |
| portfolio imports a broker **adapter** | yes |
| portfolio assigns to `order.state` | yes — "reads OMS truth; never rewrites it" |
| valuation calls `datetime.now()` | yes |
| portfolio imports `oipulse.terminal` | yes |

The guard itself caught a real distinction while being written: reconciliation needs
`BrokerPosition`, a frozen value type. Importing a dataclass is not reaching a broker,
so `brokers.protocol` is permitted while the three adapter modules stay forbidden —
the alternative was a duplicate position type for the two to disagree about.

### One import contract extended

`paper-trading-is-pure` now admits `oipulse.instruments`. Phase 11 must resolve lot
size from the instrument version valid at the valuation time (`07` §4.3, §21), and
`instruments` is a pure low layer — stdlib plus `core` only, the same tier as `events`,
which `trading` already imports. The alternative was a second copy of contract
economics.

---

## 11. What was NOT executed — environment-blocked

Kept separate from §10. **None is equivalent to the static check standing in for it.**

| Required | Status | What stood in |
|---|---|---|
| `pytest -v` | **blocked** — not installed, no package index | `unittest` ran the same tests |
| `mypy --strict oipulse --show-error-codes` | **blocked** — not installed | `check_typing_strict.py` is an AST subset with **no inference and no assignment checking**. The Phase 9 verification pass found a real unreachable-branch error with actual mypy that this cannot find. **Largest gap.** |
| `lint-imports` | **blocked** — not installed | The AST guard checks declared imports, not the resolved transitive graph |
| Real PostgreSQL migration test | **blocked** — no alembic/sqlalchemy, `DATABASE_URL` unset | Migration `0011` verified by source inspection only. **It has never executed** — including the two new tests that insert an unbalanced attribution row and require the database to refuse it |
| Real FastAPI tests | **blocked** — not installed | `test_api_runtime.py` written and skipped; the AST checks assert route shape, not status codes or the knowledge-time refusal |

---

## 12. Known limitations

1. **No tax lots.** The inherited weighted-average method has none, so
   specific-identification questions are unanswerable. Brief §7 required not choosing
   silently; this states the consequence of the choice the architecture had already
   made.
2. **Greeks are supplied, never computed.** `PortfolioGreeks` is populated by the
   caller from Phase 4 features. Nothing in Phase 11 wires that aggregation, so a
   snapshot built today reports greeks as absent unless a caller provides them. The
   `07` §4.3 scaling rule is implemented in `economics.py` but the feature plumbing is
   not.
3. **No corporate actions.** Brief §20: splits, dividends, expiry, assignment,
   exercise, contract adjustments and symbol changes are **not** implemented, because
   the design specifies none. A position in an expiring option is not expired, assigned
   or exercised by anything here. A test asserts no such handler was invented.
4. **Nothing is persisted.** The portfolio layer holds state in memory; no repository
   writes `portfolio_snapshots`. The database-level residual constraint — the
   strongest expression of this phase's central property — is therefore declared but
   never exercised.
5. **Attribution inputs are supplied, not derived.** `attribute_position` takes greeks
   and market moves as arguments. Computing them from two consecutive `MarketState`s
   is not implemented, so the decomposition is exercised with constructed inputs
   rather than end-to-end from a real price path.
6. **Execution and slippage attribution need a decision price.** Both components are
   `NOT_EVALUABLE` unless a caller supplies one; the Phase 7 `Fill` carries
   `reference_price` and `slippage_per_unit`, but wiring them into attribution is not
   done.
7. **`simple_period_return` is not TWR or MWR.** With cash flows it is not a
   performance figure anyone should compare across accounts. Labelled, not fixed.
8. **Position reconciliation returns corrections rather than applying them.** No
   caller applies them, so the corrected-position path is exercised only through the
   returned mapping.

---

## 13. Remaining risks

1. **Unverified typing.** 185 files pass a subset check; none through `mypy --strict`.
2. **Migration `0011` has never run** — three tables, eight added columns and two
   CHECK constraints on a populated table.
3. **The residual constraint is untested against a database.** Items 4 and 2 combine:
   the property this phase exists to protect is enforced in code and in a schema that
   has never been applied.
4. **The greek explain is exercised on constructed inputs.** Its behaviour on a real
   price path — where cross-greeks and third-order terms actually matter — is unknown,
   and the residual it would produce there is exactly the number that matters.
5. **Untested HTTP surface** — ~290 lines of router exercised only by AST parse.

---

## 14. Git

One commit on `phase11-portfolio-attribution`, containing only Phase 11 work. No tag
created. No merge into `main`. No force-push, reset or history rewrite.
