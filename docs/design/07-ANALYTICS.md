# OI Pulse v2 — Analytics Architecture

> **Deliverable J.** The feature registry, the purity contract, and the seven analytic
> domains. Per the brief's §12: every formula has a documented definition, units,
> conventions, assumptions and tests.

---

## 1. The purity contract

Every analytic is a pure function:

```python
def compute(state: MarketState, params: Params) -> MetricValue | list[MetricValue]
```

Constraints, enforced by an import-linter contract in CI:

- `analytics/*` may import `marketstate` and `core` only.
- **No database access.** No repository, no session, no query.
- **No HTTP.** No provider, no adapter.
- **No clock.** `available_at` is derived from the state and the feature definition, never
  from "now".
- **No I/O, no globals, no mutation of the input state.**

This is not stylistic. It is what makes analytics identically usable by live processing,
reconstruction, replay, research and backtesting — one implementation, no divergence
between "live" and "historical" code paths. It is also what makes them trivially
testable with constructed states and no fixtures.

Anything needing I/O (fetching a reference series, persisting a result) belongs in the
caller, not the analytic.

---

## 2. The feature registry

Every metric the system can produce is declared. Nothing computes off-registry.

```python
@feature(
    identifier="PUT_OI_MIGRATION",
    version=2,
    definition="Net displacement of put open interest between strikes over the window, "
    "weighted by OI magnitude and expressed in strike points.",
    inputs=["oi_by_strike(PE)"],
    formula="sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)",
    units="strike_points",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=["oi_coverage>=0.95", "quality!=UNRELIABLE"],
    scope=Scope.EXPIRY,
)
def put_oi_migration(state: MarketState, p: Params) -> MetricValue: ...
```

### Registry fields

| Field | Why it exists |
|---|---|
| `identifier` | stable name |
| `version` | **v1 and v2 coexist** and are independently referenceable |
| `definition` | prose — rendered in the UI on hover |
| `inputs` | which state surfaces are consumed; drives dependency ordering |
| `formula` | the mathematical statement |
| `units` | prevents the classic "is this lakhs or contracts?" error |
| `sampling_frequency` | a 15-minute feature does not recompute every second |
| `lookback` | window length |
| `availability_delay` | the gap between window completion and legitimate consumption |
| `normalization` | z-score, percentile, raw |
| `quality_requirements` | **the feature does not compute if unmet** |
| `scope` | underlying / expiry / strike / contract |
| `implementation_ref` | code location and version |

### Versioning discipline
Changing a formula's *meaning* requires a new version. Both remain registered and
computable. Every `MetricValue` records the version that produced it, so a research result
from March remains interpretable after a v3 lands in June.

The registry is generated from these decorators into machine-readable form, served by the
API and rendered in the terminal. A user hovering GEX sees the exact convention in force —
satisfying the brief's requirement that conventions are never implicit.

### Quality gating
`quality_requirements` are evaluated before computation. A feature requiring greeks does
**not** compute over a period where greeks are absent (for example a historical-OI-only
backfill window, `06-UPSTOX_INTEGRATION.md` §7). It returns *unavailable*, not a number
derived from missing inputs.

---

## 3. Availability computation

Availability is governed by **input readiness**, never by market timestamps. A feature must
not become consumable merely because its inputs' `observed_at` values are old — what
matters is when those inputs were actually *available* to us.

```
available_at = max(
    lookback_end,                 # the window is complete
    latest_input_available_at,    # every required input is actually available
    computed_at,                  # computation has finished
) + availability_delay
```

where `latest_input_available_at` resolves per input kind:

| Input kind | Availability |
|---|---|
| Raw observation | `ingested_at` — when we received it, **not** `observed_at` |
| Derived dependency | that dependency's own `available_at` (recursively) |

`availability_delay` is measured **from the point all three conditions are satisfied**, and
models propagation, not computation — computation time is already captured by `computed_at`.

### Invariants

```
available_at >= lookback_end
available_at >= every required input's availability
available_at >= computed_at
```

All three are tested per feature (`15-TESTING.md` §3, §2.2).

### Why `observed_at` is the wrong input

```
observation:  observed_at = 11:40:00   ingested_at = 11:44:00

observed_at-based:  available_at = 11:40:02   ← look-ahead. We did not hold
                                                the input until 11:44.
readiness-based:    available_at = 11:44:02   ← correct.
```

Using market time here would reintroduce look-ahead **inside the very mechanism built to
prevent it**, and it would do so silently — the value would look plausible.

See `05-DATA_LIFECYCLE_PIT.md` §5 for the window-completion rule and the worked 11:45:02
example.

---

## 4. The seven domains

### 4.1 Positioning
| Feature | Notes |
|---|---|
| `OI_CHANGE`, `OI_CHANGE_PCT` | vs. a reference resolved by point-in-time lookup, never a stored `prev_*` |
| `OI_CONCENTRATION` | Herfindahl index over strike OI — declared, not ad hoc |
| `OI_WALL_CALL`, `OI_WALL_PUT` | locally extreme OI by a declared definition (top-k and prominence threshold) |
| `OI_WALL_MIGRATION` | wall strike displacement over time |
| `PUT_OI_MIGRATION`, `CALL_OI_MIGRATION` | §5 |
| `BUILDUP_CLASSIFICATION` | the OI/price matrix — an **interpretation**, not a signal |
| `PRICE_OI_RELATIONSHIP` | correlation of ΔOI and Δprice over the window |
| `VOLUME_OI_RATIO` | turnover relative to open position |
| `PCR`, `PCR_OI_CHANGE` | `put/call`. Reported as a number with its convention documented. **No bullish/bearish label attached** |

### 4.2 Volatility
`ATM_IV` · `IV_CHANGE` · `IV_SKEW` (declared convention: 25-delta put IV minus 25-delta
call IV, and a strike-based variant, both registered separately) · `IV_TERM_STRUCTURE` ·
`REALIZED_VOL` (close-to-close and Parkinson, separate features) ·
`IMPLIED_REALIZED_SPREAD` · `IV_RANK`, `IV_PERCENTILE`.

`IV_RANK` and `IV_PERCENTILE` declare a minimum history requirement and return
**insufficient history** rather than a number computed from a short window. Fabricating a
percentile is worse than withholding it.

### 4.3 Greeks
`DELTA_EXPOSURE` · `GAMMA_EXPOSURE` · `THETA_EXPOSURE` · `VEGA_EXPOSURE`, per contract and
aggregated by strike, expiry and underlying.

Aggregation convention is explicit and versioned: exposure = greek × OI × lot size ×
contract multiplier, with the lot size resolved from the **instrument version valid at
`observed_at`** (`01-DOMAIN_MODEL.md` §3). A lot-size revision does not retroactively
rewrite historical exposure.

### 4.4 Gamma
`GEX_BY_STRIKE` · `GEX_TOTAL` · `GEX_BY_EXPIRY` · `GEX_CONCENTRATION` · `GEX_PROFILE` ·
`GAMMA_FLIP_LEVEL`.

The dealer-positioning sign convention is a **registered parameter of the feature**, not a
hidden assumption, and two variants are registered separately rather than one being
silently assumed.

> **Presented as market-structure information only.** The system does not assert
> "positive GEX = bullish" or any equivalent. Per the brief's §12 and §31, such claims
> require context the metric alone does not carry. The UI renders GEX with its convention
> and without a directional label.

### 4.5 Price
`RETURN` (multiple horizons) · `TREND` · `MOMENTUM` · `RANGE` · `ATR` · `VWAP` (where
volume data permits; otherwise unavailable rather than approximated) · `VOLUME_ZSCORE` ·
`REALIZED_MOVE`.

Deliberately **not** a general technical-analysis library. Each feature exists because
market state, signals, research or risk consume it. Indicators without a downstream
consumer are not added (brief §31).

### 4.6 Futures
`FUTURES_PRICE` · `FUTURES_OI` · `FUTURES_OI_CHANGE` · `BASIS` · `BASIS_CHANGE` ·
`ANNUALIZED_BASIS` · `SPOT_FUTURES_DIVERGENCE` · `FUTURES_OPTIONS_CONFIRMATION`.

Futures are integrated into market state and signal evidence — not a disconnected page.
Futures OI against futures price is the cleanest long/short buildup read available, and
it corroborates or contradicts options positioning.

### 4.7 Market structure
`SUPPORT_FROM_POSITIONING` · `RESISTANCE_FROM_POSITIONING` · `STRUCTURE_MIGRATION` ·
`MAX_PAIN` · `REGIME`.

Support and resistance here are **positioning-derived**, explicitly not price-technical
levels, and named to make that unambiguous.

`REGIME` is deterministic and rule-based, returning
`TRENDING_UP` · `TRENDING_DOWN` · `RANGE` · `HIGH_VOLATILITY` · `LOW_VOLATILITY` ·
`BREAKOUT` · `BREAKDOWN` · `TRANSITION` · `UNKNOWN` — with the evidence that produced it
and a strength value only where statistically justified. **No ML** (brief §31); `UNKNOWN`
is a legitimate and frequently correct answer.

---

## 5. OI migration — a tracked entity

Migration is not a per-tick number. It is a lifecycle-tracked analytical observation
(`01-DOMAIN_MODEL.md` §7), because the brief's §13 requires it to be researchable.

```
detect      candidate displacement exceeds threshold over the window
            → OIMigration(status=FORMING, first_observed_at)
confirm     persists across N consecutive windows, magnitude sustained
            → status=CONFIRMED
track       destination strike updated as it continues; last_observed_at advances
fade        displacement reverses or decays below threshold
            → status=FADED, duration finalized
```

Stored: origin strike, destination strike, expiry, option type, direction, magnitude,
duration, first/last observed, confidence, evidence.

The worked case from the brief — `25,000 PE → 25,200 PE → 25,300 PE` — is one migration
entity with an advancing destination and a growing duration, not three unrelated
observations. That is what makes "how do migrations of this shape resolve?" answerable.

---

## 6. Execution model

```
MarketStateBuilt(checkpoint)
   → resolve features due at this cadence (sampling_frequency)
   → topologically order by declared inputs
   → for each: check quality_requirements → compute (pure) → MetricValue
   → persist batch with observed_at / computed_at / available_at
   → emit MetricsComputed
```

Features declare their inputs, so ordering is derived rather than hand-maintained. A
feature whose dependency is unavailable is skipped with a recorded reason, not computed
from a default.

Expensive features (long lookbacks, cross-expiry surfaces) run on their own cadence rather
than on every checkpoint.

---

## 7. Testing

Every registered feature requires, before it may be registered (`15-TESTING.md`):

1. A **unit test** with a constructed `MarketState` and a hand-computed expected value.
2. A **units test** asserting the declared unit matches the output.
3. A **quality test** asserting it refuses to compute when `quality_requirements` are unmet.
4. An **availability test** asserting `available_at` respects the window-completion rule.
5. A **determinism test** — same inputs, same `inputs_digest`, same value.
6. **Property tests** where an invariant exists (PCR ≥ 0; exposure aggregation across
   strikes equals the total; migration magnitude is bounded by the strike range).

CI fails if a feature is registered without this set. The registry is the enforcement
point: a decorator without tests is a build error, not a review comment.
