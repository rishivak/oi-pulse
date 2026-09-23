# OI Pulse v2 — Claude Code Operating Contract

## Mission

Implement the approved OI Pulse v2 architecture exactly as defined in `docs/design/00–20`.

This is an implementation of an approved architecture, not an architecture redesign.

## Mandatory operating rules

1. Implement only the current phase explicitly authorized by the user.
2. Never start the next phase automatically.
3. At the end of every phase:

   * run the phase gate,
   * run relevant tests,
   * run regression tests,
   * inspect the resulting behavior,
   * produce a verification report,
   * stop.
4. Do not declare a phase complete merely because tests pass.
5. Do not declare a phase complete from string-presence checks alone.
6. Verify invariants semantically across the affected modules and documents.
7. Preserve deterministic, point-in-time behavior.
8. Do not introduce look-ahead bias.
9. Do not silently change architectural decisions.
10. Do not add a new technology, provider, service, queue, database, or major dependency unless explicitly authorized.
11. Upstox remains the sole external market-data/broker provider.
12. Keep the modular-monolith architecture.
13. PostgreSQL remains the source of durable truth.
14. Redis remains the supporting coordination/live-read mechanism where already designed.
15. Do not introduce ML.
16. Do not enable live trading.
17. Do not send real broker orders.
18. Do not expose broker credentials or access tokens to the frontend.
19. Do not delete historical market data.
20. Do not perform destructive database operations such as DROP/TRUNCATE unless explicitly authorized.
21. Do not rewrite functioning modules merely for stylistic reasons.
22. Prefer incremental refactoring over broad rewrites.
23. Preserve existing working behavior unless the approved architecture explicitly changes it.

## Git rules

Before implementation:

* verify working tree status,
* create a dedicated implementation branch,
* record the current commit.

After every completed phase:

* ensure tests and gates pass,
* review changed files,
* create one clean commit for the phase,
* do not mix unrelated cleanup into the phase commit.

Never:

* force-push,
* reset another person's work,
* rewrite history,
* delete branches,
* modify production deployment,
  without explicit authorization.

## Trading safety

The following remain disabled throughout Phases 1–9:

* live order submission
* live order modification
* live order cancellation
* automatic trading
* real-money strategy execution

Phase 10 may implement the broker adapter and reconciliation path, but live execution remains disabled behind the documented three gates.

## Data correctness

The following are architectural invariants:

MarketState identity:

(underlying_id, market_time, knowledge_horizon, build_context_id)

Determinism:

same observations + same K + same BuildContext = same MarketState

Availability:

available_at >= lookback_end
available_at >= latest required input availability
available_at >= computed_at

Point-in-time correctness:

* observed_at describes when the fact was true.
* ingested_at describes when OI Pulse knew it.
* available_at describes when a derived value became consumable.
* decision_time is an action/query concept, not an additional persisted time dimension.

Never use observed_at alone to establish feature availability.

## Phase execution protocol

For every phase:

1. Read the complete phase definition.
2. Read all dependencies and previous-phase gates.
3. Inspect the existing implementation before editing.
4. State the implementation plan.
5. Implement the smallest coherent change.
6. Run focused tests.
7. Run regression tests.
8. Run static/type/lint checks applicable to the repository.
9. Run the documented phase gate.
10. Perform semantic verification of the architectural invariants.
11. Produce:

    * files changed,
    * behavior changed,
    * tests run,
    * gate results,
    * known limitations,
    * remaining risks.
12. Stop and wait for explicit authorization for the next phase.

## Completion standard

A phase is COMPLETE only when:

* implementation exists,
* required tests exist,
* tests pass,
* phase gate passes,
* no architectural invariant is violated,
* no unresolved blocker remains,
* evidence is documented,
* Git checkpoint is clean.

If any of these fail, report the failure and continue fixing the CURRENT phase only.

Do not move forward simply because the implementation is "good enough".

## When uncertain

Do not invent requirements.

Use, in order:

1. `docs/design/00–20`
2. existing code and tests
3. existing ADRs
4. explicit user instructions

If ambiguity remains and it changes architecture or behavior materially, stop before making the architectural decision.

## Documentation discipline

When implementation changes behavior covered by the design documents:

* update the relevant design document,
* record the reason,
* preserve the architecture freeze/version trail,
* do not silently let code diverge from the design.

## Final principle

The system is built as:

data → state → analytics → signals → research → replay → paper trading → risk → execution → portfolio → terminal

Do not skip layers.
