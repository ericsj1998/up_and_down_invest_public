"""T240 — 봉 캐시: 비면 전부 · 있으면 꼬리만 · 머리는 한 번 · 진행 중 봉은 저장 안 함 · 축 축소."""

# ruff: noqa: ARG002 — 가짜 저장소·어댑터는 프로토콜 서명을 그대로 지킨다

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.market import MarketSession, MarketStatus
from updown.marketdata.ingest.repository import InstrumentNotFoundError
from updown.marketdata.ingest.timeframes import floor_to_interval
from updown.orchestration.walkforward.stored_candles import StoredCandles, needed_frames

AAPL = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)
HOUR = timedelta(hours=1)


def _candle(ts: datetime) -> Candle:
    return Candle(
        instrument=AAPL,
        timeframe=Timeframe.H1,
        ts=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )


class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[datetime, Candle] = {}
        self.resolved = 0

    async def resolve_instrument(self, market: Market, symbol: str) -> tuple[int, Instrument]:
        self.resolved += 1
        raise InstrumentNotFoundError("없음")

    async def upsert_instrument(self, instrument: Instrument) -> int:
        return 7

    async def fetch_candles(
        self,
        instrument: Instrument,
        instrument_id: int,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        return [self.rows[ts] for ts in sorted(self.rows) if start <= ts <= end]

    async def upsert_candles(self, instrument_id: int, candles: list[Candle]) -> int:
        for c in candles:
            self.rows[c.ts] = c
        return len(candles)


class FakeQuotes:
    """요청 구간의 정시 봉을 만들어 준다 — 지금 시각의 진행 중 봉까지."""

    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self.open_market = True

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        return MarketStatus(
            instrument=instrument,
            session=MarketSession.REGULAR if self.open_market else MarketSession.CLOSED,
            is_order_allowed=self.open_market,
            as_of=datetime.now(UTC),
            next_open=None,
            next_close=None,
        )

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls.append((start, end))
        out: list[Candle] = []
        ts = floor_to_interval(start, timeframe)
        if ts < start:
            ts += HOUR
        while ts <= end:
            out.append(_candle(ts))
            ts += HOUR
        return out


def _cache() -> tuple[StoredCandles, FakeRepo, FakeQuotes]:
    repo, quotes = FakeRepo(), FakeQuotes()
    return StoredCandles(quotes, repo), repo, quotes  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_empty_store_fetches_everything_but_keeps_only_closed_bars() -> None:
    cache, repo, quotes = _cache()
    now = datetime.now(UTC)
    start = floor_to_interval(now, Timeframe.H1) - HOUR * 10
    rows = await cache.get_candles(AAPL, Timeframe.H1, start, now)
    assert len(quotes.calls) == 1
    assert rows[-1].ts == floor_to_interval(now, Timeframe.H1), "진행 중 봉도 돌려준다"
    assert rows[-1].ts not in repo.rows, "진행 중 봉은 저장하지 않는다"
    assert len(repo.rows) == len(rows) - 1


@pytest.mark.asyncio
async def test_second_call_only_fetches_the_tail() -> None:
    cache, repo, quotes = _cache()
    now = datetime.now(UTC)
    start = floor_to_interval(now, Timeframe.H1) - HOUR * 10
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    quotes.calls.clear()
    rows = await cache.get_candles(AAPL, Timeframe.H1, start, now)
    assert len(quotes.calls) == 1
    tail_from, _ = quotes.calls[0]
    assert tail_from == max(repo.rows), "마지막 저장 봉부터"
    assert rows and rows[0].ts >= start


@pytest.mark.asyncio
async def test_head_is_fetched_once_when_store_starts_late() -> None:
    cache, repo, quotes = _cache()
    now = datetime.now(UTC)
    recent = floor_to_interval(now, Timeframe.H1)
    for i in range(1, 4):
        repo.rows[recent - HOUR * i] = _candle(recent - HOUR * i)
    start = recent - timedelta(days=10)
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    heads = [c for c in quotes.calls if c[0] == start]
    assert len(heads) == 1, "머리는 한 번 받는다"
    quotes.calls.clear()
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    assert all(c[0] != start for c in quotes.calls), "같은 머리를 다시 받지 않는다"


@pytest.mark.asyncio
async def test_instrument_is_resolved_once() -> None:
    cache, repo, _ = _cache()
    now = datetime.now(UTC)
    await cache.get_candles(AAPL, Timeframe.H1, now - HOUR * 3, now)
    await cache.get_candles(AAPL, Timeframe.H1, now - HOUR * 3, now)
    assert repo.resolved == 1


@pytest.mark.asyncio
async def test_closed_market_asks_for_the_tail_only_once_per_interval() -> None:
    cache, _, quotes = _cache()
    now = datetime.now(UTC)
    start = floor_to_interval(now, Timeframe.H1) - HOUR * 10
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    quotes.open_market = False
    quotes.calls.clear()
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    assert len(quotes.calls) == 1, "닫힌 장에서는 간격 안에 한 번만 묻는다"
    quotes.open_market = True
    quotes.calls.clear()
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    await cache.get_candles(AAPL, Timeframe.H1, start, now)
    assert len(quotes.calls) == 2, "열린 장에서는 매번 묻는다"


def test_needed_frames_is_step_entry_daily() -> None:
    assert needed_frames(Timeframe.H1, Timeframe.M5) == (Timeframe.M5, Timeframe.H1, Timeframe.D1)
    assert needed_frames(Timeframe.D1, Timeframe.M5) == (Timeframe.M5, Timeframe.D1)
