# OI Pulse v2 — Signal Architecture

> **Deliverable K.** Signals are evidence-driven, lifecycle-tracked and explainable.
> Per the brief's §14 and §31: never an unexplained `BUY / SELL / 87% confidence`.

---

## 1. What a signal is — and is not

| A signal **is** | A signal **is not** |
|---|---|
| An inference about developing market structure | A trade recommendation |
| Backed by referenced evidence | A black-box score |
| Lifecycle-tracked with a lifespan | An instantaneous emission |
| Explicitly falsifiable via an invalidation condition | Always right |
| Accompanied by an explicit contradiction assessment | A one-sided argument |

A signal says *"put support structure is strengthening, here is why, here is what argues
against it, and here is what would prove it wrong."* It does not say *"buy calls."*
Converting a signal into a trade is a strategy's job, and it passes through risk
(`11-TRADING.md`).

---

## 2. The signal record

```
Signal
├── id, type, underlying_id, expiry_id
├── horizon                    the timescale the inference applies to
├── created_at, updated_at, available_at, expires_at
├── strength                   derived from weighted evidence, never free-floating
├── status                     FORMING | ACTIVE | CONFIRMED | INVALIDATED | EXPIRED | FADED
├── evidence[]                 SUPPORTING
├── contradiction_assessment   NONE_OBSERVED | Evidence[]  — always present
├── invalidation_condition     a declared, evaluable predicate
├── state_checkpoint_ref       what the system knew
└── provenance                 rule version, feature versions, inputs digest
```

### Evidence
```
Evidence
├── kind               SUPPORTING | CONTRADICTING
├── metric_value_ref   FK — the exact computed value
├── observation_refs[] the raw rows behind it
├── statement          human-readable rendering
├── weight             contribution to strength
└── observed_at
```

Evidence **references** metric and observation rows. It is not a rendered string with
numbers baked in. This is what makes the chain
`Signal → Evidence → MetricValue → MarketState → Observation` a foreign-key path rather
than a log search, and it is why explainability survives even when the UI changes.

### Contradiction assessment

Contradiction is **always assessed**, but a rule is never forced to produce some. Requiring
a non-empty list invites authors to manufacture a token objection to satisfy the schema,
which is worse than honestly reporting none.

```
contradiction_assessment:
    NONE_OBSERVED                  # assessed; no material contradiction found
  | [ Evidence, Evidence, ... ]    # assessed; these argue against
```

`NONE_OBSERVED` is a **positive claim** — *we looked and found nothing material* — not an
absent field. The distinction is auditable: a rule whose assessment is always
`NONE_OBSERVED` across many firings is flagged for review, because an assessment that
never finds anything is usually not assessing.

### Strength
Derived by a **declared, versioned function** of the weighted evidence set — typically a
normalized weighted sum with any contradicting evidence subtracted. The function is
registered like a feature, with its own version recorded on each signal.

There is deliberately **no field** in which to record an unexplained confidence number. If
strength cannot be derived from evidence, it cannot be set.

---

## 3. Lifecycle

```
                  conditions partially met
        ┌──────────────────────────────────► FORMING
        │                                       │ conditions fully met
        │                                       ▼
        │                                    ACTIVE ◄──────┐
        │                                    │  │  │       │ conditions persist,
        │        persistence criteria met    │  │  │       │ evidence updated
        │        ┌───────────────────────────┘  │  └───────┘
        │        ▼                              │
        │    CONFIRMED                          │
        │        │                              │
        │        │  invalidation_condition true │
        │        ├──────────────────────────────┤
        │        ▼                              ▼
        │   INVALIDATED                    EXPIRED (expires_at reached)
        │
        └── evidence decays below threshold ──► FADED
```

### Transition table — normative

The diagram above is illustrative; **this table is authoritative.** Any transition not
listed is invalid and raises.

| From | To | Trigger |
|---|---|---|
| — | `FORMING` | entry conditions partially met |
| `FORMING` | `ACTIVE` | all entry conditions met |
| `FORMING` | `FADED` | evidence decays below threshold before activation |
| `FORMING` | `EXPIRED` | `expires_at` reached while still forming |
| `ACTIVE` | `ACTIVE` | evidence updated, conditions still met (self-transition; appends history, updates `strength`) |
| `ACTIVE` | `CONFIRMED` | persistence criteria met — declared per rule as N consecutive evaluations with sustained strength |
| `ACTIVE` | `INVALIDATED` | `invalidation_condition` evaluates true |
| `ACTIVE` | `FADED` | evidence decays below threshold |
| `ACTIVE` | `EXPIRED` | `expires_at` reached |
| `CONFIRMED` | `CONFIRMED` | evidence updated (self-transition) |
| `CONFIRMED` | `INVALIDATED` | `invalidation_condition` true |
| `CONFIRMED` | `FADED` | evidence decays below threshold |
| `CONFIRMED` | `EXPIRED` | `expires_at` reached |

Terminal states: `INVALIDATED` · `EXPIRED` · `FADED`.

Two rules the diagram left ambiguous, now explicit:
- **`CONFIRMED → INVALIDATED` is permitted.** Confirmation is not absorbing; treating it as
  such would hide the cases most worth studying.
- **A signal never returns to `FORMING`, and never leaves a terminal state.** A recurrence
  is a **new signal** with its own id, so research counts two occurrences rather than one
  long-lived entity.

Every transition emits a domain event (`03-EVENT_MODEL.md` §3) and appends to the signal's
history. Nothing is overwritten; the sequence of states a signal passed through is itself
research material.

**Why lifecycle matters:** "what happens after a `PUT_SUPPORT_MIGRATION` signal?" is only
answerable if signals have a start, a duration and an outcome. An emission-only model can
answer "what happened after this instant?" but not "how do these resolve?"

---

## 4. Rule definition

Signal rules are declared, not hand-coded ad hoc:

```python
@signal_rule(
    signal_type="PUT_SUPPORT_MIGRATION",
    version=1,
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[
        ("PUT_OI_MIGRATION", 2),
        ("OI_WALL_PUT", 1),
        ("FUTURES_OI_CHANGE", 1),
        ("ATM_IV", 1),
    ],
    quality_requirements=["quality != UNRELIABLE"],
)
def put_support_migration(ctx: RuleContext) -> RuleOutcome:
    ...
```

- `requires_features` pins **exact feature versions**. A feature bumping to v3 does not
  silently change a rule's behaviour; the rule must opt in, and that is a versioned change
  to the rule.
- `evaluation_interval` throttles evaluation independently of checkpoint cadence.
- `quality_requirements` mean a rule does not fire on unreliable state.
- The rule returns supporting evidence **and** a contradiction assessment, which may be
  `NONE_OBSERVED`. The framework flags a rule whose assessment is *always* `NONE_OBSERVED`
  across many firings — that usually means the rule is not looking, rather than that the
  market is unanimous.

Rules are pure over `RuleContext` (a state plus its available metric values) — same
purity contract as analytics, same testability, same single implementation across live,
replay and backtest.

---

## 5. Worked example

```
SIGNAL   PUT_SUPPORT_MIGRATION            strength 0.68    status ACTIVE
         NIFTY · 2026-03-05 expiry · horizon 30m
         created 11:45:02 · available 11:45:02 · expires 12:15:02

SUPPORTING EVIDENCE
  ✓ Put OI migrated +100 strike points over 15m   [PUT_OI_MIGRATION@v2, w 0.30]
  ✓ Put wall moved 24,900 → 25,000                [OI_WALL_PUT@v1,      w 0.25]
  ✓ Futures OI +2.1% with price up                [FUTURES_OI_CHANGE@v1,w 0.20]
  ✓ Spot holding above VWAP                       [VWAP@v1,             w 0.10]

CONTRADICTING EVIDENCE
  ⚠ Call resistance at 25,200 unchanged           [OI_WALL_CALL@v1,     w -0.12]
  ⚠ ATM IV expanding +1.2%                        [IV_CHANGE@v1,        w -0.05]

INVALIDATION
  put wall returns below 24,950, or spot closes below VWAP for 10m

DATA QUALITY   OK · coverage 0.99 · coherence SNAPSHOT_ANCHORED
```

Every line is clickable through to the metric value, then to the state, then to the raw
observations — and every one names the feature version that produced it.

---

## 6. Signal catalogue (initial)

Positioning: `PUT_SUPPORT_MIGRATION` · `CALL_RESISTANCE_MIGRATION` · `OI_EXPANSION` ·
`OI_UNWINDING` · `POSITIONING_SHIFT` · `CONCENTRATION_BUILDING`.

Volatility: `VOLATILITY_EXPANSION` · `VOLATILITY_CONTRACTION` · `SKEW_STEEPENING` ·
`TERM_STRUCTURE_INVERSION`.

Structure: `BREAKOUT_CONTEXT` · `BREAKDOWN_CONTEXT` · `GAMMA_CONCENTRATION_SHIFT` ·
`REGIME_TRANSITION`.

Cross-market: `FUTURES_OPTIONS_DIVERGENCE` · `BASIS_ANOMALY`.

All are **observations about market structure**. None is named for a trade direction, and
that naming discipline is deliberate — it keeps the layer honest.

---

## 7. Anti-patterns explicitly prevented

| Anti-pattern | Prevention |
|---|---|
| Unexplained confidence score | No field for it; strength derives from weighted evidence |
| Contradiction never assessed | `contradiction_assessment` is mandatory; `NONE_OBSERVED` is an explicit finding, and a rule that always returns it is flagged |
| Signal as trade recommendation | Naming discipline; strategies are a separate layer behind risk |
| Silent behaviour change from a formula edit | Rules pin exact feature versions |
| Signal on bad data | `quality_requirements`; no firing on `UNRELIABLE` |
| Unfalsifiable signal | `invalidation_condition` is mandatory |
| Evidence that cannot be traced | Evidence references rows, not rendered strings |
| Hindsight-inflated performance | Signals carry `available_at`; research uses it |

---

## 8. Signals in research

Because signals are entities with lifespans and typed evidence, research can ask:

- What is the forward-return distribution after `PUT_SUPPORT_MIGRATION` reaches `ACTIVE`?
- Does `CONFIRMED` outperform `ACTIVE`, and is the confirmation delay worth the missed move?
- Which evidence items actually carry the predictive weight, and which are decorative?
- How does behaviour differ by regime, expiry proximity and time of day?
- What fraction reach `INVALIDATED`, and how quickly?

Question three is the one that improves the system over time, and it is only answerable
because evidence is stored individually and weighted rather than collapsed into a score.
Detail in `09-RESEARCH.md`.
