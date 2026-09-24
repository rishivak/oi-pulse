# Recorded fixtures — real provider captures only

Everything in this directory is a **real, sanitized capture from a live Upstox feed**.
Nothing synthetic may be placed here, and nothing here may be edited to change payload
semantics. Synthetic fixtures live in `tests/fixtures/synthetic/` and are labelled as
such in every payload.

The distinction is load-bearing. A test passing against a synthetic fixture proves that
our code handles a shape we invented; only a recorded fixture can show that the provider
actually sends that shape. Blurring the two would let an assumption be mistaken for
evidence, which is how the V2-versus-V3 protocol error survived as long as it did.

## Status

**`upstox_v3/` is currently EMPTY, and that is a reported gap, not an oversight.**

Capture requires network access to `api.upstox.com`, valid OAuth credentials and a live
market session. The development environment has none of these: DNS resolution fails for
`api.upstox.com`, no credentials are configured, and no package index is reachable to
install a Protobuf runtime. Fabricating frames here would be indistinguishable from real
evidence to every later reader, so no files were created.

Until real captures exist, the Phase 2 gate cannot pass. `tests/phase2/test_recorded_fixtures.py`
enforces the contract below and reports the absence rather than skipping quietly.

## Required captures (`upstox_v3/`)

| # | Capture | Why it is required |
|---|---|---|
| 1 | `market_info` frame | must be routed as status, never normalized into an observation |
| 2 | initial/snapshot frame, if the feed delivers one | distinguishes first-state anchoring from live updates |
| 3 | LTPC frame | the minimal live-price path |
| 4 | full / Greeks frame | Phase 2 option analytics depend on it |
| 5 | multiple instruments | proves per-instrument routing |
| 6 | multiple option expiries | expiry is a first-class dimension |
| 7 | frames from **two** feed sessions | the only evidence that can settle identity behaviour across sessions |

## File layout

Each capture is two files sharing a stem:

```
upstox_v3/<stem>.bin       raw binary frame, byte-for-byte as received
upstox_v3/<stem>.json      manifest describing it
```

## Manifest schema

```json
{
  "captured_at": "2026-03-05T09:47:12.481+05:30",
  "feed": "upstox_market_data_v3",
  "subscription_mode": "full",
  "instrument_type": "option",
  "frame_kind": "live_feed",
  "proto_revision": "MarketDataFeedV3 (revision as published, or 'unknown')",
  "feed_session_ordinal": 1,
  "instrument_keys": ["NSE_FO|..."],
  "expiries": ["2026-03-05"],
  "sanitization": "access token, authorized URI, account identifiers removed; payload semantics unchanged",
  "notes": ""
}
```

`frame_kind` is one of `market_info`, `initial_snapshot`, `live_feed`.
`feed_session_ordinal` distinguishes the two required sessions.

## Sanitization rules

Remove the access token, the authorized WebSocket URI and any account identifier.
**Do not alter market payload semantics** — no rounding, no renaming, no reordering, no
substituted instrument keys. A sanitized capture that no longer decodes the way the real
frame did is worse than no capture, because it looks like evidence.
