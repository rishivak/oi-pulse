"""The signal-rule registry — `08-SIGNALS.md` §4.

```python
@signal_rule(
    signal_type="PUT_SUPPORT_MIGRATION",
    version=1,
    horizon="30m",
    evaluation_interval="5m",
    requires_features=[("PUT_OI_MIGRATION", 2), ("OI_WALL_PUT", 1), ...],
    quality_requirements=["quality != UNRELIABLE"],
)
def put_support_migration(ctx: RuleContext) -> RuleOutcome: ...
```

Four properties, each preventing a named anti-pattern from `08` §7:

* **`requires_features` pins exact versions.** A feature bumping to v3 does not
  silently change a rule's behaviour; the rule must opt in, and that is a versioned
  change to the rule.
* **`evaluation_interval` throttles evaluation** independently of checkpoint cadence.
* **`quality_requirements`** mean a rule does not fire on unreliable state.
* **The outcome carries a contradiction assessment**, which may be `NONE_OBSERVED`.
  The framework flags a rule whose assessment is *always* `NONE_OBSERVED` across many
  firings, because an assessment that never finds anything is usually not assessing.

A rule returns a `RuleOutcome`, never a `Signal`. Constructing the signal — identity,
strength, availability, lifecycle — is the evaluator's job, so every rule gets those
right by construction rather than by discipline.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from oipulse.marketstate.staleness import QualityStatus
from oipulse.signals.context import RuleContext
from oipulse.signals.model import NONE_OBSERVED, ContradictionAssessment, Evidence
from oipulse.signals.strength import StrengthFunction, normalized_weighted_sum

__all__ = [
    "RULES",
    "DuplicateRule",
    "RuleOutcome",
    "RuleRegistry",
    "SignalRuleSpec",
    "UnknownRule",
    "parse_interval",
    "signal_rule",
]

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h|d)$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class DuplicateRule(ValueError):
    """`(signal_type, version)` registered twice.

    Raised rather than overwritten: the second registration would change the meaning
    of every signal already stored under that version.
    """


class UnknownRule(KeyError):
    """No such `(signal_type, version)`."""


def parse_interval(text: str) -> timedelta:
    match = _DURATION.match(text.strip())
    if match is None:
        raise ValueError(f"unparseable interval {text!r}; expected e.g. '5m', '30m', '1d'")
    return timedelta(seconds=float(match.group(1)) * _UNITS[match.group(2)])


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """What a rule decides. Deliberately not a `Signal`.

    `entry_conditions_met` distinguishes ACTIVE from FORMING: partially met conditions
    produce a forming signal, fully met ones an active signal (`08` §3).

    `contradiction` is mandatory. `NONE_OBSERVED` is a positive claim, not an omission.
    """

    fired: bool
    entry_conditions_met: bool = False
    supporting: tuple[Evidence, ...] = ()
    contradiction: ContradictionAssessment = NONE_OBSERVED
    invalidation_condition: str = ""
    #: Set when the rule itself determines the signal should be invalidated.
    invalidated: bool = False
    detail: str = ""

    @staticmethod
    def no_signal(detail: str = "") -> RuleOutcome:
        return RuleOutcome(fired=False, detail=detail)

    @property
    def contradicting(self) -> tuple[Evidence, ...]:
        if isinstance(self.contradiction, str):
            return ()
        return self.contradiction

    @property
    def found_no_contradiction(self) -> bool:
        return self.contradiction == NONE_OBSERVED


RuleFn = Callable[[RuleContext], RuleOutcome]


@dataclass(frozen=True, slots=True)
class SignalRuleSpec:
    """A registered rule's declared metadata."""

    signal_type: str
    version: int
    definition: str
    horizon: str
    evaluation_interval: str
    #: Exact `(identifier, version)` pairs. Version-pinned by design.
    requires_features: tuple[tuple[str, int], ...]
    quality_requirements: tuple[str, ...]
    evaluate: RuleFn
    implementation_ref: str
    strength_function: StrengthFunction = normalized_weighted_sum
    #: N consecutive evaluations with sustained strength for ACTIVE -> CONFIRMED.
    persistence_evaluations: int = 3
    #: Declared default thresholds. A deployment may override; the digest changes.
    default_config: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def key(self) -> tuple[str, int]:
        return (self.signal_type, self.version)

    @property
    def label(self) -> str:
        return f"{self.signal_type}@v{self.version}"

    @property
    def horizon_delta(self) -> timedelta:
        return parse_interval(self.horizon)

    @property
    def evaluation_delta(self) -> timedelta:
        return parse_interval(self.evaluation_interval)

    def refuses_quality(self, status: QualityStatus) -> bool:
        """Does the declared quality gate reject this state?

        Only the documented form is understood. An unparseable requirement is treated
        as refusing, because a gate that passes when it cannot check is not a gate.
        """
        for raw in self.quality_requirements:
            normalised = raw.replace(" ", "")
            if normalised == "quality!=UNRELIABLE":
                if status is QualityStatus.UNRELIABLE:
                    return True
            elif normalised == "quality==OK":
                if status is not QualityStatus.OK:
                    return True
            else:
                return True
        return False

    def as_dict(self) -> dict[str, object]:
        """Machine-readable form, served by `/signals/types` (`12` §168)."""
        return {
            "signal_type": self.signal_type,
            "version": self.version,
            "definition": self.definition,
            "horizon": self.horizon,
            "evaluation_interval": self.evaluation_interval,
            "requires_features": [f"{i}@v{v}" for i, v in self.requires_features],
            "quality_requirements": list(self.quality_requirements),
            "implementation_ref": self.implementation_ref,
            "strength_function": (
                f"{self.strength_function.name}@v{self.strength_function.version}"
            ),
            "persistence_evaluations": self.persistence_evaluations,
            "default_config": dict(self.default_config),
        }


class RuleRegistry:
    """All declared rules, keyed by `(signal_type, version)`. Versions coexist."""

    __slots__ = ("_by_key",)

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], SignalRuleSpec] = {}

    def register(self, spec: SignalRuleSpec) -> SignalRuleSpec:
        if spec.key in self._by_key:
            raise DuplicateRule(
                f"{spec.label} is already registered by "
                f"{self._by_key[spec.key].implementation_ref}. Changing a rule's meaning "
                f"requires a NEW version; overwriting would change the meaning of "
                f"signals already stored under this one."
            )
        self._by_key[spec.key] = spec
        return spec

    def get(self, signal_type: str, version: int) -> SignalRuleSpec:
        try:
            return self._by_key[(signal_type, version)]
        except KeyError as exc:
            raise UnknownRule(f"{signal_type}@v{version} is not registered") from exc

    def latest(self, signal_type: str) -> SignalRuleSpec:
        versions = [k[1] for k in self._by_key if k[0] == signal_type]
        if not versions:
            raise UnknownRule(f"{signal_type} is not registered")
        return self._by_key[(signal_type, max(versions))]

    def all(self) -> tuple[SignalRuleSpec, ...]:
        """Deterministic order. Iteration order never affects evaluation."""
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    def types(self) -> tuple[str, ...]:
        return tuple(sorted({k[0] for k in self._by_key}))

    def as_dict(self) -> list[dict[str, object]]:
        return [spec.as_dict() for spec in self.all()]

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key


#: The one rule registry. Populated at import time by the catalogue.
RULES = RuleRegistry()


def signal_rule(
    *,
    signal_type: str,
    version: int,
    definition: str,
    horizon: str,
    evaluation_interval: str,
    requires_features: Sequence[tuple[str, int]],
    quality_requirements: Sequence[str],
    strength_function: StrengthFunction = normalized_weighted_sum,
    persistence_evaluations: int = 3,
    default_config: Sequence[tuple[str, str]] = (),
    registry: RuleRegistry | None = None,
) -> Callable[[RuleFn], RuleFn]:
    """Declare a signal rule. Every field is mandatory."""

    def decorate(fn: RuleFn) -> RuleFn:
        spec = SignalRuleSpec(
            signal_type=signal_type,
            version=version,
            definition=definition.strip(),
            horizon=horizon,
            evaluation_interval=evaluation_interval,
            requires_features=tuple(requires_features),
            quality_requirements=tuple(quality_requirements),
            evaluate=fn,
            implementation_ref=f"{fn.__module__}.{fn.__qualname__}",
            strength_function=strength_function,
            persistence_evaluations=persistence_evaluations,
            default_config=tuple(default_config),
        )
        # Validate now: a bad interval must fail at import, not mid-session.
        parse_interval(spec.horizon)
        parse_interval(spec.evaluation_interval)
        if not spec.requires_features:
            raise ValueError(f"{spec.label}: a rule must pin at least one feature version")
        # `is not None`, not `or`: RuleRegistry.__len__ makes an empty registry falsy,
        # so `registry or RULES` would silently register into the global one exactly
        # when a test passes a fresh registry to isolate itself.
        target = registry if registry is not None else RULES
        target.register(spec)
        fn.__rule_spec__ = spec  # type: ignore[attr-defined]
        return fn

    return decorate
