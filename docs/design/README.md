# OI Pulse v2 — Architecture Document Set

**Status: FROZEN** after three correction passes. Implementation may begin. Architecture
changes require an explicit unfreeze and a recorded decision in `19-DECISIONS.md`
(see `20-ARCHITECTURE_FREEZE.md` §14).

Start with `20-ARCHITECTURE_FREEZE.md` for the authoritative summary, then `00-OVERVIEW.md`.

| # | Document | Brief deliverable |
|---|---|---|
| [00](00-OVERVIEW.md) | System architecture + diagram | A, B |
| [01](01-DOMAIN_MODEL.md) | Domain model | C |
| [02](02-DATA_MODEL.md) | Data model + ERD | D |
| [03](03-EVENT_MODEL.md) | Event model | E |
| [04](04-MARKETSTATE.md) | MarketState + checkpointing | F |
| [05](05-DATA_LIFECYCLE_PIT.md) | Data lifecycle + bitemporal point-in-time model | G, H |
| [06](06-UPSTOX_INTEGRATION.md) | Upstox integration | I |
| [07](07-ANALYTICS.md) | Analytics + feature registry | J |
| [08](08-SIGNALS.md) | Signal architecture | K |
| [09](09-RESEARCH.md) | Research + leakage model | L |
| [10](10-REPLAY.md) | Replay + backtesting | M |
| [11](11-TRADING.md) | Paper trading, risk, execution, reconciliation, portfolio | N, O, P, Q |
| [12](12-API_SPEC.md) | API specification | R |
| [13](13-FRONTEND_IA.md) | Frontend information architecture | S |
| [14](14-DEPLOYMENT.md) | Deployment | T |
| [15](15-TESTING.md) | Testing strategy | U |
| [16](16-OBSERVABILITY.md) | Observability | V |
| [17](17-SECURITY.md) | Security model | W |
| [18](18-ROADMAP.md) | Implementation roadmap | X |
| [19](19-DECISIONS.md) | Decisions and trade-offs | Y |
| [20](20-ARCHITECTURE_FREEZE.md) | **Architecture freeze** | gate |

## Legacy audit (reference material)

The existing application's architecture is **not** carried forward. It was audited for
domain concepts, Upstox integration details and lessons learned. Those findings live one
level up in `docs/`: `ARCHITECTURE.md`, `DATA_FLOW.md`, `DOMAIN_MODEL.md`,
`API_INVENTORY.md`, `DATABASE_INVENTORY.md`, `TECHNICAL_DEBT.md` (29 findings, `D-01`–`D-29`).

## The governing question

> What exactly did OI Pulse know at that moment, what could it legitimately infer, and
> what decision was made from that information?

## The five commitments everything else follows from

1. **Market data is canonical and un-owned** — never scoped to a user.
2. **Four stored time dimensions** — `observed_at`, `ingested_at`, `computed_at`,
   `available_at` — with three enforced query modes (`market_truth_at`, `knowledge_at`,
   `tradable_information_at`). `decision_time` is an action parameter, not a fifth field.
   Availability derives from **input readiness**, never market time.
3. **Raw → derived → interpretation → signal** are never collapsed.
4. **Observations are the only source of historical truth**; states and metrics are
   recomputable materializations, identified by
   `(underlying, market_time, knowledge_horizon, build_context_id)`.
5. **Risk is non-bypassable** (decisions are an append-only sequence, each recording the
   state it evaluated), and broker state is authoritative for execution — a lost
   acknowledgement yields `UNKNOWN`, never an inferred outcome.
