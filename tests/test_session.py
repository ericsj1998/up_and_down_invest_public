"""세션 판정 테스트 — **서머타임과 "모른다"가 핵심이다**.

정규장 시각이 맞는지만 재면 부족하다. 이 모듈이 실제로 막아야 하는 실패는 둘이다:

1. **서머타임을 안 타서** 미국 세션이 한 해의 절반 동안 한 시간 밀리는 것
2. **휴장일을 모르면서 "거래 가능"이라고 답하는 것**

둘 다 조용히 틀리고, 결과가 그럴듯해서 발견되지 않는다.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.domain.market import MarketSession
from updown.common.domain.session import (
    MarketCalendar,
    SessionConfigError,
    Tradability,
    load_calendar,
    parse_calendar,
    regular_only,
)


@pytest.fixture(scope="module")
def calendar() -> MarketCalendar:
    """실제 설정 파일로 만든 달력 — 픽스처를 손으로 쓰지 않는 이유.

    Returns:
        세션 달력.

    Note:
        가짜 설정으로 테스트하면 `config/market_sessions.yml` 이 틀려도 통과한다.
        이 파일이 곧 운영 값이므로 그것을 검증한다.
    """
    return load_calendar()


def _utc(iso: str) -> datetime:
    """`2026-08-06T14:00:00Z` 를 UTC aware 로."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)


@pytest.mark.parametrize(
    ("market", "moment", "expected"),
    [
        # ── KRX (서머타임 없음) ──────────────────────────────
        (Market.KRX, "2026-08-06T00:30:00Z", MarketSession.REGULAR),  # KST 09:30
        (Market.KRX, "2026-08-06T06:29:00Z", MarketSession.REGULAR),  # KST 15:29
        (Market.KRX, "2026-08-05T23:40:00Z", MarketSession.PRE_OPEN),  # KST 08:40
        (Market.KRX, "2026-08-06T08:00:00Z", MarketSession.POST_CLOSE),  # KST 17:00
        (Market.KRX, "2026-08-08T02:00:00Z", MarketSession.CLOSED),  # 토요일
        # ── 미국 · 서머타임(EDT · UTC-4) ─────────────────────
        (Market.NASDAQ, "2026-08-06T13:30:00Z", MarketSession.REGULAR),  # ET 09:30
        (Market.NASDAQ, "2026-08-06T13:29:00Z", MarketSession.PRE_OPEN),  # ET 09:29
        (Market.NASDAQ, "2026-08-06T20:00:00Z", MarketSession.POST_CLOSE),  # ET 16:00
        # ── 미국 · 표준시(EST · UTC-5) — 같은 UTC 시각이 다른 세션 ──
        (Market.NASDAQ, "2026-01-15T13:30:00Z", MarketSession.PRE_OPEN),  # ET 08:30
        (Market.NASDAQ, "2026-01-15T14:30:00Z", MarketSession.REGULAR),  # ET 09:30
        # ── 코인 ────────────────────────────────────────────
        (Market.UPBIT, "2026-08-06T03:00:00Z", MarketSession.ALWAYS_OPEN),
        (Market.UPBIT, "2026-08-08T03:00:00Z", MarketSession.ALWAYS_OPEN),  # 토요일도
    ],
)
def test_session_at(
    calendar: MarketCalendar, market: Market, moment: str, expected: MarketSession
) -> None:
    """시각 → 세션."""
    assert calendar.session_at(market, _utc(moment)) is expected


def test_same_utc_hour_is_different_session_across_dst(calendar: MarketCalendar) -> None:
    """🔴 **같은 UTC 시각이 계절에 따라 다른 세션이다.**

    이 테스트가 서머타임 처리의 전부다. 오프셋을 하드코딩하면 여기서 깨진다 —
    실측에서도 AAPL 정규장 UTC 창이 13~20 과 14~21 사이를 오갔다.
    """
    summer = calendar.session_at(Market.NASDAQ, _utc("2026-07-15T13:30:00Z"))
    winter = calendar.session_at(Market.NASDAQ, _utc("2026-01-15T13:30:00Z"))
    assert summer is MarketSession.REGULAR
    assert winter is MarketSession.PRE_OPEN
    assert summer is not winter


def test_trading_date_uses_local_calendar(calendar: MarketCalendar) -> None:
    """🔴 KRX 월요일 봉이 UTC 로는 일요일이다.

    실측에서 KRX 일봉 511개 중 **101개가 UTC 기준 토·일**이었다. UTC 날짜로 거래일을
    판정하면 월요일이 통째로 주말로 분류된다.
    """
    # 2026-08-02(일) 15:00 UTC = 2026-08-03(월) 00:00 KST
    moment = _utc("2026-08-02T15:00:00Z")
    assert moment.date() == date(2026, 8, 2)  # UTC 로는 일요일
    assert calendar.trading_date(Market.KRX, moment) == date(2026, 8, 3)  # 현지로는 월요일


def test_naive_datetime_is_rejected(calendar: MarketCalendar) -> None:
    """naive 시각은 거부한다 (절대 규칙 #7)."""
    with pytest.raises(SessionConfigError, match="naive"):
        calendar.session_at(Market.KRX, datetime(2026, 8, 6, 9, 30))


def test_every_market_is_configured(calendar: MarketCalendar) -> None:
    """🔴 `Market` 에 있는 시장은 **전부** 설정에 있어야 한다.

    빠진 시장이 있으면 그 시장 봉이 판정될 때 예외가 나거나, 더 나쁘게는 기본값으로
    24시간 장이 되어 주식이 밤에도 열린 것으로 잡힌다. 시장을 추가할 때 설정을
    빠뜨리는 것을 여기서 잡는다.
    """
    missing = [m for m in Market if m not in calendar.hours]
    assert not missing, f"config/market_sessions.yml 에 없는 시장: {missing}"


def test_unknown_market_is_rejected() -> None:
    """설정에 없는 시장은 **기본값으로 넘어가지 않는다.**

    24시간 장으로 떨어지면 주식이 밤에도 열린 것으로 판정되고, 그 위에서 잰 갭·시가·
    종가가 전부 다른 뜻이 된다 (절대 규칙 #8).
    """
    partial = parse_calendar({"markets": {"UPBIT": {"timezone": "UTC", "always_open": True}}})
    with pytest.raises(SessionConfigError, match="세션 설정이 없다"):
        partial.session_at(Market.KRX, _utc("2026-08-06T00:30:00Z"))


def test_missing_regular_window_is_rejected() -> None:
    """정규장 창을 빠뜨린 것과 24시간 장인 것을 구분한다."""
    with pytest.raises(SessionConfigError, match="always_open"):
        parse_calendar({"markets": {"KRX": {"timezone": "Asia/Seoul", "trading_days": ["mon"]}}})


class TestTradability:
    """거래 가능 판정 — **3값인 것이 요점이다**."""

    def test_coin_is_always_open(self, calendar: MarketCalendar) -> None:
        """코인은 언제나 거래 가능."""
        verdict, _ = calendar.tradability(Market.UPBIT, _utc("2026-01-01T03:00:00Z"))
        assert verdict is Tradability.OPEN

    def test_weekend_is_closed_without_holiday_data(self, calendar: MarketCalendar) -> None:
        """주말은 휴장일 정보 없이도 단정할 수 있다 — 요일만으로 결정되기 때문이다."""
        verdict, why = calendar.tradability(Market.KRX, _utc("2026-08-08T02:00:00Z"))
        assert verdict is Tradability.CLOSED
        assert "영업 요일" in why

    def test_unknown_when_holidays_are_missing(self, calendar: MarketCalendar) -> None:
        """🔴 **휴장일을 모르면 '거래 가능'이라고 답하지 않는다.**

        크리스마스 10:00 에 `OPEN` 을 돌려주는 것이 이 모듈이 막아야 할 실패다.
        `CLOSED` 로 접지 않는 이유는 대응이 다르기 때문이다 — `CLOSED` 는 기다리는
        것이고 `UNKNOWN` 은 확인하고 움직이는 것이다.
        """
        # 2025 는 휴장일 유효 구간(2026) 밖 — 목록이 있어도 그 밖은 UNKNOWN (T238)
        verdict, why = calendar.tradability(Market.KRX, _utc("2025-08-06T00:30:00Z"))
        assert verdict is Tradability.UNKNOWN
        assert "휴장일" in why

    def test_open_once_trading_days_are_known(self, calendar: MarketCalendar) -> None:
        """거래일을 알려 주면 단정할 수 있다."""
        filled = calendar.with_trading_days(
            Market.KRX, [date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
        )
        verdict, why = filled.tradability(Market.KRX, _utc("2026-08-06T00:30:00Z"))
        assert verdict is Tradability.OPEN
        assert why == "정규장"

    def test_holiday_is_derived_from_absence(self, calendar: MarketCalendar) -> None:
        """🔴 휴장일은 **목록으로 받지 않고 거꾸로 구한다.**

        영업 요일인데 거래일에 없으면 휴장일이다. 목록을 직접 받으면 "빠뜨린 휴장일"과
        "정말 영업일"을 구분할 수 없다.
        """
        # 8/5(수)·8/7(금) 만 거래 → 8/6(목) 은 휴장일로 잡혀야 한다
        filled = calendar.with_trading_days(Market.KRX, [date(2026, 8, 5), date(2026, 8, 7)])
        assert date(2026, 8, 6) in filled.holidays[Market.KRX]
        verdict, why = filled.tradability(Market.KRX, _utc("2026-08-06T00:30:00Z"))
        assert verdict is Tradability.CLOSED
        assert "휴장일" in why

    def test_outside_known_range_is_unknown(self, calendar: MarketCalendar) -> None:
        """⚠️ 적재 구간 밖은 여전히 '모른다' — 미래 휴장일은 알 수 없다."""
        filled = calendar.with_trading_days(Market.KRX, [date(2026, 8, 5), date(2026, 8, 7)])
        verdict, why = filled.tradability(Market.KRX, _utc("2027-08-05T00:30:00Z"))
        assert verdict is Tradability.UNKNOWN
        assert "밖" in why

    def test_pre_open_is_not_tradable(self, calendar: MarketCalendar) -> None:
        """장전은 거래 불가 — 세션은 `PRE_OPEN` 이지만 신규 진입은 못 한다."""
        filled = calendar.with_trading_days(Market.KRX, [date(2026, 8, 5), date(2026, 8, 6)])
        verdict, why = filled.tradability(Market.KRX, _utc("2026-08-05T23:40:00Z"))
        assert verdict is Tradability.CLOSED
        assert "장전" in why


def _candle(market: Market, iso: str) -> Candle:
    """테스트용 봉."""
    return Candle(
        instrument=Instrument(market, "TEST", "테스트", AssetType.STOCK, Currency.USD),
        timeframe=Timeframe.M15,
        ts=_utc(iso),
        open=Decimal(1),
        high=Decimal(1),
        low=Decimal(1),
        close=Decimal(1),
        volume=Decimal(1),
    )


def test_regular_only_drops_extended_hours(calendar: MarketCalendar) -> None:
    """🔴 **이 함수를 안 쓰면 갭이 사라진다.**

    실측: AAPL 갭 중앙값이 정규장 0.414% → 시간외 포함 0.008% (52배 축소).
    """
    candles = [
        _candle(Market.NASDAQ, "2026-08-06T12:00:00Z"),  # ET 08:00 장전
        _candle(Market.NASDAQ, "2026-08-06T14:00:00Z"),  # ET 10:00 정규장
        _candle(Market.NASDAQ, "2026-08-06T21:00:00Z"),  # ET 17:00 애프터
    ]
    kept = regular_only(candles, calendar)
    assert [c.ts.hour for c in kept] == [14]


def test_regular_only_keeps_all_coin_candles(calendar: MarketCalendar) -> None:
    """코인은 하나도 안 걸러진다 — 24시간 장에는 '정규장 아닌 시간'이 없다."""
    candles = [_candle(Market.UPBIT, f"2026-08-06T{h:02d}:00:00Z") for h in (0, 8, 16, 23)]
    assert len(regular_only(candles, calendar)) == 4


class TestMarketGroup:
    """운용 갈래 — **코인 / 국내주식 / 해외주식**."""

    def test_every_market_is_classified(self) -> None:
        """🔴 `Market` 에 있는 시장은 전부 갈래가 있어야 한다.

        빠지면 그 시장이 비용·통화·휴장일 취급 없이 섞인다.
        """
        for market in Market:
            assert MarketGroup.of(market) in MarketGroup

    def test_domestic_and_foreign_are_split(self) -> None:
        """`AssetType` 으로는 못 가르는 것을 가른다.

        둘 다 `STOCK` 이지만 거래세·통화·서머타임 취급이 다르다.
        """
        assert MarketGroup.of(Market.KRX) is MarketGroup.DOMESTIC_STOCK
        assert MarketGroup.of(Market.NASDAQ) is MarketGroup.FOREIGN_STOCK
        assert MarketGroup.of(Market.NYSE) is MarketGroup.FOREIGN_STOCK

    def test_coin_is_its_own_group(self) -> None:
        """코인은 갭도 휴장일도 없어 따로 둔다."""
        assert MarketGroup.of(Market.UPBIT) is MarketGroup.COIN
