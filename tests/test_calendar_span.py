"""주봉·월봉 — **경계가 시장 현지시각인가**를 먼저 본다 (C2-3).

UTC 로 자르면 뉴욕 월말 종가가 다음 달로 넘어간다. 그 결함은 숫자를 보고는 알 수 없고
(월봉이 그럴듯하게 나온다) 경계 근처 봉을 세어야만 보인다 — 그래서 첫 테스트가 그것이다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.marketdata.ingest.calendar_span import (
    CalendarSpan,
    CalendarSpanError,
    aggregate_calendar,
    zone_for,
)


def instrument(market: Market) -> Instrument:
    """시장만 다른 종목.

    Args:
        market: 시장.

    Returns:
        종목.
    """
    return Instrument(
        market=market,
        symbol="TEST",
        name="테스트",
        asset_type=AssetType.STOCK,
        currency=Currency.USD,
    )


def bar(market: Market, moment: datetime, close: str, volume: str = "10") -> Candle:
    """일봉 하나.

    Args:
        market: 시장.
        moment: 봉 시각 (UTC).
        close: 종가. 고·저는 여기서 파생한다.
        volume: 거래량.

    Returns:
        캔들.
    """
    price = Decimal(close)
    return Candle(
        instrument=instrument(market),
        timeframe=Timeframe.D1,
        ts=moment,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=Decimal(volume),
    )


def test_month_boundary_follows_market_local_time() -> None:
    """🔴 뉴욕 3월 31일 **시간외** 20:00 봉은 3월 월봉이다 — UTC 로는 4월 1일인데도.

    Note:
        이것이 현지시각으로 자르는 유일한 실제 근거다. 처음에 "16:00 종가가 UTC 로
        다음 날"이라고 적었는데 **틀렸다** — 16:00 EST 는 21:00 UTC 같은 날이다.
        갈리는 것은 시간외처럼 19:00 ET 를 넘긴 봉뿐이다.
    """
    # 뉴욕 2026-03-31 20:00 EDT(UTC-4) = 2026-04-01 00:00 UTC.
    after_hours = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    bars = aggregate_calendar(
        [bar(Market.NASDAQ, after_hours, "100")], CalendarSpan.MONTH, Market.NASDAQ
    )

    assert [item.label for item in bars] == ["2026-03"]


def test_utc_market_puts_the_same_bar_in_april() -> None:
    """같은 봉이 코인(UTC)에서는 4월이다 — 경계 규칙이 실제로 갈린다는 확인이다."""
    after_hours = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    bars = aggregate_calendar(
        [bar(Market.UPBIT, after_hours, "100")], CalendarSpan.MONTH, Market.UPBIT
    )

    assert [item.label for item in bars] == ["2026-04"]


def test_regular_session_bars_are_unaffected_by_the_zone_choice() -> None:
    """⚠️ 정규장 봉은 UTC 로 잘라도 **같은 답**이 나온다 — 오늘의 tz 처리는 무해하다.

    Note:
        이 사실을 테스트로 박아 두는 이유: 나중에 "tz 처리가 복잡하니 UTC 로 통일하자"
        는 말이 나올 때, 무엇을 잃는지가 여기 있어야 한다. 잃는 것은 정규장이 아니라
        **시간외와 세션이 UTC 자정을 넘는 시장**이다 (위 테스트).
    """
    # KRX 정규장 09:00~15:30 KST = 00:00~06:30 UTC · 뉴욕 09:30~16:00 ET = 13:30~21:00 UTC.
    krx = bar(Market.KRX, datetime(2026, 3, 31, 6, 30, tzinfo=UTC), "100")
    nasdaq = bar(Market.NASDAQ, datetime(2026, 3, 31, 20, 0, tzinfo=UTC), "100")

    assert aggregate_calendar([krx], CalendarSpan.MONTH, Market.KRX)[0].label == "2026-03"
    assert aggregate_calendar([krx], CalendarSpan.MONTH, Market.UPBIT)[0].label == "2026-03"
    assert aggregate_calendar([nasdaq], CalendarSpan.MONTH, Market.NASDAQ)[0].label == "2026-03"
    assert aggregate_calendar([nasdaq], CalendarSpan.MONTH, Market.UPBIT)[0].label == "2026-03"


def test_month_ohlc_takes_first_open_and_last_close() -> None:
    """시가=첫 봉, 종가=마지막 봉, 고·저=극값, 거래량=합."""
    start = datetime(2026, 3, 2, 12, tzinfo=UTC)
    candles = [
        bar(Market.UPBIT, start, "100", "5"),
        bar(Market.UPBIT, start + timedelta(days=1), "130", "7"),
        bar(Market.UPBIT, start + timedelta(days=2), "90", "3"),
    ]
    bars = aggregate_calendar(candles, CalendarSpan.MONTH, Market.UPBIT)

    assert len(bars) == 1
    assert bars[0].open == Decimal(100)
    assert bars[0].close == Decimal(90)
    assert bars[0].high == Decimal(131)
    assert bars[0].low == Decimal(89)
    assert bars[0].volume == Decimal(15)
    assert bars[0].bars == 3


def test_week_uses_iso_weeks_across_a_year_boundary() -> None:
    """ISO 주 — 연말연시가 같은 주면 **같은 봉**이다.

    Note:
        `%Y-%W` 같은 포맷으로 자르면 12월 31일과 1월 1일이 연도가 달라 갈라진다.
        ISO 주는 그 경우를 정의로 처리한다.
    """
    # 2026-12-31(목)과 2027-01-01(금)은 같은 ISO 주다.
    candles = [
        bar(Market.UPBIT, datetime(2026, 12, 31, 12, tzinfo=UTC), "100"),
        bar(Market.UPBIT, datetime(2027, 1, 1, 12, tzinfo=UTC), "110"),
    ]
    bars = aggregate_calendar(candles, CalendarSpan.WEEK, Market.UPBIT)

    assert len(bars) == 1
    assert bars[0].close == Decimal(110)


def test_incomplete_span_is_kept_with_its_bar_count() -> None:
    """진행 중인 달을 버리지 않는다 — 버리면 화면에서 이번 달이 사라진다."""
    bars = aggregate_calendar(
        [bar(Market.UPBIT, datetime(2026, 8, 3, 12, tzinfo=UTC), "100")],
        CalendarSpan.MONTH,
        Market.UPBIT,
    )

    assert bars[0].bars == 1


def test_unsorted_input_is_rejected() -> None:
    """정렬을 가정하고 시·종가를 뽑으므로, 어긋나면 멈춘다 (절대 규칙 #8)."""
    candles = [
        bar(Market.UPBIT, datetime(2026, 3, 5, 12, tzinfo=UTC), "100"),
        bar(Market.UPBIT, datetime(2026, 3, 2, 12, tzinfo=UTC), "110"),
    ]
    with pytest.raises(CalendarSpanError, match="오름차순"):
        aggregate_calendar(candles, CalendarSpan.MONTH, Market.UPBIT)


def test_empty_input_is_rejected() -> None:
    """빈 입력에 빈 목록을 주지 않는다 — "봉이 없다"와 "안 넣었다"는 다르다."""
    with pytest.raises(CalendarSpanError):
        aggregate_calendar([], CalendarSpan.MONTH, Market.UPBIT)


def test_zones_are_resolved_from_the_tz_database() -> None:
    """시간대는 tz DB 에서 온다 — 오프셋을 박으면 서머타임에 한 시간 밀린다."""
    assert str(zone_for(Market.NASDAQ)) == "America/New_York"
    assert str(zone_for(Market.KRX)) == "Asia/Seoul"
    # 24시간 장은 "현지"가 없다.
    assert str(zone_for(Market.UPBIT)) == "UTC"
