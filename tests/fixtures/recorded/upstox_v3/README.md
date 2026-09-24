# `upstox_v3/` — recorded Upstox V3 market-data frames

**This directory is intentionally tracked while empty.** It previously existed only on
the implementer's disk: git does not track empty directories, so a fresh clone did not
have it and the contract test failed on the verifier's machine. This file is what makes
the directory — and the contract below — travel with the repository.

**No frames have been captured yet.** Capture requires network access to
`api.upstox.com`, valid OAuth credentials and a live NSE market session, none of which
were available in the implementation environment. No `.bin` file here is fabricated,
and none ever may be: a fabricated capture is indistinguishable from real evidence to
every later reader, which is exactly the confusion this directory exists to prevent.

See `../README.md` for the recorded-versus-synthetic rule. Synthetic fixtures live in
`tests/fixtures/synthetic/` and are labelled in every payload.

## File layout

Each capture is two files sharing a stem:

```
<stem>.bin     raw binary frame, byte-for-byte as received off the socket
<stem>.json    manifest describing it
```

## Manifest schema

Every key is required. `tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract`
enforces this.

| Key | Meaning |
|---|---|
| `captured_at` | ISO-8601 with offset, as observed |
| `feed` | always `upstox_market_data_v3` |
| `subscription_mode` | `ltpc`, `full`, or `option_greeks` |
| `instrument_type` | `index`, `option`, `future`, … |
| `frame_kind` | `market_info`, `initial_snapshot`, or `live_feed` |
| `proto_revision` | the published V3 proto revision, or `unknown` |
| `feed_session_ordinal` | 1 or 2 — distinguishes the two required sessions |
| `instrument_keys` | list of instrument keys present in the frame |
| `expiries` | list of expiry dates present, if any |
| `sanitization` | what was removed, and confirmation that payload semantics are unchanged |
| `notes` | optional |

Example:

```json
{
  "captured_at": "2026-03-05T09:47:12.481+05:30",
  "feed": "upstox_market_data_v3",
  "subscription_mode": "full",
  "instrument_type": "option",
  "frame_kind": "live_feed",
  "proto_revision": "unknown",
  "feed_session_ordinal": 1,
  "instrument_keys": ["NSE_FO|..."],
  "expiries": ["2026-03-05"],
  "sanitization": "access token, authorized URI and account identifiers removed; payload semantics unchanged",
  "notes": ""
}
```

## Required capture set

| # | Capture | Why |
|---|---|---|
| 1 | `market_info` frame | must route as status, never become an observation |
| 2 | initial/snapshot frame, if delivered | distinguishes anchoring from live updates |
| 3 | LTPC frame | the minimal live-price path |
| 4 | full / Greeks frame | Phase 2 option analytics depend on it |
| 5 | multiple instruments | proves per-instrument routing |
| 6 | multiple option expiries | expiry is a first-class dimension |
| 7 | frames from **two** feed sessions | the only evidence that can settle cross-session identity behaviour |

## Sanitization

Remove the access token, the authorized WebSocket URI and any account identifier.
**Do not alter market payload semantics** — no rounding, no renaming, no reordering, no
substituted instrument keys. A sanitized capture that no longer decodes the way the real
frame did is worse than no capture, because it still looks like evidence.
