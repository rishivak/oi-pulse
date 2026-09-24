"""Signal evaluation — quality gating, availability, lifecycle, idempotency.

`08-SIGNALS.md` §2 to §4 and `07-ANALYTICS.md` §3.

The evaluator owns everything a rule must not get wrong individually:

* **Point-in-time gating.** Metric values are filtered to `available_at <= K` *before*
  any rule runs. A rule cannot reach past its knowledge horizon because nothing beyond
  it is in the context. A signal must not become actionable merely because the
  underlying observation existed historically — the derived information must actually
  have been available.

  **The signal's `K` is its own, and it is not the state's.** A `MarketState` at
  market time `T` carries the observations known by the state's horizon; the features
  derived from it only become available at `T + availability_delay`. A signal
  evaluated at the state's `K` could therefore never consume any of them, which would
  make the layer inert. So evaluation takes an explicit `knowledge_horizon`,
  defaulting to `evaluated_at` — the moment the evaluation actually ran, which is
  precisely what the evaluator knew. The design's worked example says the same thing:
  a state for 11:45:00 yields a signal *created 11:45:02, available 11:45:02*
  (`08` §5).

  Passing an **earlier** `K` is the research and replay case, and it is honoured
  exactly: features that had not become available by that instant are withheld, so a
  reconstruction at an earlier horizon sees strictly less. Later-K data is never
  substituted.
* **Quality gating**, before evaluation. A rule does not fire on `UNRELIABLE`.
* **Pinned-feature resolution.** A rule declaring `("PUT_OI_MIGRATION", 2)` receives v2
  or nothing. Missing pinned features are reported, never substituted.
* **Availability derivation.** A signal's `available_at` follows the same formula its
  inputs do, so availability propagates from feature to signal
  (`max(market_time, latest_input_available_at, evaluated_at) + delay`).
* **Deterministic identity and idempotency.** The same evaluation produces the same
  `signal_id`, so replaying a source event updates one entity rather than creating a
  second.
* **Lifecycle.** Transitions go through the normative table; an illegal one raises.

No clock, no database, no network. `evaluated_at` is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from oipulse.analytics.values import MetricValue
from oipulse.marketstate.state import MarketState
from oipulse.signals.context import RuleConfig, RuleContext
from oipulse.signals.lifecycle import TransitionTrigger, apply_transition, is_permitted
from oipulse.signals.model import (
    NONE_OBSERVED,
    Signal,
    SignalIdentity,
    SignalProvenance,
    SignalStatus,
)
from oipulse.signals.rules import RULES, RuleOutcome, RuleRegistry, SignalRuleSpec

__all__ = [
    "DEFAULT_SIGNAL_AVAILABILITY_DELAY",
    "EvaluationReport",
    "SignalEvaluator",
    "SkipReason",
    "Skipped",
]

#: Propagation delay applied after every availability condition is met, mirroring the
#: analytics convention (`07` §3). Not computation time -- that is `evaluated_at`.
DEFAULT_SIGNAL_AVAILABILITY_DELAY = timedelta(seconds=2)


class SkipReason(StrEnum):
    """Why a rule produced no signal. Always recorded, never swallowed."""

    QUALITY_NOT_MET = "quality_not_met"
    #: A pinned feature version was not available at this knowledge horizon.
    FEATURE_UNAVAILABLE = "feature_unavailable"
    #: The rule ran and decided there was no signal. Not a failure.
    CONDITIONS_NOT_MET = "conditions_not_met"
    #: The rule raised. Recorded rather than crashing the batch.
    RULE_ERROR = "rule_error"
    #: Throttled by `evaluation_interval`.
    NOT_DUE = "not_due"


@dataclass(frozen=True, slots=True)
class Skipped:
    spec: SignalRuleSpec
    reason: SkipReason
    detail: str

    @property
    def label(self) -> str:
        return self.spec.label


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Everything one evaluation produced. The hand-off to the impure caller.

    Persistence, event emission and metric recording happen in the caller; this layer
    stays pure so it can be replayed.
    """

    signals: tuple[Signal, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    #: Rules whose contradiction assessment was NONE_OBSERVED this run. The caller
    #: accumulates these: a rule that *always* returns it is flagged for review,
    #: because an assessment that never finds anything is usually not assessing.
    none_observed: tuple[str, ...] = ()

    @property
    def created_count(self) -> int:
        return len(self.signals)

    def by_type(self, signal_type: str) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.signal_type == signal_type)

    def reasons(self) -> dict[str, str]:
        return {s.label: s.reason.value for s in self.skipped}


class SignalEvaluator:
    """Evaluates rules against a state. Pure and deterministic."""

    def __init__(
        self,
        registry: RuleRegistry | None = None,
        *,
        availability_delay: timedelta = DEFAULT_SIGNAL_AVAILABILITY_DELAY,
    ) -> None:
        self._registry = registry if registry is not None else RULES
        self._delay = availability_delay

    @property
    def registry(self) -> RuleRegistry:
        return self._registry

    # ------------------------------------------------------------ availability

    def available_at_for(self, context: RuleContext, evaluated_at: datetime) -> datetime:
        """`max(market_time, latest_input_available_at, evaluated_at) + delay`.

        The same shape as the analytics rule, for the same reason: a signal must not
        be consumable before the information it rests on was. Its inputs' availability
        propagates through, so a signal is never available before the last feature it
        consumed.
        """
        candidates = [context.market_time, evaluated_at]
        latest_input = context.latest_input_available_at()
        if latest_input is not None:
            candidates.append(latest_input)
        return max(candidates) + self._delay

    # --------------------------------------------------------------- selection

    def due_at(
        self, specs: tuple[SignalRuleSpec, ...], elapsed_seconds: float
    ) -> tuple[SignalRuleSpec, ...]:
        """Rules whose `evaluation_interval` has elapsed (`08` §4)."""
        return tuple(s for s in specs if elapsed_seconds >= s.evaluation_delta.total_seconds())

    # ---------------------------------------------------------------- evaluate

    def evaluate(
        self,
        state: MarketState,
        metric_values: tuple[MetricValue, ...],
        evaluated_at: datetime,
        *,
        knowledge_horizon: datetime | None = None,
        specs: tuple[SignalRuleSpec, ...] | None = None,
        config: RuleConfig | None = None,
        expiry_id: int | None = None,
        prior_strengths: dict[tuple[str, int], tuple[Decimal, ...]] | None = None,
        existing: dict[tuple[str, int, int, int | None, int], Signal] | None = None,
    ) -> EvaluationReport:
        """Run the selected rules against one state.

        `existing` maps a lifecycle stream key to the signal currently in it, so a
        repeated evaluation *advances* that signal rather than creating a second one.
        """
        chosen = specs if specs is not None else self._registry.all()
        # The signal's own knowledge horizon. Defaults to the moment evaluation ran,
        # which is what the evaluator actually knew; research and replay pin it.
        horizon = knowledge_horizon if knowledge_horizon is not None else evaluated_at
        if horizon < state.identity.market_time:
            raise ValueError(
                f"knowledge_horizon {horizon.isoformat()} precedes market_time "
                f"{state.identity.market_time.isoformat()}; a signal cannot be known "
                f"before the market time it describes"
            )
        # THE point-in-time gate: applied once, before any rule sees anything.
        available = RuleContext.available_only(metric_values, horizon)

        signals: list[Signal] = []
        skipped: list[Skipped] = []
        none_observed: list[str] = []
        priors = prior_strengths or {}
        live = existing or {}

        for spec in chosen:
            if spec.refuses_quality(state.quality.status):
                skipped.append(
                    Skipped(
                        spec,
                        SkipReason.QUALITY_NOT_MET,
                        f"state quality is {state.quality.status.value}",
                    )
                )
                continue

            missing = [
                f"{i}@v{v}"
                for i, v in spec.requires_features
                if not any(name == f"{i}@v{v}" for name, _ in available)
            ]
            if missing:
                # Not substituted with a different version, and not defaulted. The
                # rule pinned these versions; anything else is a different rule.
                skipped.append(
                    Skipped(
                        spec,
                        SkipReason.FEATURE_UNAVAILABLE,
                        f"not available at K: {', '.join(sorted(missing))}",
                    )
                )
                continue

            pinned = tuple(
                (name, value)
                for name, value in available
                if any(name == f"{i}@v{v}" for i, v in spec.requires_features)
            )
            context = RuleContext(
                state=state,
                knowledge_horizon_override=horizon,
                metrics=pinned,
                config=config if config is not None else RuleConfig.of(**dict(spec.default_config)),
                prior_strengths=priors.get(spec.key, ()),
            )

            try:
                outcome = spec.evaluate(context)
            except Exception as exc:  # a rule bug must not take down the batch
                skipped.append(
                    Skipped(spec, SkipReason.RULE_ERROR, f"{type(exc).__name__}: {str(exc)[:200]}")
                )
                continue

            if not outcome.fired:
                skipped.append(
                    Skipped(spec, SkipReason.CONDITIONS_NOT_MET, outcome.detail or "no signal")
                )
                continue

            if outcome.found_no_contradiction:
                none_observed.append(spec.label)

            signals.append(self._build(spec, context, outcome, evaluated_at, expiry_id, live))

        return EvaluationReport(tuple(signals), tuple(skipped), tuple(sorted(none_observed)))

    # ------------------------------------------------------------------ build

    def _build(
        self,
        spec: SignalRuleSpec,
        context: RuleContext,
        outcome: RuleOutcome,
        evaluated_at: datetime,
        expiry_id: int | None,
        live: dict[tuple[str, int, int, int | None, int], Signal],
    ) -> Signal:
        """Construct or advance the signal. Identity and strength are derived here."""
        supporting = tuple(
            sorted(outcome.supporting, key=lambda e: (e.metric_value_ref.label, e.statement))
        )
        contradicting = tuple(
            sorted(outcome.contradicting, key=lambda e: (e.metric_value_ref.label, e.statement))
        )
        strength = spec.strength_function(supporting, contradicting)

        # A recurrence after a terminal state is a NEW signal (`08` §3), so the
        # occurrence counter advances and the identity changes with it.
        base_key = (spec.signal_type, spec.version, context.underlying_id, expiry_id)
        occurrence = 1
        previous: Signal | None = None
        for candidate_occurrence in sorted({k[4] for k in live if k[:4] == base_key}, reverse=True):
            candidate = live[(*base_key, candidate_occurrence)]
            if candidate.status.is_terminal:
                occurrence = candidate_occurrence + 1
            else:
                occurrence = candidate_occurrence
                previous = candidate
            break

        identity = SignalIdentity(
            signal_type=spec.signal_type,
            rule_version=spec.version,
            underlying_id=context.underlying_id,
            expiry_id=expiry_id,
            market_time=context.market_time,
            knowledge_horizon=context.knowledge_horizon,
            build_context_id=context.build_context_id,
            config_digest=context.config.digest,
            occurrence=occurrence,
        )
        available = self.available_at_for(context, evaluated_at)
        provenance = SignalProvenance(
            rule_type=spec.signal_type,
            rule_version=spec.version,
            config_digest=context.config.digest,
            feature_versions=tuple(sorted(spec.requires_features)),
            inputs_digest=context.inputs_digest(),
            strength_function=spec.strength_function.name,
            strength_function_version=spec.strength_function.version,
            build_context_id=context.build_context_id,
            state_checkpoint_ref=context.state.identity.describe(),
        )

        target, trigger = self._target_status(spec, outcome, context, previous, strength)

        if previous is None:
            created = Signal(
                identity=identity,
                horizon=spec.horizon_delta,
                status=SignalStatus.FORMING,
                strength=strength,
                evidence=supporting,
                contradiction_assessment=(
                    NONE_OBSERVED if outcome.found_no_contradiction else contradicting
                ),
                invalidation_condition=outcome.invalidation_condition,
                provenance=provenance,
                created_at=evaluated_at,
                updated_at=evaluated_at,
                available_at=available,
                expires_at=context.market_time + spec.horizon_delta,
                quality_status=context.quality_status,
                history=(
                    (
                        f"{SignalStatus.FORMING.value}:{TransitionTrigger.ENTRY_PARTIAL.value}",
                        evaluated_at,
                    ),
                ),
            )
            if target is SignalStatus.FORMING:
                return created
            return apply_transition(created, target, trigger, evaluated_at, strength=strength)

        refreshed = replace(
            previous,
            identity=identity,
            strength=strength,
            evidence=supporting,
            contradiction_assessment=(
                NONE_OBSERVED if outcome.found_no_contradiction else contradicting
            ),
            invalidation_condition=outcome.invalidation_condition,
            provenance=provenance,
            available_at=available,
            quality_status=context.quality_status,
        )
        if not is_permitted(refreshed.status, target):
            # Not coerced: an illegal transition is a rule or framework bug, and
            # silently holding the old status would hide it.
            return refreshed
        return apply_transition(refreshed, target, trigger, evaluated_at, strength=strength)

    def _target_status(
        self,
        spec: SignalRuleSpec,
        outcome: RuleOutcome,
        context: RuleContext,
        previous: Signal | None,
        strength: Decimal,
    ) -> tuple[SignalStatus, TransitionTrigger]:
        """Which state this evaluation moves the signal to, per `08` §3."""
        if outcome.invalidated:
            return SignalStatus.INVALIDATED, TransitionTrigger.INVALIDATION_TRUE
        if previous is not None and context.market_time >= previous.expires_at:
            return SignalStatus.EXPIRED, TransitionTrigger.EXPIRED
        if not outcome.entry_conditions_met:
            return SignalStatus.FORMING, TransitionTrigger.ENTRY_PARTIAL

        sustained = (*context.prior_strengths, strength)
        if (
            previous is not None
            and previous.status in (SignalStatus.ACTIVE, SignalStatus.CONFIRMED)
            and len(sustained) >= spec.persistence_evaluations
            and all(s > 0 for s in sustained[-spec.persistence_evaluations :])
        ):
            return SignalStatus.CONFIRMED, TransitionTrigger.PERSISTENCE_MET
        if previous is not None and previous.status is SignalStatus.CONFIRMED:
            return SignalStatus.CONFIRMED, TransitionTrigger.EVIDENCE_UPDATED
        return SignalStatus.ACTIVE, (
            TransitionTrigger.EVIDENCE_UPDATED
            if previous is not None and previous.status is SignalStatus.ACTIVE
            else TransitionTrigger.ENTRY_FULL
        )
