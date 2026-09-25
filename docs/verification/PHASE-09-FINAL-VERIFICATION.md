# OI Pulse v2 — Phase 9 Independent Verification Report

## Status: PHASE 9: PASS

---

## 1. Provenance & Exact Base Tree Verification

### Commits & Trees
- **Branch**: `phase9-risk`
- **Base Commit**: `4117531fe5b826e034bed4bec6c950939f52881f` (on `origin/main`)
- **Phase 8 Verified Tag (`oi-pulse-v2-phase8`)**: `43bbd5cf2823819e10812baddcabc478efb8b237`
- **Phase 8 Verified Tree (`43bbd5c^{tree}`)**: `8ccc325104a65c99f6a45b496a5eec97fc9d97c2`
- **Base Commit Tree (`4117531^{tree}`)**: `8ccc325104a65c99f6a45b496a5eec97fc9d97c2`
- **`origin/main^{tree}`**: `8ccc325104a65c99f6a45b496a5eec97fc9d97c2`
- **Tree Identity Check**:
  - `43bbd5c^{tree} == 4117531^{tree} == origin/main^{tree}` (0 diff, byte-identical).
- **Claude Implementation Commit**: `44d945effe97e6281cd7bd198be4f1e0aa30f568`
- **Final Verified Commit**: `817ca85`

---

## 2. Defects Identified and Resolved

### 1. Strict Typing Issue in Venue Health Limit Check
- **Location**: `oipulse/trading/risk/limits.py:612`
- **Defect**: `state.venue_health` is non-optional `VenueHealth`. An unreachable `if health is None:` defensive check triggered `mypy --strict` error (`Statement is unreachable [unreachable]`).
- **Fix**: Removed the unreachable branch, restoring strict type validation across all 161 source files with 0 errors.

### 2. Comprehensive Mutation Testing for Authorization Guard
- **Location**: `tests/phase9/test_integration_and_guards.py`
- **Enhancement**: Added `TestRiskAuthorizationGuardMutation` exercising real AST mutations against all six enforcement rules in `tools/check_risk_authorization.py`:
  - Missing authorization columns on `PaperOrder`.
  - Construction of `PaperOrder` outside `runtime.py`.
  - Inverted positional ordering of `is_actionable_at` and `PaperOrder` construction in `submit()`.
  - Layer violation (risk importing `oipulse.trading.orders`).
  - Intent mutation within the risk package.
  - Decision `authorizes()` bypassing intent ID comparison.

### 3. Comprehensive FastAPI `TestClient` Runtime Verification
- **Location**: `tests/phase9/test_integration_and_guards.py`
- **Enhancement**: Added `TestRiskApiRuntime` exercising live HTTP interactions across all `/risk/*` endpoints:
  - 503 Service Unavailable when unconfigured.
  - Profiles listing, retrieval, registration (with 409 conflict on duplicate versions).
  - Risk state and limit utilization status retrieval.
  - Intent evaluation (`POST /risk/evaluate`) and decision querying.
  - Immediate operational kill switch (`POST /risk/kill-switch`, `DELETE /risk/kill-switch`).

---

## 3. Detailed Verification Against Authoritative Specification

### A. The Three-Layer Authorization Model
The central Phase 9 invariant:
```
TradeIntent → RiskEvaluation → approved RiskDecision → execution authorization
```
is verified across all three layers:
1. **Static AST Guard (`tools/check_risk_authorization.py`)**:
   - `PaperOrder` carries composite key `(authorizing_risk_decision_id, authorizing_decision_sequence)`.
   - Only `PaperTradingRuntime` constructs `PaperOrder`.
   - The `is_actionable_at` check precedes `PaperOrder` construction positionally in `submit()`.
   - Risk package cannot reach execution modules (`orders`, `brokers`, `execution`, `runtime`, `ledger`).
   - Risk never mutates `TradeIntent`.
   - `RiskDecisionRecord.authorizes` strictly matches `intent_id`.
2. **Database Composite Foreign Key & Trigger (Migration `0009_phase9_risk.py`)**:
   - `fk_trade_orders_authorizing_decision`: Composite FK on `(intent_id, authorizing_decision_sequence)` references `risk_decisions(intent_id, sequence_no)`. An order can only reference a decision evaluated for its *own* `intent_id`.
   - `trg_trade_orders_require_approved_decision`: Trigger asserts the referenced decision is `APPROVED` or `MODIFIED`. Rejections and expired decisions are rejected at the DB level.
3. **Runtime Enforcement (`PaperTradingRuntime.submit`)**:
   - Refuses submission before constructing orders if risk is unevaluated, rejected, or expired.

### B. Fail-Closed Default & Intent-Scoped Authorization
- **No-Risk-Policy Behavior**: Accounts with no risk policy attach `UNEVALUATED_RISK` (`evaluated=False`). `is_actionable_at()` evaluates to `False`, refusing execution. No orders are constructed.
- **Intent Immutability**: Resizing calculates an `approved_quantity` on the decision record while `TradeIntent.quantity` and content digest remain strictly immutable.
- **Expiry Inclusivity**: Approvals are valid up to $T_{\text{evaluation}} + \text{validity}$ (inclusive boundary tested at $T - \varepsilon$, $T$, and $T + \varepsilon$).

### C. Point-in-Time & Knowledge Horizons
- **Evaluation Knowledge Horizon**: Risk evaluation operates at its own knowledge horizon $K_{\text{eval}}$ and evaluates states available up to $K_{\text{eval}}$. States with $K_{\text{state}} > K_{\text{eval}}$ are refused (`test_a_state_assembled_at_k2_is_refused_at_k1`).
- **Look-Ahead Prevention**: Greek and exposure inputs missing from the canonical market state evaluate as `NOT_EVALUABLE` and fail closed, never guessing or substituting stale numbers.

### D. Concurrency & Pending Intents
- **State Headroom**: `RiskState.pending_intent_ids` tracks in-flight non-terminal intents.
- **Sequential Semantics**: Competing intents submitted in sequence consume limits (e.g. available cash, position limits); the second intent sees the first's commitment and breaches fail-closed.

### E. Idempotency & Audit Chain
- **Deterministic Identity**: Decisions are content-addressed via `inputs_digest` (hashing intent digest, policy digest, and `risk_state_ref`). Evaluation timestamp is excluded from the digest to ensure reproducibility across replays.
- **Audit Traversal**: All decision evidence fields are structured (`LimitEvaluation` records, category, status, headroom, limits) answering all 13 regulatory provenance questions.

---

## 4. Independent Quality Gate Summary

| Check | Target / Command | Result | Details |
|---|---|---|---|
| **Test Suite** | `pytest -v` | **PASS** | 1130 passed, 14 skipped (DB integration tests requiring PostgreSQL), 0 failed |
| **Strict Type Checking** | `mypy --strict oipulse` | **PASS** | `Success: no issues found in 161 source files` |
| **Linting** | `ruff check .` | **PASS** | 0 lint errors |
| **Formatting** | `ruff format --check .` | **PASS** | 283 files cleanly formatted |
| **Compilation** | `python -m compileall -q oipulse tools tests` | **PASS** | Bytecode compiles cleanly |
| **Risk Authorization Guard** | `python tools/check_risk_authorization.py` | **PASS** | All 6 checks passing & mutation-tested |
| **Paper Safety Guard** | `python tools/check_paper_trading_safety.py` | **PASS** | Live execution verified absent |
| **Import Boundaries** | `python tools/check_import_boundaries.py` | **PASS** | 15 layer contracts armed and clean |
| **Clock Access Guard** | `python tools/check_clock_access.py` | **PASS** | 0 wall-clock access outside `core/clock.py` |
| **Migration Chain** | `python tools/check_migration_chain.py` | **PASS** | Linear chain 0001 $\rightarrow$ 0009, 1 head |
| **Migration Order** | `python tools/check_migration_order.py` | **PASS** | Valid DDL ordering across 9 revisions |
| **Schema Parity** | `python tools/check_schema_parity.py` | **PASS** | 47 tables mirrored in upgrade/downgrade |
| **Alert Purity Guard** | `python tools/check_alert_purity.py` | **PASS** | Alerts cannot mutate signal truth |
| **Strategy Surface** | `python tools/check_strategy_surface.py` | **PASS** | Strategy interface closed |
| **Temporal Repository**| `python tools/check_temporal_repository.py` | **PASS** | All queries take temporal bounds |
| **Strict Typing Guard**| `python tools/check_typing_strict.py` | **PASS** | 161 source files compliant |

---

## 5. Scope & Boundary Enforcement

- **No Phase 10+ Leaks**: Verified no OMS, live broker adapters, broker reconciliation, or live execution routing exists.
- **Seam Integrity**: Risk sits strictly between `TradeIntent` and execution without bypassing.

---

## 6. Final Decision

**PHASE 9: PASS**
