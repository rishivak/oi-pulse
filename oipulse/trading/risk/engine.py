"""`RiskEngine` — the gate. Deterministic, pure, fail-closed.

`11-TRADING.md` §3:

> `trading/risk` may not import `trading/oms`, any broker adapter, or any strategy
> module. It receives an intent plus context and returns a decision. It is
> independently testable with no trading infrastructure present, which is the point:
> the component that says "no" must not depend on the components it constrains.

`evaluate` is a pure function of `(intent, state, policy, at)`. It reaches for
nothing: no ledger, no market data, no clock, no database. That is what makes
`inputs_digest` meaningful — the three things hashed into it really are the only
things that determined the outcome.

### Fail-closed, stated precisely

Phase 9 brief §22: *never default an uncertain risk state to APPROVED*. Three
distinct uncertainties, three refusals:

* A **breached** limit rejects.
* A limit configured but **not evaluable** rejects. A missing input is not a pass.
* A **knowledge-horizon violation** rejects before any limit runs: a state assembled
  at K2 cannot be read by an evaluation at K1 < K2.

Only a state where every configured limit was evaluated and passed produces an
approval. `NOT_CONFIGURED` limits do not block — the policy chose not to have them —
but they are recorded, so a reader can see exactly which checks were absent.

### Resizing

`11` §3 defines `MODIFIED`. Where a *quantity* limit is the only thing binding, the
engine can approve a smaller quantity rather than refusing outright. It does this
only for limits that are genuinely about size, and only when `allow_resizing` is on.
It **never mutates the intent** (brief §11): the reduction is recorded as
`approved_quantity` on the decision, and the intent remains exactly what the strategy
asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from oipulse.trading.intents import TradeIntent
from oipulse.trading.risk.decision import (
    LimitEvaluation,
    LimitStatus,
    RiskDecisionRecord,
    RiskVerdict,
    inputs_digest_for,
)
from oipulse.trading.risk.limits import evaluate_all
from oipulse.trading.risk.policy import LimitCategory, RiskPolicy
from oipulse.trading.risk.state import RiskState

__all__ = ["RISK_UNAVAILABLE", "RiskEngine", "RiskInputsUnavailable"]

#: Limits whose breach can be cured by trading less. Only these are resizable: a
#: kill switch, a stale state or a closed session cannot be satisfied by a smaller
#: order, and pretending otherwise would turn a hard refusal into a small one.
_RESIZABLE = frozenset(
    {
        "max_order_quantity",
        "max_position_per_instrument",
        "max_position_per_underlying",
        "max_position_per_strategy",
    }
)


class RiskInputsUnavailable(Exception):
    """Raised when evaluation cannot proceed at all.

    Distinct from a rejection: a rejection is a decision, this is the absence of
    one. Callers must treat it as "no authorization", never as a pass — and the
    engine's own `evaluate` never raises it for a *limit* problem, only for a
    structurally impossible evaluation such as a horizon violation.
    """


#: Sentinel detail used when the engine refuses before running any limit.
RISK_UNAVAILABLE = "risk inputs unavailable; fail-closed"


@dataclass(frozen=True, slots=True)
class RiskEngine:
    """The Phase 9 risk gate. Satisfies the Phase 8 `RiskGate` protocol.

    Immutable and holds only its policy and settings, so two engines with the same
    policy are interchangeable and an evaluation cannot depend on engine history.
    """

    policy: RiskPolicy
    #: How long an approval remains actionable. `11` §3: "default: seconds, not
    #: minutes". An approval must not survive a market that has moved.
    approval_validity: timedelta = timedelta(seconds=30)
    #: Whether a size-only breach may be met with a smaller approved quantity.
    #: Off by default: silently trading less than a strategy asked for is a
    #: behaviour an operator should opt into, not discover.
    allow_resizing: bool = False

    @property
    def name(self) -> str:
        return f"RISK:{self.policy.label}"

    @property
    def evaluates_risk(self) -> bool:
        """True. This is a real engine, not the Phase 8 pass-through."""
        return True

    # ----------------------------------------------------------------- evaluate

    def evaluate(
        self,
        intent: TradeIntent,
        state: RiskState,
        *,
        sequence_no: int,
        at: datetime,
        knowledge_horizon: datetime | None = None,
    ) -> RiskDecisionRecord:
        """Evaluate one intent against one state. Pure and deterministic.

        `at` is **market time**, supplied by the caller. Nothing here reads a
        clock; `tools/check_clock_access.py` enforces that no module can.

        `knowledge_horizon` is *this evaluation's* horizon, defaulting to `at`. It
        is deliberately **not** the intent's. `11` §3 is explicit that the two
        differ and that the distinction is the point:

            11:45  intent created      state_checkpoint_ref -> S(11:45)
            11:47  re-evaluated        risk_state_ref       -> S(11:47)

        A risk evaluation at 11:47 is *supposed* to see the 11:47 state. What it
        must not see is a state assembled with knowledge from after its own horizon
        — brief §13's "market state available at K2, risk evaluation at K1: the K2
        state must not be used".
        """
        horizon = knowledge_horizon if knowledge_horizon is not None else at
        digest = inputs_digest_for(
            intent_digest=intent.content_digest,
            policy_digest=self.policy.policy_digest,
            risk_state_ref=state.risk_state_ref,
        )
        common = {
            "intent_id": intent.intent_id,
            "sequence_no": sequence_no,
            "evaluated": True,
            "risk_state_ref": state.risk_state_ref,
            "risk_evaluation_time": at,
            "inputs_digest": digest,
            "policy_id": self.policy.policy_id,
            "policy_version": self.policy.version,
            "policy_digest": self.policy.policy_digest,
            "requested_quantity": intent.total_quantity,
            # The evaluation's horizon, not the intent's. The intent's own
            # knowledge_time remains on the intent and in the audit chain.
            "knowledge_horizon": horizon,
        }

        # 1. Point-in-time coherence, before any limit. A state assembled with
        #    knowledge the decision did not have is look-ahead, and no amount of
        #    limit-passing makes a decision built on it legitimate.
        violation = self._check_horizon(state, horizon)
        if violation is not None:
            return RiskDecisionRecord(
                verdict=RiskVerdict.REJECTED,
                reason=violation.detail,
                approved_quantity=0,
                limits_evaluated=(violation,),
                **common,  # type: ignore[arg-type]
            )

        # 2. Every limit, in the declared order, none short-circuiting.
        evaluations = evaluate_all(intent, state, self.policy.limits)
        breaches = tuple(e for e in evaluations if e.status is LimitStatus.BREACHED)
        unevaluable = tuple(e for e in evaluations if e.status is LimitStatus.NOT_EVALUABLE)

        # 3. Fail-closed on a limit that could not be checked. A configured limit
        #    whose input was missing is not a pass (brief §22).
        if unevaluable:
            names = ", ".join(e.limit_id for e in unevaluable)
            return RiskDecisionRecord(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    f"{len(unevaluable)} configured limit(s) could not be evaluated "
                    f"({names}); refusing rather than assuming they would have passed"
                ),
                approved_quantity=0,
                limits_evaluated=evaluations,
                **common,  # type: ignore[arg-type]
            )

        # 4. Breaches. Resize only where the breach is genuinely about size.
        if breaches:
            resized = self._resize(intent, state, breaches)
            if resized is not None and resized > 0:
                return RiskDecisionRecord(
                    verdict=RiskVerdict.MODIFIED,
                    reason=(
                        f"reduced from {intent.total_quantity} to {resized} to satisfy "
                        + ", ".join(e.limit_id for e in breaches)
                    ),
                    approved_quantity=resized,
                    approved_legs=self._scale_legs(intent, resized),
                    approved_until=at + self.approval_validity,
                    limits_evaluated=evaluations,
                    **common,  # type: ignore[arg-type]
                )
            return RiskDecisionRecord(
                verdict=RiskVerdict.REJECTED,
                reason="; ".join(f"{e.limit_id}: {e.detail}" for e in breaches),
                approved_quantity=0,
                limits_evaluated=evaluations,
                **common,  # type: ignore[arg-type]
            )

        # 5. Approved in full, with a bounded validity horizon.
        return RiskDecisionRecord(
            verdict=RiskVerdict.APPROVED,
            reason=(
                f"all {sum(1 for e in evaluations if e.status is LimitStatus.PASSED)} "
                f"evaluated limit(s) passed under {self.policy.label}"
            ),
            approved_quantity=intent.total_quantity,
            approved_legs=tuple((i, leg.quantity) for i, leg in enumerate(intent.legs)),
            approved_until=at + self.approval_validity,
            limits_evaluated=evaluations,
            **common,  # type: ignore[arg-type]
        )

    # ----------------------------------------------------------------- internals

    @staticmethod
    def _check_horizon(state: RiskState, horizon: datetime) -> LimitEvaluation | None:
        """Refuse a state that knows more than this evaluation is allowed to.

        Brief §13's worked case: a market state available at K2 must not be used by
        an evaluation at K1 < K2. Checked before any limit, because no amount of
        limit-passing makes a decision built on look-ahead legitimate.
        """
        if state.knowledge_time > horizon:
            return LimitEvaluation(
                category=LimitCategory.DATA,
                limit_id="knowledge_horizon",
                status=LimitStatus.BREACHED,
                limit_value=horizon.isoformat(),
                observed_value=state.knowledge_time.isoformat(),
                detail=(
                    f"the risk state was assembled at knowledge horizon "
                    f"{state.knowledge_time.isoformat()}, later than this "
                    f"evaluation's {horizon.isoformat()}; reading it would consume "
                    f"information the evaluation was not entitled to"
                ),
            )
        return None

    def _resize(
        self, intent: TradeIntent, state: RiskState, breaches: tuple[LimitEvaluation, ...]
    ) -> int | None:
        """The largest quantity that would satisfy every size-based breach, or None.

        Returns None -- meaning "reject, do not resize" -- whenever any breach is
        not resizable. A kill switch cannot be satisfied by trading less, and
        approving a smaller order against one would be the exact "silently convert a
        rejection into a resize" the brief §9 forbids.
        """
        if not self.allow_resizing:
            return None
        if any(e.limit_id not in _RESIZABLE for e in breaches):
            return None

        allowed = intent.total_quantity
        for breach in breaches:
            if breach.limit_value is None or breach.observed_value is None:
                return None
            limit = Decimal(breach.limit_value)
            observed = Decimal(breach.observed_value)
            excess = observed - limit
            if excess <= 0:  # pragma: no cover - a breach implies a positive excess
                continue
            allowed = min(allowed, intent.total_quantity - int(excess))
        return max(allowed, 0)

    @staticmethod
    def _scale_legs(intent: TradeIntent, approved_total: int) -> tuple[tuple[int, int], ...]:
        """Distribute an approved total across legs, largest-remainder, deterministic.

        A multi-leg intent reduced pro-rata keeps its shape; a spread scaled
        unevenly would become a different position. The remainder goes to the
        earliest legs so the split is reproducible rather than dependent on
        floating-point rounding.
        """
        total = intent.total_quantity
        if total == 0:  # pragma: no cover - guarded by IntentLeg validation
            return ()
        exact = [(leg.quantity * approved_total) / total for leg in intent.legs]
        floors = [int(value) for value in exact]
        remainder = approved_total - sum(floors)
        for index in range(min(remainder, len(floors))):
            floors[index] += 1
        return tuple((index, quantity) for index, quantity in enumerate(floors))
