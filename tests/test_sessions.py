"""거래 세션 달력 검증 (P1 §1-0j).

핵심 주장 하나를 지킨다: **주식의 야간·주말 구멍은 결측이 아니고, 세션 내부 구멍만
결측이다.** 그리고 그 필터가 진짜 결측을 삼키지 않아야 한다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.ingest.integrity import (
    IntegrityThresholds,
    find_empty_trading_days,
    find_missing_ranges,
    inspect_candles,
)
from updown.marketdata.ingest.sessions import (
    SessionCalendar,
    SessionCalendarError,
    calendar_for,
)

SAMSUNG = Instrument(
    market=Market.KRX,
    symbol="005930",
    name="삼성전자",
    asset_type=AssetType.STOCK,
    currency=Currency.KRW,
)
BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

#: KRX 일봉 ts = 현지 자정 = 전날 15:00Z (실측 — docs/platform/toss_api_notes.md 함정 ⑧).
DAY1 = datetime(2026, 8, 3, 15, 0, tzinfo=UTC)  # 8/4 00:00 KST (화)
DAY2 = datetime(2026, 8, 4, 15, 0, tzinfo=UTC)  # 8/5 00:00 KST (수)
DAY3 = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)  # 8/6 00:00 KST (목)


def bar(ts: datetime, timeframe: Timeframe = Timeframe.M5) -> Candle:
    """검사용 정상 봉."""
    return Candle(
        instrument=SAMSUNG,
        timeframe=timeframe,
        ts=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(10),
    )


def daily(*stamps: datetime) -> list[Candle]:
    """일봉 목록."""
    return [bar(ts, Timeframe.D1) for ts in stamps]


# ---------------------------------------------------------------------------
# 1. 달력 구성 — 일봉이 곧 거래일이다
# ---------------------------------------------------------------------------


def test_trading_day_comes_from_daily_candles() -> None:
    """일봉이 있는 날 = 거래일. 휴장일 목록을 따로 알 필요가 없다."""
    calendar = SessionCalendar.from_daily(daily(DAY1, DAY2))
    # 장중 (8/4 09:30 KST = 8/4 00:30Z)
    assert calendar.trading_day(DAY1 + timedelta(minutes=30)) == DAY1
    # 다음 거래일
    assert calendar.trading_day(DAY2 + timedelta(hours=1)) == DAY2


def test_time_outside_any_trading_day_is_none() -> None:
    """거래일 밖은 None — 오류가 아니라 '장이 닫혀 있었다'는 답이다."""
    calendar = SessionCalendar.from_daily(daily(DAY1))
    assert calendar.trading_day(DAY1 - timedelta(hours=1)) is None
    assert calendar.trading_day(DAY1 + timedelta(hours=25)) is None


def test_weekend_gap_does_not_stretch_friday_session() -> None:
    """금요일 세션이 월요일 개장까지 이어지면 주말 구멍이 '세션 내부'가 된다."""
    friday = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)  # 8/7 00:00 KST (금)
    monday = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)  # 8/10 00:00 KST (월)
    calendar = SessionCalendar.from_daily(daily(friday, monday))
    saturday_noon = friday + timedelta(hours=36)
    assert calendar.trading_day(saturday_noon) is None, (
        "24시간으로 자르지 않으면 주말이 금요일 세션에 흡수된다"
    )
    assert not calendar.same_session(friday + timedelta(hours=1), monday + timedelta(hours=1))


def test_stock_without_daily_candles_is_refused() -> None:
    """일봉 없이 조용히 '전부 한 세션'으로 넘기지 않는다 (절대 규칙 #8)."""
    with pytest.raises(SessionCalendarError, match="일봉이 없어"):
        calendar_for(Market.KRX, [])


def test_coin_gets_always_open_without_daily() -> None:
    """코인은 일봉을 요구하지 않는다 — 휴장이 없어 필요가 없다."""
    calendar = calendar_for(Market.UPBIT, None)
    assert calendar.always_open
    assert calendar.same_session(DAY1, DAY1 + timedelta(days=400))


# ---------------------------------------------------------------------------
# 2. 결측 판정 — 야간·주말은 정상, 세션 내부는 결함
# ---------------------------------------------------------------------------


def test_overnight_gap_is_not_a_defect_for_stocks() -> None:
    """🔴 이 모듈의 존재 이유 — 매일 밤이 결측으로 잡히면 안 된다."""
    calendar = SessionCalendar.from_daily(daily(DAY1, DAY2))
    candles = [
        bar(DAY1 + timedelta(hours=6, minutes=25)),  # 8/4 장 마감 무렵
        bar(DAY2 + timedelta(minutes=0)),  # 8/5 개장
    ]
    assert find_missing_ranges(candles, Timeframe.M5, calendar) == []


def test_intraday_gap_is_still_a_defect() -> None:
    """세션 내부 구멍은 진짜 결측이다 — 필터가 이것까지 삼키면 안 된다."""
    calendar = SessionCalendar.from_daily(daily(DAY1))
    candles = [
        bar(DAY1 + timedelta(hours=1)),
        bar(DAY1 + timedelta(hours=1, minutes=20)),  # 15분(3봉) 비었다
    ]
    gaps = find_missing_ranges(candles, Timeframe.M5, calendar)
    assert len(gaps) == 1
    assert gaps[0] == (
        DAY1 + timedelta(hours=1, minutes=5),
        DAY1 + timedelta(hours=1, minutes=15),
    )


def test_coin_behaviour_is_unchanged_without_a_calendar() -> None:
    """달력을 안 주면 기존 동작 그대로다 — 코인 회귀 방지."""
    coin_bars = [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.M5,
            ts=datetime(2026, 8, 4, 0, 0, tzinfo=UTC),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
        ),
        Candle(
            instrument=BTC,
            timeframe=Timeframe.M5,
            ts=datetime(2026, 8, 4, 0, 15, tzinfo=UTC),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
        ),
    ]
    assert len(find_missing_ranges(coin_bars, Timeframe.M5)) == 1


def test_whole_missing_trading_day_is_caught() -> None:
    """🔴 세션 필터의 사각지대 — 하루가 통째로 비면 앞뒤가 다른 세션이라 안 걸린다."""
    calendar = SessionCalendar.from_daily(daily(DAY1, DAY2, DAY3))
    candles = [
        bar(DAY1 + timedelta(hours=1)),
        bar(DAY3 + timedelta(hours=1)),  # DAY2 가 통째로 없다
    ]
    assert find_missing_ranges(candles, Timeframe.M5, calendar) == [], (
        "세션이 다르므로 구간 판정에는 안 걸린다 — 그래서 별도 검사가 필요하다"
    )
    empty = find_empty_trading_days(candles, calendar)
    assert empty == [(DAY2, DAY2)], "통째로 빈 거래일을 놓치면 결측을 못 세는 쪽으로 기운다"


def test_empty_trading_days_is_noop_for_coin() -> None:
    """24시간 장에는 거래일 경계가 없다."""
    assert find_empty_trading_days([bar(DAY1)], SessionCalendar.always()) == []


# ---------------------------------------------------------------------------
# 3. 통합 — inspect_candles 가 세션을 존중한다
# ---------------------------------------------------------------------------


def test_inspect_candles_respects_sessions() -> None:
    """3일치 주식 봉에서 야간 구멍이 이슈로 올라오지 않아야 한다."""
    calendar = SessionCalendar.from_daily(daily(DAY1, DAY2, DAY3))
    candles = [
        bar(day + timedelta(minutes=step * 5))
        for day in (DAY1, DAY2, DAY3)
        for step in range(12)  # 각 거래일 1시간치
    ]
    report = inspect_candles(candles, thresholds=IntegrityThresholds(), sessions=calendar)
    assert report.is_clean, f"야간 구멍이 결측으로 잡혔다: {report.issues}"


def test_inspect_candles_without_sessions_flags_stock_nights() -> None:
    """달력을 빼먹으면 야간이 결측으로 잡힌다 — 이 실패가 '반드시 넘겨라'의 근거다."""
    candles = [bar(day + timedelta(minutes=step * 5)) for day in (DAY1, DAY2) for step in range(12)]
    report = inspect_candles(candles, thresholds=IntegrityThresholds())
    assert not report.is_clean
