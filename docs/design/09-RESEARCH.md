# OI Pulse v2 — Research Architecture

> **Deliverable L.** Research is a first-class domain, not a reporting afterthought.
> Its defining constraint is that results must be **point-in-time correct** — a research
> result that cannot be reproduced live is worse than no result.

---

## 1. What research answers

| Question | Mechanism |
|---|---|
| When condition X occurs, what happens next? | Event study |
| Does signal type S have forward edge? | Signal evaluation |
| Which evidence items actually predict? | Evidence attribution |
| Does this strategy work over history? | Backtest (`10-REPLAY.md`) |
| Under which regimes does it work? | Conditional breakdown |
| Was the original hypothesis actually correct? | Study re-run against later data |

The last one is the point of the whole layer. Per the brief's §34, a researcher must be
able to revisit a hypothesis and get a deterministic answer.

---

## 2. The research leakage model

Every feature declares five window fields. This is the heart of the document.

| Field | Meaning |
|---|---|
| `lookback_start` | earliest observation the feature may consume |
| `lookback_end` | latest observation the feature may consume |
| `availability_time` | when the computed value may be consumed by a strategy |
| `forward_start` | earliest point of the outcome window |
| `forward_end` | latest point of the outcome window |

### The window-completion rule

> `available_at = max(lookback_end, latest_input_available_at, computed_at)
> + availability_delay`

A raw input's availability is its `ingested_at`; a derived dependency's is its own
`available_at`. Equivalently `available_at` is `>= lookback_end`, `>= every required
input's availability`, and `>= computed_at`.

A feature is not available merely because some component data already exists — nor because
its inputs' market timestamps are old. A late-arriving input (observed 11:40, ingested
11:44) pushes availability to 11:44 + delay, not 11:40 + delay.

```
Feature:    15-minute OI migration
Window:     11:30:00 → 11:45:00      (lookback_start → lookback_end)
Available:  11:45:02                 (+ 2s availability_delay)

It cannot be consumed by a strategy before 11:45:02 — not at 11:42,
even though observations from 11:30–11:42 already existed then.
```

### Enforcement, not convention

The research and backtest engines **reject** an invalid feature request rather than
warning:

```python
class FeatureAccessError(Exception): ...

def get_feature(self, feature_id, version, at: Timestamp) -> MetricValue:
    mv = self._lookup(feature_id, version, at)
    if mv.available_at > at:
        raise FeatureAccessError(
            f"{feature_id}@{version} available at {mv.available_at}, "
            f"requested at {at} — look-ahead refused"
        )
    return mv
```

The strategy API exposes **only** features passing `tradable_information_at(decision_time)`.
A strategy cannot request an unavailable feature and ignore a warning, because there is no
path by which it receives one. Researcher discipline is not a control (brief §10).

### Forward windows
`forward_start` and `forward_end` bound the **outcome** measurement and are deliberately
separated from the feature window, so the two cannot be confused. Outcome data is by
definition future-looking relative to the decision point — legitimate for measuring what
happened, never available to the decision itself. The engine keeps them in separate
namespaces: outcome data is inaccessible from any strategy or rule context.

---

## 3. Event studies

```
EventStudy
├── question               prose statement of the hypothesis
├── condition              an evaluable predicate over MarketState + features
├── universe               underlyings, expiries, strike selection
├── period                 study start and end
├── query_mode             knowledge_at (default) | market_truth_at (flagged)
├── horizons[]             5m, 15m, 30m, 60m, EOD
├── controls               regime, time-of-day, expiry-proximity buckets
├── sampling               event/overlap policy (below)
├── feature_versions{}     exact versions pinned
└── minimum_sample         below which no result is reported
```

Worked example: *"When put OI rises more than 15% near ATM, what happens over the next
5/15/30/60 minutes?"*

### Computed statistics

| Group | Measures |
|---|---|
| Sample | count, coverage, excluded-for-quality count |
| Central | mean return, median return |
| Distribution | std dev, skew, quantiles, full histogram |
| Outcome | win rate, profit factor |
| Excursion | MFE, MAE, time-to-MFE, time-to-invalidation |
| Risk | max drawdown within horizon, realized volatility |
| Breakdowns | by regime, by expiry proximity, by time of day, by quality status |

### Event sampling and overlap

High-frequency observations plus multi-horizon forward windows produce **heavily
overlapping events**, and a raw event count then badly overstates the evidence:

```
11:00 event ─┐
11:05 event ─┤  all four share most of a 30-minute forward window.
11:10 event ─┤  Raw n = 4.  Independent information ≈ 1.
11:15 event ─┘
```

Treating n=4 as four independent observations inflates significance — the classic way a
spurious result survives a sample-size check. The study therefore declares its sampling
policy:

```
EventStudy.sampling
├── event_sampling_policy     ALL | FIRST_PER_CLUSTER | DECORRELATED
├── overlap_policy            ALLOW | DROP_OVERLAPPING | CLUSTER
├── minimum_event_separation  e.g. 30m — at least the longest forward horizon
└── cluster_method            TIME_PROXIMITY | SIGNAL_INSTANCE | REGIME_BLOCK
```

| Policy | Behaviour |
|---|---|
| `ALL` | every qualifying event; overlap permitted — **effective sample still reported** |
| `FIRST_PER_CLUSTER` | one event per cluster, the earliest — the conservative default |
| `DECORRELATED` | enforce `minimum_event_separation ≥ longest forward horizon` |

**Every result reports both counts**, always:

```
raw events            412
clusters              63
effective sample      63        ← significance is judged on this
mean overlap          4.8 events/cluster
```

`minimum_event_separation` defaults to the longest forward horizon in the study, so
forward windows cannot overlap unless the researcher opts in.

Phase 1 does not require sophisticated overlap-corrected statistics (block bootstrap,
Newey–West). It **does** require that the engine know events overlap and say so — reporting
`n = 412` when there are 63 independent clusters is the failure being prevented.

### Honesty requirements
- **Minimum sample enforced on the effective (post-clustering) count**, not the raw count.
  Below it the study reports *insufficient sample*, not a mean of nine clusters.
- **Quality-filtered by default.** States with `UNRELIABLE` quality are excluded and the
  exclusion count is reported, so a result cannot quietly rest on bad data.
- **No parameter optimization in the first pass** (brief §21 of the earlier brief, §16
  here). The first question is *does the relationship exist at all?* Optimizing thresholds
  before establishing existence is how spurious results are manufactured.
- **Multiple-comparison awareness.** Running many horizons and breakdowns inflates the
  chance of a spurious hit; the study result records how many comparisons were made so a
  reader can discount accordingly.

---

## 4. Datasets

A `Dataset` is a materialized, point-in-time-correct extract:

```
Dataset
├── id, name, created_at
├── query_mode, knowledge_horizon, build_context_id
├── period, universe
├── feature_versions{}
├── builder_version          which MarketState builder
├── row_count
├── content_hash             ← reproducibility check
└── quality_summary
```

`content_hash` lets a study be re-run later and the dataset verified identical. If a
dataset rebuild produces a different hash with the same parameters, something
non-deterministic has changed and the study result is suspect — CI treats this as a
failure (`15-TESTING.md`).

Datasets are **retention-locked** (`02-DATA_MODEL.md` §8): the observations, checkpoints
and metrics they reference cannot be pruned while a study result depends on them.

---

## 5. Signal evaluation

For every signal type, measure forward behaviour after each lifecycle transition:

| Measured at | Question |
|---|---|
| `FORMING` | does early entry pay, or is it noise? |
| `ACTIVE` | the base case |
| `CONFIRMED` | does waiting for confirmation beat the missed move? |
| `INVALIDATED` | how fast does invalidation arrive, and how costly is it? |

Reported per type: sample count, forward-return distribution by horizon, MFE/MAE,
time-to-MFE, time-to-invalidation, hit rate, and breakdowns by regime, expiry proximity
and time of day.

### Evidence attribution
Because evidence is stored individually with weights, research can ask which items carry
the predictive load. Signals are grouped by evidence presence/absence and forward returns
compared. An evidence item that does not separate outcomes is decorative and is a
candidate for removal or reweighting.

This is the mechanism by which the system improves rather than accumulating rules.

---

## 6. Bias controls

| Bias | Control |
|---|---|
| **Look-ahead** | `knowledge_at` default; `available_at` enforced in the engine; deliberate-violation tests must fail |
| **Survivorship** | universe resolved *as-of* each point in time from instrument versions, so expired contracts are present as they were |
| **Corrections leakage** | append-only corrections; a backtest sees the value believed then, not the corrected one |
| **Backfill leakage** | backfilled rows carry `ingested_at` at load time and are invisible to earlier knowledge queries |
| **Selection** | condition evaluated across the whole universe and period; no post-hoc filtering |
| **Multiple comparisons** | comparison count recorded on every result |
| **Overfitting** | no parameter optimization in the first pass; out-of-sample split required before any tuning |
| **Quality laundering** | degraded/unreliable states excluded by default, exclusions reported |
| **Silent formula drift** | feature versions pinned per study |

---

## 7. Reproducibility

A `StudyResult` records everything needed to reproduce it: dataset content hash, feature
versions, builder version, rule versions, query mode, knowledge horizon, code version,
random seed, parameters, and the resulting statistics.

Re-running with identical inputs must produce identical output. This is a CI test, not an
aspiration — and it is only achievable because analytics are pure, the clock is injected,
and state assembly has one implementation.

---

## 8. What research does not do

- **No ML before deterministic analytics are validated** (brief §31). The first job is
  establishing whether documented, explainable relationships exist.
- **No automatic strategy generation.** Research measures hypotheses; it does not invent
  them.
- **No results below minimum sample.**
- **No presentation of a backtest as an expectation.** A `BacktestResult` reports what
  happened over specific history under stated assumptions, with those assumptions —
  latency, spread, slippage, fees — printed alongside the number, never buried.
