"""as-of 뷰의 빠른 길이 느린 길과 **같은 답**을 내는가 (T848 재현 최적화 A · 2026-11-17).

`visible_upto` 는 첫 봉부터 세던 것을 이분 탐색으로, `AsOfSequence` 의 구간 읽기는 보이는 앞부분을
통째로 복사하던 것을 필요한 구간만 읽게 바꿨다. 긴 재현에서 걸음마다 이력 전체를 훑던 자리다.
여기서는 옛 구현을 그대로 옮겨 두고 무작위 입력으로 맞댄다 — 한 번이라도 다르면 재현이 달라진다.
"""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.context.guard import AsOfSequence, visible_upto
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.ingest.timeframes import interval

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def candles_at(offsets: list[int], frame: Timeframe) -> list[Candle]:
    """봉 간격의 배수 자리에 선 봉들(빈 자리 · 겹친 자리 포함 가능)."""
    step = interval(frame)
    return [
        Candle(
            instrument=BTC,
            timeframe=frame,
            ts=ORIGIN + step * offset,
            open=Decimal(1000 + index),
            high=Decimal(1002 + index),
            low=Decimal(998 + index),
            close=Decimal(1000 + index),
            volume=Decimal(10),
        )
        for index, offset in enumerate(offsets)
    ]


def slow_visible_upto(candles: list[Candle], as_of: datetime, frame: Timeframe) -> int:
    """옛 구현 그대로 — 첫 봉부터 센다."""
    step = interval(frame)
    visible = 0
    for candle in candles:
        if candle.ts + step > as_of:
            break
        visible += 1
    return visible


def test_visible_upto_matches_the_linear_count() -> None:
    """빈 자리 · 겹친 시각 · 경계 시각 · 창 밖 시각까지 옛 답과 같다."""
    rnd = random.Random(848)
    for _ in range(300):
        frame = rnd.choice([Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1])
        size = rnd.randrange(0, 60)
        offsets = sorted(rnd.choices(range(80), k=size))  # 오름차순 · 겹침 허용
        rows = candles_at(offsets, frame)
        step = interval(frame)
        for _ in range(20):
            as_of = (
                ORIGIN
                + step * rnd.randrange(-2, 84)
                + timedelta(seconds=rnd.choice([0, 0, 1, -1, 17]))
            )
            assert visible_upto(rows, as_of, frame) == slow_visible_upto(rows, as_of, frame)


def test_visible_upto_accepts_a_view_as_source() -> None:
    """리스트가 아닌 순서형(뷰)에도 같은 답."""
    rows = candles_at(list(range(40)), Timeframe.M15)
    view = AsOfSequence(rows, 30, ORIGIN)
    step = interval(Timeframe.M15)
    for k in range(-1, 45):
        as_of = ORIGIN + step * k
        assert visible_upto(view, as_of, Timeframe.M15) == slow_visible_upto(
            list(view), as_of, Timeframe.M15
        )


def test_slices_match_the_copy_then_slice_path() -> None:
    """구간 읽기 — 음수 · 생략 · 거꾸로 걸음까지 옛 길(복사 뒤 자르기)과 같은 봉 · 같은 순서."""
    rnd = random.Random(8481)
    pick = [None, 0, 1, 2, 5, 9, 10, 11, 30, -1, -2, -5, -10, -11, -40]
    steps = [None, 1, 2, 3, -1, -2]
    for _ in range(200):
        size = rnd.randrange(0, 25)
        rows = candles_at(list(range(size)), Timeframe.H1)
        visible = rnd.randrange(0, size + 1)
        view = AsOfSequence(rows, visible, ORIGIN)
        for _ in range(40):
            cut = slice(rnd.choice(pick), rnd.choice(pick), rnd.choice(steps))
            want = list(rows[:visible])[cut]
            got = view[cut]
            assert isinstance(got, list)
            assert len(got) == len(want)
            assert all(a is b for a, b in zip(got, want, strict=True))
