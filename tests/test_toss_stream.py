"""T240 — 토스 폴링 스트림: 장중에만 · 바뀐 봉만 · 마감은 한 번 · 하루 마지막 봉은 시계가 닫는다."""
# ruff: noqa: ARG002 — 가짜 어댑터는 프로토콜 서명을 그대로 지킨다

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.session import Tradability
from updown.marketdata.toss.stream import TossCandleStream, poll_seconds_for

AAPL = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)
T0 = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)  # 월 10:00 EDT


def _candle(ts: datetime, close: str) -> Candle:
    return Candle(
        instrument=AAPL,
        timeframe=Timeframe.H1,
        ts=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(close),
        volume=Decimal(10),
    )


class Calendar:
    def __init__(self) -> None:
        self.open = True

    def tradability(self, market: Market, moment: datetime) -> tuple[Tradability, str]:
        return (Tradability.OPEN, "정규장") if self.open else (Tradability.CLOSED, "휴장일")

    def is_regular(self, market: Market, moment: datetime) -> bool:
        return True

    def with_trading_days(self, market: Market, days: object) -> Calendar:  # pragma: no cover
        return self

    def trading_date(self, market: Market, moment: datetime) -> date:  # pragma: no cover
        return moment.date()


class Quotes:
    def __init__(self) -> None:
        self.rows: list[Candle] = []
        self.calls = 0

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls += 1
        return list(self.rows)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _stream(quotes: Quotes, calendar: Calendar, clock: Clock) -> TossCandleStream:
    return TossCandleStream(
        quotes,
        [AAPL],
        Timeframe.H1,
        calendar=calendar,  # type: ignore[arg-type]
        poll_seconds=0,
        clock=clock,
    )


def test_poll_interval_is_bounded() -> None:
    assert poll_seconds_for(Timeframe.M1) == 10
    assert poll_seconds_for(Timeframe.H1) == 30
    assert poll_seconds_for(Timeframe.M15) == 30


@pytest.mark.asyncio
async def test_emits_open_bar_then_closes_it_when_the_next_arrives() -> None:
    quotes, calendar, clock = Quotes(), Calendar(), Clock(T0 + timedelta(minutes=10))
    quotes.rows = [_candle(T0, "100")]
    stream = _stream(quotes, calendar, clock)
    it = stream.stream()
    first = await anext(it)
    assert first.closed is False and first.candle.ts == T0
    quotes.rows = [_candle(T0, "100.5")]
    second = await anext(it)
    assert second.closed is False and second.candle.close == Decimal("100.5"), "값이 바뀐 같은 봉"
    quotes.rows = [_candle(T0, "101"), _candle(T0 + timedelta(hours=1), "102")]
    clock.now = T0 + timedelta(hours=1, minutes=1)
    third = await anext(it)
    assert third.closed is False and third.candle.close == Decimal(101), "마감 직전 갱신"
    fourth = await anext(it)
    assert fourth.closed is True and fourth.candle.ts == T0 and fourth.candle.close == Decimal(101)
    fifth = await anext(it)
    assert fifth.closed is False and fifth.candle.ts == T0 + timedelta(hours=1)
    await it.aclose()


@pytest.mark.asyncio
async def test_last_bar_of_the_day_is_closed_by_the_clock() -> None:
    quotes, calendar, clock = Quotes(), Calendar(), Clock(T0 + timedelta(minutes=10))
    quotes.rows = [_candle(T0, "100")]
    stream = _stream(quotes, calendar, clock)
    it = stream.stream()
    await anext(it)
    clock.now = T0 + timedelta(hours=1)
    closed = await anext(it)
    assert closed.closed is True and closed.candle.ts == T0
    await it.aclose()


@pytest.mark.asyncio
async def test_closed_market_polls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    quotes, calendar, clock = Quotes(), Calendar(), Clock(T0)
    calendar.open = False
    quotes.rows = [_candle(T0, "100")]
    stream = _stream(quotes, calendar, clock)
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 3:
            calendar.open = True

    monkeypatch.setattr("updown.marketdata.toss.stream.asyncio.sleep", _sleep)
    it = stream.stream()
    first = await anext(it)
    assert quotes.calls == 1 and slept[:3] == [60.0, 60.0, 60.0], "닫힌 동안은 조회하지 않았다"
    assert first.candle.ts == T0
    await it.aclose()
