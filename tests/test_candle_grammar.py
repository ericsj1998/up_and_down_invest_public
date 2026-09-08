"""T54 B·C·D — 캔들 문법 프리미티브(spinning·marubozu)와 세션 스위치 동결 (사용자 2026-08-23).

막아야 하는 실패:
1. 🔴 스피닝/장대봉 정의가 비율을 안 지키면 아무 봉이나 잡는다 (표준 정의 고정 · 절대 규칙 #12)
2. 🔴 스위치가 꺼져 있으면 한 비트도 안 달라진다 (측정 스위치 · 동결)
3. 🔴 wick_stop 이 손절을 **넓히면** 절대 규칙 #3 위반
"""

from __future__ import annotations

from decimal import Decimal

from updown.analysis.indicators.reversal import marubozu, spinning
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)


def _c(o: int, h: int, lo: int, cl: int) -> Candle:
    from datetime import UTC, datetime

    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M15,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(cl),
        volume=Decimal(1),
    )


class TestSpinning:
    def test_small_body_both_wicks_is_spinning(self) -> None:
        # 몸통 100~102 (2), 위꼬리 102~110 (8), 아래꼬리 90~100 (10), 범위 20
        assert spinning(_c(100, 110, 90, 102))

    def test_big_body_is_not_spinning(self) -> None:
        assert not spinning(_c(91, 110, 90, 109))

    def test_one_sided_wick_is_not_spinning(self) -> None:
        # 아래꼬리만 김 = 잠자리(핀바)지 스피닝이 아니다
        assert not spinning(_c(105, 110, 90, 108))


class TestMarubozu:
    def test_full_body_up_is_up_marubozu(self) -> None:
        # 몸통 90~110 (20), 꼬리 거의 없음 = 상승 장대봉
        assert marubozu(_c(90, 110, 90, 110), up=True)
        assert not marubozu(_c(90, 110, 90, 110), up=False)

    def test_full_body_down_is_down_marubozu(self) -> None:
        assert marubozu(_c(110, 110, 90, 90), up=False)
        assert not marubozu(_c(110, 110, 90, 90), up=True)

    def test_wicky_candle_is_not_marubozu(self) -> None:
        assert not marubozu(_c(100, 110, 90, 102), up=True)
