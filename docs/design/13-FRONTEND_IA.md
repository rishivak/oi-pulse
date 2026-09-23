# OI Pulse v2 — Frontend Information Architecture

> **Deliverable S.** The terminal is the visible surface, not the product. Each screen
> answers one concrete decision or research question. Dense professional presentation over
> decoration (brief §26).

---

## 1. Principles

1. **One screen, one question.** A screen that answers three questions answers none well.
2. **No giant dashboard.** Explicitly rejected by the brief. The Command Center
   summarizes and routes; it does not contain everything.
3. **The frontend is a consumer of the domain model.** It does not compute analytics, and
   it does not define conventions. Where the legacy UI decided what PCR > 1.2 means — and
   two screens disagreed — v2 renders what the API returns.
4. **Quality is always visible.** Every screen shows data-quality status. A number without
   its reliability is misleading.
5. **Everything is traceable.** Any displayed value is clickable through to its feature
   definition, then its inputs, then raw observations.
6. **Time is a first-class control, on both axes.** Every screen works at `now` or at a
   historical `(market_time, knowledge_time)` pair, using the same components. Both are
   always visible; hindsight is never implicit.
7. **Information hierarchy over completeness.** Prioritize; do not enumerate.

---

## 2. Screen map

```
COMMAND CENTER ──── what is happening? where should I look?
   │
   ├── OPTION SURFACE      what does the chain look like?
   ├── POSITIONING         where is positioning, and where is it moving?
   ├── VOLATILITY          what is volatility doing?
   ├── MARKET STRUCTURE    what levels and regime?
   ├── SIGNALS             what is developing, and why?
   ├── ALERTS              what do I want to be told about?
   │
   ├── RESEARCH            does this relationship exist?
   ├── REPLAY              what did it look like as it happened?
   ├── BACKTEST            would this have worked?
   │
   ├── PAPER TRADING       what would this trade do?
   ├── PORTFOLIO           what do I hold, and how is it performing?
   ├── RISK                what are my limits and utilization?
   └── JOURNAL             what was I thinking?
```

Screens appear **only when their backend capability is real**. No "Coming soon" pages —
the legacy app shipped three stubs over working endpoints, which is worse than not
listing them.

---

## 3. Command Center

Answers: **what is happening, and where should I look?**

```
┌──────────────────────────────────────────────────────────────────────────┐
│ NIFTY  25,120.40  +0.42%        11:42:15 IST      DATA QUALITY: OK  ●    │
├───────────────────────────┬──────────────────────────────────────────────┤
│ REGIME                    │ POSITIONING                                  │
│ Trending Up               │ Put support      → 25,000  (↑ from 24,900)   │
│ evidence: 4 supporting    │ Call resistance  → 25,200  (unchanged)       │
│ 2 contradicting           │ PCR 1.14 · concentration 0.31                │
├───────────────────────────┼──────────────────────────────────────────────┤
│ VOLATILITY                │ GAMMA                                        │
│ ATM IV 14.8%  +1.2%       │ GEX concentrated near ATM                    │
│ Skew −2.1 (steepening)    │ convention: dealer-short-gamma  v2           │
├───────────────────────────┴──────────────────────────────────────────────┤
│ ACTIVE SIGNALS                                                           │
│ ● PUT_SUPPORT_MIGRATION      0.68  ACTIVE     11:45 → 12:15              │
│ ● CALL_OI_UNWINDING          0.41  FORMING                               │
├──────────────────────────────────────────────────────────────────────────┤
│ CONTRADICTIONS                                                           │
│ ⚠ IV expanding while support strengthens                                 │
│ ⚠ Call resistance unchanged despite upside migration                     │
└──────────────────────────────────────────────────────────────────────────┘
```

The **Contradictions** panel is deliberate. A terminal that only shows confirming
information trains overconfidence; surfacing tension between signals is the single most
valuable thing this screen does.

No directional recommendation appears anywhere on it.

---

## 4. Analysis screens

| Screen | Question | Content |
|---|---|---|
| **Option Surface** | What does the chain look like? | Dense CE ‖ strike ‖ PE grid: OI, ΔOI, LTP, bid/ask, IV, delta, gamma. Expiry selector **functional** across all expiries. Heatmap and surface views of the same resource. |
| **Positioning** | Where is positioning and where is it moving? | Walls with history, migration entities with origin → destination and duration, concentration, buildup classification with its evidence, top ΔOI. |
| **Volatility** | What is volatility doing? | ATM IV series, skew curve, term structure across expiries, realized vs implied, IV rank (or *insufficient history*). |
| **Market Structure** | What levels and what regime? | Positioning-derived support/resistance with migration history, max pain, regime with evidence and transitions, GEX profile with flip level. |
| **Signals** | What is developing and why? | List with strength, status, horizon. Detail shows supporting and contradicting evidence, each traceable to metric → state → observation, plus the invalidation condition. |
| **Alerts** | What do I want to be told about? | Rule builder over features/signals/state, dry-run against historical state, occurrence history, delivery status. |

### Shared controls

A single time control applies to every analysis screen, and it exposes **both axes**, not
one:

```
┌──────────────────────────────────────────────────────────┐
│  MARKET TIME     2026-03-03  11:42:00 IST                │
│  KNOWLEDGE TIME  2026-03-03  11:42:00 IST   ● in sync    │
└──────────────────────────────────────────────────────────┘

when they diverge, the indicator is unmistakable:

┌──────────────────────────────────────────────────────────┐
│  MARKET TIME     2026-03-03  11:42:00 IST                │
│  KNOWLEDGE TIME  2026-03-05  09:00:00 IST   ⚠ HINDSIGHT  │
│  Showing what we now know about that moment.             │
└──────────────────────────────────────────────────────────┘
```

A single `as_of` control is not enough for serious research. Without seeing both, a user
cannot tell *"what the system knew then"* from *"what we know now about then"* — and those
two readings support opposite conclusions. The distinction is available on **every**
analysis and research screen, not only in replay.

Default is in-sync (`knowledge_time = market_time`), the honest reading. Hindsight mode is
entered deliberately and is visually loud for as long as it is active.

The expiry selector is **real state**, propagated into every query and shared across
screens. The legacy inert `expiries[0]` selector is the specific defect being designed out.

---

## 5. Research screens

| Screen | Question | Content |
|---|---|---|
| **Research** | Does this relationship exist? | Study builder (condition, universe, period, horizons, controls, **sampling/overlap policy**). Results: **raw events and effective sample after clustering**, distributions, MFE/MAE, breakdowns by regime/expiry/time-of-day. Prominently displays exclusion counts and comparison count. Market time and knowledge time both shown. |
| **Replay** | What did it look like as it happened? | Full terminal driven by a replay clock. Transport controls, speed 1×–10×. **A persistent banner shows market time and knowledge horizon** (the same two-axis control as above), because a replay screen that looks like live is dangerous. |
| **Backtest** | Would this have worked? | Run config, equity curve, trade list, per-trade drill-through to the `MarketState` that produced the intent. The assumption set — latency, spread, slippage, fees — is displayed **beside** the headline number, never in a footnote. |

Replay reuses the analysis screens rather than duplicating them; only the data source
changes. The look-ahead guarantee is inherited from the replay context
(`10-REPLAY.md` §8), not reimplemented in the frontend where it would drift.

---

## 6. Trading screens

| Screen | Question | Content |
|---|---|---|
| **Paper Trading** | What would this trade do? | Intent builder (multi-leg), pre-trade risk preview showing every limit and its utilization, order blotter with full state including `UNKNOWN`, fills, positions. |
| **Portfolio** | What do I hold and how is it performing? | Positions with greeks, realized/unrealized P&L, exposure, margin, drawdown. Attribution by direction/vol/theta/gamma/execution/slippage/costs, sliceable by strategy, regime, expiry, time of day — **with the residual shown**. |
| **Risk** | What are my limits and utilization? | Every limit with current utilization, decision history with reasons, kill switch (prominent, confirmed). |
| **Journal** | What was I thinking? | Entries linked to signals, intents and trades. Supports revisiting whether the original hypothesis was correct. |

A `PAPER` / `LIVE` badge is persistent and unmissable wherever an account is in context.
Live trading UI does not render at all unless **all three** gates hold
(`17-SECURITY.md` §4): the `LIVE_TRADING_ENABLED` feature flag, the second confirmation
environment variable, and the `LIVE_TRADE` permission on the principal.

---

## 7. Cross-cutting UI

### Data quality
A persistent header indicator: `OK` / `DEGRADED` / `UNRELIABLE`, expanding to the issue
list. Individual stale values are marked inline. An `UNRELIABLE` state visibly desaturates
derived panels rather than rendering them as though they were trustworthy.

### Traceability
Every metric supports drill-through:

```
value → feature definition (name, version, formula, units, convention, availability)
      → inputs (the metric values consumed)
      → MarketState (observed_at, coherence mode, quality)
      → raw observations (with observed_at / ingested_at)
```

This is the brief's §30 requirement made concrete, and it is only possible because
provenance is persisted rather than logged.

### Time display
All times IST with an explicit label; UTC available on hover. Where `observed_at` and
`ingested_at` differ materially, both are shown — hiding ingestion lag hides the reason a
live decision differed from a backtest.

### Empty and insufficient states
Distinct and honest: *no data collected for this period* · *insufficient history for this
statistic* · *feature unavailable — quality requirements unmet* · *not yet available at
this timestamp*. Never a zero, never a blank chart.

---

## 8. Technical approach

Next.js App Router, TypeScript, TanStack Query, Tailwind. Charts via a single library
(`lightweight-charts` for time series where it fits; one additional library for surfaces
and heatmaps only if genuinely required — the brief forbids adding a second charting
library without a compelling reason).

- **Shared time/expiry/underlying state** in URL parameters, so any view is linkable and
  shareable — including a historical `as_of`. This falls directly out of the API being
  time-parameterized.
- **SSE** for live updates, with the market-hours-aware reconnect behaviour the legacy app
  got right being one of the few things worth carrying forward.
- **No business logic.** No conventions decided in the frontend. No thresholds hardcoded
  in components.
- Error boundaries and loading states per route — all absent in the legacy app.
