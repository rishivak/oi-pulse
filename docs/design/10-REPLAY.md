# OI Pulse v2 — Replay and Backtest Architecture

> **Deliverable M.** Replay reconstructs the market state sequence from canonical
> observations. Backtesting runs strategies over that sequence. Both must be deterministic
> and free of look-ahead by construction.

---

## 1. Replay and backtest are one mechanism

A backtest is replay with a strategy attached and a fill simulator on the end. Sharing the
mechanism means a backtest cannot diverge from a replay, and neither can diverge from live
processing — because all three drive the *same* pipeline.

```
                  ┌──────────────────────────────────────────┐
  observations ──►│  build_state()   ← the same function     │
                  │  analytics       ← the same features     │
                  │  signals         ← the same rules        │
                  └───────────────┬──────────────────────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
      live: SSE/UI          replay: UI/analysis    backtest: strategy
                                                    → TradeIntent
                                                    → risk
                                                    → fill simulator
                                                    → portfolio
```

There is no separate "historical" code path. That divergence is the classic source of
backtest/live mismatch and is removed structurally rather than policed.

---

## 2. The replay context

```
ReplayContext
├── run_id
├── universe                underlyings, expiries
├── period                  start_time → end_time
├── market_clock    T       advances through the period
├── knowledge_horizon K     advances in lockstep by default
├── step_mode               CHECKPOINT | FIXED_INTERVAL | EVENT
├── speed                   1x | 5x | 10x | MAX (as fast as compute allows)
└── build_context_id, feature_versions{}, rule_versions{}
```

`build_context_id` subsumes builder, staleness-policy and feature-set versions
(`04-MARKETSTATE.md` §1).

### Knowledge horizon modes

| Mode | Meaning | Use |
|---|---|---|
| `K = T` (default) | reproduces what the system could actually have known | backtesting, honest replay |
| `K` pinned later | "what would we conclude about that morning using everything we know now?" | market-truth analysis — **explicitly flagged** |
| `K < T` | incoherent | disallowed |

The default is the honest one. Market-truth mode is recorded on the run so no reader can
mistake one for the other.

---

## 3. Determinism

Three mechanisms, all structural:

1. **Injected clock.** During replay, `Clock` is a `ReplayClock` returning `T`. No module
   calls `datetime.now()` — lint-enforced — so no code can accidentally observe real time.
2. **Pure analytics and rules.** No I/O, no globals, no hidden state
   (`07-ANALYTICS.md` §1).
3. **Pinned versions.** Builder, feature and rule versions are fixed for the run and
   recorded on it.

### Deterministic observation ordering

`(observed_at, channel_sequence, id)` is **not** a total order. `channel_sequence` is
scoped to a feed session and resets on reconnect (`03-EVENT_MODEL.md` §2), so two
observations from different sessions are not comparable by it — and a replay spanning a
reconnect would order them arbitrarily.

The replay ordering key is therefore explicitly cross-session:

```
1. observed_at                      market time
2. feed_session_ordinal             sessions ordered by their first ingested_at,
                                    assigned once and stored — not re-derived per run
3. channel_sequence                 within a session, where the provider supplies it
4. id                               stable tiebreaker; guarantees totality
```

Step 2 is what makes the key total across reconnects. Step 4 guarantees a deterministic
result even when the provider supplies no sequence at all (assumption A-1,
`20-ARCHITECTURE_FREEZE.md` §10) — in that case ordering degrades to
`(observed_at, session, id)`, which is still deterministic, merely less faithful to true
arrival order. Determinism never depends on an unverified provider guarantee.

Other ordering hazards: iteration over instruments is sorted by `InstrumentId`; no logic
depends on dictionary or set ordering.

### The determinism test
Re-derived domain events from a replay are compared against those produced by the original
live run for the same period, versions and knowledge horizon. They must match. Divergence
indicates hidden non-determinism and fails CI (`03-EVENT_MODEL.md` §6). This is the
strongest available reproducibility check and is cheap, since it reuses the normal
pipeline.

---

## 4. Stepping

| Mode | Advances to |
|---|---|
| `CHECKPOINT` | each existing `state_checkpoint` in the period — fastest, and the default |
| `FIXED_INTERVAL` | every N seconds, reconstructing state where no checkpoint exists |
| `EVENT` | every observation — highest fidelity, slowest |

Checkpoint stepping is the default because checkpoints already exist at the cadence the
live system used, so it reproduces the live decision points exactly. Fixed-interval and
event stepping exist for finer-grained analysis, at the cost of reconstruction time.

### Knowledge-aware checkpoint selection

> A checkpoint may be reused **only** when its `market_time`, `knowledge_horizon` and
> `build_context_id` all match the replay context. Anything else is reconstructed.

This is not an optimization detail — it is the invariant. A checkpoint built live at
`K = 11:50` must never satisfy a replay step at `K = 11:43`: it may incorporate
observations that had not arrived by 11:43, which is exactly the look-ahead the whole
architecture exists to prevent, arriving through a cache.

An *earlier*-K checkpoint is equally unusable — it is a different state, missing data that
had arrived by the requested `K`. Selection is therefore **exact match on the full
identity tuple, never nearest-match** (`04-MARKETSTATE.md` §5). A mismatch means
reconstruct.

Practical consequence: a replay in the default lockstep mode (`K = T`) reuses the live
checkpoints, because they were written at `K = T` too. A market-truth replay with `K`
pinned later reuses **none** of them and reconstructs throughout — slower, and correct.

Where no matching checkpoint exists, `build_state()` reconstructs from observations
(`04-MARKETSTATE.md` §5) — so replay works over any period with observation coverage,
whether or not it was checkpointed.

---

## 5. The strategy interface

```python
class Strategy(Protocol):
    def on_state(self, ctx: StrategyContext) -> list[TradeIntent]: ...
```

`StrategyContext` exposes:

| Available | Not available |
|---|---|
| `state: MarketState` at T | any repository or session |
| `features` — only those with `available_at <= T` | any feature not yet available |
| `signals` — only those with `available_at <= T` | future states or observations |
| `positions`, `account` — as of T | outcome/forward-window data |
| `clock.now()` → T | the wall clock |

**A strategy cannot reach the database.** It receives a context and returns intents. This
is what makes look-ahead impossible rather than merely discouraged — there is no API
surface through which future data could be obtained.

Requesting an unavailable feature raises `FeatureAccessError` (`09-RESEARCH.md` §2). The
backtest fails loudly rather than silently producing an optimistic result.

---

## 6. Fill simulation

A backtest's realism lives here, and every assumption is explicit and recorded on the run.

```
FillModel
├── latency               intent → order → venue, distribution or fixed
├── spread                from observed bid/ask where available; else a declared assumption
├── slippage              model: mid | touch | fraction-of-spread | size-impact
├── partial_fills         enabled, with size-vs-liquidity rules
├── rejection             probability / conditions
├── fees                  brokerage, STT, exchange charges, GST, stamp duty
└── market_impact         optional, off by default
```

Where real bid/ask exists in the observation store, it is used. Where it does not — for
example over a historical-OI-only backfill period (`06-UPSTOX_INTEGRATION.md` §7) — the
model must use a declared assumption, and **the run is flagged as assumption-based** so
its results are never read as equivalent to a run over full-fidelity data.

`BacktestResult` prints the assumption set alongside every number. A backtest is a
statement about specific history under stated assumptions, never an expectation.

---

## 7. Backtest execution path

The backtest uses the **real** risk engine and OMS, not simplified stand-ins:

```
Strategy.on_state(ctx)
   → TradeIntent
   → RiskEngine.evaluate()        ← the production risk engine
   → RiskDecision (sequence)
   → OMS order state machine      ← the production state machine
   → PaperBrokerAdapter + FillModel
   → Fill → Position → Portfolio  ← the production portfolio logic
```

This matters: a strategy that passes backtest but would be rejected live by a position
limit is a strategy that does not work. Running the same risk engine catches it before
capital is involved, and it also exercises the risk engine over far more scenarios than
live trading would.

---

## 8. Replay driving the UI

The terminal can be driven by a replay run (`13-FRONTEND_IA.md`). The UI receives states,
metrics and signals through the same channel as live, tagged with the replay run id.

The critical constraint: **the UI must expose only what was available at the replay
timestamp.** Because the replay context already enforces this at the data layer — the UI
receives only available features and signals — the guarantee is inherited rather than
reimplemented in the frontend, where it would inevitably drift.

Replay-derived domain events are written to a namespace scoped by `run_id` and never into
the live outbox, so a replay can never trigger a real alert or a real order.

---

## 9. Limits, stated honestly

| Limit | Consequence |
|---|---|
| Replay fidelity is bounded by observation coverage | A period with WS gaps replays with those gaps — recorded, never interpolated |
| Historical intraday option-chain state is not obtainable retroactively | Replay before collection started is limited to what backfill provides (historical OI and OHLC), with quality marked accordingly |
| Backfilled periods cannot support quote-dependent features | Those features decline to compute; fill simulation is assumption-based and flagged |
| Fill simulation is a model | Reported with its assumptions; never presented as what would certainly have happened |
| Market impact of our own orders is not modelled by default | Acceptable at retail size; the option exists and its absence is stated |

Stating these is part of the deliverable. A replay engine that quietly interpolates over
gaps produces confident, wrong answers — the precise failure this architecture exists to
prevent.
