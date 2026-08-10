from datetime import datetime, timezone

from app.services.timeframe_service import (
    MarketPoint,
    Timeframe,
    aggregate_points,
    bucket_start,
    timeframe_minutes,
)


def test_timeframe_minutes() -> None:
    assert timeframe_minutes(Timeframe.ONE_MINUTE) == 1
    assert timeframe_minutes(Timeframe.FIVE_MINUTE) == 5
    assert timeframe_minutes(Timeframe.FIFTEEN_MINUTE) == 15
    assert timeframe_minutes(Timeframe.THIRTY_MINUTE) == 30
    assert timeframe_minutes(Timeframe.ONE_HOUR) == 60


def test_bucket_start_boundaries() -> None:
    ts = datetime(2026, 3, 18, 13, 37, 41, tzinfo=timezone.utc)
    assert bucket_start(ts, Timeframe.ONE_MINUTE) == datetime(2026, 3, 18, 13, 37, 0, tzinfo=timezone.utc)
    assert bucket_start(ts, Timeframe.FIVE_MINUTE) == datetime(2026, 3, 18, 13, 35, 0, tzinfo=timezone.utc)
    assert bucket_start(ts, Timeframe.FIFTEEN_MINUTE) == datetime(2026, 3, 18, 13, 30, 0, tzinfo=timezone.utc)
    assert bucket_start(ts, Timeframe.THIRTY_MINUTE) == datetime(2026, 3, 18, 13, 30, 0, tzinfo=timezone.utc)
    assert bucket_start(ts, Timeframe.ONE_HOUR) == datetime(2026, 3, 18, 13, 0, 0, tzinfo=timezone.utc)


def test_aggregate_points_for_bucket() -> None:
    points = [
        MarketPoint(ts=datetime(2026, 3, 18, 13, 31, 5, tzinfo=timezone.utc), ltp=23810.5, oi=1000, volume=15000),
        MarketPoint(ts=datetime(2026, 3, 18, 13, 37, 5, tzinfo=timezone.utc), ltp=23815.9, oi=1050, volume=18000),
        MarketPoint(ts=datetime(2026, 3, 18, 13, 44, 5, tzinfo=timezone.utc), ltp=23808.4, oi=990, volume=21000),
        MarketPoint(ts=datetime(2026, 3, 18, 13, 59, 55, tzinfo=timezone.utc), ltp=23822.0, oi=1060, volume=26000),
    ]

    agg = aggregate_points(points, Timeframe.THIRTY_MINUTE)
    assert agg is not None
    assert agg.bucket_start == datetime(2026, 3, 18, 13, 30, 0, tzinfo=timezone.utc)
    assert agg.bucket_end == datetime(2026, 3, 18, 14, 0, 0, tzinfo=timezone.utc)
    assert agg.open_ltp == 23810.5
    assert agg.close_ltp == 23822.0
    assert agg.ltp_change == 11.5
    assert agg.open_oi == 1000
    assert agg.close_oi == 1060
    assert agg.oi_change == 60
    assert agg.oi_high == 1060
    assert agg.oi_low == 990
    assert agg.volume == 26000
    assert agg.points == 4


def test_aggregate_points_with_missing_data() -> None:
    points = [
        MarketPoint(ts=datetime(2026, 3, 18, 9, 15, 1, tzinfo=timezone.utc), ltp=None, oi=100, volume=None),
        MarketPoint(ts=datetime(2026, 3, 18, 9, 15, 30, tzinfo=timezone.utc), ltp=10.0, oi=None, volume=1000),
    ]

    agg = aggregate_points(points, Timeframe.ONE_MINUTE)
    assert agg is not None
    assert agg.open_ltp == 10.0
    assert agg.close_ltp == 10.0
    assert agg.ltp_change == 0.0
    assert agg.open_oi == 100
    assert agg.close_oi == 100
    assert agg.oi_change == 0
    assert agg.oi_high == 100
    assert agg.oi_low == 100
    assert agg.volume == 1000
