# Upstox V3 Protobuf Decoder Verification

Verification Date: 2026-09-24  
Branch: `phase2-v3-integration`  
Base Commit: `375b94085c6bd6030ebde8b4ce841607da77ace3`  

---

## 1. Protobuf Provenance & Runtime Environment

* **Official Proto Schema Source:**  
  `https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto`  
  Package: `com.upstox.marketdatafeederv3udapi.rpc.proto`
* **Local Proto Definition:**  
  `oipulse/marketdata/providers/upstox/proto/MarketDataFeed.proto` (verified `IDENTICAL` to upstream).
* **Protobuf Compiler & Runtime:**  
  * `protoc` Protobuf Python Version: `5.29.0`  
  * Installed `google.protobuf` runtime: `5.29.5`  
  * Python interpreter: `3.12.14`
* **Generated Code Policy:**  
  * `MarketDataFeed_pb2.py` is isolated in `oipulse/marketdata/providers/upstox/proto/`.
  * Excluded from manual refactoring / Ruff code style rewriting via `pyproject.toml`.
  * `__pycache__` remains ignored.

---

## 2. Decoder Architecture & Implementation

* **Module:** `oipulse/marketdata/providers/upstox/decoder.py`
* **Decoder Class:** `UpstoxV3ProtoDecoder` (implements `ProtoFrameDecoder` protocol).
* **Decoder Flow:**
  ```text
  Raw binary frame (bytes)
            ↓
  FeedResponse.ParseFromString(...)
            ↓
  Message Classification (market_info / initial_feed / live_feed)
            ↓
  Union Unpacking (LTPC / firstLevelWithGreeks / marketFF / indexFF)
            ↓
  DecodedFeedMessage
            ↓
  Canonical Normalization (normalize_ws_tick / normalize_index_tick)
            ↓
  Canonical Observations (QuoteObservation, GreeksObservation, IndexObservation)
  ```

---

## 3. Decoded Message & Field Verification

| Feed Union / Message Type | Decoded Fields Captured | Canonical Normalization Target |
| :--- | :--- | :--- |
| `market_info` | `segment_status`, `cas_market_status`, `pre_open_session_status`, `provider_timestamp` (`currentTs`) | Filtered out of observation pipeline; logged as operational status |
| `ltpc` | `ltp`, `close_price`, `last_trade_qty`, `iep`, `ltt` | `QuoteObservation` (`ltp`, `prev_close`) |
| `firstLevelWithGreeks` | `ltp`, `close_price`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`, `delta`, `theta`, `gamma`, `vega`, `rho`, `volume`, `oi`, `iv` | `QuoteObservation` (quotes, volume, oi) & `GreeksObservation` (iv, delta, gamma, theta, vega, rho) |
| `fullFeed.marketFF` | `ltpc` fields, 5-level / 30-level `depth`, `option_greeks`, `volume` (`vtt`), `oi`, `iv`, `atp`, `tbq`, `tsq`, `marketOHLC` | `QuoteObservation` & `GreeksObservation` |
| `fullFeed.indexFF` | `ltpc` fields, `open`, `high`, `low`, `close` (from `marketOHLC`) | `IndexObservation` (`ltp`, `open`, `high`, `low`, `prev_close`) |

---

## 4. Provider Identity & Ordering Fields

* **`provider_event_id`:** **ABSENT**. Confirmed absent from schema and feed payloads.
* **`channel_sequence`:** **ABSENT**. Confirmed absent from schema and feed payloads.
* **Provider Timestamps:**
  * `ltt` (last trade time in ms) and `currentTs` (server timestamp in ms) are converted to UTC `datetime` objects.
  * Timestamps are never promoted to message sequences or event IDs.
* **Local Diagnostics:**
  * `received_seq` is maintained as a local diagnostic counter on `V3Frame`.
  * Content digest is generated deterministically by `resolve_identity()` with `WEAK` confidence tier.

---

## 5. Error Handling & Determinism

* **Empty Frames (`b""`):** Raises `V3ProtobufDecodeError("cannot decode empty binary frame")`.
* **Malformed Protobuf:** Raises `V3ProtobufDecodeError` wrapping `google.protobuf.message.DecodeError`.
* **Determinism:** Verified by test — decoding identical byte sequences twice produces identical `DecodedFeedMessage` lists and identical canonical observations.

---

## 6. Static Quality & Test Suite Results

```bash
# 1. pytest -q
249 passed, 6 skipped in 1.00s  [PASS]

# 2. mypy --strict oipulse --show-error-codes
Success: no issues found in 53 source files  [PASS]

# 3. ruff check oipulse tools tests
All checks passed!  [PASS]

# 4. ruff format --check oipulse tools tests
78 files already formatted  [PASS]

# 5. python -m compileall -q oipulse tools tests
(clean, exit 0)  [PASS]

# 6. Architecture Guards
PASS  no wall-clock access outside oipulse/core/clock.py
PASS  layer boundaries clean (4 contracts armed)
PASS  every repository read accepts a temporal bound
```

---

## 7. Status Verdict

```text
STEP 6: DECODER IMPLEMENTED AND LOCALLY VERIFIED
```
