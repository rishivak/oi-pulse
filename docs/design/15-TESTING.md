# OI Pulse v2 — Testing Strategy

> **Deliverable U.** Testing is a core feature, not a phase. Several tests here are
> defined by what they must **reject** — those are the ones that keep the system honest.

---

## 1. Layers

| Layer | Scope | Speed |
|---|---|---|
| Unit | pure functions: features, rules, state assembly, risk, P&L | ms |
| Property | invariants: idempotency, ordering, aggregation, monotonicity | ms |
| Contract | module boundaries, protocol conformance | ms |
| Integration | Postgres, Redis, Upstox adapter, event pipeline | seconds |
| Replay | historical reconstruction, determinism | seconds–minutes |
| Backtest | look-ahead, reproducibility | minutes |
| Execution | order state machine, partial fills, rejection, **UNKNOWN** | ms |
| Risk | limit enforcement, non-bypassability | ms |

Unit and property tests dominate by count, which is achievable only because analytics,
rules, risk and state assembly are pure (`07-ANALYTICS.md` §1).

---

## 2. Tests that must FAIL

The most important section. Each asserts that an unsafe operation is **refused**.

### 2.1 Look-ahead is refused
```python
def test_strategy_cannot_see_future_observation():
    obs = observation(instrument=NIFTY_25000_CE, observed_at="11:40:00", ingested_at="11:44:00")
    ctx = strategy_context(at="11:42:00", mode=KNOWLEDGE)
    assert obs not in ctx.observations  # arrived after the decision point
```

### 2.2 A feature before `available_at` is refused
The canonical fixture from `05-DATA_LIFECYCLE_PIT.md` §2:
```python
def test_feature_not_available_before_available_at():
    # observed 11:40:00 · ingested 11:40:01 · computed 11:40:02 · available 11:40:03
    ctx = strategy_context(at="11:40:02")
    with pytest.raises(FeatureAccessError):
        ctx.features.get("SOME_FEATURE", version=1)
```

### 2.3 The window-completion rule
From `09-RESEARCH.md` §2:
```python
def test_15m_window_feature_unavailable_mid_window():
    # 15-minute OI migration, window 11:30:00 → 11:45:00, available 11:45:02
    with pytest.raises(FeatureAccessError):
        strategy_context(at="11:42:00").features.get("PUT_OI_MIGRATION", version=2)
    assert strategy_context(at="11:45:02").features.get("PUT_OI_MIGRATION", version=2)
```

### 2.4 An order without an approved risk decision is refused
```python
def test_order_requires_approved_risk_decision():
    intent = make_intent()
    with pytest.raises(IntegrityError):  # DB constraint, not app logic
        insert_order(intent_id=intent.id, authorizing_risk_decision_id=None)


def test_order_cannot_use_another_intents_approval():
    a, b = make_intent(), make_intent()
    approval_b = approve(b)
    with pytest.raises(IntegrityError):
        insert_order(intent_id=a.id, authorizing_risk_decision_id=approval_b.id)


def test_no_code_path_reaches_broker_without_risk():
    # static call-graph assertion over trading/*
    assert not call_paths_bypassing("trading.risk", to="trading.brokers")
```

### 2.5 A terminal state is never assumed after a lost acknowledgement
```python
def test_lost_ack_does_not_become_rejected():
    order = submit_order(broker=FailingAckBroker())
    assert order.state == OrderState.UNKNOWN
    assert order.state not in TERMINAL_STATES


def test_no_resubmit_from_unknown():
    order = order_in_state(OrderState.UNKNOWN)
    with pytest.raises(UnsafeRetryError):
        order_manager.retry(order)
```

### 2.6 Trading on unreliable data is refused
```python
def test_risk_rejects_unreliable_state():
    decision = risk.evaluate(intent, state=state(quality=UNRELIABLE))
    assert decision.decision == RiskDecision.REJECTED
    assert "stale_data" in decision.reasons
```

### 2.7 Backfill does not contaminate earlier knowledge
```python
def test_backfilled_oi_invisible_before_backfill_ran():
    backfill(trade_date="2026-01-15", ingested_at="2026-03-01T02:00:00Z")
    rows = repo.knowledge_at("2026-01-15T11:00:00Z").observations(...)
    assert rows == []  # we did not have it then
```

### 2.8 Corrections do not rewrite history
```python
def test_correction_does_not_change_earlier_belief():
    original = observation(value=100, observed_at="11:40", ingested_at="11:40")
    corrected = correction_of(original, value=105, ingested_at="13:00")
    assert repo.knowledge_at("12:00").value_of(instrument) == 100
    assert repo.knowledge_at("14:00").value_of(instrument) == 105


def test_market_truth_requires_explicit_knowledge_horizon():
    # the two readings of "truth at 11:40" must be distinguishable
    assert repo.market_truth_at("11:40", knowledge_as_of="11:45").value == 100
    assert repo.market_truth_at("11:40", knowledge_as_of="14:00").value == 105
    with pytest.raises(TypeError):
        repo.market_truth_at("11:40")  # no defaulted knowledge horizon
```

### 2.9 State identity includes the knowledge horizon
```python
def test_same_market_time_different_K_are_different_states():
    a = build_state("NIFTY", T="11:42", K="11:42")
    b = build_state("NIFTY", T="11:42", K="11:50")  # late data arrived between
    assert a.identity != b.identity
    assert a != b  # not merely a cache key difference


def test_checkpoint_unique_on_full_tuple():
    persist_checkpoint(u, T="11:42", K="11:42", ctx=CTX)
    persist_checkpoint(u, T="11:42", K="11:50", ctx=CTX)  # must NOT conflict
    assert count_checkpoints(u, T="11:42") == 2
```

### 2.10 A later-K checkpoint cannot satisfy an earlier-K request
```python
def test_later_knowledge_checkpoint_is_not_substituted():
    persist_checkpoint(u, T="11:42", K="11:50", ctx=CTX)
    state = get_state(u, market_time="11:42", knowledge_time="11:43")
    assert state.knowledge_horizon == "11:43"  # reconstructed, not reused
    assert metrics.checkpoint_rebuild_total(reason="knowledge_mismatch") == 1


def test_earlier_knowledge_checkpoint_is_not_substituted_either():
    persist_checkpoint(u, T="11:42", K="11:40", ctx=CTX)
    state = get_state(u, market_time="11:42", knowledge_time="11:43")
    assert state.knowledge_horizon == "11:43"  # exact match only, never nearest
```

### 2.11 A different build context is distinguishable
```python
def test_staleness_policy_change_yields_new_build_context():
    c1 = build_context(staleness_policy=POLICY_A)
    c2 = build_context(staleness_policy=POLICY_B)
    assert c1.id != c2.id  # content-addressable
    s1 = build_state(u, T, K, ctx=c1)
    s2 = build_state(u, T, K, ctx=c2)
    assert s1.identity != s2.identity  # never silently conflated
```

### 2.12 Availability follows input readiness, not market time
```python
def test_late_raw_input_delays_availability():
    # input observed 11:40, ingested 11:44; feature delay 2s
    mv = compute_feature("SOME_FEATURE", inputs=[late_observation])
    assert mv.available_at == "11:44:02"  # NOT 11:40:02
    assert mv.available_at >= mv.computed_at


def test_dependency_availability_propagates():
    # derived B depends on derived A; A available 11:46:00, B delay 1s
    b = compute_feature("B", dependencies=[a_available_at("11:46:00")])
    assert b.available_at >= "11:46:01"  # cannot precede its dependency
```

### 2.13 Aggregate events are applied in order
```python
def test_event_2_not_consumed_before_event_1():
    publish(order_event(aggregate_id=o, sequence=2))
    publish(order_event(aggregate_id=o, sequence=1))
    dispatch_all()
    assert applied_order(o) == [1, 2]  # sequence, not arrival


def test_out_of_sequence_event_is_deferred_not_dropped():
    publish(order_event(aggregate_id=o, sequence=2))  # 1 still missing
    dispatch_all()
    assert status_of(o, sequence=2) == "pending"  # deferred
    assert metrics.aggregate_ordering_deferrals_total >= 1
```

### 2.14 Inbox applies a business mutation exactly once
```python
def test_crash_between_mutation_and_ack_does_not_double_apply():
    # the transactional inbox commits marker + mutation together
    with crash_after_mutation_before_commit():
        with pytest.raises(SimulatedCrash):
            consume(position_event, subscriber="portfolio")
    assert position_qty() == 0  # transaction rolled back whole

    consume(position_event, subscriber="portfolio")  # redelivery
    consume(position_event, subscriber="portfolio")  # and again
    assert position_qty() == 100  # applied exactly once
    assert metrics.consumer_inbox_duplicate_total >= 1
```

### 2.15 Replay is deterministic across a feed reconnect
```python
def test_replay_order_stable_across_reconnect():
    # two feed sessions, overlapping observed_at, channel_sequence resets to 0
    run_a = replay(period).observation_order()
    run_b = replay(period).observation_order()
    assert run_a == run_b  # total order, not arbitrary


def test_ordering_deterministic_without_provider_sequence():
    # assumption A-1 unmet: no provider_event_id, no channel_sequence
    assert replay(period).observation_order() == replay(period).observation_order()
```

### 2.16 Historical OI cannot masquerade as full state
```python
def test_daily_oi_not_served_as_intraday_instant():
    backfill_daily_oi(date="2026-01-15")
    state = get_state(u, market_time="2026-01-15T11:23:17Z")
    assert state.quality.status in (DEGRADED, UNRELIABLE)
    assert state.coherence_mode == "STREAM_ONLY"
    assert state.legs_with_quotes == 0  # no LTP/bid/ask invented


def test_quote_dependent_feature_declines_over_backfill_only_period():
    with pytest.raises(QualityRequirementsUnmet):
        compute_feature("IV_SKEW", state=backfill_only_state)
```

---

## 3. Unit tests

**Every registered feature requires**, before registration is permitted
(`07-ANALYTICS.md` §7):

1. hand-computed expected value on a constructed `MarketState`;
2. declared units match output;
3. refuses to compute when `quality_requirements` are unmet;
4. `available_at` respects the window-completion rule;
5. determinism — same inputs → same `inputs_digest` → same value;
6. property tests where an invariant exists.

CI fails if a `@feature` decorator exists without this set. The registry is the
enforcement point.

Also unit-tested: state assembly under each coherence mode and staleness breach; signal
rules including contradicting-evidence production; risk limits individually; the order
state machine's permitted and forbidden transitions; P&L and attribution arithmetic
including the residual.

---

## 4. Property tests

| Property | Assertion |
|---|---|
| Ingestion idempotency | replaying an observation batch changes no row counts |
| Identity discrimination | two distinct events at one timestamp produce two rows |
| Ordering independence | shuffled arrival order yields identical final state |
| Aggregation consistency | per-strike exposure summed equals the reported total |
| PCR domain | `pcr >= 0`; undefined when call OI is zero, never infinite |
| Migration bounds | magnitude within the observed strike range |
| Position folding | position equals the fold over its fills, always |
| Fill conservation | summed fill quantity never exceeds order quantity |
| State monotonicity | a `MarketState` never incorporates `observed_at > T` or `ingested_at > K` |
| Identity completeness | states differing only in `K` or `build_context_id` are never equal |
| Availability monotonicity | `available_at >= max(lookback_end, inputs' availability, computed_at)` |
| Version isolation | `feature@v1` and `@v2` produce independent values |

Hypothesis-based, with generated states and event sequences.

---

## 5. Contract tests

- **Import-linter contracts** (`00-OVERVIEW.md` §5): `analytics/*` imports only
  `marketstate` and `core`; nothing imports `api/`; `trading/risk` imports neither
  `trading/oms` nor any broker adapter.
- **No wall-clock access**: AST scan forbidding `datetime.now()` / `time.time()` outside
  `core.clock`.
- **Protocol conformance**: `PaperBrokerAdapter` and `UpstoxBrokerAdapter` satisfy
  `BrokerAdapter` identically, exercised by a shared adapter test suite.
- **No unbounded repository queries**: every observation-store method requires a time mode.

---

## 6. Integration tests

Real Postgres and Redis via containers; Upstox recorded/replayed at the HTTP and WS layer
(never live in CI).

Covered: ingestion → store → quality → state → analytics → signals → alerts end to end;
outbox delivery with subscriber idempotency under redelivery; WS gap → REST recovery;
REST/WS divergence detection; partition creation; migration expand/contract; reconciliation
rebuilding local state from broker truth.

---

## 7. Replay and determinism tests

```python
def test_replay_reproduces_live_event_stream():
    live = load_domain_events(period, run="live")
    replayed = replay(
        period, knowledge_horizon=LOCKSTEP, build_context_id=CTX, feature_versions=FV
    ).events
    assert replayed == live  # divergence ⇒ hidden non-determinism
```

Also: state reconstruction with and without checkpoints yields identical states; a
reconstructed state is byte-identical to the one built live; pruning checkpoints changes
no reconstructed result; replay never emits into the live outbox.

---

## 8. Backtest tests

- Reproducibility: identical config → identical result, including dataset `content_hash`.
- Strategy has no repository access (attribute assertion on `StrategyContext`).
- Outcome/forward-window data is unreachable from strategy context.
- Universe resolves as-of, so expired contracts appear as they were (survivorship).
- Assumption set is present on every result; a run over backfill-only data is flagged
  assumption-based.

---

## 9. Execution and risk tests

Order state machine: every permitted transition; every forbidden transition raises;
partial fill sequences; duplicate broker callbacks are idempotent on `broker_fill_id`;
rejection handling; cancellation races.

Reconciliation: acknowledgement loss; process restart mid-submit; missed broker events;
broker-side cancellation; manual broker-side change; and the acceptance criterion —
**wipe local order/position state, reconcile, and match the broker**.

Risk: each limit at boundary, just under, just over; kill switch halts immediately;
decisions append rather than replace; an order binds to the exact approving decision.

---

## 10. CI gates

```
lint · type-check · import-linter · clock-access scan
unit + property
contract
integration (containers)
replay determinism
backtest reproducibility
feature-registry completeness  ← every @feature has its six tests
security scan · dependency audit
```

Merge is blocked on all of them. The registry-completeness and clock-access gates are the
two that prevent slow erosion — without them, a feature lands without tests or a
`datetime.now()` creeps in, and determinism is quietly lost.

---

## 11. What is not tested

Stated rather than implied: no live Upstox calls in CI; no live-trading tests against a
real broker (the paper adapter and recorded fixtures stand in); market-impact modelling is
not validated because it is off by default; UI tests are limited to component and
integration level, with no full visual regression suite in the initial build.
