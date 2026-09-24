# OI Pulse v2 — Upstox Integration Architecture

> **Deliverable I.** Upstox is the sole external market-data and broker integration.
> No Bloomberg, Refinitiv, Polygon, Zerodha, Angel, IB, scraping, or unofficial sources.

---

## 1. Provider abstraction

Upstox sits behind two protocols so the domain never depends on a vendor:

```python
class MarketDataProvider(Protocol):
    async def discover_instruments(...) -> list[InstrumentDescriptor]
    async def fetch_option_chain(underlying, expiry, *, as_of) -> OptionChainSnapshot
    async def fetch_quotes(instrument_keys) -> list[QuoteObservation]
    async def fetch_historical_ohlc(instrument, interval, frm, to) -> list[OHLCObservation]
    async def fetch_historical_oi(underlying, expiry, date) -> list[HistoricalOIObservation]
    def subscribe(instrument_keys, channels) -> AsyncIterator[MarketObservation]

class BrokerAdapter(Protocol):
    async def place_order(request: BrokerOrderRequest) -> BrokerAck
    async def cancel_order(broker_order_id) -> BrokerAck
    async def modify_order(broker_order_id, changes) -> BrokerAck
    async def get_order(broker_order_id) -> BrokerOrderState
    async def list_orders(*, since) -> list[BrokerOrderState]     # reconciliation
    async def list_trades(*, since) -> list[BrokerFill]           # reconciliation
    async def get_positions() -> list[BrokerPosition]             # reconciliation
    def subscribe_order_updates() -> AsyncIterator[BrokerOrderEvent]
```

Implementations: `UpstoxMarketDataProvider`, `UpstoxBrokerAdapter`, `PaperBrokerAdapter`.
The paper adapter implements `BrokerAdapter` exactly, so nothing upstream knows which is
in use.

The `list_orders` / `list_trades` / `get_positions` methods exist **specifically for
reconciliation** (`11-TRADING.md` §6) and are not optional parts of the protocol.

---

## 2. REST vs WebSocket — role split

Per the brief's §2, these are not interchangeable.

### REST is used for
| Purpose | Notes |
|---|---|
| Instrument and contract discovery | daily, plus on demand |
| Expiry list refresh | daily — **not** every collection cycle (a legacy defect) |
| Option-chain snapshots | the **cross-sectional consistency set** (`04-MARKETSTATE.md` §4) |
| Historical OHLC candles | backfill |
| **Historical OI** | backfill — see §7 |
| Reconciliation | REST is authoritative when it disagrees with WS |
| Recovery after a WS gap | out-of-band immediate fetch |
| Broker order/trade/position queries | reconciliation truth source |

### WebSocket is used for
| Purpose | Notes |
|---|---|
| Live quotes, OI, greeks | the primary live path |
| Market depth | where subscribed |
| Live index/spot ticks | |
| Broker order updates | live order state transitions |

**REST polling is not the live path.** The legacy system polls the chain every 1–30
minutes and calls it real-time; v2 polls the chain on a slower cadence purely as a
coherence anchor, and takes live movement from WS.

---

## 3. Authentication

Upstox OAuth 2.0, entirely server-side.

```
GET /auth/login → generate state (CSRF), persist with TTL → redirect to Upstox dialog
  → callback: validate + single-use consume state
  → exchange code for token
  → encrypt at rest, store against the BrokerCredential
  → audit record (no token values)
```

Rules (`17-SECURITY.md`):
- Tokens never reach the browser, never appear in logs, never appear in any API response.
- Encrypted at rest with an authenticated cipher; the algorithm is documented accurately —
  the legacy codebase claims AES-256-GCM while using Fernet (AES-128-CBC + HMAC-SHA256).
- Token expiry is tracked; a `TOKEN_EXPIRING` event fires ahead of expiry rather than
  discovering it through a 401 mid-session.
- On 401, the affected collector/trader is **suspended**, not left retrying every cycle.

---

## 4. Rate limits and budget

Upstox enforces per-endpoint limits. The design treats request budget as a managed
resource, because the expiry dimension multiplies demand.

- A **central rate-limit governor** in `marketdata/providers` holds a token bucket per
  endpoint class, shared across roles via Redis.
- Every caller acquires budget before issuing a request; exhaustion queues rather than
  fails.
- Chain-poll cadence is derived from `(underlyings × expiries) / budget`, not hardcoded.
  Adding an expiry to a universe therefore visibly costs cadence rather than silently
  causing throttling.
- `429` honours `Retry-After` and feeds back into the governor.
- Discovery and expiry refresh are cached daily — the legacy system re-fetches the expiry
  list on *every* collection cycle, which is pure waste.

---

## 5. Subscription capacity — the `SubscriptionPlanner`

Connection and subscription limits are finite and the expiry dimension multiplies demand
fast. Discovering that a universe is too large via a runtime WebSocket failure is
unacceptable; capacity is therefore a **planning decision made before subscribing**.

```
requested universe (underlyings × expiries × strikes × futures)
        ↓
required data modes per instrument   (LTPC | Greeks | Full/depth)
        ↓
instrument count per mode
        ↓
connection + subscription budget
        ↓
allocation across connections
        ↓
CapacityResult
```

### Capacity result

| Verdict | Meaning | Behaviour |
|---|---|---|
| `ACCEPTED` | the universe fits within budget | subscribe as planned |
| `DEGRADED` | fits only by reducing data modes or strike breadth | subscribe to the reduced plan, **record the reduction as a data-quality fact**, and report what was dropped |
| `UNSATISFIABLE` | cannot fit under any allowed degradation | **refuse to start**; the universe must be narrowed explicitly |

`DEGRADED` is not silent. The reduction is recorded so a later researcher can see that
depth was unavailable for a period because of a capacity decision, not because the venue
stopped sending it — a distinction that is invisible after the fact unless captured.

### Degradation ladder
Applied in order, each step recorded:
1. drop `depth` (Full → Greeks) on far-from-ATM strikes,
2. narrow the strike band toward ATM,
3. drop back expiries, nearest retained last,
4. drop non-primary underlyings.

Spot and front-expiry ATM coverage are never degraded — losing them would make
`MarketState` `UNRELIABLE` by definition (`04-MARKETSTATE.md` §3).

### Budget inputs

> **Phase-2-confirmed assumptions, not architectural constants.** Review indicates roughly
> 2 WebSocket connections per user, with combined subscription limits around 2,000 for
> LTPC/Greeks and 1,500 for Full mode (Full including LTPC, five depth levels, extended
> metadata and greeks). These values are **configuration**, verified during the Phase 2
> soak (assumption A-5, `20-ARCHITECTURE_FREEZE.md` §10) and changed without code edits.

The planner reads its budget from configuration and reports the numbers it used in every
`CapacityResult`, so a plan is auditable against the limits in force at the time.

### When it runs
On universe creation or change, on startup before subscribing, and as a dry-run via
`POST /universes/{id}/plan` (`12-API_SPEC.md`) so capacity can be checked before a change
is committed.

---

## 6. WebSocket lifecycle

```
connect → authenticate → subscribe(universe) → stream
   │
   ├── each message: assign feed_session_id, channel_sequence
   │                 normalize → MarketObservation → append-only store
   │
   ├── sequence discontinuity → WEBSOCKET_GAP issue
   │                          → trigger out-of-band REST chain fetch
   │                          → affected states marked RECOVERING
   │
   ├── heartbeat/staleness watchdog → if no message within budget, force reconnect
   │
   └── disconnect → new feed_session_id on reconnect
                  → RECONNECT_GAP issue covering the outage window
                  → REST resync before resuming normal cadence
```

**A new `feed_session_id` on every connection** is what makes sequence numbers meaningful;
sequences are only comparable within a session. The gap window is recorded permanently so
research can exclude or flag it — observations inside a gap are never fabricated or
interpolated.

Subscription is driven by the `SubscriptionPlanner`'s accepted plan (§5), and re-planned on
universe change without dropping the connection where the provider permits.

### Feed version — V3, binary Protobuf

External verification established that the Upstox **V2** market-data WebSocket is
discontinued and **V3** is the live feed. V3 carries **binary Protobuf frames**, not
JSON, and V3 authorization returns the authorized WebSocket URI:

```
OAuth token -> V3 market-data authorize endpoint -> authorized WebSocket URI
  -> connect -> subscribe (V3 request format) -> binary Protobuf frames
  -> decode with the official V3 .proto -> normalize to canonical observations
```

The decoder is **injected, not bundled** (AD-31). `UpstoxV3FeedClient` raises
`ProtoDecoderUnavailable` at construction when none is supplied, and there is no
fallback to the V2 JSON parser: a Protobuf frame parsed as JSON yields no observations
while the process reports itself healthy, which is worse than refusing to start. The
subscription *control* channel is JSON; the *data* frames are not. The two must not be
confused.

Subscription capacity is still decided by `SubscriptionPlanner` **before** any
subscription is sent (§5). Observed provider limits are recorded as observations, never
hardcoded in the adapter as architectural truths.

### Provider sequence and timestamp semantics — verified

**A-1 is resolved, negatively.** Verification of the live V3 feed observed, across two
distinct feed sessions:

```
provider_event_id : absent
channel_sequence  : absent
```

So Upstox V3 supplies **no provider identity and no provider ordering**. This is the
provider's actual behaviour, recorded rather than worked around. The adapter does not
search for candidate field names: an earlier version tried `id`, `seq`, `msg_id` and
others, which risked promoting a coincidentally-named field to a provider identity with
`STRONG` confidence and running sequence gap detection over it.

The table below therefore describes the general contract; the last row is the Upstox V3
case and the only one that occurs on this feed:

| Provider supplies | Identity used | `identity_confidence` | Gap detection |
|---|---|---|---|
| Stable per-event id | `provider_event_id` | `STRONG` | exact — missing ids detectable |
| Per-channel sequence | `(feed_session_id, channel, channel_sequence)` | `STRONG` | exact — discontinuity detectable |
| Neither — **Upstox V3** | `(instrument_id, observed_at, source, content_hash)`, an **OI Pulse-derived** digest, never labelled a provider event id | `WEAK` | **connectivity-based only** — reconnect, staleness against the heartbeat budget, and coverage; never sequence gaps |

Rules that hold in every case:
- `identity_confidence` is **stored on every observation**, so a consumer always knows how
  trustworthy dedup and ordering were for that row.
- Gap detection never claims more than the identity supports. Under `WEAK`, `WEBSOCKET_GAP`
  is not raised from sequence analysis; only `STALE_PRICE` and coverage-based issues are,
  and the data-quality surface says so rather than implying clean coverage.
- Replay ordering stays deterministic regardless, because its key falls back to
  `(observed_at, feed_session_ordinal, id)` (`10-REPLAY.md` §3).

- **Nothing is synthesized.** A `provider_event_id` or `channel_sequence` is never
  derived from the local `received_seq`, the content digest or a timestamp. A
  synthesized sequence is indistinguishable downstream from a real one and would satisfy
  every gap check while proving nothing (AD-30).
- **Absent fields stay absent.** A decoded frame reports only the fields it actually
  carried; a fabricated zero is indistinguishable from a real zero.

**Provider timestamp mapping** remains open (assumption A-3) pending recorded V3 frames:
provider timestamps are preserved **where actually supplied**, and where absent
`observed_at` falls back to receipt time, `ingested_at` equals it, and the substitution
is **recorded per feed** — because it materially weakens the bitemporal guarantee and
must not be silent.

---

## 7. Historical data — what is and is not available

> **Full historical intraday option-chain state cannot be reconstructed for arbitrary past
> timestamps from the Upstox APIs. However, historical OI can be obtained for supported
> dates and may be used for limited backfill and research.**

That is the precise statement. Both halves matter.

### Available
| Data | Endpoint | Use |
|---|---|---|
| Historical OI across strikes for an underlying/expiry/date | Upstox OI endpoint | limited backfill and research |
| Historical OHLC candles | historical candle endpoint | price context, realized volatility |
| Instrument/contract master | discovery | instrument versions |

### Not available after the fact
Historical intraday **LTP per option leg**, **bid/ask**, **intraday greeks**, **depth**,
and a **complete synchronized option surface** for an arbitrary past timestamp.

### The guardrail
Historical OI backfill must **not** be taken to imply that the above exist. A backfilled
day yields OI history — useful for positioning research — and nothing more.

Enforcement is structural rather than advisory:
- Backfilled rows land in `obs_historical_oi` with `source = REST_HIST_OI` and
  `observation_kind = HISTORICAL_DAILY_OI`, carrying an `observation_date` and an explicit
  `valid_from`/`valid_to` session interval — **date granularity, never an instant**
  (`02-DATA_MODEL.md` §3). A daily OI figure can therefore never be served as though it
  were live intraday state at 11:23:17.
- They carry `observed_at` = historical time, `ingested_at` = backfill run time, so they
  are correctly invisible to `knowledge_at` before the backfill
  (`05-DATA_LIFECYCLE_PIT.md` §6).
- A `MarketState` reconstructed for a backfilled-only period reports
  `coherence_mode = STREAM_ONLY` with low `coverage_ratio` and
  `quality_status = DEGRADED` or `UNRELIABLE`, because quotes and greeks are genuinely
  absent.
- Features declaring `quality_requirements` that include quotes or greeks **will not
  compute** over such a period. A research run over backfilled history therefore cannot
  silently produce results that depend on data that never existed.

### Consequence for the roadmap
Live collection remains the only route to full-fidelity history, so ingestion is
front-loaded (`18-ROADMAP.md`). Historical OI backfill usefully extends *positioning*
research backwards; it does not substitute for running the collector.

---

## 8. Normalization

The provider adapter is the **only** place vendor shapes exist. It converts:

| Vendor concept | Internal |
|---|---|
| `instrument_key` string | `InstrumentId` via `instrument_vendor_mappings` valid at `observed_at` |
| chain row with paired CE/PE | two per-leg observations, `option_type` as data |
| `option_greeks` block | `GreeksObservation` — **all** of iv/delta/gamma/theta/vega |
| `market_data` block | `QuoteObservation` — including bid/ask and `provider_prev_oi` |
| vendor timestamps | `observed_at`, timezone-normalized to UTC |
| vendor error codes | typed domain errors |

Every field the vendor provides is captured. The legacy system parses greeks, bid/ask and
`prev_oi` and then discards them — data that cannot be recovered once the moment passes.

Unknown or newly added vendor fields are preserved in a `raw_extra` JSONB column rather
than dropped, so a schema addition is never silently lost before we notice it.

---

## 9. Error handling

| Condition | Behaviour |
|---|---|
| `401` | typed `AuthError`; suspend affected role; emit `auth_required`; do not retry |
| `429` | honour `Retry-After`; return budget to the governor; retry within budget |
| `5xx` / timeout | bounded retry, exponential backoff with jitter |
| Malformed payload | store raw, raise `IMPOSSIBLE_VALUE`, do not crash the consumer |
| Partial chain | store what arrived; mark `is_complete = false`; raise `INCOMPLETE_CHAIN` |
| WS disconnect | §6 |
| **Broker submit, response lost** | **never inferred** — order → `UNKNOWN` → reconciliation (`11-TRADING.md` §5) |

A circuit breaker per endpoint class prevents a sustained outage from consuming the entire
rate budget on failures.

---

## 10. Broker integration specifics

- `UpstoxBrokerAdapter` is implemented **last** and sits behind a feature flag defaulting
  to off (brief §21).
- Internal idempotency keys (`client_order_intent_id`, `client_order_attempt_id`,
  `idempotency_key`) provide local dedup and audit. They **do not** guarantee that Upstox
  will reject a duplicate submission — that is not ours to promise.
- Safety therefore comes from the `UNKNOWN` → `PENDING_RECONCILIATION` path and from
  reconciliation against `list_orders` / `list_trades`, where **broker state is
  authoritative**.
- Order-update WS is treated as a *hint* that accelerates reconciliation, never as the
  sole source of truth — missed events are assumed possible.
