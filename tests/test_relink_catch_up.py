"""소켓이 다시 붙은 뒤 — 끊긴 사이 닫힌 봉을 받아 판정한다 · 못 붙는 동안은 쉰다 (T852 E4).

지키는 것:

- 스트림이 **안에서 스스로 다시 붙어도** 러너가 안다(재연결 수가 늘면 따라잡기를 부른다).
- 끊긴 사이 닫힌 봉이 **없으면 거래소에 묻지 않는다** — 재연결의 대부분은 봉 중간이다.
- 닫힌 봉이 있으면 한 번 받아 그 봉을 판정한다.
- 닫힌 지 오래인데 봉이 없으면(장이 닫힌 시장) 한 번만 묻고 기다리지 않는다.
- 한 번도 못 받고 이어서 끊기면 쉬고 다시 붙는다(받다가 끊긴 첫 번째는 바로).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.gate import ws as ws_mod
from updown.marketdata.gate.ws import (
    RELINK_PAUSE_MAX_S,
    GateCandleStream,
    LiveCandle,
    relink_pause_s,
)
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.live_feed import LiveFeed
from updown.orchestration.walkforward.live_runner import LiveRunner

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
H1 = timedelta(hours=1)


def hour_floor() -> datetime:
    now = datetime.now(UTC)
    return now.replace(minute=0, second=0, microsecond=0)


def bar(ts: datetime) -> Candle:
    one = Decimal(100)
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=ts,
        open=one,
        high=one,
        low=one,
        close=one,
        volume=Decimal(1),
    )


class Log:
    def __init__(self) -> None:
        self.names: list[str] = []

    def warning(self, name: str, **_: object) -> None:
        self.names.append(name)

    def error(self, name: str, **_: object) -> None:
        self.names.append(name)


class Quotes:
    """`through` 까지 닫힌 1H 봉을 내는 가짜 조회 어댑터 — 부른 횟수를 센다."""

    def __init__(self, through: datetime | None) -> None:
        self.through = through
        self.calls = 0

    async def get_candles(
        self, ref: Any, frame: Any, start: datetime, end: datetime
    ) -> list[Candle]:
        del ref, frame, end
        self.calls += 1
        if self.through is None:
            return []
        out: list[Candle] = []
        at = self.through
        while at >= start:
            out.append(bar(at))
            at -= H1
        return sorted(out, key=lambda item: item.ts)


class Runner:
    """`_after_relink` · `_catch_up_entry` 가 만지는 부분만 가진 최소 러너(메서드는 실제 것)."""

    _after_relink = LiveRunner._after_relink  # pyright: ignore[reportPrivateUsage]
    _catch_up_entry = LiveRunner._catch_up_entry  # pyright: ignore[reportPrivateUsage]

    def __init__(self, last_closed: datetime, quotes: Quotes) -> None:
        seed: dict[Timeframe, Sequence[Candle]] = {
            Timeframe.H1: [bar(last_closed - H1 * i) for i in range(30)]
        }
        self._feed = LiveFeed(seed, Timeframe.H1)
        self._quotes = quotes
        self._log = Log()
        self.entry = Timeframe.H1
        self.instrument = BTC
        self.steps = 0
        self.heals = 0

    async def _step_this_bar(self) -> None:
        self.steps += 1

    async def _heal(self) -> None:
        self.heals += 1


@pytest.fixture
def naps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """러너가 쉰 시간들 — 실제로는 안 쉰다."""
    slept: list[float] = []

    async def nap(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(live_runner.asyncio, "sleep", nap)
    return slept


def relink(run: Runner) -> None:
    asyncio.run(run._after_relink())  # pyright: ignore[reportPrivateUsage]


def test_a_relink_in_the_middle_of_a_bar_asks_nothing(naps: list[float]) -> None:
    quotes = Quotes(hour_floor() - H1)
    run = Runner(hour_floor() - H1, quotes)  # 마지막 마감 봉까지 다 들고 있다
    relink(run)
    assert quotes.calls == 0, "놓친 봉이 없으면 판 58 개가 재연결마다 봉을 다시 받지 않는다"
    assert run.steps == 0 and naps == []


def test_a_bar_that_closed_while_cut_off_is_fetched_and_judged(naps: list[float]) -> None:
    quotes = Quotes(hour_floor() - H1)
    run = Runner(hour_floor() - 2 * H1, quotes)  # 끊긴 사이 봉 하나가 닫혔다
    relink(run)
    assert quotes.calls == 1 and run.steps == 1
    assert run._feed.cursor == hour_floor()  # pyright: ignore[reportPrivateUsage]
    assert naps == [] and "live_relink_bar_missing" not in run._log.names  # pyright: ignore[reportPrivateUsage]


def test_several_missed_bars_are_judged_once(naps: list[float]) -> None:
    quotes = Quotes(hour_floor() - H1)
    run = Runner(hour_floor() - 5 * H1, quotes)
    relink(run)
    assert quotes.calls == 1 and run.steps == 1, "늦은 신호는 신선도 문이 거른다 — 판정은 한 번"
    assert run._feed.cursor == hour_floor() and run._feed.gaps == 0  # pyright: ignore[reportPrivateUsage]
    assert naps == []


def test_a_market_that_has_been_shut_for_hours_is_asked_once(naps: list[float]) -> None:
    quotes = Quotes(None)
    run = Runner(hour_floor() - 6 * H1, quotes)
    relink(run)
    assert quotes.calls == 1 and run.steps == 0
    assert naps == [], "닫힌 지 오래인 봉은 기다려도 안 온다"


class Idle:
    """마감 봉을 안 받는 급전 — 걸음까지 안 간다. 받은 횟수만 센다."""

    def __init__(self) -> None:
        self.gaps = 0
        self.pushed = 0

    def push(self, frame: Timeframe, candle: Candle, *, closed: bool) -> bool:
        del frame, candle, closed
        self.pushed += 1
        return False


class Stream:
    """두 번째 봉을 내기 전에 **안에서 다시 붙은** 스트림."""

    def __init__(self) -> None:
        self.reconnects = 0

    async def stream(self) -> AsyncIterator[LiveCandle]:
        yield LiveCandle(candle=bar(hour_floor()), closed=False)
        self.reconnects += 1
        yield LiveCandle(candle=bar(hour_floor()), closed=False)
        yield LiveCandle(candle=bar(hour_floor()), closed=False)


def test_the_runner_notices_a_relink_hidden_inside_the_stream() -> None:
    calls: list[int] = []

    class Made:
        _consume = LiveRunner._consume  # pyright: ignore[reportPrivateUsage]

        def __init__(self) -> None:
            self._stream = Stream()
            self._feed = Idle()
            self._log = Log()
            self.entry = Timeframe.H1

        async def _after_relink(self) -> None:
            calls.append(self._stream.reconnects)

    asyncio.run(cast("Any", Made())._consume())
    assert calls == [1], "재연결 한 번에 따라잡기 한 번 — 봉마다 다시 부르지 않는다"


def test_a_failed_catch_up_does_not_stop_the_stream() -> None:
    class Made:
        _consume = LiveRunner._consume  # pyright: ignore[reportPrivateUsage]

        def __init__(self) -> None:
            self._stream = Stream()
            self._feed = Idle()
            self._log = Log()
            self.entry = Timeframe.H1

        async def _after_relink(self) -> None:
            raise RuntimeError("조회 실패")

    made = Made()
    asyncio.run(cast("Any", made)._consume())
    assert made._feed.pushed == 3, "따라잡기가 실패해도 봉은 계속 받는다"  # pyright: ignore[reportPrivateUsage]
    assert made._log.names == ["live_runner_heal_failed"]  # pyright: ignore[reportPrivateUsage]


def test_the_pause_starts_at_the_second_miss_and_is_capped() -> None:
    assert [relink_pause_s(n) for n in (0, 1, 2, 3, 4, 5)] == [0.0, 0.0, 1.0, 2.0, 4.0, 8.0]
    assert relink_pause_s(40) == RELINK_PAUSE_MAX_S
    assert relink_pause_s(10_000) == RELINK_PAUSE_MAX_S


UPDATE = {
    "channel": "futures.candlesticks",
    "event": "update",
    "result": [
        {
            "t": 1755000000,
            "v": 1,
            "c": "63502.6",
            "h": "63600",
            "l": "63400",
            "o": "63450",
            "n": "1h_BTC_USDT",
        }
    ],
}


class Dead:
    """붙자마자 끊기는 소켓."""

    async def __aenter__(self) -> Dead:
        raise OSError("연결 거부")

    async def __aexit__(self, *_: object) -> bool:
        return False


class Alive:
    def __init__(self) -> None:
        self._frames = [json.dumps(UPDATE)]

    async def send(self, payload: str) -> None:
        del payload

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("닿지 않는다")

    async def __aenter__(self) -> Alive:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


def test_a_stream_that_cannot_connect_rests_between_tries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[object] = []
    slept: list[float] = []

    def fake_connect(*_: object, **__: object) -> object:
        sock: object = Dead() if len(opened) < 3 else Alive()
        opened.append(sock)
        return sock

    real_sleep = asyncio.sleep

    async def nap(seconds: float) -> None:
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(ws_mod.asyncio, "sleep", nap)
    stream = GateCandleStream([BTC], Timeframe.H1, Decimal("0.0001"))

    async def first() -> Any:
        agen = stream.stream()
        try:
            return await asyncio.wait_for(agen.__anext__(), timeout=2)
        finally:
            await agen.aclose()

    got = asyncio.run(first())
    assert got.candle.close == Decimal("63502.6")
    assert stream.reconnects == 3
    assert slept == [1.0, 2.0], "첫 끊김은 바로 · 이어서 못 붙으면 1 초 · 2 초 쉬고 다시"
