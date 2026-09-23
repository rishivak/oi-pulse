# OI Pulse — Domain Model (Phase 0 Audit)

Part 1 documents the domain as it exists. Part 2 proposes the target model from the brief,
marking each concept by whether the data to support it exists today.

---

# Part 1 — Current domain model

## Concepts that exist

### `Underlying`
Not a first-class object. It is a **string** (`"NIFTY"`) carried through every table and
signature, validated against `SUPPORTED_UNDERLYINGS` and mapped to a vendor key by
`UNDERLYING_INSTRUMENT_KEYS`. The `instruments` table is the closest thing to a real
entity but is referenced by no foreign key — it is seeded reference data only.

### `OptionExpiry`
A real entity: `(underlying, expiry_date)`, unique. Populated lazily.

### `OISnapshot` — the central aggregate
One row per `(user, underlying, expiry, interval_min, bucket_ts)`, holding
`spot_price`, `total_call_oi`, `total_put_oi`, `pcr`, and owning a collection of
`OIStrikeSnapshot` children. **Chain-shaped**: the unit is "the whole chain at a moment".

### `OIStrikeSnapshot`
One row per `(snapshot, strike)` holding **both** legs side by side —
`call_oi`/`put_oi`, `call_ltp`/`put_ltp`, `call_iv`/`put_iv`, and so on.

> This is the most consequential modelling decision in the codebase. Representing CE and PE
> as paired columns rather than as two rows of a single `OptionContract` makes strike-row
> reads convenient, but it means there is no `OptionContract` entity, no `instrument_key`
> per leg, and every per-leg operation duplicates code for call and put. `save_snapshot`
> has two near-identical 40-line blocks for exactly this reason. Per-leg analytics —
> greeks, GEX, skew — all fight this shape.

### `MarketDataEvent` / `OITimeBar` — the Phase-2 alternative
Instrument-shaped: one row **per leg**, keyed by `instrument_key`, with `option_type`
as data rather than as column prefixes. This is the correct shape for the target
architecture and already exists.

### `OISignal` / `OIInterpretation`
Two parallel enums for the same concept, differing only in casing and naming
(`long_buildup` vs `LONG_BUILDUP`), with `NEUTRAL` vs `NO_SIGNIFICANT_CHANGE`.
`OISignal` is per strike per leg and computed-then-discarded; `OIInterpretation` is
per bar and persisted. They should be one enum.

### `CollectorJob`
A subscription: "collect this underlying at this interval for this user." Always the
front expiry.

### `OutboxEvent`
A durable domain event with `event_type`, JSONB `payload`, `schema_version`, and a
delivery state machine (`pending → published | failed`). The `schema_version` field shows
the right instinct and is currently always `1`.

### `Alert`
Schema only: `alert_type` + JSONB `condition_json` + `is_enabled` + `last_triggered_at`.
No evaluator, no delivery, no UI.

### `User` / `UpstoxAccount` / `UserPreference`
Conventional. Session is a signed `user:{id}` cookie; there is no session entity.

## Concepts that do NOT exist

`OptionContract` · `FutureContract` · `Quote` · `MarketDepth` · `OHLC` ·
`MarketState` · `MarketRegime` · `Signal` · `Evidence` · `TradeIntent` · `Order` ·
`Fill` · `Position` · `Portfolio` · `RiskLimit` · `BacktestRun` · `ResearchExperiment` ·
`JournalEntry` · `DataQualityCheck`.

## Ubiquitous-language problems

| Term | Problem |
|---|---|
| "signal" | `OISignal` is a per-strike *classification*, not a tradeable signal. The target model uses "Signal" for something quite different. Rename the existing one to `BuildupClassification` before introducing the new `Signal`. |
| "interval" vs "timeframe" | `interval_min` (int, collection cadence) and `timeframe` (string enum, aggregation window) are different concepts, partially mapped by `_interval_to_timeframe`, and easily confused. |
| "bucket_ts" vs "bucket_start_ts" | Same concept, two names, **and two incompatible flooring rules** (IST-floored vs UTC-floored — see DATA_FLOW.md §2). |
| "PCR" | Genuinely ambiguous in the codebase: `_safe_pcr(call, put)` and `safe_pcr(put, call)` take arguments in **opposite orders**, and the frontend disagrees with itself on what a high PCR means. Must be defined once, explicitly. |
| "snapshot" | Means both the Phase-1 row and, informally, any point-in-time capture. |

---

# Part 2 — Target domain model

Feasibility marks: **✅ data exists** · **🟡 needs a migration, vendor data already arrives** ·
**🔶 needs new ingestion** · **🔴 needs new subsystem**

## Market data layer

### `Underlying` ✅
Promote from string to entity, reusing `instruments`. Add `lot_size` (re-verify — current
seed values are stale), `tick_size`, `strike_step`, `exchange`, session calendar reference.

### `OptionContract` 🟡
The missing entity. `instrument_key` · `underlying` · `expiry` · `strike` · `option_type` ·
`lot_size`. `market_data_events` already carries these fields per leg — this is a
normalization of existing data, not new ingestion.

### `FutureContract` 🔶
`instrument_key` · `underlying` · `expiry` · `lot_size`. Needs a new ingestion path.

### `OptionSnapshot` (per leg) 🟡
The canonical per-leg record the brief asks for:

| Field | Status |
|---|---|
| `timestamp`, `underlying`, `instrument`, `expiry`, `strike`, `optionType` | ✅ stored |
| `ltp`, `volume`, `oi` | ✅ stored |
| `previousOi` | 🟡 vendor sends `prev_oi`; we compute our own from the prior snapshot and discard the vendor's |
| `iv` | ✅ stored |
| `delta`, `gamma`, `theta`, `vega` | 🟡 **parsed in `UpstoxOptionGreeks` and dropped** |
| `bid`, `ask` | 🟡 **parsed in `UpstoxMarketData` and dropped** |
| `bidQuantity`, `askQuantity` | 🔶 not in the current REST response model; needs the WS feed or a quote endpoint |

Persisting the 🟡 rows is a migration plus ~20 lines in `save_snapshot` — **no new vendor
call, no extra rate-limit cost**. This is the highest value-per-unit-effort change in the
entire plan and gates GEX, skew, and exposure analytics.

### `Quote` / `MarketDepth` 🔶 · `OHLC` 🔶
Need the WebSocket feed and/or the historical-candle endpoint.

## Market state

### `MarketState` 🟡 — the keystone
Materialized per `(underlying, timestamp)`, reproducible from stored history.
Field-by-field feasibility:

| Field | Status |
|---|---|
| `spot`, `spotChange` | ✅ |
| `vwap` | 🔶 needs volume-weighted price data |
| `atmStrike` | ✅ already computed at read time in `/oi/strikes` |
| `pcr`, `callOi`, `putOi` | ✅ |
| `callWall`, `putWall` | ✅ pure function over existing strike rows |
| `support`, `resistance` | ✅ derivable from walls |
| `oiMigration` | ✅ derivable once walls are stored per timestamp |
| `atmIv`, `ivChange` | ✅ IV is already stored |
| `ivSkew` | ✅ call vs put IV per strike is already stored |
| `gex`, `deltaExposure`, `gammaExposure` | 🟡 blocked only on persisting greeks |
| `volume` | ✅ |
| `volumeZScore` | ✅ once enough history accumulates |
| `trend`, `volatilityRegime`, `marketRegime` | 🔴 needs the regime engine |

**Most of `MarketState` is reachable with one migration.** That is the finding that should
drive sequencing.

### `MarketRegime` 🔴
`regime` · `timestamp` · `evidence` · `strength`. Deterministic rules first, per the brief's
§15 — no ML.

## Analytics

`PositioningMetrics` ✅ (walls, concentration, migration, velocity — all pure functions over
stored data) · `VolatilityMetrics` ✅ for skew/ATM IV, ⏳ for rank/percentile pending history
depth · `GreeksExposure` 🟡 · `PriceContext` 🔶.

## Signals

### `Signal` 🔴 and `Evidence` 🔴
`id` · `timestamp` · `underlying` · `signalType` · `strength` · `expiry` · `strike` ·
`evidence[]` · `contradictingEvidence[]` · `marketRegime`.

The brief's explainability requirement (§30) means `Evidence` must be a **first-class
persisted record** — `{ observation, value, direction, supports }` — not a rendered string.
The audit trail Signal → Analytics → Raw observation → Snapshot must be walkable in the
data, which requires every evidence item to reference the snapshot row it came from.

## Research, trading, risk — all 🔴

`ResearchExperiment` · `StrategyDefinition` · `SignalEvaluation` · `BacktestRun` ·
`BacktestResult` · `ReplaySession` · `TradeIntent` · `PaperOrder` · `Fill` · `Position` ·
`Portfolio` · `RiskLimit` · `RiskCheck` · `Order` + state machine · `JournalEntry`.

Two design constraints from the brief worth recording now:

1. **`TradeIntent` is the seam.** `PaperBrokerAdapter` and `UpstoxBrokerAdapter` must consume
   the identical type, and nothing may reach a broker without passing the risk engine.
2. **Replay must be point-in-time honest.** Every query in the replay path needs an
   explicit `as_of` bound. `/oi/history-bars` already accepts `for_date`, which is the
   right hook, but honesty has to be enforced at the query layer rather than trusted to
   callers — otherwise look-ahead bias leaks in silently.

## Data quality 🔴

`DataQualityCheck` · `CollectionGap`. Nothing today detects a missing bucket, a stale
price, a duplicated `market_data_events` row, or a holiday mis-collection.
