"""The feature registry — `07-ANALYTICS.md` §2.

Every metric the system can produce is declared here. **Nothing computes off-registry**,
which is the mechanism that prevents silent formula drift: a number in the database can
always be traced to a declaration stating its formula, units and convention, as it stood
when the value was produced.

```python
@feature(
    identifier="PUT_OI_MIGRATION",
    version=2,
    definition="...",
    inputs=["oi_by_strike(PE)"],
    formula="sum(oi_delta_s * (s - s_ref)) / sum(|oi_delta_s|)",
    units="strike_points",
    sampling_frequency="5m",
    lookback="15m",
    availability_delay="2s",
    normalization="none",
    quality_requirements=["oi_coverage>=0.95", "quality!=UNRELIABLE"],
    scope=Scope.EXPIRY,
)
def put_oi_migration(state, p): ...
```

**Versioning discipline.** Changing a formula's *meaning* requires a new version. Both
remain registered and independently computable, so a research result from March stays
interpretable after a v3 lands in June. `(identifier, version)` is the key; re-registering
an existing pair raises rather than overwriting, because a silent overwrite would change
the meaning of values already stored under that version.

**Quality gating** is evaluated *before* computation (§2). A feature requiring greeks does
not compute over a window without greeks; it returns `Unavailable`, not a number derived
from missing inputs.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from oipulse.analytics.values import (
    MetricValue,
    Scope,
    ScopeRef,
    Unavailable,
    UnavailableReason,
)
from oipulse.marketstate.staleness import QualityStatus

if TYPE_CHECKING:  # pragma: no cover - import cycle only exists for type checkers
    from oipulse.analytics.context import ComputeContext

__all__ = [
    "REGISTRY",
    "ComputeFn",
    "DuplicateFeature",
    "FeatureSpec",
    "Normalization",
    "QualityRequirement",
    "Registry",
    "UnknownFeature",
    "feature",
    "parse_duration",
    "unavailable",
]

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d)$")
_UNITS: dict[str, float] = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


class DuplicateFeature(ValueError):
    """`(identifier, version)` was registered twice.

    Raised rather than overwritten: the second registration would silently change the
    meaning of every value already stored under that version.
    """


class UnknownFeature(KeyError):
    """No such `(identifier, version)` in the registry."""


class Normalization(str):
    """Declared normalization. A plain string subtype so the registry stays readable."""

    NONE = "none"
    ZSCORE = "zscore"
    PERCENTILE = "percentile"
    PERCENT = "percent"
    RATIO = "ratio"


def parse_duration(text: str) -> timedelta:
    """`"15m"` -> 15 minutes. Rejects anything unparseable rather than defaulting to 0.

    A silently-zero lookback would make a windowed feature compute off a single point
    and still look like it had a window.
    """
    match = _DURATION.match(text.strip())
    if match is None:
        raise ValueError(f"unparseable duration {text!r}; expected e.g. '2s', '15m', '1d'")
    return timedelta(seconds=float(match.group(1)) * _UNITS[match.group(2)])


@dataclass(frozen=True, slots=True)
class QualityRequirement:
    """One parsed entry from `quality_requirements`.

    Two forms are supported, both taken from the design's own examples:
    `"quality!=UNRELIABLE"` and `"oi_coverage>=0.95"`. Anything else raises at
    registration, so a typo becomes a build error rather than a requirement that
    silently never fires.
    """

    raw: str
    metric: str
    operator: str
    threshold: float | None = None
    status: QualityStatus | None = None

    @staticmethod
    def parse(text: str) -> QualityRequirement:
        match = re.match(r"^\s*([a-z_]+)\s*(>=|<=|!=|==|>|<)\s*(\S+)\s*$", text)
        if match is None:
            raise ValueError(f"unparseable quality requirement {text!r}")
        metric, operator, operand = match.groups()
        if metric == "quality":
            try:
                status = QualityStatus(operand.lower())
            except ValueError as exc:
                raise ValueError(f"unknown quality status in {text!r}") from exc
            return QualityRequirement(text, metric, operator, status=status)
        try:
            threshold = float(operand)
        except ValueError as exc:
            raise ValueError(f"non-numeric threshold in {text!r}") from exc
        return QualityRequirement(text, metric, operator, threshold=threshold)

    def satisfied_by(self, *, status: QualityStatus, measures: dict[str, float]) -> bool:
        if self.metric == "quality" and self.status is not None:
            return _compare_rank(status, self.operator, self.status)
        observed = measures.get(self.metric)
        if observed is None or self.threshold is None:
            # An unmeasurable requirement is treated as unmet. The alternative --
            # passing when we cannot check -- is how a gate stops gating.
            return False
        return _compare_number(observed, self.operator, self.threshold)


def _compare_rank(left: QualityStatus, operator: str, right: QualityStatus) -> bool:
    if operator == "!=":
        return left is not right
    if operator == "==":
        return left is right
    return _compare_number(float(left.rank), operator, float(right.rank))


def _compare_number(left: float, operator: str, right: float) -> bool:
    return {
        ">=": left >= right,
        "<=": left <= right,
        ">": left > right,
        "<": left < right,
        "==": left == right,
        "!=": left != right,
    }[operator]


ComputeFn = Callable[["ComputeContext"], "MetricValue | Unavailable | list[MetricValue]"]


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """A registered feature's complete declared metadata (`07` §2).

    Every field in the design's registry table is present and mandatory. A feature that
    cannot state its units, its lookback or its quality requirements is a feature whose
    output nobody can safely interpret.
    """

    identifier: str
    version: int
    definition: str
    inputs: tuple[str, ...]
    formula: str
    units: str
    sampling_frequency: str
    lookback: str
    availability_delay: str
    normalization: str
    quality_requirements: tuple[QualityRequirement, ...]
    scope: Scope
    compute: ComputeFn
    implementation_ref: str
    #: Registered features this one consumes, as `(identifier, version)`. Drives the
    #: topological ordering in `engine.py` and the availability propagation in §3.
    depends_on: tuple[tuple[str, int], ...] = ()
    #: Declared, named parameters. A convention such as GEX's dealer sign lives here
    #: rather than as a hidden constant (`07` §4.4).
    parameters: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> tuple[str, int]:
        return (self.identifier, self.version)

    @property
    def label(self) -> str:
        return f"{self.identifier}@v{self.version}"

    @property
    def lookback_delta(self) -> timedelta:
        return parse_duration(self.lookback)

    @property
    def availability_delay_delta(self) -> timedelta:
        return parse_duration(self.availability_delay)

    @property
    def sampling_delta(self) -> timedelta:
        return parse_duration(self.sampling_frequency)

    def unmet_requirements(
        self, *, status: QualityStatus, measures: dict[str, float]
    ) -> tuple[QualityRequirement, ...]:
        """Which requirements fail. Evaluated **before** computation (`07` §2)."""
        return tuple(
            r
            for r in self.quality_requirements
            if not r.satisfied_by(status=status, measures=measures)
        )

    def as_dict(self) -> dict[str, object]:
        """Machine-readable form, served by `/features` (`12-API_SPEC.md` §161)."""
        return {
            "identifier": self.identifier,
            "version": self.version,
            "definition": self.definition,
            "inputs": list(self.inputs),
            "formula": self.formula,
            "units": self.units,
            "sampling_frequency": self.sampling_frequency,
            "lookback": self.lookback,
            "availability_delay": self.availability_delay,
            "normalization": self.normalization,
            "quality_requirements": [r.raw for r in self.quality_requirements],
            "scope": self.scope.value,
            "implementation_ref": self.implementation_ref,
            "depends_on": [f"{i}@v{v}" for i, v in self.depends_on],
            "parameters": list(self.parameters),
        }


class Registry:
    """All declared features, keyed by `(identifier, version)`.

    Versions coexist. `v1` and `v2` are independently referenceable and independently
    computable, which is what lets a March research result stay interpretable after a
    v3 lands in June.
    """

    __slots__ = ("_by_key",)

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        if spec.key in self._by_key:
            existing = self._by_key[spec.key]
            raise DuplicateFeature(
                f"{spec.label} is already registered by {existing.implementation_ref}. "
                f"Changing a formula's meaning requires a NEW version; overwriting "
                f"would silently change the meaning of values already stored."
            )
        self._by_key[spec.key] = spec
        return spec

    def get(self, identifier: str, version: int) -> FeatureSpec:
        try:
            return self._by_key[(identifier, version)]
        except KeyError as exc:
            raise UnknownFeature(f"{identifier}@v{version} is not registered") from exc

    def latest(self, identifier: str) -> FeatureSpec:
        versions = [k[1] for k in self._by_key if k[0] == identifier]
        if not versions:
            raise UnknownFeature(f"{identifier} is not registered")
        return self._by_key[(identifier, max(versions))]

    def versions_of(self, identifier: str) -> tuple[int, ...]:
        return tuple(sorted(k[1] for k in self._by_key if k[0] == identifier))

    def all(self) -> tuple[FeatureSpec, ...]:
        """Deterministic order: identifier then version. Iteration order never leaks."""
        return tuple(self._by_key[key] for key in sorted(self._by_key))

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted({k[0] for k in self._by_key}))

    def by_scope(self, scope: Scope) -> tuple[FeatureSpec, ...]:
        return tuple(s for s in self.all() if s.scope is scope)

    def as_dict(self) -> list[dict[str, object]]:
        return [s.as_dict() for s in self.all()]

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key


#: The one registry. Populated at import time by the domain modules.
REGISTRY = Registry()


def feature(
    *,
    identifier: str,
    version: int,
    definition: str,
    inputs: Sequence[str],
    formula: str,
    units: str,
    sampling_frequency: str,
    lookback: str,
    availability_delay: str,
    normalization: str,
    quality_requirements: Sequence[str],
    scope: Scope,
    depends_on: Sequence[tuple[str, int]] = (),
    parameters: Sequence[str] = (),
    registry: Registry | None = None,
) -> Callable[[ComputeFn], ComputeFn]:
    """Declare a feature. Every field is mandatory — there is no partial declaration.

    The decorator returns the undecorated function, so a feature stays directly
    callable and directly unit-testable without going through the registry.
    """

    def decorate(fn: ComputeFn) -> ComputeFn:
        spec = FeatureSpec(
            identifier=identifier,
            version=version,
            definition=definition.strip(),
            inputs=tuple(inputs),
            formula=formula,
            units=units,
            sampling_frequency=sampling_frequency,
            lookback=lookback,
            availability_delay=availability_delay,
            normalization=normalization,
            quality_requirements=tuple(QualityRequirement.parse(q) for q in quality_requirements),
            scope=scope,
            compute=fn,
            implementation_ref=f"{fn.__module__}.{fn.__qualname__}",
            depends_on=tuple(depends_on),
            parameters=tuple(parameters),
        )
        # Validate the durations now: a bad string must fail at import, not at the
        # first computation during market hours.
        parse_duration(spec.lookback)
        parse_duration(spec.availability_delay)
        parse_duration(spec.sampling_frequency)
        # `is not None`, not `or`: `Registry.__len__` makes an EMPTY registry falsy,
        # so `registry or REGISTRY` silently registered into the global one whenever
        # the caller passed a fresh registry -- which is exactly when a test is trying
        # to isolate itself.
        target = registry if registry is not None else REGISTRY
        target.register(spec)
        fn.__feature_spec__ = spec  # type: ignore[attr-defined]
        return fn

    return decorate


def unavailable(
    spec: FeatureSpec, scope_ref: ScopeRef, reason: UnavailableReason, detail: str = ""
) -> Unavailable:
    """State an absence in one line, carrying the feature identity with it."""
    return Unavailable(spec.identifier, spec.version, scope_ref, reason, detail)
