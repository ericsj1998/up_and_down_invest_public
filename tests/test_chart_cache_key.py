"""작도 캐시 열쇠 — 라이브 창이 밀려 봉 수가 같아도 다시 그린다 (2026-10-03 "BTC 한시간봉이 점프").

라이브 급전(T313)은 창을 넘는 옛 봉을 버려 봉 수가 늘지 않는다. 봉 수만 열쇠로 쓰던 캐시는
배포 뒤 첫 그림을 계속 줬고, 화면은 17시에서 멈춘 차트 끝에 실시간 봉을 붙여 점프처럼 보였다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.apps.api.walkforward import chart_key
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

INSTRUMENT = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 10, 2, tzinfo=UTC)


def bars(first: int, n: int) -> list[Candle]:
    return [
        Candle(
            instrument=INSTRUMENT,
            timeframe=Timeframe.H1,
            ts=T0 + timedelta(hours=first + k),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
        )
        for k in range(n)
    ]


def test_sliding_window_with_same_count_changes_the_key() -> None:
    before = bars(0, 800)
    after = bars(1, 800)  # 새 봉 하나가 들어오고 가장 옛 봉 하나가 빠졌다 — 봉 수는 같다
    assert len(before) == len(after)
    assert chart_key(before) != chart_key(after)


def test_same_rows_keep_the_key() -> None:
    assert chart_key(bars(0, 800)) == chart_key(bars(0, 800))


def test_growing_replay_still_changes_the_key() -> None:
    assert chart_key(bars(0, 10)) != chart_key(bars(0, 11))


def test_empty_rows() -> None:
    assert chart_key([]) == (0, None)
