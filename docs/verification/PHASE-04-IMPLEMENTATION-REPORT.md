# Phase 4 — Analytics Implementation Report

**Base SHA:** `c707b0f1c655ad9d12514453185d35e5cd7e712a` (`origin/main`, "Merge pull
request #3 from rishivak/phase3-marketstate"), which contains the verified Phase 3
checkpoint `fd421a9`.
**Branch:** `phase4-analytics`
**Implementation SHA:** the tip of `phase4-analytics` — `git rev-parse phase4-analytics`.
A commit cannot contain its own hash.

```
PHASE 4 IMPLEMENTATION COMPLETE — AWAITING INDEPENDENT VERIFICATION
```

Not claimed as PASS. §11 states exactly what was not executed here.

---

## 1. What was built

| Module | Purpose |
|---|---|
| `oipulse/analytics/registry.py` | `@feature` decorator, `FeatureSpec`, `Registry`, quality-requirement parsing |
| `oipulse/analytics/values.py` | `MetricValue`, `Unavailable`, `ScopeRef`, `inputs_digest` |
| `oipulse/analytics/availability.py` | the `07` §3 formula and its three invariants |
| `oipulse/analytics/context.py` | `ComputeContext` — the complete explicit input set |
| `oipulse/analytics/emit.py` | the single place a `MetricValue` is constructed |
| `oipulse/analytics/engine.py` | quality gating, topological ordering, `ExecutionReport` |
| `oipulse/analytics/migration.py` | `OIMigration` lifecycle tracking (`07` §5) |
| `oipulse/analytics/domains/*` | the seven domains, 54 features |
| `oipulse/persistence/analytics_tables.py` | `metric_values`, `interp_labels`, `metric_oi_migrations` |
| `oipulse/migrations/versions/0004_phase4_analytics.py` | the migration for those three tables |
| `oipulse/api/features.py` | `GET /features`, `/features/{id}/versions/{v}`, `/features/{id}/values` |

Modified: `oipulse/observability/metrics.py` (five Phase 4 metrics and two emit
helpers), `oipulse/api/app.py` (router), `oipulse/marketstate/{state,builder}.py` (see
§10), and three test modules whose migration-chain assertions legitimately changed.

---

## 2. Purity

`analytics/*` may import `marketstate` and `core` only — the `analytics-is-pure`
contract, armed in Phase 1 and now load-bearing. No database, no HTTP, no clock, no
`observability`, no globals, no mutation of the input state.

Three consequences are visible in the design rather than merely asserted:

* **`computed_at` is a parameter, never a reading.** An analytic that could read the
  clock could not be replayed, which would defeat the single-implementation rule.
* **Metric emission lives in the caller.** `observability.METRICS` is process-global
  mutable state, so `ExecutionReport` is the hand-off and
  `record_feature_computed` / `record_feature_skipped` take primitives.
* **History is supplied, never fetched.** A feature needing a lookback receives a tuple
  of prior states.

**The contract caught a real mistake during implementation.** The first draft put the
`metric_values` table declarations in `analytics/store/schema.py`, and the guard
rejected the SQLAlchemy import. It was right: a table declaration inside the pure layer
puts a database dependency one import away from every feature. They now live in
`persistence/analytics_tables.py`.

`test_the_analytics_package_imports_no_database_or_clock` asserts this on the source as
well, because an accidental import is easy and quiet.

---

## 3. The feature registry

54 features across the seven domains: positioning 13, volatility 10, futures 8, price 8,
gamma 6, structure 5, greeks 4.

Every registry field from `07` §2 is mandatory — `identifier`, `version`, `definition`,
`inputs`, `formula`, `units`, `sampling_frequency`, `lookback`, `availability_delay`,
`normalization`, `quality_requirements`, `scope`, `implementation_ref` — and
`test_every_feature_declares_every_registry_field` enforces it over all 54. A feature
that cannot state its units or its lookback is one whose output nobody can interpret.

**Nothing computes off-registry.** The registry is the single catalogue, loaded on
package import so a caller cannot run against a partially populated one.

**Versioning.** `(identifier, version)` is the key, and re-registering an existing pair
raises `DuplicateFeature` rather than overwriting — a silent overwrite would change the
meaning of values already stored under that version. v1 and v2 coexist and are
independently computable.

A defect was found here by the tests and fixed: `Registry.__len__` made an *empty*
registry falsy, so `registry or REGISTRY` in the decorator silently registered into the
global registry whenever a caller passed a fresh one — precisely when a test is trying
to isolate itself. Now `registry if registry is not None else REGISTRY`.

---

## 4. Full registry

| Feature | v | Domain | Formula | Units | Lookback | Delay | Scope | Quality gate |
|---|---|---|---|---|---|---|---|---|
| `ANNUALIZED_BASIS` | 1 | futures | `(future / spot - 1) * (day_count / days_to_expiry)` | rate_annualised | 0s | 2s | underlying | quality!=UNRELIABLE |
| `ATM_IV` | 1 | volatility | `mean(iv_call(atm), iv_put(atm))` | iv_decimal | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `ATR` | 1 | price | `mean(|p_t - p_{t-1}|)` | price | 30m | 2s | underlying | quality!=UNRELIABLE |
| `BASIS` | 1 | futures | `front_future.ltp - spot.ltp` | price | 0s | 2s | underlying | quality!=UNRELIABLE |
| `BASIS_CHANGE` | 1 | futures | `basis(T) - basis(T - lookback)` | price | 15m | 2s | underlying | quality!=UNRELIABLE |
| `BUILDUP_CLASSIFICATION` | 1 | positioning | `matrix(sign(delta_price), sign(delta_oi))` | category | 15m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `CALL_OI_MIGRATION` | 1 | positioning | `sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)` | strike_points | 15m | 2s | expiry | oi_coverage>=0.95, quality!=UNRELIABLE |
| `DELTA_EXPOSURE` | 1 | greeks | `sum(delta * oi * lot_size * contract_multiplier)` | delta_shares | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `FUTURES_OI` | 1 | futures | `front_future.oi` | contracts | 0s | 2s | underlying | quality!=UNRELIABLE |
| `FUTURES_OI_CHANGE` | 1 | futures | `front_future.oi(T) - front_future.oi(T - lookback)` | contracts | 15m | 2s | underlying | quality!=UNRELIABLE |
| `FUTURES_OPTIONS_CONFIRMATION` | 1 | futures | `agree(sign(delta futures_oi), sign(delta options_oi))` | category | 15m | 2s | underlying | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `FUTURES_PRICE` | 1 | futures | `front_future.ltp` | price | 0s | 2s | underlying | quality!=UNRELIABLE |
| `GAMMA_EXPOSURE` | 1 | greeks | `sum(gamma * oi * lot_size * contract_multiplier)` | gamma_shares_per_point | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `GAMMA_FLIP_LEVEL` | 1 | gamma | `strike where cumulative_gex(s) crosses 0, linear between brackets` | strike | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `GEX_BY_EXPIRY` | 1 | gamma | `sum over strikes of GEX_BY_STRIKE for the expiry` | gamma_shares_per_point | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `GEX_BY_STRIKE` | 1 | gamma | `per strike: sum(sign(convention, type) * gamma * oi * lot_size * multiplier)` | gamma_shares_per_point | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `GEX_CONCENTRATION` | 1 | gamma | `sum((|gex_s| / sum(|gex|)) ** 2)` | index | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `GEX_PROFILE` | 1 | gamma | `cumulative sum over ascending strikes of GEX_BY_STRIKE` | gamma_shares_per_point | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `GEX_TOTAL` | 1 | gamma | `sum over expiries and strikes of GEX_BY_STRIKE` | gamma_shares_per_point | 0s | 2s | underlying | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `IMPLIED_REALIZED_SPREAD` | 1 | volatility | `atm_iv - realized_vol_close_to_close` | vol_annualised | 30m | 2s | underlying | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `IV_CHANGE` | 1 | volatility | `atm_iv(T) - atm_iv(T - lookback)` | iv_decimal | 15m | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `IV_PERCENTILE` | 1 | volatility | `count(iv_window <= iv_now) / count(iv_window)` | ratio | 1d | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `IV_RANK` | 1 | volatility | `(iv_now - min(iv_window)) / (max(iv_window) - min(iv_window))` | ratio | 1d | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `IV_SKEW_DELTA` | 1 | volatility | `iv_put(delta ~ -0.25) - iv_call(delta ~ +0.25)` | iv_decimal | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `IV_SKEW_STRIKE` | 1 | volatility | `iv_put(atm - offset) - iv_call(atm + offset)` | iv_decimal | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `IV_TERM_STRUCTURE` | 1 | volatility | `atm_iv(expiry_2) - atm_iv(expiry_1)` | iv_decimal | 0s | 2s | underlying | quality!=UNRELIABLE, greeks_coverage>=0.90 |
| `MAX_PAIN` | 1 | structure | `argmin_K sum_s oi_call(s)*max(K-s,0) + oi_put(s)*max(s-K,0)` | strike | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `MOMENTUM` | 1 | price | `p(T) - p(T - lookback)` | price | 15m | 2s | underlying | quality!=UNRELIABLE |
| `OI_CHANGE` | 1 | positioning | `total_oi(T) - total_oi(T - lookback)` | contracts | 15m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `OI_CHANGE_PCT` | 1 | positioning | `(total_oi(T) - total_oi(T - lookback)) / total_oi(T - lookback)` | ratio | 15m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `OI_CONCENTRATION` | 1 | positioning | `sum((oi_s / sum(oi)) ** 2)` | index | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `OI_WALL_CALL` | 1 | positioning | `argmax_s oi_call(s) subject to oi_call(s) >= prominence * mean(oi_call(others))` | strike | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `OI_WALL_MIGRATION` | 1 | positioning | `wall_strike(T) - wall_strike(T - lookback)` | strike_points | 15m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `OI_WALL_PUT` | 1 | positioning | `argmax_s oi_put(s) subject to oi_put(s) >= prominence * mean(oi_put(others))` | strike | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `PCR` | 1 | positioning | `sum(oi_put) / sum(oi_call)` | ratio | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `PCR_OI_CHANGE` | 1 | positioning | `delta_sum(oi_put) / delta_sum(oi_call)` | ratio | 15m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `PRICE_OI_RELATIONSHIP` | 1 | positioning | `corr(delta_price_t, delta_oi_t) over the window` | correlation | 30m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `PUT_OI_MIGRATION` | 1 | positioning | `sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)` | strike_points | 15m | 2s | expiry | oi_coverage>=0.95, quality!=UNRELIABLE |
| `RANGE` | 1 | price | `max(p_window) - min(p_window)` | price | 30m | 2s | underlying | quality!=UNRELIABLE |
| `REALIZED_MOVE` | 1 | price | `|p(T) - p(T - lookback)| / p(T - lookback)` | ratio | 30m | 2s | underlying | quality!=UNRELIABLE |
| `REALIZED_VOL_CLOSE_TO_CLOSE` | 1 | volatility | `stdev(ln(p_t / p_{t-1})) * sqrt(periods_per_year)` | vol_annualised | 30m | 2s | underlying | quality!=UNRELIABLE |
| `REALIZED_VOL_PARKINSON` | 1 | volatility | `sqrt(ln(high/low)^2 / (4 * ln 2)) * sqrt(periods_per_year)` | vol_annualised | 0s | 2s | underlying | quality!=UNRELIABLE |
| `REGIME` | 1 | structure | `rules over (realised_move, trend_slope, range) with declared thresholds; UNKNOWN when none applies` | category | 30m | 2s | underlying | quality!=UNRELIABLE |
| `RESISTANCE_FROM_POSITIONING` | 1 | structure | `argmax_{s > spot} oi_call(s)` | strike | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `RETURN` | 1 | price | `p(T) / p(T - lookback) - 1` | ratio | 15m | 2s | underlying | quality!=UNRELIABLE |
| `SPOT_FUTURES_DIVERGENCE` | 1 | futures | `(f(T)/f(T-lb) - 1) - (s(T)/s(T-lb) - 1)` | ratio | 15m | 2s | underlying | quality!=UNRELIABLE |
| `STRUCTURE_MIGRATION` | 1 | structure | `mid(support, resistance)(T) - mid(support, resistance)(T - lookback)` | strike_points | 30m | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `SUPPORT_FROM_POSITIONING` | 1 | structure | `argmax_{s < spot} oi_put(s)` | strike | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `THETA_EXPOSURE` | 1 | greeks | `sum(theta * oi * lot_size * contract_multiplier)` | currency_per_day | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `TREND` | 1 | price | `ols_slope(price_t ~ t)` | price_per_step | 30m | 2s | underlying | quality!=UNRELIABLE |
| `VEGA_EXPOSURE` | 1 | greeks | `sum(vega * oi * lot_size * contract_multiplier)` | currency_per_vol_point | 0s | 2s | expiry | quality!=UNRELIABLE, greeks_coverage>=0.90, oi_coverage>=0.95 |
| `VOLUME_OI_RATIO` | 1 | positioning | `sum(volume) / sum(oi)` | ratio | 0s | 2s | expiry | quality!=UNRELIABLE, oi_coverage>=0.95 |
| `VOLUME_ZSCORE` | 1 | price | `(v_now - mean(v_window)) / stdev(v_window)` | zscore | 30m | 2s | expiry | quality!=UNRELIABLE |
| `VWAP` | 1 | price | `sum(ltp_i * volume_i) / sum(volume_i)` | price | 0s | 2s | expiry | quality!=UNRELIABLE |

---

## 5. Availability

```
available_at = max(lookback_end, latest_input_available_at, computed_at) + availability_delay
```

`latest_input_available_at` resolves per input kind exactly as `07` §3 specifies: a raw
observation contributes its **`ingested_at`**, a derived dependency contributes its own
**`available_at`**, recursively. The delay is applied *after* the max, so a long delay
cannot be absorbed by a late input instead of extending past it.

The worked example reproduces: an input observed 11:40 and ingested 11:44, with a 2 s
delay, yields `11:44:02` — not `11:40:02`.

Tested over every feature:

* all three invariants hold on every produced value;
* `test_a_late_ingested_input_delays_availability` — two states differing only in
  `ingested_at` produce different `available_at`;
* `test_observed_at_alone_never_determines_availability` — an explicit negative
  asserting `available_at > observed_at + delay` when the input arrived late;
* `test_a_dependent_is_never_available_before_its_dependency` — propagation along a
  resolved chain.

---

## 6. Quality gating

Requirements are parsed at registration (a typo is an import error, not a gate that
never fires) and evaluated **before** computation. Two forms, both from the design's own
examples: `quality!=UNRELIABLE` and `oi_coverage>=0.95`.

Every feature declares `quality!=UNRELIABLE`; an UNRELIABLE state computes nothing, and
`test_the_engine_skips_rather_than_computing` asserts `computed_count == 0`.

`07` §2's worked case is a test: a backfill window with no greeks fails the gate of every
feature requiring `greeks_coverage>=0.90`, returning *unavailable* rather than a number
derived from missing inputs.

**A requirement naming something unmeasurable counts as unmet.** A gate that passes when
it cannot check is not a gate.

---

## 7. Missing stays missing

No feature converts absence to zero. A sum over no observations is `None`; `PCR` with
zero call OI is `UNDEFINED`, not infinity; `VWAP` without volume is unavailable rather
than an unweighted mean masquerading as a weighted one; `IV_RANK` and `IV_PERCENTILE`
return `INSUFFICIENT_HISTORY` below their declared minimum, because fabricating a
percentile is worse than withholding it.

`Unavailable` is a first-class result carrying a typed reason, not an error, and the
engine reports every one.

---

## 8. Conventions made explicit

* **GEX dealer sign is a registered parameter**, with two variants —
  `DEALER_SHORT_CALLS_LONG_PUTS` and `DEALER_LONG_ALL`. An unknown convention is refused,
  never defaulted. No GEX feature carries a directional label; a test asserts the word
  "bullish"/"bearish" appears nowhere except inside an explicit disclaimer.
* **Lot size is supplied for `observed_at`** and never guessed. Exposure features return
  `MISSING_INPUT` without it — `18-ROADMAP.md` names lot-size correctness as gating all
  exposure work because the legacy seeds are stale, and a guessed lot size is a wrong
  exposure that looks right.
* **Support/resistance are `*_FROM_POSITIONING`**, so a stored row can never be mistaken
  for a price-technical level.
* **`REGIME` is rule-based with declared thresholds**, and `UNKNOWN` is a real answer.
* **`BUILDUP_CLASSIFICATION` and `PCR` carry no directional label.**

---

## 9. OI analytics and migration

Everything is computed from `MarketState`, never from a database query. The reference for
`OI_CHANGE` and friends is resolved by point-in-time lookup from supplied history, never
from a stored `prev_*`: a stored previous value cannot be reconstructed for an arbitrary
past moment and would leak the *current* previous value into a historical computation.
`provider_prev_oi` is kept distinct — it is the provider's own assertion, a separate
observation.

`MigrationTracker` implements the `07` §5 lifecycle. The brief's worked case
(`25,000 PE → 25,200 PE → 25,300 PE`) is asserted to produce **one** entity with an
advancing destination, an unrewritten origin, `windows=3`, `CONFIRMED` status and a
10-minute duration. Decay **fades** rather than deletes, because a migration that stopped
is a fact research needs.

---

## 10. Two changes outside `analytics/`

**`Provenance.max_input_ingested_at` (additive, defaults to `None`).** Phase 4
availability must derive from input readiness, and `MarketState` carried no
`ingested_at`. Without this an analytic could only see `observed_at` and would compute
an `available_at` preceding the moment the input existed — look-ahead inside the
mechanism built to prevent it. The Phase 3 builder now tracks it in the same pass; all
Phase 3 tests remain green.

**Five metrics and two emit helpers in `observability/metrics.py`**, per `16` §3:
`analytics_compute_duration_seconds`, `analytics_skipped_total`,
`feature_unavailable_total`, `feature_availability_lag_seconds`,
`feature_input_readiness_lag_seconds`.

---

## 11. Tests and checks executed

| Check | Command | Result |
|---|---|---|
| Full suite | `python3 -m unittest discover -s tests -t .` | **461 tests: 439 pass, 15 errors, 7 skipped** |
| Phase 4 only | `tests/phase4/` | **111 pass** |
| Lint | `ruff check oipulse tools tests` | pass |
| Format | `ruff format --check oipulse tools tests` | pass, 121 files |
| Byte-compile | `python -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, 4 contracts |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 6 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 26 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 4 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 83 files |

**The 15 errors are pre-existing on the base and unrelated to Phase 4:** 10 are
`ModuleNotFoundError: No module named 'google'` (the Phase 2 V3 protobuf decoder tests)
and 5 are FastAPI `TestClient` tests added by the Phase 3 verification commit. Neither
`protobuf` nor `fastapi` is installable here. The base produces the same 15 before any
Phase 4 code existed. **113 tests added, zero regressions.**

Phase 4 test breakdown: registry conformance 23, hand-computed values 42, properties and
migration lifecycle 16, engine/API/observability 30.

The mandatory six per `15-TESTING.md` §3 are covered as: (1) hand-computed values in
`test_domains.py`; (2)–(5) asserted over **every** registered feature by iterating the
registry in `test_registry_conformance.py`, rather than copied 54 times — writing them
out per feature guarantees the 55th is the one somebody forgets; (6) property tests in
`test_properties.py`, including the three the design names (PCR ≥ 0, exposure
aggregation equals the total, migration magnitude bounded by the strike range).

### Not executed here

`pytest`, `mypy --strict`, `lint-imports`, `alembic upgrade head`, and `/features` over
HTTP. DNS resolution fails for `pypi.org`, so no package can be installed; there is no
PostgreSQL and no FastAPI. The stdlib typing guard passes over all 83 files and is
explicitly **not** equivalent to mypy — no inference, no assignment compatibility, and a
nominal-only override check.

---

## 12. Known limitations

1. **No `metric_values` writer or reader.** The schema, the migration and the endpoint
   contract exist; wiring durable persistence needs a database to test against.
   `/features/{id}/values` answers 503 naming the missing reader rather than returning an
   empty series, which would be indistinguishable from the feature having produced
   nothing.
2. **Lot size and days-to-expiry are caller-supplied parameters.** Resolving them from
   the instrument version valid at `observed_at` requires the instrument store, which the
   pure layer may not reach. The features refuse rather than guess, and the roadmap's
   "re-verify lot sizes against Upstox contract data" remains outstanding.
3. **`REALIZED_VOL_*` annualisation factors are declared parameters with defaults**
   (98280 five-minute periods per year; 252 days). They are conventions, not facts, and a
   caller may state its own.
4. **`ATR` uses absolute step change, not a true high/low/close range**, because a
   `MarketState` carries a point observation rather than a bar. Documented in the
   feature's own definition.
5. **`interp_labels` is declared but unpopulated.** Interpretation labels belong to the
   Phase 5 signal surface; the table is created now because it is part of `02` §6.
6. **No performance measurement.** The full 54-feature run over one state completes well
   inside the test suite's 1.3 s total, but no benchmark against production-sized chains
   was run — that needs realistic data volumes.
7. **`.env.example` is unreadable in this sandbox** (read-deny list), so git reports a
   phantom modification it cannot diff. Excluded from the commit.

---

## 13. Scope control

No signals, alerts, research, event studies, replay, backtesting, paper trading, risk,
OMS, portfolio attribution, terminal or ML. No dashboard and no BUY/SELL conclusion.

Phase 1–3 behaviour is unchanged apart from the additive `max_input_ingested_at` in §10.
