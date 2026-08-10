"""OI analytics: delta calculations, PCR, OI signal classification.

All functions are pure (no DB/IO) so they're easily unit-tested.
Results are stored alongside each snapshot at write time to avoid
recomputation on every dashboard query.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class OISignal(str, Enum):
    """
    Informational classification only — NOT a trading signal.
    Based on OI direction + price direction matrix.
    """
    LONG_BUILDUP = "long_buildup"           # OI ↑, price ↑  (CE)
    SHORT_BUILDUP = "short_buildup"          # OI ↑, price ↓  (CE)
    LONG_UNWINDING = "long_unwinding"        # OI ↓, price ↓  (CE)
    SHORT_COVERING = "short_covering"        # OI ↓, price ↑  (CE)
    NEUTRAL = "neutral"


class OIInterpretation(str, Enum):
    """Instrument-agnostic OI interpretation used for timeframe analytics."""

    LONG_BUILDUP = "LONG_BUILDUP"
    SHORT_BUILDUP = "SHORT_BUILDUP"
    SHORT_COVERING = "SHORT_COVERING"
    LONG_UNWINDING = "LONG_UNWINDING"
    NO_SIGNIFICANT_CHANGE = "NO_SIGNIFICANT_CHANGE"


@dataclass(slots=True)
class StrikeAnalytics:
    strike: float
    call_oi: int
    put_oi: int
    call_oi_change: int
    put_oi_change: int
    call_oi_change_pct: float | None
    put_oi_change_pct: float | None
    call_ltp: float | None
    put_ltp: float | None
    call_ltp_change: float | None
    put_ltp_change: float | None
    call_iv: float | None
    put_iv: float | None
    strike_pcr: float | None
    ce_signal: OISignal
    pe_signal: OISignal
    net_oi_change: int


@dataclass(slots=True)
class SnapshotAnalytics:
    total_call_oi: int
    total_put_oi: int
    pcr: float | None
    total_call_oi_change: int
    total_put_oi_change: int
    net_oi_change: int
    strikes: list[StrikeAnalytics]

    # Ranked lists (strike, delta)
    top_call_oi_additions: list[tuple[float, int]]
    top_put_oi_additions: list[tuple[float, int]]
    top_call_oi_reductions: list[tuple[float, int]]
    top_put_oi_reductions: list[tuple[float, int]]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_pct(new: int | None, old: int | None) -> float | None:
    if old is None or old == 0 or new is None:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _safe_pcr(call_oi: int | None, put_oi: int | None) -> float | None:
    if call_oi is None or call_oi == 0 or put_oi is None:
        return None
    return round(put_oi / call_oi, 4)


def classify_ce_signal(oi_change: int, ltp_change: float | None) -> OISignal:
    """Classify a CE strike based on OI and price movement."""
    if ltp_change is None:
        return OISignal.NEUTRAL
    if oi_change > 0 and ltp_change > 0:
        return OISignal.LONG_BUILDUP
    if oi_change > 0 and ltp_change < 0:
        return OISignal.SHORT_BUILDUP
    if oi_change < 0 and ltp_change < 0:
        return OISignal.LONG_UNWINDING
    if oi_change < 0 and ltp_change > 0:
        return OISignal.SHORT_COVERING
    return OISignal.NEUTRAL


def classify_pe_signal(oi_change: int, ltp_change: float | None) -> OISignal:
    """Put OI classification (price direction inverted vs CE)."""
    if ltp_change is None:
        return OISignal.NEUTRAL
    if oi_change > 0 and ltp_change < 0:
        return OISignal.LONG_BUILDUP
    if oi_change > 0 and ltp_change > 0:
        return OISignal.SHORT_BUILDUP
    if oi_change < 0 and ltp_change > 0:
        return OISignal.LONG_UNWINDING
    if oi_change < 0 and ltp_change < 0:
        return OISignal.SHORT_COVERING
    return OISignal.NEUTRAL


def classify_oi_interpretation(
    ltp_change: float | None,
    oi_change: int | None,
) -> OIInterpretation:
    """
    Classify timeframe-level OI behavior.

    Uses the standard matrix:
      LTP↑ + OI↑ => LONG_BUILDUP
      LTP↓ + OI↑ => SHORT_BUILDUP
      LTP↑ + OI↓ => SHORT_COVERING
      LTP↓ + OI↓ => LONG_UNWINDING
    """
    if ltp_change is None or oi_change is None:
        return OIInterpretation.NO_SIGNIFICANT_CHANGE

    if ltp_change > 0 and oi_change > 0:
        return OIInterpretation.LONG_BUILDUP
    if ltp_change < 0 and oi_change > 0:
        return OIInterpretation.SHORT_BUILDUP
    if ltp_change > 0 and oi_change < 0:
        return OIInterpretation.SHORT_COVERING
    if ltp_change < 0 and oi_change < 0:
        return OIInterpretation.LONG_UNWINDING
    return OIInterpretation.NO_SIGNIFICANT_CHANGE


def safe_pcr(put_oi: int | None, call_oi: int | None) -> float | None:
    """Public PCR helper with zero-denominator and null guards."""
    if put_oi is None or call_oi is None or call_oi == 0:
        return None
    return round(put_oi / call_oi, 4)


def safe_change_pcr(put_oi_change: int | None, call_oi_change: int | None) -> float | None:
    """PCR variant based on OI change with zero-denominator protection."""
    if put_oi_change is None or call_oi_change is None or call_oi_change == 0:
        return None
    return round(put_oi_change / call_oi_change, 4)


# ── Core calculation ──────────────────────────────────────────────────────────

@dataclass
class StrikeInput:
    strike: float
    call_oi: int | None = None
    put_oi: int | None = None
    call_ltp: float | None = None
    put_ltp: float | None = None
    call_volume: int | None = None
    put_volume: int | None = None
    call_iv: float | None = None
    put_iv: float | None = None
    call_prev_oi: int | None = None
    put_prev_oi: int | None = None
    call_prev_ltp: float | None = None
    put_prev_ltp: float | None = None


def compute_snapshot_analytics(
    strikes: Sequence[StrikeInput],
    top_n: int = 5,
) -> SnapshotAnalytics:
    """Compute all analytics for a set of strikes in a single pass."""
    computed: list[StrikeAnalytics] = []

    total_call_oi = 0
    total_put_oi = 0
    total_call_oi_change = 0
    total_put_oi_change = 0

    for s in strikes:
        call_oi = s.call_oi or 0
        put_oi = s.put_oi or 0
        call_prev = s.call_prev_oi or 0
        put_prev = s.put_prev_oi or 0

        call_oi_change = call_oi - call_prev
        put_oi_change = put_oi - put_prev

        call_ltp_change = (
            round(s.call_ltp - s.call_prev_ltp, 2)
            if s.call_ltp is not None and s.call_prev_ltp is not None
            else None
        )
        put_ltp_change = (
            round(s.put_ltp - s.put_prev_ltp, 2)
            if s.put_ltp is not None and s.put_prev_ltp is not None
            else None
        )

        total_call_oi += call_oi
        total_put_oi += put_oi
        total_call_oi_change += call_oi_change
        total_put_oi_change += put_oi_change

        computed.append(StrikeAnalytics(
            strike=s.strike,
            call_oi=call_oi,
            put_oi=put_oi,
            call_oi_change=call_oi_change,
            put_oi_change=put_oi_change,
            call_oi_change_pct=_safe_pct(call_oi, call_prev) if call_prev else None,
            put_oi_change_pct=_safe_pct(put_oi, put_prev) if put_prev else None,
            call_ltp=s.call_ltp,
            put_ltp=s.put_ltp,
            call_ltp_change=call_ltp_change,
            put_ltp_change=put_ltp_change,
            call_iv=s.call_iv,
            put_iv=s.put_iv,
            strike_pcr=_safe_pcr(call_oi, put_oi),
            ce_signal=classify_ce_signal(call_oi_change, call_ltp_change),
            pe_signal=classify_pe_signal(put_oi_change, put_ltp_change),
            net_oi_change=call_oi_change + put_oi_change,
        ))

    # Top-N ranked lists
    def _top(field: str, reverse: bool, n: int) -> list[tuple[float, int]]:
        return [
            (a.strike, getattr(a, field))
            for a in sorted(computed, key=lambda x: getattr(x, field), reverse=reverse)[:n]
        ]

    return SnapshotAnalytics(
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        pcr=_safe_pcr(total_call_oi, total_put_oi),
        total_call_oi_change=total_call_oi_change,
        total_put_oi_change=total_put_oi_change,
        net_oi_change=total_call_oi_change + total_put_oi_change,
        strikes=computed,
        top_call_oi_additions=_top("call_oi_change", True, top_n),
        top_put_oi_additions=_top("put_oi_change", True, top_n),
        top_call_oi_reductions=_top("call_oi_change", False, top_n),
        top_put_oi_reductions=_top("put_oi_change", False, top_n),
    )
