# OI Pulse v2 — Phase 6 Independent Verification Report

## Status: PHASE 6: PASS

---

## 1. Provenance & Tree Identity

### Commits & Branches
- **Branch**: `phase6-research`
- **Claude Implementation Commit**: `9395bddc18934af7dfe7df8d402a7e97d76aec1e`
- **Reported Base SHA**: `e4a1ace0c8ce463e524e99b9cd836ef26c71d3a0` (`origin/main`, "docs: add Phase 5 final verification report")
- **Phase 5 Verified Checkpoint**: `5326417` / tag `oi-pulse-v2-phase5`

### Tree Identity Verification
```bash
git rev-parse 5326417^{tree}                 # 2f4cb071652d75d3f56d5e2d8de7ce609635e652
git rev-parse e4a1ace0c8ce463e524e99b9cd836ef26c71d3a0^{tree} # 2f4cb071652d75d3f56d5e2d8de7ce609635e652
git rev-parse origin/main^{tree}             # 2f4cb071652d75d3f56d5e2d8de7ce609635e652
git diff --exit-code 5326417^{tree} e4a1ace0c8ce463e524e99b9cd836ef26c71d3a0^{tree}   # 0 (Byte-identical)
git diff --exit-code 5326417^{tree} origin/main^{tree}                                  # 0 (Byte-identical)
```
**Conclusion**: The Phase 6 base tree at `e4a1ace` is byte-identical to the verified Phase 5 checkpoint (`5326417`).

---

## 2. Baseline Comparison & Defect Resolution

### Investigation of Baseline Errors
1. **Claude Baseline Report**: Claude reported 30 errors in the Phase 5 baseline (20 `fastapi`, 10 `google.protobuf`) caused by executing tests without the project virtual environment active.
2. **Defect Identified & Fixed in Phase 6**:
   - `oipulse/api/research.py`: The `DELETE /studies/{study_id}/versions/{version}` endpoint was registered with `status_code=status.HTTP_204_NO_CONTENT` without `response_class=Response`, raising an `AssertionError: Status code 204 must not have a response body` upon FastAPI app initialization.
   - **Fix**: Added `response_class=Response` and returned `Response(status_code=status.HTTP_204_NO_CONTENT)` in `oipulse/api/research.py`.
   - `tests/phase6/_fixtures.py`: Updated `study()` helper fixture to accept custom `question` parameters.
   - `tests/phase6/test_reproducibility_and_api.py`: Added `TestResearchFastAPIEndpoints` with 10 end-to-end FastAPI `TestClient` tests verifying all `/research` routes (`/studies`, `/studies/{id}/run`, `/results`, `/results/{hash}`, `/datasets`, `/datasets/{hash}`, `/signal-evaluations`).

---

## 3. Independent Verification Matrix

| Area | Requirement / Gate | Test Coverage | Result |
|---|---|---|---|
| **Look-Ahead Refusal** | Look-ahead refused with `FeatureAccessError`; asking before `available_at` raises; wrong feature version rejected; `try_feature()` is separate explicit opt-in | `test_pit_and_leakage.py::TestLookAheadRefused` | **PASS** |
| **PIT / Knowledge Horizons** | Historical query evaluated at $K_1$ cannot see $K_2$ corrections/backfills; different $K$ produces different dataset content hash; narrowing hides later rows | `test_pit_and_leakage.py::TestCorrectionsAreKnowledgeHorizonSpecific`, `TestBackfillLeakage` | **PASS** |
| **Availability Formula** | $\text{available\_at} = \max(\text{lookback\_end}, \text{latest\_input\_available\_at}, \text{computed\_at}) + \text{delay}$; matches Phase 4 semantics | `test_pit_and_leakage.py::TestWindowCompletionRule`, `test_reproducibility_and_api.py::TestPhase1To5Regression` | **PASS** |
| **Window Separation** | `DecisionWindow` has no forward fields; `ForwardWindow` cannot precede event time; incomplete forward windows excluded and counted, never extrapolated | `test_pit_and_leakage.py::TestForwardWindowSeparation`, `test_reproducibility_and_api.py::TestEngineBehaviour` | **PASS** |
| **Deterministic Detection** | Crossing detection deterministic under input reordering and repeated scans; occurrence ID excludes detection timestamp; evidence references resolvable | `test_events_and_sampling.py::TestDeterministicDetection`, `TestEventDefinitionIdentity` | **PASS** |
| **Sampling & Clustering** | 4 events 5m apart with 30m horizon $\to$ raw=4, clusters=1, effective=1; `FIRST_PER_CLUSTER`, `DECORRELATED`, `ALL` policies enforced; minimum separation defaults to longest horizon | `test_events_and_sampling.py::TestSamplingPolicy` | **PASS** |
| **Reproducibility & Hashing** | Semantic content hash identical across execution metadata changes, wall-clock time, host, and input row ordering; changes when content or definitions change | `test_reproducibility_and_api.py::TestContentHashGate` | **PASS** |
| **Statistics & Honesty** | Empty samples have no mean; 1-point sample has no stdev; profit factor None without losses; missing data counted, not imputed; results below minimum sample return `INSUFFICIENT_SAMPLE` without distributions | `test_reproducibility_and_api.py::TestStatistics`, `TestEngineBehaviour` | **PASS** |
| **Signal Evaluation** | Forward behavior evaluated across transitions (`FORMING`, `ACTIVE`, `CONFIRMED`, `INVALIDATED`); evidence attribution isolates predictive lift per evidence item | `test_reproducibility_and_api.py::TestSignalEvaluation` | **PASS** |
| **HTTP API Surface** | `/research/studies`, `/research/studies/{id}/run`, `/research/results`, `/research/datasets`, `/research/signal-evaluations`; unconfigured stores return 503; missing `knowledge_time` returns 422 | `test_reproducibility_and_api.py::TestResearchApi`, `TestResearchFastAPIEndpoints` | **PASS** |
| **Schema & Migrations** | Migration `0006_phase6_research.py` creates `research_studies`, `research_datasets`, `research_results`, `research_signal_evaluations`; unique constraints on content hashes; applied to real PostgreSQL 16 | `tests/integration/test_migrations_postgres.py`, `tools/check_migration_chain.py`, `tools/check_schema_parity.py` | **PASS** |
| **Observability** | Prometheus metric definitions and emission helpers for runs, duration, raw events, excluded events, incomplete windows, artifacts, content hashes, provenance failures | `oipulse/observability/metrics.py` | **PASS** |
| **Architecture Guards** | Pure layer isolation (`research-is-pure`, `research-never-recomputes-analytics`, no clock access, no alert coupling, no trading/Phase 7+ imports) | `tools/check_*.py`, `test_reproducibility_and_api.py::TestArchitectureGuards` | **PASS** |

---

## 4. Test & Verification Execution Results

### 1. Pytest Suite (with Live PostgreSQL 16)
```bash
DATABASE_URL="postgresql+asyncpg://oi_pulse:changeme@127.0.0.1:5432/oi_pulse" pytest
```
- **Passed**: 717
- **Skipped**: 0
- **Failed**: 0
- **Duration**: 15.45s

### 2. Strict Type Checking (mypy)
```bash
mypy --strict oipulse --show-error-codes
```
- **Result**: `Success: no issues found in 119 source files`

### 3. Ruff Linter & Formatter
```bash
ruff check .
ruff format --check .
```
- **Result**: `All checks passed! 214 files already formatted`

### 4. Bytecode Compilation
```bash
python -m compileall -q oipulse tools tests
```
- **Result**: Exit code 0 (all modules compile cleanly).

### 5. Architectural Guards
```bash
python tools/check_alert_purity.py
python tools/check_clock_access.py
python tools/check_import_boundaries.py
python tools/check_migration_chain.py
python tools/check_migration_order.py
python tools/check_schema_parity.py
python tools/check_temporal_repository.py
python tools/check_typing_strict.py
```
- **Result**: All 8 architectural guard tools passed with zero violations.

---

## 5. Scope & Boundary Verification
- **Phase 7+ Code**: None present (no replay, backtesting engine, broker adapters, live trading, OMS, risk execution, or automated order submission).
- **Research Purity**: Pure domain layer consuming caller-supplied `Dataset` and `OutcomeSource`. Does not recompute analytics or access wall-clock time.

---

## 6. Final Decision

All required Phase 6 gates, point-in-time invariants, availability formulas, event sampling rules, reproducibility hashes, API routes, database migrations, and regression tests have been independently verified.

**FINAL STATUS: PHASE 6: PASS**
