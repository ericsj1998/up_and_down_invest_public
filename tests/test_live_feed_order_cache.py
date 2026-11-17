"""급전의 줄 세우기 기억과 "새 봉이 생길 수 없는 동안은 묻지 않는다" (T852 S6 · E6).

지키는 것:

- `judged` · `observed` 는 봉을 넣고 빼는 어떤 순서 뒤에도 **부를 때마다 새로 줄 세운 것과 같은
  값**을 낸다(기억은 봉이 바뀔 때 버려진다).
- 돌려받은 목록을 고쳐도 급전이 안 망가진다.
- `last_ts` 는 그 축의 마지막 마감 봉 시각이다.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward.live_feed import LiveFeed

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
M5 = timedelta(minutes=5)
H1 = timedelta(hours=1)


def bar(ts: datetime, price: int = 100) -> Candle:
    value = Decimal(price)
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M5,
        ts=ts,
        open=value,
        high=value,
        low=value,
        close=value,
        volume=Decimal(1),
    )


def fresh(window: int | None = None) -> LiveFeed:
    seed: dict[Timeframe, Sequence[Candle]] = {
        Timeframe.H1: [bar(T0 + H1 * i) for i in range(30)],
        Timeframe.M5: [bar(T0 + M5 * i) for i in range(200)],
    }
    return LiveFeed(seed, Timeframe.H1, window=window)


def slow_judged(feed: LiveFeed, frame: Timeframe) -> list[Candle]:
    """예전 구현 — 부를 때마다 전부 줄 세운다(대조용)."""
    rows = feed._rows[frame]  # pyright: ignore[reportPrivateUsage]
    out = [rows[key] for key in sorted(rows) if key < feed.cursor]
    return out if feed.window is None else out[-feed.window :]


def slow_observed(feed: LiveFeed, frame: Timeframe) -> list[Candle]:
    rows = feed._rows[frame]  # pyright: ignore[reportPrivateUsage]
    return [rows[key] for key in sorted(rows)]


def same(feed: LiveFeed) -> None:
    for frame in (Timeframe.H1, Timeframe.M5):
        assert feed.judged(frame) == slow_judged(feed, frame)
        assert feed.observed(frame) == slow_observed(feed, frame)


def test_reads_match_a_fresh_sort_after_any_mix_of_changes() -> None:
    rng = random.Random(7)
    for window in (None, 40):
        feed = fresh(window)
        same(feed)
        hour = 30
        five = 200
        for _ in range(300):
            move = rng.choice(["push_h1", "push_m5", "backfill", "overwrite", "read"])
            if move == "push_h1":
                feed.push(Timeframe.H1, bar(T0 + H1 * hour, hour), closed=True)
                hour += 1
            elif move == "push_m5":
                feed.push(Timeframe.M5, bar(T0 + M5 * five, five), closed=True)
                five += rng.choice([1, 1, 2])  # 가끔 구멍
            elif move == "backfill":
                at = rng.randrange(0, five)
                feed.backfill(Timeframe.M5, [bar(T0 + M5 * at, 7), bar(T0 + M5 * (at + 1), 7)])
            elif move == "overwrite":
                at = rng.randrange(max(0, five - 20), five)
                feed.push(Timeframe.M5, bar(T0 + M5 * at, 999), closed=True)
            same(feed)


def test_a_returned_list_can_be_changed_without_hurting_the_feed() -> None:
    feed = fresh()
    feed.judged(Timeframe.M5).clear()
    feed.observed(Timeframe.M5).clear()
    same(feed)
    assert len(feed.observed(Timeframe.M5)) == 200


def test_a_view_frame_added_and_dropped_keeps_reads_right() -> None:
    feed = fresh()
    m15 = timedelta(minutes=15)
    assert feed.add_frame(Timeframe.M15, [bar(T0 + m15 * i) for i in range(50)]) == 50
    assert len(feed.observed(Timeframe.M15)) == 50
    assert feed.last_ts(Timeframe.M15) == T0 + m15 * 49
    assert feed.drop_frame(Timeframe.M15) is True
    assert feed.last_ts(Timeframe.M15) is None


def test_last_ts_follows_new_bars() -> None:
    feed = fresh()
    assert feed.last_ts(Timeframe.M5) == T0 + M5 * 199
    feed.push(Timeframe.M5, bar(T0 + M5 * 200), closed=True)
    assert feed.last_ts(Timeframe.M5) == T0 + M5 * 200
    feed.push(Timeframe.M5, bar(T0 + M5 * 201), closed=False)
    assert feed.last_ts(Timeframe.M5) == T0 + M5 * 200, "진행 중 봉은 안 센다"
