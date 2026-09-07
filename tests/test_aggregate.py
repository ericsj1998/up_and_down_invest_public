"""캔들 합성 검증 (P1 §1-0i · `marketdata/ingest/aggregate.py`).

15m·4h 는 DB 에 없고 5m 에서 합성한다. 그 합성이 **근사가 아니라 정확**하다는 것이
측정 전체의 전제이므로, 여기서 그 전제를 고정한다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.ingest.aggregate import AggregationError, aggregate, bucket_start

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def five_minute(count: int, *, start: datetime = START) -> list[Candle]:
    """5m 캔들 `count` 개 — 값은 봉 번호에서 유도해 검산 가능하게 한다."""
    return [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.M5,
            ts=start + timedelta(minutes=5 * index),
            open=Decimal(100 + index),
            high=Decimal(110 + index),
            low=Decimal(90 + index),
            close=Decimal(105 + index),
            volume=Decimal(2),
        )
        for index in range(count)
    ]


def test_three_five_minute_bars_make_one_fifteen() -> None:
    merged = aggregate(five_minute(3), Timeframe.M15)
    assert merged.expected == 3
    assert len(merged.candles) == 1
    assert merged.incomplete == 0


def test_ohlcv_is_standard_aggregation() -> None:
    """시가=첫 봉 · 종가=마지막 봉 · 고저=구간 극값 · 거래량=합. 근사가 없다."""
    bar = aggregate(five_minute(3), Timeframe.M15).candles[0]
    assert bar.open == Decimal(100)  # 첫 봉의 시가
    assert bar.close == Decimal(107)  # 마지막 봉(index 2)의 종가 105+2
    assert bar.high == Decimal(112)  # max(110, 111, 112)
    assert bar.low == Decimal(90)  # min(90, 91, 92)
    assert bar.volume == Decimal(6)  # 2 x 3


def test_bucket_boundary_is_utc_midnight_aligned() -> None:
    """거래소가 상위 봉을 UTC 자정 기준으로 끊는다 — 로컬 기준이면 결정론이 깨진다."""
    span = timedelta(hours=4)
    assert bucket_start(datetime(2026, 1, 1, 5, 30, tzinfo=UTC), span) == datetime(
        2026, 1, 1, 4, 0, tzinfo=UTC
    )
    assert bucket_start(datetime(2026, 1, 1, 3, 59, tzinfo=UTC), span) == datetime(
        2026, 1, 1, 0, 0, tzinfo=UTC
    )


def test_bars_land_in_the_right_bucket() -> None:
    """00:00 시작 5m 봉 6개 → 15m 봉 2개 (00:00, 00:15)."""
    merged = aggregate(five_minute(6), Timeframe.M15)
    assert [bar.ts for bar in merged.candles] == [
        START,
        START + timedelta(minutes=15),
    ]


def test_incomplete_group_is_counted_not_dropped() -> None:
    """버리면 계열에 구멍이 생겨 ATR 이 더 왜곡되고, 조용히 포함하면 왜곡이 안 보인다."""
    merged = aggregate(five_minute(4), Timeframe.M15)
    assert len(merged.candles) == 2
    assert merged.incomplete == 1  # 두 번째 그룹이 1봉뿐이다
    assert merged.incomplete_ratio == Decimal("0.5")


def test_partial_group_at_the_start_also_counts() -> None:
    """구간 시작이 버킷 경계와 안 맞는 경우 — 첫 그룹이 불완전하다."""
    merged = aggregate(five_minute(3, start=START + timedelta(minutes=5)), Timeframe.M15)
    assert merged.incomplete >= 1


def test_incomplete_ratio_is_zero_for_empty_result() -> None:
    merged = aggregate(five_minute(3), Timeframe.M15)
    assert merged.incomplete_ratio == 0


def test_non_multiple_target_is_rejected() -> None:
    """정확한 배수가 아니면 합성이 근사가 되고, 그 오차는 ATR·손절가로 번진다."""
    hourly = [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.H1,
            ts=START + timedelta(hours=index),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
        )
        for index in range(4)
    ]
    with pytest.raises(AggregationError, match="정확한 배수가 아니다"):
        aggregate(hourly, Timeframe.M15)


def test_same_timeframe_is_rejected() -> None:
    """5m → 5m 은 합성이 아니다. 조용히 원본을 돌려주면 "합성했다"고 믿게 된다."""
    with pytest.raises(AggregationError, match="정확한 배수가 아니다"):
        aggregate(five_minute(3), Timeframe.M5)


def test_empty_input_is_rejected() -> None:
    with pytest.raises(AggregationError, match="빈 캔들"):
        aggregate([], Timeframe.M15)


def test_four_hour_needs_forty_eight_five_minute_bars() -> None:
    merged = aggregate(five_minute(48), Timeframe.H4)
    assert merged.expected == 48
    assert len(merged.candles) == 1
    assert merged.incomplete == 0


def test_result_timeframe_is_the_target() -> None:
    """합성 결과가 원본 시간축을 달고 있으면 이후 계산이 엉뚱한 간격을 쓴다."""
    merged = aggregate(five_minute(3), Timeframe.M15)
    assert all(bar.timeframe is Timeframe.M15 for bar in merged.candles)


def test_candles_stay_ascending() -> None:
    merged = aggregate(five_minute(30), Timeframe.M15)
    stamps = [bar.ts for bar in merged.candles]
    assert stamps == sorted(stamps)
