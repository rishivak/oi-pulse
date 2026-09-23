# OI Pulse v2 — Deployment Architecture

> **Deliverable T.** One codebase, several process roles, PostgreSQL + Redis.
> No microservices, no Kafka (brief §31).

---

## 1. Process roles

One image, one codebase; the role is selected at startup.

```bash
python -m platform.run --role api
python -m platform.run --role ingestor
python -m platform.run --role processor
python -m platform.run --role trader     # feature-flagged, off by default
python -m platform.run --role jobs
python -m platform.run --role all        # development convenience
```

| Role | Responsibility | Scaling | Stateful? |
|---|---|---|---|
| `api` | HTTP + SSE. Reads persisted state; may request deterministic state reconstruction. **No business analytics, strategy or trading logic.** | horizontal | no |
| `ingestor` | Upstox WS + REST polling → observation store | one per feed partition | WS session |
| `processor` | quality → state assembly → analytics → signals → alerts | one per underlying partition | in-memory live state |
| `trader` | strategies → risk → OMS → broker → reconciliation | **exactly one** | order authority |
| `jobs` | discovery, backfill, reconciliation sweeps, research batch, retention, partitions | one (leader-elected) | no |

### Why these seams

- **`api` performs no business logic** — no analytics, no strategy evaluation, no trading
  decisions. It may serve a `MarketState` reconstruction, which *is* computation but is
  deterministic, read-only and shares one implementation with the live path
  (`04-MARKETSTATE.md` §5). The earlier "never computes" wording was wrong:
  `GET /market/state` reconstructs whenever no matching checkpoint exists.

  **Where reconstruction executes:** in-process in `api`, bounded by a request timeout and
  a concurrency semaphore, so a burst of deep historical queries cannot starve ordinary
  reads. If measurement shows reconstruction latency affecting API p95, it moves behind a
  dedicated query role — a deployment change, not an architectural one, because the
  function is identical either way.
- **`ingestor` is isolated** — a WS consumer must never block on analytics. Its only job
  is getting bytes durably stored, fast.
- **`processor` holds live state in memory** — recoverable from the last checkpoint plus
  forward replay (`04-MARKETSTATE.md` §5), so it is restartable without data loss.
- **`trader` is singular** — it holds live order authority. It can be killed without
  affecting data collection, and it reconciles on startup before accepting intents.

### Environments

| Environment | Topology |
|---|---|
| Development | `--role all` in one container |
| Staging | `api` + one combined worker + `trader` disabled |
| Production | separate `api` (×N), `ingestor`, `processor`, `jobs`; `trader` only when explicitly enabled |

The code is identical across all three. Splitting is a deployment decision.

---

## 2. Partitioning and coordination

Single-instance roles are enforced, not assumed — the legacy system's "keep only one
worker per DB" comment in a docstring is not a control.

| Concern | Mechanism |
|---|---|
| Leader election (`jobs`, `trader`) | Redis lock with TTL and heartbeat renewal; loss of lock halts the role |
| `processor` sharding | by underlying; each shard holds a Redis lock on its key set |
| `ingestor` sharding | by instrument-key range; each owns its WS subscription set |
| Per-bucket safety | idempotent writes on observation identity — correctness does not depend on locks |
| Outbox contention | `FOR UPDATE SKIP LOCKED` |

Locks are an optimization against duplicate work. **Correctness rests on idempotent
identity-keyed writes**, so a lock failure degrades efficiency rather than data integrity.

---

## 3. Configuration

`pydantic-settings`, environment-sourced, validated at startup. The process **refuses to
start** on invalid configuration rather than failing later.

| Group | Keys |
|---|---|
| Core | `APP_ENV`, `ROLE`, `LOG_LEVEL`, `INSTANCE_ID` |
| Database | `DATABASE_URL`, pool sizes, statement timeout |
| Redis | `REDIS_URL`, pool size |
| Upstox | client id/secret, redirect URI, API/WS bases, rate-limit budgets, **subscription capacity budgets** (`06` §5) |
| Security | `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET_KEY`, cookie policy |
| Market data | universe defaults, chain poll cadence, WS heartbeat budget |
| State | checkpoint cadence, **staleness budgets per category**, anchor max age, reconstruction concurrency limit |
| Analytics | enabled features, cadence overrides |
| Trading | `LIVE_TRADING_ENABLED` (default **false**), broker timeouts, reconciliation interval |
| Retention | per-tier policies, partition lead time |

Startup validation: no placeholder secrets; encryption key valid and correct length;
database reachable and migrated to head; Redis reachable; in production, docs disabled,
`reload` off, secure cookies on, and `LIVE_TRADING_ENABLED` requiring a second explicit
confirmation variable.

**Staleness budgets are configuration, carried in `staleness_policy_version` and therefore
part of `build_context_id`** (`04-MARKETSTATE.md` §1) — changing a budget yields a new
build context, so states built under different budgets never collide.

---

## 4. Database operations

**Migrations** — Alembic, run as an explicit job step, never implicitly on API startup
(the legacy `alembic upgrade head && python run_api.py` couples deploy to boot and races
across replicas). Forward-only; expand/contract for breaking changes so a rollback of code
does not require a rollback of schema.

**Partitions** — `jobs` creates daily/monthly partitions a month ahead and alerts if lead
time falls below a week. A missing partition must never be discovered by a failed insert.

**Connection pooling** — per role: `api` many short connections; `ingestor` few with large
batch writes; `processor` moderate; `trader` few, long-lived.

---

## 5. Containers and runtime

Multi-stage build; non-root user; a single image for all roles.

Corrections to the legacy setup, which shipped development servers to every environment:
- **Production runs `uvicorn` without `reload`** — `run_api.py` currently hardcodes
  `reload=True`.
- **Frontend runs `next build` + `next start`**, not `next dev`.
- Health and readiness endpoints wired to container probes.
- Graceful shutdown on SIGTERM: stop accepting work, drain in-flight, release locks, close
  WS cleanly.

---

## 6. Deployment sequence

```
1. build + test + import-linter contracts + security scan
2. run migrations (expand phase)      ← separate job, gated
3. deploy api                         ← rolling, backward-compatible
4. deploy ingestor                    ← brief WS gap; recorded as a data-quality issue
5. deploy processor                   ← resumes from last checkpoint
6. deploy jobs
7. deploy trader                      ← only if enabled; reconciles before accepting intents
8. verify: health, ingestion latency, checkpoint cadence, reconciliation clean
9. run migrations (contract phase)    ← next release
```

Ingestor deployment causes a brief WS gap. This is **recorded as a `RECONNECT_GAP`
quality issue and followed by REST recovery** (`06-UPSTOX_INTEGRATION.md` §6) — deploys
are visible in the data-quality record rather than silently degrading history. Deploys are
scheduled outside market hours wherever possible.

---

## 7. Backup and recovery

| Asset | Policy |
|---|---|
| Observations | continuous WAL archiving + daily base backup. **Irreplaceable** — an option chain cannot be re-fetched for a past instant |
| Decision artifacts | same; never pruned |
| Checkpoints, metrics | backed up, but **recomputable** — acceptable to restore lazily |
| Redis | not backed up; all contents reconstructable |

Recovery objectives: RPO ≈ WAL shipping interval for observations; RTO measured in hours.
Restore is **tested on a schedule**, not assumed — an untested backup is a hypothesis.

`processor` recovery needs no special handling: load the last checkpoint, replay
observations forward.

---

## 8. Scaling path

Measured triggers, not speculation:

| Signal | Response |
|---|---|
| API latency p95 breach | add `api` replicas |
| Ingestion lag (`ingested_at − observed_at`) p99 breach | shard `ingestor` by instrument range |
| Checkpoint cadence slipping | shard `processor` by underlying |
| Observation table growth | shorten partition interval; archive cold partitions |
| Outbox lag | increase dispatcher batch/concurrency |

`19-DECISIONS.md` records the thresholds at which a separate time-series store or a
message broker would be reconsidered. Until those are **measured**, Postgres plus Redis is
the answer.
