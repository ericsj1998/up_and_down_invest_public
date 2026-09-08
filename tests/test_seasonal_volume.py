"""거래량 기준선 — **같은 시간대끼리 비교한다** (T03).

막아야 하는 실패 셋:

1. 자기 자신을 분모에 넣어 급증이 희석되는 것
2. 표본 부족을 1.0 으로 채워 워밍업 구간의 급증 판정이 조용히 죽는 것
3. 코인에서 요일을 빼 주말이 항상 "거래량 부족"으로 잡히는 것
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.indicators.seasonal_volume import (
    MIN_SAMPLES,
    seasonal_ratio,
    slot_of,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
AAPL = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)


def bar(ts: datetime, volume: str, who: Instrument = BTC) -> Candle:
    """테스트용 봉."""
    return Candle(
        instrument=who,
        timeframe=Timeframe.M15,
        ts=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(volume),
    )


class TestSlot:
    def test_coin_slot_includes_weekday(self) -> None:
        """🔴 코인은 요일을 넣는다 — 주말이 평일 기준선에 눌리면 안 된다."""
        monday = datetime(2026, 8, 10, 3, 15, tzinfo=UTC)
        tuesday = monday + timedelta(days=1)
        assert slot_of(Market.UPBIT, monday) != slot_of(Market.UPBIT, tuesday)
        assert slot_of(Market.UPBIT, monday) == slot_of(Market.UPBIT, monday + timedelta(days=7))

    def test_stock_slot_excludes_weekday(self) -> None:
        """주식은 거래일이 주 5일뿐이라 요일까지 나누면 표본이 주 1개가 된다."""
        monday = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
        tuesday = monday + timedelta(days=1)
        assert slot_of(Market.NASDAQ, monday) == slot_of(Market.NASDAQ, tuesday)

    def test_different_time_is_different_slot(self) -> None:
        base = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
        assert slot_of(Market.NASDAQ, base) != slot_of(Market.NASDAQ, base + timedelta(minutes=15))


class TestSeasonalRatio:
    def _weekly(self, volumes: list[str]) -> list[Candle]:
        """같은 요일·같은 시각의 봉을 주 단위로 만든다."""
        start = datetime(2026, 1, 5, 3, 15, tzinfo=UTC)  # 월요일
        return [bar(start + timedelta(weeks=i), v) for i, v in enumerate(volumes)]

    def test_warmup_is_none_not_one(self) -> None:
        """⛔ 표본 부족을 1.0 으로 채우지 않는다 — 급증 판정이 조용히 죽는다."""
        got = seasonal_ratio(self._weekly(["10"] * (MIN_SAMPLES + 1)), Market.UPBIT)
        assert got[:MIN_SAMPLES] == [None] * MIN_SAMPLES
        assert got[MIN_SAMPLES] is not None

    def test_ratio_against_same_slot_average(self) -> None:
        """같은 슬롯 과거 평균 대비로 잰다."""
        got = seasonal_ratio(self._weekly(["10", "10", "10", "10", "30"]), Market.UPBIT)
        assert got[4] == 3.0

    def test_self_is_not_in_the_denominator(self) -> None:
        """🔴 폭발한 거래량이 자기 분모를 끌어올리면 배수가 축소된다."""
        got = seasonal_ratio(self._weekly(["10", "10", "10", "10", "100"]), Market.UPBIT)
        assert got[4] == 10.0  # 자기 포함이면 100/28 = 3.6 이 됐을 것

    def test_zero_baseline_is_none_not_infinity(self) -> None:
        """⚠️ 무한대 배수는 급증이 아니라 데이터 부재다."""
        got = seasonal_ratio(self._weekly(["0", "0", "0", "0", "50"]), Market.UPBIT)
        assert got[4] is None

    def test_slots_do_not_leak(self) -> None:
        """다른 시간대의 봉이 기준선에 섞이지 않는다."""
        start = datetime(2026, 1, 5, 3, 15, tzinfo=UTC)
        rows = [bar(start + timedelta(weeks=i), "10") for i in range(MIN_SAMPLES)]
        rows.append(bar(start + timedelta(weeks=MIN_SAMPLES), "30"))
        # 다른 슬롯(한 시간 뒤)에 거대한 거래량이 있어도 위 판정에 영향이 없다
        rows.append(bar(start + timedelta(hours=1), "99999"))
        rows.sort(key=lambda c: c.ts)
        got = seasonal_ratio(rows, Market.UPBIT)
        assert got[[c.ts for c in rows].index(start + timedelta(weeks=MIN_SAMPLES))] == 3.0
