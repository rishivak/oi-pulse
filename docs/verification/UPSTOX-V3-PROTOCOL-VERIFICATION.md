# Upstox V3 Market Data Feed — Protocol & Schema Verification

Verification Date: 2026-09-24  
Target Branch: `phase2-v3-integration`  
Base Commit: `637e8bfbea4fbed0863e2d69360a8d2082547ad3`  

---

## A. Official Source & Provenance

* **Official V3 Market Data Feed Documentation:**  
  `https://upstox.com/developer/api-documentation/v3/get-market-data-feed/`
* **Official V3 Protobuf Schema URL:**  
  `https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`
* **Verification Date:** 2026-09-24

---

## B. Local vs Official Protobuf Comparison

Comparison between local `oipulse/marketdata/providers/upstox/proto/MarketDataFeed.proto` and official Upstox V3 schema:

| Item | Local Definition | Official Definition | Result |
| :--- | :--- | :--- | :--- |
| `syntax` | `proto3` | `proto3` | `IDENTICAL` |
| `package` | `com.upstox.marketdatafeederv3udapi.rpc.proto` | `com.upstox.marketdatafeederv3udapi.rpc.proto` | `IDENTICAL` |
| `imports` | `google/protobuf/wrappers.proto` | `google/protobuf/wrappers.proto` | `IDENTICAL` |
| `FeedResponse` | Message with `type` (1), `feeds` (2), `currentTs` (3), `marketInfo` (4) | Same field numbers & types | `IDENTICAL` |
| `Type` (enum) | `initial_feed=0`, `live_feed=1`, `market_info=2` | Same enum values | `IDENTICAL` |
| `Feed` | Oneof `FeedUnion` (`ltpc=1`, `fullFeed=2`, `firstLevelWithGreeks=3`), `requestMode=4` | Same field numbers & oneof | `IDENTICAL` |
| `LTPC` | `ltp=1`, `ltt=2`, `ltq=3`, `cp=4`, `iep=5` (`DoubleValue`) | Same field numbers & types | `IDENTICAL` |
| `FullFeed` | Oneof `FullFeedUnion` (`marketFF=1`, `indexFF=2`) | Same field numbers & oneof | `IDENTICAL` |
| `MarketFullFeed` | 16 fields (`ltpc`, `marketLevel`, `optionGreeks`, `marketOHLC`, `atp`, `vtt`, `oi`, `iv`, `tbq`, `tsq`, `iep`, `rp`, `ieq`, `iiqTotal`, `iiqM`, `casEligible`) | Same 16 fields & numbers | `IDENTICAL` |
| `IndexFullFeed` | `ltpc=1`, `marketOHLC=2` | Same field numbers & types | `IDENTICAL` |
| `FirstLevelWithGreeks` | `ltpc=1`, `firstDepth=2`, `optionGreeks=3`, `vtt=4`, `oi=5`, `iv=6` | Same field numbers & types | `IDENTICAL` |
| `OptionGreeks` | `delta=1`, `theta=2`, `gamma=3`, `vega=4`, `rho=5` | Same field numbers & types | `IDENTICAL` |
| `MarketLevel` | `repeated Quote bidAskQuote = 1` | Same field numbers & types | `IDENTICAL` |
| `Quote` | `bidQ=1`, `bidP=2`, `askQ=3`, `askP=4` | Same field numbers & types | `IDENTICAL` |
| `MarketOHLC` | `repeated OHLC ohlc = 1` | Same field numbers & types | `IDENTICAL` |
| `OHLC` | `interval=1`, `open=2`, `high=3`, `low=4`, `close=5`, `vol=6`, `ts=7` | Same field numbers & types | `IDENTICAL` |
| `RequestMode` (enum) | `ltpc=0`, `full_d5=1`, `option_greeks=2`, `full_d30=3` | Same enum values | `IDENTICAL` |
| `MarketStatus` (enum) | `PRE_OPEN_START=0` .. `CLOSING_END=5` | Same enum values | `IDENTICAL` |
| `MarketInfo` | `segmentStatus=1`, `casMarketStatus=2`, `preOpenSessionStatus=3` | Same map structures | `IDENTICAL` |
| `StatusInfo` | `status=1`, `updatedTime=2` | Same field numbers & types | `IDENTICAL` |

**Classification:** `IDENTICAL`

---

## C. Provider Protocol Specifications

1. **V3 Authorization Endpoint:**
   * `GET https://api.upstox.com/v3/feed/market-data-feed/authorize`
   * Request Headers: `Authorization: Bearer <access_token>`, `Accept: application/json`
   * Response: JSON containing `status: "success"` and `data.authorizedRedirectUri` (e.g. `wss://api.upstox.com/v3/feed/market-data-feed?token=...`).
2. **WebSocket URI Lifecycle:**
   * The generated WebSocket URI contains a temporary, single-use authentication token.
   * On disconnect or reconnect, a new authorization request must be executed to obtain a fresh authorized URI.
3. **Payload / Serialization Format:**
   * Client to Server (Subscription requests): JSON text or binary frames containing `guid`, `method` (`sub` / `unsub` / `change_mode`), and `data` (`mode`, `instrumentKeys`).
   * Server to Client: Binary Protocol Buffer frames (`FeedResponse` message).
4. **Message Sequence & Feed Phases:**
   * **Message 1 (`market_info`):** Emitted immediately upon connection establishment, containing segment status, CAS status, and pre-open session metadata.
   * **Message 2 (`initial_feed`):** Snapshot state for all currently subscribed instruments at the time of subscription.
   * **Subsequent Messages (`live_feed`):** Incremental real-time streaming updates whenever market data changes.
5. **Supported Subscription Modes (`RequestMode`):**
   * `ltpc` (Mode 0): Last Traded Price, Time, Quantity, Close Price.
   * `full_d5` (Mode 1) / `full`: 5-level Market Depth + OHLC + LTPC + Greeks (for derivatives) + Volume + Total Buy/Sell Qty + OI + IV.
   * `option_greeks` (Mode 2): First level quote + Option Greeks + OI + IV + LTPC.
   * `full_d30` (Mode 3): 30-level extended market depth.

---

## D. Provider Limits vs. Planner Configuration

Comparison between Upstox documented connection/subscription limits and OI Pulse `SubscriptionBudget` (`oipulse/marketdata/subscription.py`):

| Limit Dimension | Official / Vendor Limits | `SubscriptionBudget` Default | Alignment Status |
| :--- | :--- | :--- | :--- |
| **Max Concurrent Connections** | 2 WebSocket connections per API user | `max_connections = 2` | `MATCH` |
| **LTPC / Greeks Subscriptions** | Up to 2,000 instruments | `max_subscriptions_ltpc_greeks = 2000` | `MATCH` |
| **Full Depth Subscriptions** | Up to 1,500 instruments | `max_subscriptions_full = 1500` | `MATCH` |
| **Capacity Reserve Buffer** | Operational margin (best practice) | `reserve_fraction = 0.05` (5%) | `DESIGNED` |

*Note:* Per architectural constraint E and decision AD-25, vendor limits remain configurable runtime assumptions in `SubscriptionBudget` rather than hardcoded architectural constants.

---

## E. Upstream Provider Identity & Ordering Fields

Detailed examination of the official Protobuf schema (`FeedResponse`, `Feed`, `MarketFullFeed`, `LTPC`, `OHLC`):

* **`provider_event_id`:** **ABSENT**. The Upstox V3 protocol does not include a unique event identifier for individual ticks or messages.
* **`channel_sequence` / `provider_sequence`:** **ABSENT**. The feed does not contain any monotonic message counter or channel sequence number.
* **`currentTs` (in `FeedResponse`):** Millisecond wall-clock timestamp from Upstox server. This is a point-in-time timestamp, **not** a sequence number.
* **`ltt` (in `LTPC`):** Last trade timestamp in milliseconds.
* **`guid` (in subscription JSON):** Client-generated request correlation identifier, not a provider event ID.

**Conclusion:**  
* Provider sequence gaps cannot be derived from feed metadata because no upstream sequence exists.
* Gaps are detected solely as **connectivity/reconnect intervals** (disconnection, timeout, or reconnect events).
* OI Pulse maintains distinct local concepts:
  1. `received_seq`: Local receiver diagnostic ordering (monotonically assigned by collector).
  2. `event_digest`: Derived cryptographic hash of canonical normalized attributes.
  3. No fake provider sequence or event IDs are created.

---

## F. Generated Protobuf Code (`MarketDataFeed_pb2.py`)

* **Compiler Version:** Generated via `protoc` (Protobuf Python 5.29.0+ / runtime 5.29.5).
* **Descriptor Pool:** Matches `MarketDataFeed.proto` message definitions, field tags, oneofs, and types exactly.
* **Runtime Compatibility:** Verified against installed `google.protobuf 5.29.5`.
* **Linting / Code Quality:** Standard compiler-generated gencode conventions apply (not manually reformatted).
* **Build / Tracking:** Proto definition lives in `oipulse/marketdata/providers/upstox/proto/MarketDataFeed.proto`; gencode importable by V3 decoder boundary.

---

## Summary Verdict

```text
STEP 4/5: PASS — OFFICIAL V3 PROTOCOL VERIFIED
```
