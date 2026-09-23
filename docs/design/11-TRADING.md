# OI Pulse v2 — Paper Trading, Risk, Execution, Reconciliation and Portfolio

> **Deliverables N (paper trading), O (risk), P (execution), Q (portfolio).**
> Live automated execution is **out of scope for the initial build** (brief §21). The
> architecture reaches it; the roadmap does not enable it.

---

## 1. The trade pipeline

```
Signal / Strategy / Manual
        │
        ▼
   TradeIntent ─────────────── the universal seam
        │
        ▼
   RiskEngine ──────────────── the ONLY gate. Non-bypassable.
        │
   RiskDecision (immutable sequence, appended)
        │
        ▼
   OrderManager (OMS) ──────── state machine
        │
        ▼
   BrokerAdapter ───────────── PaperBrokerAdapter | UpstoxBrokerAdapter
        │
        ▼
   Fill ──► Position ──► Portfolio ──► Attribution
        │
        └──► Reconciliation ◄── broker truth is authoritative
```

There is no arrow from Strategy to BrokerAdapter. Enforced three ways: an import-linter
contract (`trading/risk` may not be bypassed in the call graph), a database constraint
(an order row requires an approved risk decision from its own intent —
`02-DATA_MODEL.md` §11), and a test asserting no code path reaches an adapter without one.

---

## 2. TradeIntent — the universal seam

```
TradeIntent
├── id, client_order_intent_id
├── account_id                  → mode PAPER | LIVE
├── source                      STRATEGY | SIGNAL | MANUAL
├── source_ref                  signal id, strategy run, user
├── legs[]                      instrument, side, qty, order_type, limit/trigger
├── time_in_force
├── constraints                 max_slippage, valid_until, all_or_none
├── rationale_ref               → the signal/evidence that motivated it
├── state_checkpoint_ref        ← what the system knew when deciding
└── created_at
```

**One type, consumed identically by paper and live.** The only difference is which adapter
the account's `mode` resolves to. This is what makes paper trading a genuine rehearsal
rather than a parallel implementation that drifts.

`state_checkpoint_ref` closes the research loop: every trade links back to exactly the
market picture that produced it, so "what did we know when we made this decision?" is a
join.

Multi-leg intents are first-class — an options platform whose unit is a single order
cannot express a spread.

---

## 3. Risk engine

### Independence
`trading/risk` may not import `trading/oms`, any broker adapter, or any strategy module.
It receives an intent plus context and returns a decision. It is independently testable
with no trading infrastructure present, which is the point: the component that says "no"
must not depend on the components it constrains.

### Decisions are an immutable sequence

```
RiskDecision
├── intent_id, sequence_no      PRIMARY KEY (intent_id, sequence_no)
├── decision                    APPROVED | REJECTED | MODIFIED
├── reasons[]                   every limit evaluated, with values
├── limits_evaluated            full snapshot for audit
├── modified_legs               when MODIFIED
├── risk_state_ref              ◄── the MarketState THIS evaluation saw
├── risk_evaluation_time        ◄── when it evaluated
├── inputs_digest               ◄── hash of portfolio + limits + state inputs
├── approved_until              ◄── validity window; null when rejected
└── decided_at
```

**Not one decision per intent.** Risk is re-evaluated on modification, retry, changed
market conditions, changed quantity or amendment. Each evaluation **appends** a new
immutable decision. An order references the **exact** decision that authorized it via
`authorizing_risk_decision_id`.

### Each decision records its own context

The intent's `state_checkpoint_ref` records what the *strategy* saw when it formed the
intent. That is **not** what a later risk evaluation saw:

```
11:45  intent created            state_checkpoint_ref → S(11:45)
11:45  risk decision #1          risk_state_ref       → S(11:45)
11:47  conditions change, re-evaluated
11:47  risk decision #2          risk_state_ref       → S(11:47)   ← different state
11:47  order placed, authorized by decision #2
```

Without a per-decision state reference, the audit trail answers "what did the strategy
see?" but not "what did risk see when it approved the order that actually went out" —
and the second question is the one that matters after a loss.

### Approvals expire

`approved_until` bounds how long an approval remains actionable (default: seconds, not
minutes; configurable per account). An order cannot sit on an approval granted under
materially different conditions and then be submitted.

Submitting against an expired approval is **refused**, not silently re-approved: the
intent must be re-evaluated, producing a new decision in the sequence. This closes the gap
where a stale approval becomes a licence to trade a market that has since moved.

### Limit categories

| Category | Examples |
|---|---|
| Position | max position per instrument / underlying / strategy |
| Order | max order quantity, max notional, max orders per interval |
| Capital | max deployed capital, max margin utilization |
| Loss | max daily loss, max drawdown, max loss per strategy |
| Exposure | max delta, gamma, vega, theta exposure; net and gross |
| Concentration | max share in one underlying / expiry / strike |
| Data | **stale data check** — reject if state quality is `UNRELIABLE` or beyond a staleness budget |
| Broker | broker health, connectivity, rate-limit headroom |
| Session | trading window, no orders outside market hours, expiry-day rules |
| Kill switch | global and per-strategy, immediate, operator-triggered |

The **data check** is the one most systems omit and it belongs here: trading on a
`MarketState` known to be unreliable is a risk decision, and the risk engine is where that
judgement is made explicit and recorded.

### Evaluation
Deterministic and ordered; every limit evaluated and recorded even after the first
rejection, so the decision shows the complete picture rather than the first failure.
Pure with respect to its inputs (intent + portfolio + state + limits), making it
exhaustively testable.

---

## 4. Order state machine

```
   CREATED
      │ validate shape, instrument, session
      ▼
  VALIDATING
      │
      ▼
  RISK_CHECK ──── REJECTED (terminal)
      │ approved
      ▼
  SUBMITTED ──────────────────────┐
      │ ack received              │ no ack / network failure
      ▼                           ▼
  ACKNOWLEDGED                 UNKNOWN
      │                           │
      │                           ▼
      │                  PENDING_RECONCILIATION
      │                           │ broker queried
      │◄──────────────────────────┤
      ▼                           ▼
PARTIALLY_FILLED ──► FILLED   (or REJECTED | CANCELLED | EXPIRED | FAILED)
```

Terminal states: `FILLED` · `REJECTED` · `CANCELLED` · `EXPIRED` · `FAILED`.

Every transition is an appended row in `trade_order_events` with timestamp, trigger and
payload — the order aggregate is event-sourced because its history is genuinely required
for audit and the volume is tiny.

**Invalid transitions raise.** The machine is a declared table of permitted transitions,
not scattered `if` statements.

---

## 5. UNKNOWN — the state most systems omit

Network ambiguity is a fact, not an exception.

```
Order submitted to broker
   → broker MAY have accepted it
   → response or acknowledgement lost (timeout, disconnect, crash, deploy)
   → OI Pulse cannot distinguish accepted from rejected
   → state := UNKNOWN
   → state := PENDING_RECONCILIATION
   → query broker order history / order status
   → broker's answer is AUTHORITATIVE
   → resolve to the actual state
```

Hard rules:

- **Never assume rejected.** Assuming rejection and resubmitting can double a position.
- **Never assume accepted.** Assuming acceptance can leave a real position untracked.
- **Never resubmit from UNKNOWN.** Retry is permitted only after reconciliation
  establishes that no order exists at the broker.
- **An order in UNKNOWN blocks** further intents for that instrument from the same
  strategy until resolved, so ambiguity cannot compound.

### Internal idempotency vs broker-side duplicate prevention

These are different things and conflating them is dangerous:

| | Internal idempotency | Broker-side duplicate prevention |
|---|---|---|
| Keys | `client_order_intent_id`, `client_order_attempt_id`, `idempotency_key` | vendor-specific, if any |
| Guarantees | our own dedup, audit trail, safe local retry | **not guaranteed** |
| Owner | us | the broker |

We maintain our keys for local dedup and audit. We do **not** assume Upstox will reject a
duplicate submission on the strength of them. Safety comes from the UNKNOWN →
reconciliation path, not from the key.

---

## 6. Reconciliation — a first-class subsystem

Not an error handler. A subsystem with its own scheduler, records and tests.

### Scope
```
local OMS  ↔  broker order state
local fills ↔ broker trades
local positions ↔ broker positions
```

### Triggers
| Trigger | When |
|---|---|
| Event-driven | any order enters `UNKNOWN` |
| Startup | every process restart, before accepting new intents |
| Periodic | on a timer during market hours |
| Post-disconnect | after any broker WS reconnect |
| Session boundary | market open and close |
| Manual | operator |

### Handles
Acknowledgement loss · process restart · WS disconnect · missed broker events · partial
fills · duplicate callbacks · broker-side cancellations · **manual broker-side changes**
(a human acting in the broker terminal) · rejections discovered late.

### Procedure
```
1. snapshot broker truth: list_orders(since), list_trades(since), get_positions()
2. snapshot local state for the same scope
3. diff → discrepancies, classified
4. resolve: broker is authoritative for order state, fills and positions
5. apply: append order events, insert missing fills (idempotent on broker_fill_id),
          correct positions
6. persist the run: broker snapshot, discrepancies, resolutions
7. emit ReconciliationCompleted; alert on unresolved or unexpected discrepancies
```

**Reconciliation must be able to rebuild local state from broker truth.** That is the
acceptance criterion: wipe local order and position state, run reconciliation, and the
result must match the broker.

Discrepancies are never silently corrected — every one is recorded with its resolution, so
a pattern of them is visible rather than absorbed.

Order-update WebSocket events are treated as *hints* that accelerate reconciliation, never
as the sole source of truth. Missed events are assumed possible.

---

## 7. Paper trading

`PaperBrokerAdapter` implements `BrokerAdapter` exactly — same protocol, same order
lifecycle, same states including `UNKNOWN` (which it can be instructed to simulate, so the
reconciliation path is exercised rather than theoretical).

Supports market, limit and stop orders, order status, partial fills, fills, cancellations,
positions, average price, realized and unrealized P&L.

Fills are produced by the same `FillModel` the backtester uses (`10-REPLAY.md` §6), driven
by live market data: spread from observed bid/ask, configurable latency and slippage,
size-aware partial fills, real fee schedule.

Paper accounts are `TradingAccount(mode=PAPER)`. A user may run several with different
risk profiles and strategies simultaneously. Because mode is account-level, **no strategy,
risk rule or portfolio calculation knows whether it is paper or live** — which is what
makes the paper results meaningful.

---

## 8. Portfolio

### Derivation
Positions are derived from **fill events**, not maintained as a running total that can
drift. A position is a fold over its fills, so it is always reconstructable and always
reconcilable.

```
Position
├── account_id, instrument_id
├── quantity, average_price
├── realized_pnl
├── opened_at, last_fill_at
└── derived: unrealized_pnl, exposure, greeks (from current MarketState)
```

### Portfolio snapshot
Taken on fill and on a timer during market hours:
positions · realized and unrealized P&L · gross and net exposure · margin utilization ·
portfolio delta, gamma, theta, vega · concentration · drawdown.

Portfolio greeks aggregate per-position greeks from the current `MarketState`, using the
**instrument version valid at that time** for lot size (`07-ANALYTICS.md` §4.3). A
lot-size revision does not retroactively rewrite historical exposure.

### Attribution
```
P&L
├── direction        delta-driven
├── volatility       vega-driven
├── time decay       theta-driven
├── convexity        gamma-driven
├── execution        fill price vs decision price
├── slippage         fill price vs expected
└── costs            brokerage, taxes, fees
```

Sliceable by strategy, signal, underlying, expiry, option type, time of day and **market
regime**. Regime attribution is the most useful cut and is only possible because regime is
persisted per state rather than computed ad hoc.

Decomposition uses a documented, versioned method (greek-based P&L explain with a residual
term). The residual is **reported, not hidden** — a large residual signals that the
decomposition is missing something, which is information.

---

## 9. Safety posture

| Control | Mechanism |
|---|---|
| Live trading disabled | feature flag, default off; `UpstoxBrokerAdapter` built last |
| Live requires explicit permission | `LIVE_TRADE` permission, separate from `PAPER_TRADE` |
| No path bypasses risk | import contract + DB constraint + test |
| Kill switch | global and per-strategy; halts new intents, optionally cancels resting orders |
| Stale data blocks trading | a risk limit, evaluated and recorded |
| Ambiguity never resolved by guessing | UNKNOWN → reconciliation |
| Restart is safe | reconciliation before accepting new intents |
| Every decision auditable | intent → risk sequence → order events → fills → position |
| Paper and live are the same code | account mode, not a separate implementation |

---

## 10. The answerable chain

For any historical trade, by foreign-key traversal:

| Question | Source |
|---|---|
| What was true? | observations via the state checkpoint |
| What did OI Pulse know? | `knowledge_as_of` at the decision time |
| When could it use that? | `available_at` on each consumed feature |
| What did the model calculate? | `metric_values` with feature versions |
| What evidence existed? | `signal_evidence`, supporting and contradicting |
| What signal existed? | `signal_signals` with lifecycle history |
| What intent was created? | `trade_intents.state_checkpoint_ref` |
| What risk decision authorized it? | `risk_decisions(intent_id, sequence_no)` |
| What order reached the broker? | `trade_orders` + `trade_order_events` |
| What happened after? | `trade_fills`, reconciliation runs |
| What was the resulting P&L? | `portfolio_snapshots`, `portfolio_attribution` |

Eleven questions, one join path, no log archaeology.
