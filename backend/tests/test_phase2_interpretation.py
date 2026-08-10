from app.services.analytics_service import (
    OIInterpretation,
    classify_oi_interpretation,
    safe_change_pcr,
    safe_pcr,
)


def test_long_buildup() -> None:
    assert classify_oi_interpretation(ltp_change=10.5, oi_change=1200) == OIInterpretation.LONG_BUILDUP


def test_short_buildup() -> None:
    assert classify_oi_interpretation(ltp_change=-4.2, oi_change=900) == OIInterpretation.SHORT_BUILDUP


def test_short_covering() -> None:
    assert classify_oi_interpretation(ltp_change=3.8, oi_change=-450) == OIInterpretation.SHORT_COVERING


def test_long_unwinding() -> None:
    assert classify_oi_interpretation(ltp_change=-2.1, oi_change=-700) == OIInterpretation.LONG_UNWINDING


def test_no_significant_change_for_zero_delta() -> None:
    assert classify_oi_interpretation(ltp_change=0.0, oi_change=0) == OIInterpretation.NO_SIGNIFICANT_CHANGE


def test_no_significant_change_for_missing_values() -> None:
    assert classify_oi_interpretation(ltp_change=None, oi_change=100) == OIInterpretation.NO_SIGNIFICANT_CHANGE
    assert classify_oi_interpretation(ltp_change=1.0, oi_change=None) == OIInterpretation.NO_SIGNIFICANT_CHANGE


def test_pcr_guards() -> None:
    assert safe_pcr(put_oi=1000, call_oi=500) == 2.0
    assert safe_pcr(put_oi=1000, call_oi=0) is None
    assert safe_pcr(put_oi=None, call_oi=100) is None


def test_change_pcr_guards() -> None:
    assert safe_change_pcr(put_oi_change=200, call_oi_change=100) == 2.0
    assert safe_change_pcr(put_oi_change=200, call_oi_change=0) is None
    assert safe_change_pcr(put_oi_change=None, call_oi_change=100) is None
