"""Computed statistics — `09-RESEARCH.md` §3.

| Group | Measures |
|---|---|
| Sample | count, coverage, excluded-for-quality count |
| Central | mean return, median return |
| Distribution | std dev, skew, quantiles, full histogram |
| Outcome | win rate, profit factor |
| Excursion | MFE, MAE, time-to-MFE, time-to-invalidation |
| Risk | max drawdown within horizon, realized volatility |

Everything here is a pure function of an explicitly supplied outcome series. Two rules
recur and are the reason several functions return `None` where a naive implementation
would return a number:

* **A statistic over an empty or too-small sample is `None`, never 0.** A mean of no
  observations is not zero, and a standard deviation of one point is not zero either.
* **Missing observations are never imputed.** `09` §3 excludes them and counts the
  exclusion; substituting zero, the previous value or the mean would manufacture data.

`Decimal` throughout: a forward-return distribution accumulated in floats drifts in the
last places, and a study is supposed to reproduce bit-for-bit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

__all__ = [
    "DistributionStats",
    "ExcursionStats",
    "OutcomeSeries",
    "SampleStats",
    "describe",
    "excursion",
]


@dataclass(frozen=True, slots=True)
class OutcomeSeries:
    """One event's forward path: offsets from the event and the observed values.

    `values` may contain `None` for an instant with no observation. They are counted
    as missing and excluded from statistics, never filled.
    """

    offsets: tuple[timedelta, ...]
    values: tuple[Decimal | None, ...]

    def __post_init__(self) -> None:
        if len(self.offsets) != len(self.values):
            raise ValueError("offsets and values must be the same length")

    @property
    def observed(self) -> tuple[tuple[timedelta, Decimal], ...]:
        return tuple(
            (offset, value)
            for offset, value in zip(self.offsets, self.values, strict=True)
            if value is not None
        )

    @property
    def missing_count(self) -> int:
        return sum(1 for value in self.values if value is None)

    @property
    def is_empty(self) -> bool:
        return not self.observed


@dataclass(frozen=True, slots=True)
class SampleStats:
    """Sample-level counts. Both raw and effective are always present."""

    raw_events: int
    effective_sample: int
    clusters: int
    excluded_quality: int
    excluded_incomplete_window: int
    missing_observations: int
    #: How many horizon/breakdown combinations this study computed. Recorded so a
    #: reader can discount for multiple comparisons (`09` §3, §6).
    comparisons: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_events": self.raw_events,
            "effective_sample": self.effective_sample,
            "clusters": self.clusters,
            "excluded_quality": self.excluded_quality,
            "excluded_incomplete_window": self.excluded_incomplete_window,
            "missing_observations": self.missing_observations,
            "comparisons": self.comparisons,
        }


@dataclass(frozen=True, slots=True)
class DistributionStats:
    """Central tendency, dispersion and shape. `None` where undefined."""

    count: int
    mean: Decimal | None = None
    median: Decimal | None = None
    stdev: Decimal | None = None
    skew: Decimal | None = None
    quantiles: tuple[tuple[str, Decimal], ...] = ()
    histogram: tuple[tuple[str, int], ...] = ()
    win_rate: Decimal | None = None
    profit_factor: Decimal | None = None
    max_drawdown: Decimal | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "mean": _s(self.mean),
            "median": _s(self.median),
            "stdev": _s(self.stdev),
            "skew": _s(self.skew),
            "quantiles": {k: str(v) for k, v in self.quantiles},
            "histogram": dict(self.histogram),
            "win_rate": _s(self.win_rate),
            "profit_factor": _s(self.profit_factor),
            "max_drawdown": _s(self.max_drawdown),
        }


@dataclass(frozen=True, slots=True)
class ExcursionStats:
    """Maximum favourable and adverse excursion, and when they occurred."""

    mfe: Decimal | None = None
    mae: Decimal | None = None
    time_to_mfe: timedelta | None = None
    time_to_mae: timedelta | None = None
    observations: int = 0
    missing: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "mfe": _s(self.mfe),
            "mae": _s(self.mae),
            "time_to_mfe_seconds": (
                None if self.time_to_mfe is None else self.time_to_mfe.total_seconds()
            ),
            "time_to_mae_seconds": (
                None if self.time_to_mae is None else self.time_to_mae.total_seconds()
            ),
            "observations": self.observations,
            "missing": self.missing,
        }


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _quantile(ordered: list[Decimal], fraction: Decimal) -> Decimal:
    """Nearest-rank quantile. Deterministic and interpolation-free.

    Interpolating would invent a value that no event produced; nearest-rank always
    returns an observation that actually occurred.
    """
    index = int((fraction * Decimal(len(ordered) - 1)).to_integral_value(rounding="ROUND_HALF_UP"))
    return ordered[max(0, min(index, len(ordered) - 1))]


def describe(
    returns: Sequence[Decimal],
    *,
    bucket: Decimal = Decimal("0.005"),
    quantiles: Sequence[str] = ("p05", "p25", "p50", "p75", "p95"),
) -> DistributionStats:
    """Summarise a set of forward returns.

    Every measure is `None` when it is undefined for the sample size rather than
    zero: reporting a standard deviation of 0 for a single observation would read as
    "no dispersion" when the truth is "not measurable".
    """
    values = list(returns)
    count = len(values)
    if count == 0:
        return DistributionStats(count=0)

    ordered = sorted(values)
    total = sum(values, start=Decimal(0))
    mean = total / Decimal(count)
    median = (
        ordered[count // 2]
        if count % 2 == 1
        else (ordered[count // 2 - 1] + ordered[count // 2]) / Decimal(2)
    )

    stdev: Decimal | None = None
    skew: Decimal | None = None
    if count >= 2:
        variance = sum(((v - mean) * (v - mean) for v in values), start=Decimal(0)) / Decimal(count)
        stdev = variance.sqrt()
        if stdev and stdev != 0 and count >= 3:
            cubed = sum((((v - mean) / stdev) ** 3 for v in values), start=Decimal(0))
            skew = cubed / Decimal(count)

    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    win_rate = Decimal(len(wins)) / Decimal(count)
    gross_win = sum(wins, start=Decimal(0))
    gross_loss = abs(sum(losses, start=Decimal(0)))
    # Undefined with no losses: an "infinite" profit factor is not a number, and
    # reporting a large one would misrepresent a sample that simply never lost.
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    running = Decimal(0)
    peak = Decimal(0)
    drawdown = Decimal(0)
    for value in values:
        running += value
        peak = max(peak, running)
        drawdown = min(drawdown, running - peak)

    buckets: dict[str, int] = {}
    if bucket > 0:
        for value in values:
            index = int((value / bucket).to_integral_value(rounding="ROUND_FLOOR"))
            key = f"{index * bucket}"
            buckets[key] = buckets.get(key, 0) + 1

    return DistributionStats(
        count=count,
        mean=mean,
        median=median,
        stdev=stdev,
        skew=skew,
        quantiles=tuple(
            (name, _quantile(ordered, Decimal(name[1:]) / Decimal(100))) for name in quantiles
        ),
        histogram=tuple(sorted(buckets.items(), key=lambda kv: Decimal(kv[0]))),
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=drawdown if drawdown < 0 else Decimal(0),
    )


def excursion(series: OutcomeSeries, baseline: Decimal) -> ExcursionStats:
    """Maximum favourable and adverse excursion relative to the event-time baseline.

    Missing instants are skipped and counted, never carried forward: a flat stretch
    created by repeating the last known value would understate both excursions.
    """
    observed = series.observed
    if not observed:
        return ExcursionStats(observations=0, missing=series.missing_count)

    mfe = mae = Decimal(0)
    time_to_mfe = time_to_mae = timedelta(0)
    for offset, value in observed:
        move = value - baseline
        if move > mfe:
            mfe, time_to_mfe = move, offset
        if move < mae:
            mae, time_to_mae = move, offset

    return ExcursionStats(
        mfe=mfe,
        mae=mae,
        time_to_mfe=time_to_mfe,
        time_to_mae=time_to_mae,
        observations=len(observed),
        missing=series.missing_count,
    )
