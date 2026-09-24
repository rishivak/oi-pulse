# Phase 1 and Phase 2 verification

Commit: `f7983f4e43b48e8fc238c058448ebab756ddf81a`
Subject: Fix the 23 reported mypy errors and three environment-dependent tests
Branch: `phase1-2-final-remediation`
Working tree before this report: clean
Date: 2026-09-24

`PHASE 1: NOT PASS`

`PHASE 2: NOT VERIFIED`

`mypy --strict oipulse --show-error-codes` failed. The remaining Phase 2 runtime checks were not started. No source file was modified. Phase 3 was not started.

## Environment

| Item | Value |
| --- | --- |
| Python | 3.12.14 |
| pytest | 8.3.3 |
| mypy | 1.13.0 |
| ruff | 0.16.5 |
| Interpreter | `/tmp/oipulse-phase1-venv` |

## First gate

### mypy

Exit code 1.

```text
oipulse/observability/readiness.py:145: error: Call to untyped function "from_url" in typed context  [no-untyped-call]
Found 1 error in 1 file (checked 50 source files)
```

The call is `aioredis.from_url(redis_url)` inside `_redis_client` at `oipulse/observability/readiness.py:145`. This is the real `mypy --strict` result, not a static approximation.

### pytest

Exit code 0.

```text
/private/tmp/oipulse-phase1-venv/lib/python3.12/site-packages/pytest_asyncio/plugin.py:208: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture caching scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to function scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session"

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
........................................................................ [ 29%]
........................................................................ [ 58%]
........................................................................ [ 87%]
...........s...................                                          [100%]
246 passed, 1 skipped in 14.55s
```

`pytest -q` does not name the skip. The only `skipTest` that is not behind `skipUnless` is `tests/phase2/test_upstox_v3.py::TestRecordedFixtureContract::test_the_required_capture_set_is_recorded_as_outstanding`, which skips when `tests/fixtures/recorded/upstox_v3` contains no `*.bin` captures.

### Other checks

| Command | Exit | Output |
| --- | --- | --- |
| `ruff check oipulse tools tests` | 0 | All checks passed! |
| `ruff format --check oipulse tools tests` | 0 | 76 files already formatted |
| `python -m compileall -q oipulse tools tests` | 0 | no output |
| `python tools/check_clock_access.py oipulse` | 0 | PASS  no wall-clock access outside oipulse/core/clock.py |
| `python tools/check_import_boundaries.py` | 0 | PASS  layer boundaries clean (4 contracts armed: analytics-is-pure, nothing-imports-api, risk-is-independent, core-is-dependency-free) |
| `python tools/check_temporal_repository.py oipulse` | 0 | PASS  every repository read accepts a temporal bound |

## Classification

| Item | Result |
| --- | --- |
| mypy | FAIL |
| pytest | PASS |
| Ruff | PASS |
| format | PASS |
| compile | PASS |
| architecture guards | PASS |
| PostgreSQL migration from legacy revision 002 | UNVERIFIED |
| Fresh-database migration | UNVERIFIED |
| Schema and identity-index inspection | UNVERIFIED |
| PostgreSQL concurrent idempotency | UNVERIFIED |
| API runtime | UNVERIFIED |
| Ingestor runtime | UNVERIFIED |
| Fresh Upstox OAuth token | UNVERIFIED |
| Upstox REST verification | UNVERIFIED |
| Official Upstox V3 protobuf dependency/schema | UNVERIFIED |
| Genuine V3 frames | UNVERIFIED |
| Decoder from the official schema | UNVERIFIED |
| Decode market_info and market-data frames | UNVERIFIED |
| Canonical normalization | UNVERIFIED |
| Multiple expiries/instruments | UNVERIFIED |
| Reconnect | UNVERIFIED |
| REST recovery after reconnect | UNVERIFIED |
| No false provider-sequence-gap claims | UNVERIFIED |
| NSE market-session soak | UNVERIFIED |
| Durable observation accumulation | UNVERIFIED |
| Metrics and sanitized real fixtures | UNVERIFIED |

Phase 1 requires mypy, pytest, Ruff, format, compile, and the architecture guards. mypy failed, so Phase 1 is not a pass.

Phase 2 was not executed. No protobuf field mapping, provider event id, channel sequence, V3 frame, or soak result was fabricated.

`PHASE 2: NOT VERIFIED`
