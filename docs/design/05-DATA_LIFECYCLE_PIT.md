# OI Pulse v2 — Data Lifecycle and Bitemporal Point-in-Time Correctness

> **Deliverables G (data lifecycle) and H (point-in-time correctness model).**
> This document is the foundation of the system's credibility. If it is wrong, every
> research result is wrong.

---

## 1. Why one timestamp is not enough

The naive statement — *"MarketState(T) may only use information observed at or before T"* —
is necessary but insufficient. It permits this failure:

```
An observation is true at 11:40 but does not reach us until 11:44.
A backtest asking "what would the strategy do at 11:42?" sees it.
The live system at 11:42 could not have.
The backtest is optimistic and the error is invisible.
```

Correct point-in-time modelling requires separating **when a fact was true** from **when
we knew it** — and, for derived values, **when a consumer could act on it**.

---

## 2. The four time dimensions

| Field | Name | Meaning | Set by |
|---|---|---|---|
| `observed_at` | valid / market time | when the fact was true per the venue | provider timestamp |
| `ingested_at` | knowledge / system time | when OI Pulse first received and persisted it | our clock at write |
| `computed_at` | computation time | when a derived value was calculated | our clock at compute |
| `available_at` | availability time | when the derived value became consumable by the strategy layer | feature definition |

### `available_at` is not `computed_at`

This is correction 2's core point and it is easy to get wrong. A feature may be *computed*
the instant its last input arrives, yet not legitimately *available* until later, because:

- the feature's window must be complete (§5);
- a declared `availability_delay` models real computation and propagation latency;
- quality requirements may demand confirmation.

```
observed_at  = 11:40:00     the fact was true
ingested_at  = 11:40:01     we received it
computed_at  = 11:40:02     we calculated the feature
available_at = 11:40:03     the strategy layer could use it

A strategy decision at 11:40:02 must NOT consume that feature.
```

This fixture is a required test (`15-TESTING.md` §3).

### `decision_time` is not a fifth timestamp

A fourth *concept* appears alongside the four stored fields, and must not be confused with
them:

| Concept | Kind |
|---|---|
| `market_time` | when the fact was true — **stored** as `observed_at` |
| `knowledge_time` | what information had reached OI Pulse — **stored** as `ingested_at` |
| `available_at` | when a derived value became legitimately consumable — **stored** |
| `decision_time` | when a strategy acts — **a consumer/action parameter, NOT a stored field** |

`decision_time` is what a caller supplies when asking *"what could I have used at this
moment?"*. It is an argument to `tradable_information_at()`, a property of a backtest step
or a live evaluation — never a column, never part of any entity's identity.

Keeping it out of storage is deliberate. Admitting it as a fifth field would invite a
sixth and a seventh, and the four-dimensional model would quietly become unmanageable.
Four stored dimensions, one action parameter.

---

## 3. The three query modes

Exposed as distinct repository methods. **There is no unbounded query for callers to reach
for** — a repository call without a mode is a compile-time/lint error.

| Mode | Predicate | Answers | Default for |
|---|---|---|---|
| `market_truth_at(valid_time=T, knowledge_as_of=K)` | `observed_at <= T AND ingested_at <= K` | What was true in the market at T, per everything we knew by K? | market-truth analysis — **explicit opt-in only** |
| `knowledge_at(T)` | `observed_at <= T AND ingested_at <= T` | What did OI Pulse know at T? | research, replay, state reconstruction |
| `tradable_information_at(T)` | above **and** `available_at <= T` | What could a strategy legitimately use at T? | strategy execution, backtesting |

### Why `market_truth_at` takes two arguments

The earlier name `market_truth_at(valid_time=T, knowledge_as_of=K)` was ambiguous once corrections exist. Given an
observation at 11:40 and a correction at 12:00, "market truth at 11:40" has two defensible
readings: the value believed at the time, or the corrected value. Making the knowledge
horizon an **explicit second argument** removes the ambiguity — the caller must say which
they mean:

```
market_truth_at(valid_time=11:40, knowledge_as_of=11:45)   → original value
market_truth_at(valid_time=11:40, knowledge_as_of=13:00)   → corrected value
```

`knowledge_at(T)` is the special case where both are `T`. Defaulting `knowledge_as_of` to
"now" is deliberately **not** offered, because that is precisely the silent look-ahead the
model exists to prevent.

Research and backtesting **default to `knowledge_at` / `tradable_information_at`**.
Market-truth mode requires an explicit flag, recorded on the study or backtest run so a
reader always knows which semantics produced a result.

### Worked example

| Query at 11:42 | Sees the 11:40/11:44 observation? |
|---|---|
| `market_truth_at(valid_time=11:42, knowledge_as_of=now)` | **Yes** — it was true at 11:40 |
| `knowledge_at(11:42)` | **No** — we did not have it until 11:44 |
| `tradable_information_at(11:42)` | **No** |

Both answers are correct for their question. Confusing them is what produces a backtest
that cannot be reproduced live.

---

## 4. Lifecycle of a fact

```
 VENUE                INGESTION            STORAGE           DERIVATION        CONSUMPTION
   │                      │                   │                   │                 │
   │ fact true            │                   │                   │                 │
   │ observed_at ─────────►                   │                   │                 │
   │                      │ normalize         │                   │                 │
   │                      │ resolve identity  │                   │                 │
   │                      │ ingested_at ──────►                   │                 │
   │                      │                   │ append-only       │                 │
   │                      │                   │ immutable         │                 │
   │                      │                   │ partitioned       │                 │
   │                      │                   ├──► quality check  │                 │
   │                      │                   │                   │                 │
   │                      │                   ├──► state assembly │                 │
   │                      │                   │    (coherent)     │                 │
   │                      │                   │                   │ computed_at     │
   │                      │                   │                   │ + availability  │
   │                      │                   │                   │   delay         │
   │                      │                   │                   │ available_at ───►
   │                      │                   │                   │                 │ strategy
   │                      │                   │                   │                 │ research
   │                      │                   │                   │                 │ UI
```

Nothing is mutated at any stage. Every arrow forward creates a new record referencing its
inputs.

---

## 5. Feature windows and availability

Every feature declares five fields (`09-RESEARCH.md` §2):

```
lookback_start · lookback_end · availability_time · forward_start · forward_end
```

**The window-completion rule:**

> A feature over a window ending at `lookback_end` has
> `available_at = max(lookback_end, latest_input_available_at, computed_at)
> + availability_delay`
>
> where a raw input's availability is its `ingested_at` and a derived dependency's is its
> own `available_at`. Equivalently: `available_at >= lookback_end`,
> `>= every required input's availability`, and `>= computed_at`.

This holds regardless of when the component data was *true* — a feature does not become
consumable because its inputs' market timestamps are old, only once those inputs actually
reached us and the window closed.

Worked example:

```
Feature:    15-minute OI migration
Window:     11:30:00 → 11:45:00
Available:  11:45:02

It cannot be consumed by a strategy before 11:45:02 — not at 11:42 merely
because some component observations already existed then.
```

The research and backtest engines **reject** a feature request whose `availability_time`
exceeds the decision time; they do not warn and continue. Enforcement lives in the engine,
not in researcher discipline (`15-TESTING.md` §3).

---

## 6. The four awkward cases

### Late arrivals
An observation with `ingested_at >> observed_at`. Stored normally with both timestamps
true. It is automatically visible to `market_truth_at` at its `observed_at` and invisible to
`knowledge_as_of` before its `ingested_at`. **No special-case code** — the bitemporal model
handles it by construction.

Latency is monitored as `ingested_at - observed_at`; a p99 breach raises an operational
alert, and an extreme value raises a `CLOCK_SKEW` quality issue rather than being trusted.

### Corrections
A venue revising a value produces a **new row** with `supersedes_observation_id` pointing
at the prior one. The original is never mutated or deleted.

Consequences, all desirable:
- `knowledge_at(T)` before the correction returns the **original** value — which is
  genuinely what we believed then, and therefore what a strategy would have acted on.
- `knowledge_at(T)` after the correction returns the corrected value.
- `market_truth_at(valid_time=T, knowledge_as_of=K)` returns the latest non-superseded value for that `observed_at` — the
  best current understanding of market truth.

A backtest is therefore not silently improved by corrections that arrived after the
decision point. This is one of the most common sources of inflated backtest results in
systems that mutate in place.

### Backfills
Backfilled data (notably historical OI — `06-UPSTOX_INTEGRATION.md` §7) carries
`observed_at` = the historical time and `ingested_at` = when the backfill ran.

**Historical OI is date-granular, not an instant.** The Upstox OI endpoint returns OI
across strikes for an underlying, expiry and *date*. Representing that as an observation
at some precise intraday timestamp would be a lie that someone reads as truth six months
later. It is therefore stored with an explicit granularity:

```
observation_kind = HISTORICAL_DAILY_OI
observation_date = 2026-05-07
valid_interval   = [session_open(2026-05-07), session_close(2026-05-07)]
```

Consequences that follow automatically:
- It can never satisfy a query for an instant inside the day at tick resolution — a
  `knowledge_at(11:23:17)` query does not return a daily aggregate as though it were live.
- A `MarketState` built over a backfilled-only period reports low `coverage_ratio` and
  `DEGRADED`/`UNRELIABLE` quality, because quotes, greeks and depth genuinely do not exist.
- Features declaring `quality_requirements` that include quotes or greeks decline to
  compute over such a period (`07-ANALYTICS.md` §2).

It is therefore **invisible to `knowledge_at(T)` for any T before the backfill**. A
strategy backtested over a period before the backfill will not see it, which is correct:
the live system at that time did not have it. Research explicitly wanting backfilled data
must use `market_truth_at` and record that choice.

Backfilled rows additionally carry a distinct `source` so they are always separable.

### Delayed or throttled feeds
Where a provider delivers on a delay or with throttling, the delay is a property of the
feed and is recorded in the feed configuration. `ingested_at` captures it automatically.
Features sourced from a delayed feed inherit an `availability_delay` at least as large as
the feed delay, so a strategy cannot consume delayed data as though it were real-time.

---

## 7. Replay semantics

Replay pins **both** a market clock and a knowledge horizon:

```
ReplayContext(
    market_time      T,   advancing
    knowledge_horizon K,  advancing in lockstep by default
)
```

- **Default (`K = T`)** — reproduces what the system could actually have known. This is the
  honest mode and the one backtests use.
- **`K` pinned to a later fixed time** — "what would we conclude about that morning using
  everything we know now?" A legitimate market-truth analysis, explicitly flagged.
- **`K` earlier than `T`** — disallowed; it is incoherent.

During replay, `Clock` is a `ReplayClock`. No module reads the wall clock, so no code can
accidentally observe real time. Detail in `10-REPLAY.md`.

---

## 8. Provenance

Every derived record carries enough to reproduce it:

| Field | Purpose |
|---|---|
| `feature_id`, `feature_version` | which definition |
| `implementation_version` | which code produced it |
| `inputs_digest` | hash of input values — recomputation must match |
| `state_checkpoint_ref` / `builder_version` | which state, assembled how |
| `observation_refs` | which raw rows |
| `knowledge_horizon` | what was knowable |
| `quality_status` | input reliability |

Recomputing a metric for the same `observed_at` and knowledge horizon with the same
versions must produce the same `inputs_digest` and the same value. A mismatch is a
determinism bug and fails CI (`15-TESTING.md` §4).

Decision artifacts embed this descriptor **inline** in addition to referencing it, so an
artifact remains self-describing even if recomputable rows are later pruned
(`02-DATA_MODEL.md` §8).

---

## 9. Retention and the audit chain

Persistence tiers are defined in `00-OVERVIEW.md` §7. The interaction with point-in-time
correctness:

- **Observations are never pruned** (except `obs_depth` at 90 days). They are the only
  irreplaceable asset — an option chain cannot be re-fetched for a past instant.
- **Checkpoints and metrics may be pruned** because they are reconstructable — *unless*
  retention-locked by a decision artifact.
- **Decision artifacts are never pruned.**

Therefore: pruning can reduce query convenience but can never change an answer, and can
never break an audit chain.

---

## 10. Failure modes this model prevents

| Failure | Prevented by |
|---|---|
| Backtest sees data that arrived late | `knowledge_as_of` default |
| Backtest sees a feature before its window completed | `available_at` + window-completion rule |
| Corrections silently improve historical results | append-only corrections with `supersedes` |
| Backfill contaminates an earlier backtest | backfill `ingested_at` at load time |
| Live/backtest divergence from separate code paths | one `build_state`, one feature implementation |
| Hidden wall-clock dependence | injected `Clock`, lint-enforced |
| Analytics silently built on stale or partial data | staleness budget + quality propagation |
| Audit chain broken by retention | retention locks + embedded provenance |
| A formula change silently reinterpreting old results | `feature_version` on every value |
| Two legs presented as simultaneous when they were not | coherence mode on every state |
