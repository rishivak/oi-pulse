# Phase 1 / Phase 2 — Final Verification

**Baseline externally verified:** commit `6e987ed`
**This remediation:** the commit carrying this document
**Phase 3:** not authorized, not started.

---

## Verdict

```
PHASE 1: NOT PASS  — 2 test failures and 21 mypy errors remain unreproduced here
PHASE 2: NOT VERIFIED — the V3 feed path has no decoder, no recorded fixtures, no soak
PHASE 3: NOT AUTHORIZED, NOT STARTED
```

Nothing below is claimed as observed unless the command that observed it is named.

---

## 1. Environment — measured, not assumed

The brief states the environment has live Upstox access. **It does not.** Measured at the
start of this remediation:

| Capability | Probe | Result |
|---|---|---|
| Package index | `pip install --user mypy` | fails: PEP 668 externally-managed, and DNS for `pypi.org` fails |
| `pypi.org:443` | `socket.create_connection` | `gaierror -3` — name resolution failure |
| `api.upstox.com:443` | `socket.create_connection` | `gaierror -3` — name resolution failure |
| PostgreSQL `localhost:5432` | `socket.create_connection` | `ConnectionRefusedError` |
| Redis `localhost:6379` | `socket.create_connection` | `ConnectionRefusedError` |
| `pytest`, `mypy`, `sqlalchemy`, `asyncpg`, `redis`, `fastapi`, `uvicorn`, `websockets`, `alembic`, `protobuf`, `import-linter` | `importlib.util.find_spec` | all **MISSING** |
| `ruff` | `find_spec` | present |
| `DATABASE_URL`, `REDIS_URL`, `UPSTOX_ACCESS_TOKEN`, `UPSTOX_CLIENT_ID`, `OIPULSE_INGESTOR_UNIVERSE` | environment | all **unset** |

Everything marked BLOCKED below traces to this table. These are not excuses for skipped
work; they are the reason specific work could not be performed, and each blocked item
names what would unblock it.

---

## 2. Item-by-item

### P0 — Phase 1 test failures (2) — **NOT FIXED, BLOCKED ON THE FAILURE OUTPUT**

`python3 -m unittest discover -s tests -t .` reports **236 passed, 6 skipped** here. The
two failures were observed in the provisioned environment with dependencies installed,
and the suites that differ there are exactly the ones this environment cannot exercise
(`pytest` collection, FastAPI import, SQLAlchemy import, the PostgreSQL integration
tests, which skip here).

No fix was attempted. Guessing which assertions to change, against a failure whose text
is unknown, is how assertions get weakened — which the brief forbids and which would be
worse than leaving the failures standing.

**To unblock:** the two test ids and their tracebacks.

### P0 — `mypy --strict`: 21 errors in 7 files — **NOT FIXED, BLOCKED**

mypy cannot be installed (no package index). The stdlib subset check
(`tools/check_typing_strict.py`) passes across 50 files, and that is explicitly **not**
equivalent to mypy: it performs no inference, no assignment compatibility, no narrowing,
and its override check is nominal rather than structural.

The error count rose 12 → 21 across the previous remediation, which is consistent with
the new modules added there. Without the actual output, the affected declarations cannot
be identified.

**To unblock:** the full `mypy --strict oipulse` output.

### P0 — Replace V2 with V3 — **STRUCTURE IMPLEMENTED, DECODER BLOCKED**

New: `oipulse/marketdata/providers/upstox/v3.py`.

Implemented and tested offline: the authorize call and `AUTHORIZE_PATH`, authorized-URI
extraction from the documented response shapes, subscription and unsubscription request
construction, frame routing, `market_info` versus market-data classification, the
`DecodedFeedMessage` shape, and every refusal condition.

**Deliberately not implemented: the Protobuf decoder.** Decoding needs the official V3
`.proto` — message names, field numbers, wire types. It is provider-owned, was not
obtainable here, and no Protobuf runtime is installable. A wrong field number does not
raise; it decodes to a plausible value for the wrong field, which then lands in durable
market data indistinguishable from a real observation.

So the boundary **fails closed**:

* `ProtoFrameDecoder` is a Protocol with no production implementation.
* `UpstoxV3FeedClient(decoder=None)` raises `ProtoDecoderUnavailable` **at construction**, before any connection exists — a client that connects first has already opened a feed session and started a gap it must then explain.
* `runtime.load_proto_decoder()` returns `None`; the ingestor exits with a distinct status rather than starting.
* There is **no JSON fallback**. Verified by test: `v3.py` contains no `json.loads` outside its docstring, and no `_pb2` / `DESCRIPTOR` / `serialized_pb` marker that would indicate a guessed schema.

`ws.py` (V2 JSON) is marked **NOT THE PRODUCTION PATH** and is no longer imported by
`runtime.py`; a test asserts both.

**UNVERIFIED against the live protocol,** each isolated in one function so verification
touches one place: `AUTHORIZE_PATH`, the authorize response field path, and the
subscribe/unsubscribe message shape.

### P0 — Provider identity model — **IMPLEMENTED**

Against the reported evidence (two V3 sessions; `provider_event_id` absent;
`channel_sequence` absent), all seven required properties hold and each is tested:

| Requirement | Where | Test |
|---|---|---|
| 1. `feed_session_id` preserved | `identity.py` | existing tier-scoping tests |
| 2. Provider timestamps preserved where supplied | `ws.py::extract_identity_hints`, `DecodedFeedMessage.provider_timestamp` | `test_absent_fields_stay_absent` |
| 3. Complete decoded payload retained for deterministic hashing | `DecodedFeedMessage.raw` | — |
| 4. Content hashing is an OI Pulse mechanism, never labelled a provider event id | `IdentityTier.is_provider_supplied`, `ObservationIdentity.is_provider_supplied` | `test_the_derived_digest_is_not_labelled_a_provider_event_id` |
| 5. Identity confidence retained | unchanged | existing |
| 6. `received_seq` kept distinct from provider ordering | new `OrderingAuthority` | `test_a_local_counter_is_never_promoted_to_provider_identity` |
| 7. No upstream gap detection claimed | `OrderingAuthority.supports_gap_detection` | `test_no_provider_gap_detection_is_claimed_without_a_provider_sequence` |

**Defect fixed.** `extract_identity_hints` previously guessed at candidate key names
(`id`, `seq`, `msg_id`, `eventId`, …). A payload carrying an unrelated `id` would have
been promoted to a *provider* identity with `STRONG` confidence and had sequence gap
detection run over it. It now reads only explicitly-named provider fields.
`test_hint_extraction_no_longer_guesses_key_names` pins this.

The synthetic fixture `SYNTHETIC_WS_TICK_WITH_IDENTITY` was updated to the explicit
field names and re-documented as describing a *hypothetical* provider, not Upstox.

### P0 — A-13 and dependent wording — **UPDATED**

`20-ARCHITECTURE_FREEZE.md` §10:

* **A-1 — RESOLVED, NEGATIVE.** Was "Upstox WS provides a usable sequence or event id".
* **A-13 — REDEFINED.** The original question (global or per-session event-id scope) is moot, because no event id exists. The standing risk now reads: *Upstox V3 provides no provider event sequence; gap detection therefore relies on connectivity, the heartbeat budget and REST recovery rather than provider sequence.*
* **A-3** re-scoped to V3 frames, still open pending recorded captures.
* **A-14 added** — the official `.proto` must match what the feed emits; blocked, mitigated by AD-31.

New decisions: **AD-30** (four-way identity distinction; nothing synthesized) and
**AD-31** (decoder injected, adapter fails closed).

Updated: `03-EVENT_MODEL.md` §2 (four concepts table; two kinds of gap),
`06-UPSTOX_INTEGRATION.md` §6 (V3 lifecycle; A-1 resolved negative),
`18-ROADMAP.md` Phase 2 (V3, Protobuf, recorded fixtures, soak in acceptance).

### P0 — WebSocket recovery semantics — **IMPLEMENTED**

The two kinds of gap are now distinguished explicitly in code and documentation:

* **Provider-sequence gap** — requires a provider sequence, therefore **not detectable on Upstox V3** and never claimed. Gated by `IdentityConfidence.supports_sequence_gap_detection`, which is False under `WEAK`.
* **Connectivity / reconnect gap** — detectable from connection loss, reconnect and elapsed silence against the **explicit heartbeat budget**.

Chain verified end to end offline: connection interruption → `RECONNECT_GAP` → recovery
plan (out of band) → REST recovery → idempotent canonical persistence → stream resumes.

A gap is **not** emitted merely because time elapsed: `STALE_FEED` is raised against the
documented budget, reports silence rather than a count of lost messages, and carries no
`expected_sequence`. `test_staleness_uses_a_defined_threshold_not_bare_elapsed_time`
pins this.

### P0 — Recorded Protobuf fixtures — **BLOCKED, INFRASTRUCTURE IN PLACE, NO CAPTURES**

`tests/fixtures/recorded/upstox_v3/` exists and is **empty**. Capture needs
`api.upstox.com`, OAuth credentials and a live market session; none exists here.
**No frames were fabricated.** A fabricated capture is indistinguishable from evidence
to every later reader, which is precisely the failure mode this directory exists to
prevent.

`tests/fixtures/recorded/README.md` defines the contract: file layout (`<stem>.bin` +
`<stem>.json`), the full manifest schema (`captured_at`, `feed`, `subscription_mode`,
`instrument_type`, `frame_kind`, `proto_revision`, `feed_session_ordinal`,
`instrument_keys`, `expiries`, `sanitization`), the seven required captures, and
sanitization rules that forbid altering payload semantics.

`TestRecordedFixtureContract` enforces it: the directory must exist and be documented,
synthetic payloads must never appear under `recorded/`, every `.bin` must have a
complete manifest, and the required set (market_info, live_feed, two feed sessions) must
be present. The "required set" test currently **skips with an explicit OUTSTANDING
message naming the blocker** — visible in `-rs` output rather than silently green.

### P0 — Protobuf decoding tests — **BLOCKED**

Cannot exist without a decoder and real frames. The fields they must cover are recorded
in the roadmap and the fixture README: instrument key, LTP, close, bid, ask, OI,
previous OI, IV, delta, gamma, theta, vega, volume, provider timestamps — with **absent
fields recorded as absent**, never defaulted. `DecodedFeedMessage` already enforces the
absent-means-absent shape and is tested.

### P0 — Subscription request verification — **PARTIAL**

Construction is implemented and tested: mode and instrument keys, multiple instruments,
multiple expiries as distinct keys, LTPC and Greeks/full modes, empty subscription
refused. `SubscriptionPlanner` remains responsible for capacity **before** subscription,
unchanged.

No provider limit is hardcoded in the adapter — verified by test.

**Not verified:** the wire format against the live protocol. Requires the endpoint.

### P1 — Reconnect and recovery against the live feed — **BLOCKED**

Steps 1–8 of the brief need a live V3 connection. The offline equivalents (reconnect
recorded, recovery executed, REST overlap idempotent, stream resumed, no false sequence
gap claimed) are covered by `TestGapTaxonomy` and `TestRecoveryPathWiring`.

### P1 — A-13 closure — **DONE as redefinition**

Not held open on a theoretical collision for a field the provider does not supply. The
risk is restated as the permanent property of this feed, and identity confidence is
retained for the derived mechanism.

### P1 — Market-session soak — **BLOCKED**

Needs a live session and a working decoder.

---

## 3. Validation actually executed

| Check | Command | Result |
|---|---|---|
| Unit + structural suite | `python3 -m unittest discover -s tests -t .` | **236 passed, 6 skipped** |
| Lint | `ruff check oipulse tools tests` | pass |
| Format | `ruff format --check oipulse tools tests` | pass, 75 files |
| Byte-compile | `python3 -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, 4 contracts |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 4 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 6 kinds / 19 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 2 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 50 files |

**Not executed here, and therefore not claimed:** `pytest`, `mypy --strict`,
`lint-imports`, `alembic upgrade head`, V3 authorization, V3 WebSocket connect, Protobuf
decoding, reconnect against the live feed, PostgreSQL persistence, and the NSE-session
soak.

The 6 skips are the 5 PostgreSQL migration integration tests and the outstanding
recorded-fixture set. Each is a reported gap, not a pass.

---

## 4. Phase gate

### Phase 1 — **NOT PASS**

| Requirement | Status |
|---|---|
| All tests pass | **unknown** — 236/236 here; 2 failures reported in the provisioned environment, output not supplied |
| mypy passes | **unknown** — 21 errors reported; mypy not installable here |
| lint-imports passes | not run |
| Migrations pass | structurally verified; not applied to PostgreSQL here |
| API entrypoint works | `--help`, `--check`, role dispatch tested |
| Ingestor entrypoint works | refuses correctly without a decoder; cannot stream |
| Guards pass | yes, all seven |

### Phase 2 — **NOT VERIFIED**

| Requirement | Status |
|---|---|
| V3 REST | not run here |
| V3 WebSocket | **blocked** — no decoder |
| Protobuf decoding | **blocked** — no `.proto`, no runtime |
| Multi-expiry | subscription construction tested; not live |
| Canonical durable observations | offline only |
| PostgreSQL idempotency | in-memory twin only |
| Bitemporal correctness | tested offline |
| SubscriptionPlanner | tested |
| Reconnect | offline only |
| Recovery | offline end-to-end |
| Real recorded V3 fixtures | **absent** |
| Provider identity model verified | implemented to the reported evidence |
| Real NSE-session soak | **not run** |
| Observability | metrics and readiness implemented |
| No Phase 3 leakage | confirmed |

---

## 5. What unblocks the rest

1. The two failing Phase 1 test ids with tracebacks.
2. The full `mypy --strict oipulse` output.
3. The official Upstox V3 `.proto` (or a provisioned environment with network access), plus a Protobuf runtime.
4. Sanitized real V3 binary frames matching the seven required captures.
5. OAuth credentials and a live NSE session for reconnect, recovery and soak.
6. A PostgreSQL and Redis instance for the integration and readiness paths.

Items 1 and 2 are pasteable into this session. Items 3–6 need a provisioned environment.
