# OI Pulse v2 — API Specification

> **Deliverable R.** APIs are shaped around **domain concepts**, not UI screens
> (brief §25). Every read endpoint supports both current and historical state.

---

## 1. Principles

1. **Domain-shaped, not screen-shaped.** No `/dashboard-data`. The legacy
   `/oi/trending`, `/oi/heatmap` endpoints are named for the widgets that consume them —
   the anti-pattern being corrected.
2. **Routes contain no business logic.** They parse, authorize, delegate, serialize.
3. **Every read is time-parameterized.** Omitting time means "now"; supplying it means
   point-in-time, using the same code path.
4. **Time semantics are explicit.** Two named axes — `market_time` and `knowledge_time` —
   on every historical read, never a single `as_of` plus a mode flag.
5. **Provenance is returned, not hidden.** Feature versions, quality and availability
   accompany the numbers.
6. **Errors are typed**, with a stable machine-readable `code`.

---

## 2. Time parameters

The API surfaces the two time **axes** directly rather than an `as_of` plus a mode flag.
`as_of` + `time_mode=market` + `knowledge_as_of` was ambiguous — it left the reader to work
out which of two timestamps governed the answer.

| Parameter | Meaning |
|---|---|
| `market_time` | valid time — what instant in the market. Absent → latest |
| `knowledge_time` | knowledge horizon — what we knew by then. Absent → equals `market_time` |
| `underlying` | symbol |
| `expiry` | date, or `front`, `next`, `monthly`, `all` — resolved relative to `market_time` |
| `page` / `cursor`, `limit` | cursor pagination on collections |

```
GET /market/state?underlying=NIFTY
    &market_time=2026-03-03T11:42:00Z
    &knowledge_time=2026-03-03T11:43:00Z
```

Mapping to the repository modes (`05-DATA_LIFECYCLE_PIT.md` §3):

| Request | Mode |
|---|---|
| `knowledge_time` omitted or equal to `market_time` | `knowledge_at(T)` |
| `knowledge_time` > `market_time` | `market_truth_at(valid_time, knowledge_as_of)` — **market-truth analysis** |
| `knowledge_time` < `market_time` | rejected — incoherent |

Because a later `knowledge_time` must be supplied **explicitly**, market-truth semantics
can never be entered by accident, and the response echoes both values back.

### Availability applies to derived data only

`available_at` is a property of derived values; raw observations do not have one. So:

| Resource | Time semantics |
|---|---|
| `/market/observations`, `/market/state`, `/option-surface` | market + knowledge time only |
| `/features`, `/signals`, and strategy/backtest context | market + knowledge time **plus** availability — results are filtered to `available_at <= decision_time` |

`decision_time` is a **request parameter on availability-filtered endpoints**, not a stored
field (`05` §2). It defaults to `knowledge_time`.

Offering an availability filter on raw observations would be meaningless, and offering it
as a universal mode would imply raw data has an availability it does not have.

### Response envelope
```json
{
  "data": { },
  "meta": {
    "market_time": "2026-03-03T11:42:15Z",
    "knowledge_time": "2026-03-03T11:42:15Z",
    "decision_time": "2026-03-03T11:42:15Z",
    "semantics": "knowledge_at",
    "quality": { "status": "OK", "coverage_ratio": 0.99, "issues": [] },
    "coherence_mode": "SNAPSHOT_ANCHORED",
    "provenance": {
      "build_context_id": "bc_7f3a…",
      "feature_versions": { "GEX_BY_STRIKE": 2 }
    }
  }
}
```

`semantics` states plainly which mode served the request: `knowledge_at`,
`market_truth_at` or `tradable_information_at`.

Quality and provenance travel with **every** response. A consumer cannot render a number
without having been told how reliable it is.

---

## 3. Endpoint map

### `/instruments`
| Method | Path | Purpose |
|---|---|---|
| GET | `/instruments` | search/filter; resolves versions at `market_time` |
| GET | `/instruments/{id}` | detail with the version valid at `market_time` |
| GET | `/instruments/{id}/versions` | full metadata history |
| GET | `/underlyings` | tracked underlyings |
| GET | `/underlyings/{symbol}/expiries` | expiries at `market_time`, with type and days-to-expiry |
| GET | `/universes` · POST · PATCH | subscription management (admin) |

### `/market`
| Method | Path | Purpose |
|---|---|---|
| GET | `/market/state` | **the primary endpoint.** Full `MarketState` at `(market_time, knowledge_time)` |
| GET | `/market/state/series` | state sequence over a period, at a step |
| GET | `/market/spot` | spot with change |
| GET | `/market/session` | session phase and calendar |
| GET | `/market/observations` | raw observations (audit/debug; admin) |

`GET /market/state` returns a checkpoint only when it matches the full identity tuple
`(underlying, market_time, knowledge_time, build_context_id)`; otherwise it reconstructs
(`04-MARKETSTATE.md` §5). A checkpoint with a later knowledge horizon is never
substituted. Transparent to the caller either way.

### `/option-surface`
| Method | Path | Purpose |
|---|---|---|
| GET | `/option-surface` | full chain: legs, OI, IV, greeks, quotes, per expiry |
| GET | `/option-surface/oi` | OI surface by strike/expiry |
| GET | `/option-surface/iv` | IV surface |
| GET | `/option-surface/greeks` | greek surface |

One surface resource with projections, not one endpoint per widget.

### `/positioning`
`/positioning` (summary) · `/walls` · `/migration` · `/concentration` · `/buildup` ·
`/oi-change`. Each accepts `market_time` / `knowledge_time` and returns values with
feature versions and `available_at`.

`GET /positioning/migration` returns tracked `OIMigration` entities with lifecycle and
duration — not per-tick deltas.

### `/volatility`
`/volatility` · `/skew` · `/term-structure` · `/atm-iv` · `/rank` · `/realized`.

`/rank` returns `{"status": "insufficient_history", "required_days": N, "available_days": M}`
rather than a fabricated percentile.

### `/greeks` and `/gamma`
`/greeks/exposure` · `/gamma/gex` · `/gamma/profile` · `/gamma/by-expiry` ·
`/gamma/concentration` · `/gamma/flip-level`.

Every GEX response includes its `convention` and `feature_version`. No directional label
is attached.

### `/futures`
`/futures` · `/futures/basis` · `/futures/oi` · `/futures/confirmation`.

### `/structure`
`/structure/levels` (positioning-derived support/resistance) · `/structure/regime`
(with evidence) · `/structure/max-pain`.

### `/features`
| Method | Path | Purpose |
|---|---|---|
| GET | `/features` | **the registry** — every feature, all versions, definitions, units, conventions, availability delay |
| GET | `/features/{id}/versions/{v}` | full definition |
| GET | `/features/{id}/values` | time series for a scope |

This is what lets the UI render a formula definition on hover, and lets a researcher
confirm exactly which definition produced a number.

### `/signals`
`GET /signals` (filter by underlying, type, status, period) · `/signals/{id}` (full
evidence, both kinds) · `/signals/{id}/history` (lifecycle transitions) · `/signals/types`
(catalogue with rule definitions).

### `/alerts`
Full CRUD on rules · `POST /alerts/rules/{id}/test` (dry-run against current or historical
state) · `GET /alerts/occurrences` · `POST /alerts/occurrences/{id}/acknowledge`.

### `/research`
`/research/studies` CRUD · `POST /research/studies/{id}/run` · `/results` ·
`/research/datasets` · `/research/signal-evaluations`.

Results include sample count, exclusion counts, comparison count and the full assumption
set.

### `/replay`
`POST /replay/sessions` (create with period, universe, speed, knowledge mode) ·
`GET /replay/sessions/{id}` · `POST .../control` (play, pause, step, seek, speed) ·
`GET .../state` (state at the current replay position) ·
`GET /replay/sessions/{id}/stream` (SSE).

### `/backtest`
`POST /backtest/runs` · `GET /backtest/runs/{id}` (status, progress) · `/results` ·
`/trades` · `/equity-curve`.

### `/paper-trading` and `/trading`
`/accounts` · `POST /intents` · `GET /intents/{id}` (with its full risk decision
sequence) · `/orders` · `/orders/{id}/events` · `/fills` · `/positions` ·
`POST /orders/{id}/cancel`.

`/trading/*` is permission-gated (`LIVE_TRADE`) and feature-flagged off.

### `/risk`
`GET|PUT /risk/profiles` · `GET /risk/status` (current utilization against every limit) ·
`GET /risk/decisions` (audit) · `POST /risk/kill-switch` · `DELETE /risk/kill-switch`.

### `/portfolio`
`/portfolio` · `/pnl` · `/greeks` · `/exposure` · `/attribution` (sliceable) ·
`/snapshots`.

### `/reconciliation`
`GET /reconciliation/runs` · `/runs/{id}` (broker snapshot, discrepancies, resolutions) ·
`POST /reconciliation/trigger`.

Reconciliation being externally visible is deliberate — an operator must be able to see
what disagreed and how it resolved.

### `/data-quality`
`GET /data-quality/status` (current, per underlying) · `/issues` (filterable) ·
`/gaps` (collection gaps, for research exclusion) · `/coverage`.

### `/journal`
CRUD on entries, linkable to signals, intents and trades.

### `/stream`
`GET /stream/events` — SSE. Subscribable channels: `market_state`, `signals`, `alerts`,
`orders`, `portfolio`, `data_quality`. Replay streams are separate and run-scoped.

### `/ops`
`/health` (liveness) · `/ready` (readiness incl. DB, Redis, broker) · `/metrics`
(Prometheus) · `/version`.

---

## 4. Errors

```json
{
  "error": {
    "code": "FEATURE_NOT_AVAILABLE",
    "message": "GEX_BY_STRIKE@2 available at 11:45:02, requested at 11:42:00",
    "details": { "feature": "GEX_BY_STRIKE", "version": 2,
                 "available_at": "...", "requested_at": "..." }
  }
}
```

| Code | HTTP | Meaning |
|---|---|---|
| `FEATURE_NOT_AVAILABLE` | 422 | look-ahead refused |
| `INSUFFICIENT_HISTORY` | 422 | statistic needs more data |
| `QUALITY_REQUIREMENTS_UNMET` | 422 | feature declined to compute |
| `NO_DATA_FOR_PERIOD` | 404 | no observations |
| `STATE_UNRELIABLE` | 409 | operation refused on unreliable state |
| `RISK_REJECTED` | 422 | with the full reason set |
| `ORDER_STATE_UNKNOWN` | 409 | resolution pending reconciliation |
| `LIVE_TRADING_DISABLED` | 403 | feature flag off |
| `PERMISSION_DENIED` | 403 | |
| `RATE_LIMITED` | 429 | with `Retry-After` |

`FEATURE_NOT_AVAILABLE` being a first-class API error rather than an internal exception
matters: a researcher hitting the API directly gets the same look-ahead protection as the
backtest engine.

---

## 5. Cross-cutting

**Auth** — session cookie (server-side, revocable) for the browser; API keys for
programmatic access. Permissions per `17-SECURITY.md`. No broker token ever appears in a
response.

**Pagination** — cursor-based on `(observed_at, id)`; offset pagination is not offered on
observation-scale collections.

**Rate limiting** — Redis-backed, per principal, not per IP and not in-process.

**Versioning** — `/api/v2/`. Breaking changes require a new prefix; additive changes do not.

**Caching** — `ETag` on historical reads. A response for a past `(market_time,
knowledge_time, build_context_id)` is immutable and may be cached indefinitely — a useful
property that falls out of the bitemporal model.
