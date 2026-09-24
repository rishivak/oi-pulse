# Phase 1 / Phase 2 — Final Verification

**Baseline externally verified:** commit `c522559`
**This remediation:** the commit carrying this document

```
PHASE 1: READY FOR EXTERNAL RE-VERIFICATION
PHASE 2: IMPLEMENTATION READY / EXTERNAL VERIFICATION PENDING
PHASE 3: NOT AUTHORIZED, NOT STARTED
```

Phase 2 is **not** claimed as PASS. No live V3 behaviour is claimed as verified.

---

## How to read this report

Findings are separated into four kinds, because conflating them is what let the previous
round look better than it was:

| Kind | Meaning |
|---|---|
| **A. Implementation defect** | Production code was wrong. Fixed. |
| **B. Test-environment assumption** | Production code was right; the *test* asserted a property of the machine. Fixed by injection. |
| **C. Genuinely unverified provider behaviour** | Cannot be known without the live feed. Fail-closed, documented, not guessed. |
| **D. External requirement still pending** | Needs an environment this one is not. |

---

## A. Implementation defects fixed

### A1 — Capacity was planned against fabricated instrument ids
`runtime.py` built its `SubscriptionRequest` from `range(len(vendor_keys))`, i.e. ids
`0..n-1`. mypy reported it as `range` where `Iterable[InstrumentId]` was expected, but
the type error was the smaller half of the problem: `SubscriptionPlanner`'s **protected
set** — the instruments that must never be dropped under degradation, spot and
front-expiry ATM — is compared by real instrument id and would have matched none of
them. Under a `DEGRADED` verdict the planner could therefore have dropped exactly the
instruments it exists to protect.

Now built from the shard's mapped ids. A universe with no mappings raises
`ConfigurationError` rather than inventing any.
*Test:* `TestCapacityUsesRealInstrumentIds` captures the request at the planner boundary
and asserts the real ids (`4001`, `4002`) arrive.

### A2 — The V3 client could not actually be consumed by the provider
`UpstoxV3FeedClient` was passed where `UpstoxMarketDataProvider` expects a `WsTransport`
(`stream(vendor_keys, mode)`), but only had `frames(connection)`. mypy flagged the
incompatibility; at runtime it would have failed at the first frame.

Resolved by implementing the contract rather than casting: `UpstoxV3FeedClient.stream()`
now owns authorize → connect → subscribe → decode → yield, so the provider never sees a
socket. It yields `V3Frame`, whose `provider_event_id` and `channel_sequence` are
annotated **`None`**, making it a type error to ever populate them.
*Tests:* `TestInterfaceConformance` (4 tests).

### A3 — The REST client did not satisfy the authorizer contract
`build_v3_feed_client` took `rest: object`, hiding that `UpstoxRestClient` had no
`get_json`. Added, budgeted against `DISCOVERY` so the authorize call cannot contend
invisibly with option-chain polling.

### A4 — `RETURNING` could have been dropped silently
`postgres.py` rebound one variable across `Insert` → `ReturningInsert`, so the
statement's static type was the pre-RETURNING one. Since `RETURNING id` is what makes
the inserted count exact rather than an estimate — and therefore what makes the
duplicate metric real — losing it would have been invisible. Two names now.

### A5 — Unnarrowed REST response bodies
`rest.py` returned `response.json()` directly (`Any`). A non-object body would have
flowed into the parsers as though it were a response envelope and failed much later with
a confusing shape error. Now narrowed with an explicit `isinstance` check that raises
`UpstoxRestError` naming the path and the actual type.

### A6 — Unreachable stop check in the V2 reconnect loop
`if self._stopping: break` was unreachable under `warn_unreachable`: the enclosing
`while not self._stopping` narrows the attribute to `False`, and mypy does not model the
`stop()` call that mutates it from another task. The narrowing was correct about the
code as written — a plain bool is the wrong primitive for cross-task signalling.
Replaced with `asyncio.Event`, which is what the situation actually calls for and which
removes the false narrowing rather than suppressing it.

### A7 — Untyped `Callable[..., Any]` boundaries in the V2 client
`authorize` is now `Callable[[], Awaitable[str]]` and `connect_factory`
`Callable[[], Awaitable[Any]]`, removing the `no-any-return` at the authorize call.

### A8 — Untyped Redis constructor call
`redis.asyncio.from_url` is untyped, producing `no-untyped-call`. A `_RedisLike`
Protocol declaring the two operations the probe uses (`ping`, `aclose`) now types the
boundary. No blanket ignore, and no pretence of knowing the rest of the client surface.

### A9 — `Column[object]` does not type a heterogeneous column list
`_identity_columns()` was annotated `list[sa.Column[object]]`. `Column` is generic and
**invariant** in its Python type, so `Column("id", BigInteger)` is a `Column[int]` and
is not assignable to `Column[object]` — 14 errors. Changed to `list[sa.Column[Any]]`,
the annotation SQLAlchemy itself uses for mixed collections. **No column type and no
runtime behaviour changed**; the migration and schema are byte-identical.

### A10 — The recorded-fixture directory did not exist in a fresh clone
`tests/fixtures/recorded/upstox_v3/` was empty, and **git does not track empty
directories**. It existed on the implementer's disk and not on the verifier's, which is
precisely why the contract test failed there and passed here. A tracked
`upstox_v3/README.md` now carries the manifest schema and required capture set, so the
directory travels with the repository. No `.bin` was fabricated.

---

## B. Test-environment assumptions fixed

These were **tests asserting properties of the machine**, not of this system. Both
passed in a bare sandbox and failed on a provisioned verifier — the clearest possible
signal that the assertion was pointed at the wrong thing.

### B1 — Preflight tests depended on ambient PostgreSQL and Redis
`test_preflight_fails_when_a_dependency_is_unreachable` asserted `"redis" in message`.
On the verifier, PostgreSQL was reachable and rejected the credentials for user `u`, so
the message contained only the PostgreSQL failure.

**Inspection first, as instructed.** `IngestorRuntime.preflight()` runs all three probes
and collects every failure — the contract is **aggregate, not fail-fast** — and that is
correct: an operator fixing a deployment should see the whole list in one restart.
Production behaviour was therefore *not* changed.

A `DependencyProbes` bundle was added to `observability/readiness.py`, defaulting to the
real probes, so tests can make each outcome deterministic. The suite now asserts the
contract explicitly:

* one failure → that dependency named;
* **all three** failures → all three named;
* a later probe still runs after an earlier one fails (recorded call order), so
  aggregation is proven rather than inferred from message formatting;
* all healthy → preflight returns (a positive case that did not previously exist);
* the DSN password never appears in a failure message — asserted against the *real*
  probe too, since injected probes cannot prove that.

No test now depends on a password, on whether PostgreSQL or Redis is installed, or on
which packages exist on the verifier's machine.

### B2 — The missing-package probe test assumed packages were absent
`check_runtime_dependencies("ingestor")` correctly returned `ok=True` on the verifier.
`check_runtime_dependencies` now accepts an optional `requirements` mapping — production
always uses `ROLE_REQUIREMENTS` — so the test supplies a sentinel package that genuinely
does not exist, and first asserts that it does not. Coverage is unchanged and stronger:
detection, the name appearing in the result, readiness becoming unsuccessful, *and* a
positive case proving the probe is not simply always failing.

### B3 — The synthetic-payload guard flagged its own documentation
The guard searched every `.md`, `.json` and `.py` under `recorded/` for the word
SYNTHETIC — including the README whose job is to explain the recorded-versus-synthetic
rule. Scoped to payload files (`.json`, `.bin`).

---

## C. Genuinely unverified provider behaviour

Unchanged by this remediation, and deliberately so.

* **The V3 Protobuf decoder is still absent.** `ProtoDecoderUnavailable` remains the
  fail-closed boundary. No `.proto` invented, no field numbers guessed, no JSON
  fallback. A wrong field number does not raise — it decodes a plausible value for the
  wrong field into durable market data.
* **`provider_event_id` and `channel_sequence` remain absent and are never synthesized.**
  Identity is the OI Pulse-derived digest, labelled as such (AD-30).
* **Provider-sequence gap detection does not exist for this feed.** Connectivity,
  heartbeat budget and REST recovery are what detect and repair loss.
* **UNVERIFIED wire formats,** each isolated in one function: `AUTHORIZE_PATH`, the
  authorize response field path, and the subscribe/unsubscribe message shape.
* **A-3** (provider timestamps on V3 frames) remains open pending real captures.

---

## D. External requirements still pending

Measured in this environment at the start of this remediation:

| Capability | Probe | Result |
|---|---|---|
| `pypi.org:443` | TCP connect | `gaierror -3`, name resolution failure |
| `api.upstox.com:443` | TCP connect | `gaierror -3`, name resolution failure |
| PostgreSQL `localhost:5432` | TCP connect | `ConnectionRefusedError` |
| Redis `localhost:6379` | TCP connect | `ConnectionRefusedError` |
| `pytest`, `mypy`, `sqlalchemy`, `asyncpg`, `redis`, `fastapi`, `websockets`, `alembic`, `protobuf`, `import-linter` | `find_spec` | all MISSING |
| `UPSTOX_ACCESS_TOKEN`, `DATABASE_URL`, `REDIS_URL` | environment | all unset |

Still required externally:

1. `mypy --strict oipulse --show-error-codes` — **the authoritative check. All 23 reported errors were fixed at the root, but mypy could not be run here to confirm zero remain.**
2. `pytest -q` and `lint-imports`.
3. The PostgreSQL migration integration suite (5 tests, skipped here).
4. The official Upstox V3 `.proto` plus a Protobuf runtime.
5. Sanitized real V3 frames matching the seven required captures.
6. Credentials and a live NSE session for reconnect, recovery and the market-session soak.

---

## Validation actually executed in this environment

| Check | Command | Result |
|---|---|---|
| Unit + structural suite | `python3 -m unittest discover -s tests -t .` | **247 passed, 6 skipped** |
| Lint | `ruff check oipulse tools tests` | pass |
| Format | `ruff format --check oipulse tools tests` | pass, 76 files |
| Byte-compile | `python3 -m compileall -q oipulse tools tests` | pass |
| Clock guard | `tools/check_clock_access.py oipulse` | pass |
| Import boundaries | `tools/check_import_boundaries.py` | pass, 4 contracts |
| Temporal repository | `tools/check_temporal_repository.py oipulse` | pass |
| Migration chain | `tools/check_migration_chain.py` | pass, 4 revisions, one head |
| Schema parity | `tools/check_schema_parity.py` | pass, 6 kinds / 19 tables |
| Migration ordering | `tools/check_migration_order.py` | pass, 2 revisions |
| Strict-typing subset | `tools/check_typing_strict.py` | pass, 50 files |
| `# type: ignore` in `oipulse/` | grep | **0** (2 textual matches are prose in docstrings) |

`pytest`, `mypy --strict`, `lint-imports`, `alembic upgrade head` and every live Upstox
operation were **not run**, for the reasons in section D. Nothing above is claimed on
their behalf.

**The 6 skips are reported gaps, not passes:** 5 PostgreSQL migration integration tests,
and the outstanding recorded-capture set, which skips with an explicit OUTSTANDING
message naming its blocker. No test was skipped or weakened to obtain a green result.

---

## Phase gate

### Phase 1 — READY FOR EXTERNAL RE-VERIFICATION

| Requirement | Status |
|---|---|
| All tests pass | 247/247 here; the two reported failures were environment assumptions and are fixed (B1, B2) |
| mypy passes | all 23 reported errors fixed at the root; **needs confirmation by running mypy** |
| lint-imports | pending |
| Migrations | structurally verified; PostgreSQL application pending |
| API / ingestor entrypoints | both dispatch; the ingestor refuses correctly without a V3 decoder |
| Guards | all seven pass |

### Phase 2 — IMPLEMENTATION READY / EXTERNAL VERIFICATION PENDING

Blocking items are unchanged and all sit in categories C and D: the Protobuf decoder,
recorded V3 frames, live reconnect and recovery, PostgreSQL persistence, and the
NSE-session soak. **Phase 2 is not PASS.**
