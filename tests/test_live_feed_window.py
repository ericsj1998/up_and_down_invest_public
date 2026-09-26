"""T313 — 라이브 급전 창 · 보기용 축 싣고 내리기 · 판정 축 (2026-09-26).

지키려는 성질:

1. **판정 창 = 연구 · 재현의 800봉 창.** `judged` 는 커서까지의 마지막 `window` 봉 — 봉인 급전을
   800봉으로 자른 것(`CappedFeed`)과 같다.
2. **급전이 가동 시간에 따라 안 자란다.** 보관은 창 + 여유(`KEEP_SLACK`)까지.
3. **판정 축은 절대 안 내린다** — 진입 축 · 판정을 깨우는 축.
4. 판정 축 = 걸음 · 진입 · 진입 한 칸 위(주 추세) · 1d · 방아쇠.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward.live_feed import KEEP_SLACK, LiveFeed
from updown.orchestration.walkforward.live_runner import (
    WATCH_TTL,
    LiveRunner,
    decision_frames,
)
from updown.orchestration.walkforward.sealed import Seal, SealedFeed

BTC = Instrument(Market.GATE, "BTC_USDT", "BTC 무기한", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
SPAN = {Timeframe.H1: timedelta(hours=1), Timeframe.M1: timedelta(minutes=1)}


def bars(frame: Timeframe, n: int, start: int = 0) -> list[Candle]:
    return [
        Candle(
            instrument=BTC,
            timeframe=frame,
            ts=T0 + SPAN[frame] * i,
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal(1),
        )
        for i in range(start, start + n)
    ]


class TestWindow:
    def test_judged_is_the_last_window_bars_like_the_research_cap(self) -> None:
        seed = bars(Timeframe.H1, 1000)
        live = LiveFeed({Timeframe.H1: seed}, Timeframe.H1, window=800)
        sealed = SealedFeed(
            {Timeframe.H1: seed}, Seal(start=seed[0].ts, end=seed[-1].ts + SPAN[Timeframe.H1])
        )
        while not sealed.finished:
            sealed.advance(Timeframe.H1)
        want = sealed.judged(Timeframe.H1)[-800:]
        assert live.judged(Timeframe.H1) == want

    def test_storage_is_capped_and_oldest_bars_go_first(self) -> None:
        live = LiveFeed({Timeframe.H1: bars(Timeframe.H1, 1000)}, Timeframe.H1, window=800)
        assert len(live.observed(Timeframe.H1)) == 800 + KEEP_SLACK
        for item in bars(Timeframe.H1, 50, start=1000):
            live.push(Timeframe.H1, item, closed=True)
        kept = live.observed(Timeframe.H1)
        assert len(kept) == 800 + KEEP_SLACK
        assert kept[-1].ts == T0 + SPAN[Timeframe.H1] * 1049
        assert len(live.judged(Timeframe.H1)) == 800

    def test_no_window_keeps_the_old_behaviour(self) -> None:
        live = LiveFeed({Timeframe.H1: bars(Timeframe.H1, 1000)}, Timeframe.H1)
        assert len(live.judged(Timeframe.H1)) == 1000


class TestViewFrames:
    def test_add_and_drop_a_view_frame(self) -> None:
        live = LiveFeed({Timeframe.H1: bars(Timeframe.H1, 10)}, Timeframe.H1, window=800)
        assert live.add_frame(Timeframe.M1, bars(Timeframe.M1, 900)) == 800 + KEEP_SLACK
        assert Timeframe.M1 in live.timeframes
        assert live.drop_frame(Timeframe.M1) is True
        assert live.timeframes == (Timeframe.H1,)

    def test_the_entry_and_wake_frames_are_never_dropped(self) -> None:
        live = LiveFeed(
            {Timeframe.H1: bars(Timeframe.H1, 10), Timeframe.M1: bars(Timeframe.M1, 10)},
            Timeframe.H1,
            window=800,
        )
        live.judge_on.add(Timeframe.M1)
        assert live.drop_frame(Timeframe.H1) is False
        assert live.drop_frame(Timeframe.M1) is False
        assert live.add_frame(Timeframe.M15, []) == 0 and Timeframe.M15 not in live.timeframes


class TestDecisionFrames:
    def test_one_hour_and_four_hour_legs(self) -> None:
        got = decision_frames([Timeframe.H1, Timeframe.H4], step=Timeframe.M5)
        assert got == (Timeframe.M5, Timeframe.H1, Timeframe.H4, Timeframe.D1)

    def test_four_hour_leg_reads_the_day_above(self) -> None:
        got = decision_frames([Timeframe.H4], step=Timeframe.M5)
        assert got == (Timeframe.M5, Timeframe.H4, Timeframe.D1)

    def test_trigger_frame_is_kept(self) -> None:
        got = decision_frames([Timeframe.H1], step=Timeframe.M5, extra=[Timeframe.M1])
        assert got == (Timeframe.M1, Timeframe.M5, Timeframe.H1, Timeframe.H4, Timeframe.D1)


class _Quotes:
    def __init__(self) -> None:
        self.asked: list[Timeframe] = []

    def supported_frames(self, frames: Any) -> tuple[Timeframe, ...]:
        return tuple(frames)

    async def get_candles(
        self, _instrument: Any, frame: Timeframe, _lo: Any, _hi: Any
    ) -> list[Any]:
        self.asked.append(frame)
        return bars(frame, 5)


def _runner(decision: frozenset[Timeframe]) -> Any:
    feed = LiveFeed({Timeframe.H1: bars(Timeframe.H1, 10)}, Timeframe.H1, window=800)
    forgot: list[Timeframe] = []
    stub = SimpleNamespace(
        _feed=feed,
        _decision=decision,
        _quotes=_Quotes(),
        _watched={},
        _refreshed={},
        _refresh_state={},
        _session=SimpleNamespace(forget_frame=forgot.append, price_frame=None),
        instrument=BTC,
        entry=Timeframe.H1,
        price_frame=Timeframe.M5,
        forgot=forgot,
    )

    def needs(frame: Timeframe) -> bool:
        return LiveRunner._needs(cast("Any", stub), frame)  # pyright: ignore[reportPrivateUsage]

    stub._needs = needs
    return stub


class TestRunnerViewFrames:
    def test_a_watched_view_frame_is_loaded_then_dropped_when_nobody_looks(self) -> None:
        stub = _runner(frozenset({Timeframe.M5, Timeframe.H1, Timeframe.H4, Timeframe.D1}))
        stub._watched[Timeframe.M1] = time.monotonic()
        added = asyncio.run(LiveRunner.refresh(stub, Timeframe.M1))
        assert added == 4 and Timeframe.M1 in stub._feed.timeframes  # 마지막(진행 중)은 버림
        stub._watched[Timeframe.M1] = time.monotonic() - WATCH_TTL - 1
        LiveRunner._drop_unwatched(stub)  # pyright: ignore[reportPrivateUsage]
        assert Timeframe.M1 not in stub._feed.timeframes and stub.forgot == [Timeframe.M1]

    def test_the_old_style_runner_neither_loads_nor_drops(self) -> None:
        stub = _runner(frozenset())
        assert asyncio.run(LiveRunner.refresh(stub, Timeframe.M1)) == 0
        assert stub._quotes.asked == []
        assert LiveRunner.viewable(stub, [Timeframe.M1, Timeframe.H1]) == (Timeframe.H1,)

    def test_viewable_offers_the_exchange_frames_when_decision_frames_are_known(self) -> None:
        stub = _runner(frozenset({Timeframe.H1}))
        got = LiveRunner.viewable(stub, [Timeframe.M1, Timeframe.H1])
        assert got == (Timeframe.M1, Timeframe.H1)

    def test_decision_frames_are_always_warm(self) -> None:
        stub = _runner(frozenset({Timeframe.H4, Timeframe.D1}))
        assert stub._needs(Timeframe.H4) and stub._needs(Timeframe.D1)
        assert not stub._needs(Timeframe.M15)
