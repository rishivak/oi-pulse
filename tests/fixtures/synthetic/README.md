# SYNTHETIC FIXTURES — NOT RECORDED PROVIDER DATA

**Every file in this directory is synthetic.** None of it was captured from the Upstox
API. No request has ever been made to `api.upstox.com` from this codebase.

## What these fixtures are

Hand-constructed payloads whose **structure** is transcribed from
`backend/app/integrations/upstox/schemas.py` — the legacy integration that did run
against the live Upstox v2 API. That makes the *shape* second-hand evidence, not a guess.
The **values** are invented.

## What these fixtures are NOT

They are not evidence of:

- actual provider field names beyond those the legacy models consumed
- whether the WebSocket feed supplies a per-event id or a per-channel sequence
  (assumption **A-1**, unresolved)
- whether WS frames carry a venue timestamp (assumption **A-3**, unresolved)
- actual connection or subscription limits (assumption **A-5**, unresolved)
- real market values, real OI, real greeks, or real spot prices
- provider ordering or delivery semantics of any kind

## Why this matters

`docs/design/06-UPSTOX_INTEGRATION.md` §6 and the Phase 2 brief both require that
provider behaviour which has not been observed is **not assumed**. Tests driven by these
fixtures verify *our* normalization, identity resolution, idempotency and lifecycle
logic. They verify nothing about Upstox.

A test passing against a synthetic fixture means "our code handles this shape
correctly", never "the provider sends this shape".

## Replacing them

The external verification run should capture real sanitized responses and place them in
a sibling `tests/fixtures/recorded/` directory, with credentials and account identifiers
stripped. At that point the assumptions above can be resolved and
`extract_identity_hints()` in `providers/upstox/ws.py` can have its speculative key list
replaced with the observed one.

Until then, the identity confidence recorded on every observation is the honest signal:
`WEAK` means we could not prove ordering, and nothing downstream claims otherwise.
