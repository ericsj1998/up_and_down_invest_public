"""봉 캐시 — DB 에 있는 만큼은 DB 에서, 없는 머리·꼬리만 브로커에서 (T240 · 2026-09-09).

## 왜 있나

토스는 웹소켓이 없고 분봉(1m)·일봉만 준다. 라이브 급전의 시드(축마다 800봉)를 매번 브로커에서
받으면 1h 800봉 = 1분봉 수만 개 = 수백 요청이다 — 첫 실측(2026-09-09 17:30 KST)에서 AAPL 판 하나가
**15분에 890 요청**을 냈고 판은 뜨지도 못했다(요청 시간 초과). 재시작·되살리기마다 같은 값을 다시
받는 것은 낭비이고 요율 사고다.

## 규칙

- 요청 구간을 먼저 DB(`candles`)에서 읽는다.
- DB 가 비었으면 전부 브로커에서. 있으면 **꼬리**(마지막 저장 봉 ~ 지금)만, 머리는 저장된 첫 봉이
  요청 시작보다 `HEAD_TOLERANCE` 이상 늦을 때 한 번만 받는다.
  주말·휴장은 빈 것이 정상이라 그 안은 안 받는다.
- 받은 봉 중 **마감된 것만** 저장한다 — 진행 중인 봉을 적으면 다음 조회가 옛 값을 사실로 읽는다.
  돌려줄 때는 진행 중인 봉도 같이 준다(러너 `refresh` 가 마지막 봉을 미마감으로 다룬다).
- 코인(웹소켓 브로커)에는 끼우지 않는다 — 조립부가 `Capability.WS` 로 가른다.
  시장 이름으로는 안 가른다.
- 캘린더를 받으면 **정규장 봉만** 돌려준다(저장은 전부). 토스 분봉은 장전·장후를 포함하는데
  스트림은 `regular_only` 로 거르므로 시드·갱신도 같은 눈이어야 한다.
  실측(2026-09-09) 시드에 장전 5m 봉이 섞였다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.market import MarketStatus, Quote
from updown.common.domain.session import MarketCalendar, regular_only
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import Capability, QuoteAdapter
from updown.marketdata.ingest.repository import CandleRepository, InstrumentNotFoundError
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.stream import CandleStream

_logger = get_logger("walkforward.stored_candles")

HEAD_TOLERANCE = timedelta(days=4)
"""저장된 첫 봉이 요청 시작보다 이만큼 늦어야 머리를 받는다 — 주말+휴일 연휴가 이 안에 든다."""
_DAILY_OR_LONGER = frozenset({Timeframe.D1})

FETCH_CHUNK = timedelta(days=7)
"""분봉 계열의 빈 구간을 나누는 폭 — 토스는 1분 원봉을 합쳐 주므로 1h 400봉이 1분봉 2만여 개 ·
페이지 112개다. 한 덩어리로 순차 받으면 60초(프록시 경유 · 2026-09-11 실측) → 7일씩 나눠
동시에 받는다. 일봉은 한 번에."""
FETCH_CONCURRENCY = 4
"""동시에 받는 덩어리 수 — 요율은 client 스로틀(그룹당 5/s)이 지키므로 여기서 겹치는 것은
왕복 지연뿐이다."""
"""일봉은 거르지 않는다 — 토스 일봉 ts(04:00Z)는 정규장 밖이라 걸면 전부 사라진다."""
CLOSED_TAIL_INTERVAL = 900.0
"""장이 닫혀 있을 때 꼬리를 다시 묻는 간격(초) — 러너의 축 갱신(5m 축은 30초마다)이 밤새 빈 요청을
내던 것을 막는다(실측 2026-09-09: 폐장 10분에 16회). 열려 있으면 매번 묻는다."""


class _StatusQuotes(Protocol):
    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """장 상태.

        Args:
            instrument: 종목.

        Returns:
            세션과 주문 가능 여부.
        """
        ...


def fetch_chunks(
    start: datetime, end: datetime, timeframe: Timeframe
) -> list[tuple[datetime, datetime]]:
    """빈 구간을 브로커 호출 덩어리로 나눈다 (순수).

    Args:
        start: 구간 시작.
        end: 구간 끝.
        timeframe: 봉 간격 — 일봉 이상은 나누지 않는다(한 호출이 몇 페이지 안 된다).

    Returns:
        `(시작, 끝)` 오름차순. `FETCH_CHUNK` 이하면 하나.
    """
    if timeframe in _DAILY_OR_LONGER or end - start <= FETCH_CHUNK:
        return [(start, end)]
    out: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        nxt = min(cur + FETCH_CHUNK, end)
        out.append((cur, nxt))
        cur = nxt
    return out


class StoredCandles:
    """조회 어댑터 앞에 서는 봉 캐시 — `QuoteAdapter` 계약을 그대로 지킨다.

    Note:
        캔들만 가로채고 나머지(시세·명세·펀딩·스트림·장 상태)는 안쪽 어댑터에 넘긴다.
        주문 어댑터를 얻는 `order_adapter` 에는 **안쪽 어댑터를** 넘겨야 한다 — 게이트가
        구체 조회 어댑터 종류로 짝을 맞춘다.
    """

    def __init__(
        self,
        quotes: QuoteAdapter,
        repo: CandleRepository,
        *,
        calendar: MarketCalendar | None = None,
    ) -> None:
        """캐시를 만든다.

        Args:
            quotes: 브로커 조회 어댑터.
            repo: 봉 저장소 (판 저장소와 같은 DB).
            calendar: 있으면 돌려주는 봉을 정규장으로 거른다 (저장은 전부).
        """
        self._quotes = quotes
        self._repo = repo
        self._calendar = calendar
        self._ids: dict[str, int] = {}
        self._head_tried: dict[tuple[str, Timeframe], datetime] = {}
        self._tail_checked: dict[tuple[str, Timeframe], float] = {}
        self.fetched = 0
        """브로커에서 받은 봉 수 누계 — 프로브·시험이 읽는다."""
        self.served = 0
        """DB 에서 읽은 봉 수 누계."""

    @property
    def inner(self) -> QuoteAdapter:
        """안쪽 어댑터 — 게이트에 넘길 때 쓴다."""
        return self._quotes

    @property
    def capabilities(self) -> frozenset[Capability]:
        """안쪽 어댑터의 능력."""
        default: frozenset[Capability] = frozenset()
        found: object = getattr(self._quotes, "capabilities", default)
        return cast("frozenset[Capability]", found)

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """기간 내 캔들 — DB 우선, 빈 곳만 브로커.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 구간 시작 (UTC).
            end: 구간 끝 (UTC).

        Returns:
            `ts` 오름차순. 마지막은 진행 중인 봉일 수 있다.
        """
        iid = await self._instrument_id(instrument)
        stored = await self._repo.fetch_candles(instrument, iid, timeframe, start, end)
        self.served += len(stored)
        span = interval(timeframe)
        ranges: list[tuple[datetime, datetime]] = []
        key = (instrument.symbol, timeframe)
        if not stored:
            ranges.append((start, end))
        else:
            tried = self._head_tried.get(key)
            if stored[0].ts - start > HEAD_TOLERANCE and (tried is None or start < tried):
                self._head_tried[key] = start
                ranges.append((start, stored[0].ts))
            if end - stored[-1].ts > span and await self._tail_due(instrument, key):
                ranges.append((stored[-1].ts, end))
        merged: dict[datetime, Candle] = {c.ts: c for c in stored}
        now = datetime.now(UTC)
        for a, b in ranges:
            pieces = fetch_chunks(a, b, timeframe)
            fresh = await self._fetch_pieces(instrument, timeframe, pieces)
            self.fetched += len(fresh)
            if not fresh:
                continue
            closed = [c for c in fresh if c.ts + span <= now]
            if closed:
                await self._repo.upsert_candles(iid, closed)
            for c in fresh:
                merged[c.ts] = c
            _logger.info(
                "stored_candles_filled",
                payload={
                    "symbol": instrument.symbol,
                    "frame": timeframe.value,
                    "from": a.isoformat(),
                    "to": b.isoformat(),
                    "fetched": len(fresh),
                    "stored": len(closed),
                    "had": len(stored),
                    "chunks": len(pieces),
                },
            )
        rows = [merged[ts] for ts in sorted(merged) if start <= ts <= end]
        if self._calendar is None or timeframe in _DAILY_OR_LONGER:
            return rows
        return regular_only(rows, self._calendar)

    async def _fetch_pieces(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        pieces: Sequence[tuple[datetime, datetime]],
    ) -> list[Candle]:
        """덩어리들을 동시에(`FETCH_CONCURRENCY`) 받아 `ts` 오름차순 하나로 합친다.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            pieces: `(시작, 끝)` 목록 — 경계 봉이 겹치면 뒤 것이 남는다(같은 봉이다).

        Returns:
            합친 봉. 한 덩어리라도 실패하면 예외 — 부분 결과를 저장하지 않는다.
        """
        gate = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def _one(a: datetime, b: datetime) -> list[Candle]:
            async with gate:
                return await self._quotes.get_candles(instrument, timeframe, a, b)

        parts = await asyncio.gather(*(_one(a, b) for a, b in pieces))
        by_ts: dict[datetime, Candle] = {}
        for part in parts:
            for c in part:
                by_ts[c.ts] = c
        return [by_ts[ts] for ts in sorted(by_ts)]

    async def get_quote(self, instrument: Instrument) -> Quote:
        """안쪽 어댑터에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            시세.
        """
        return await self._quotes.get_quote(instrument)

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        """안쪽 어댑터에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            계약 명세.
        """
        return await self._quotes.contract_spec(instrument)

    async def funding_rate(self, instrument: Instrument) -> Decimal | None:
        """안쪽 어댑터에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            펀딩률 (없으면 None).
        """
        return await self._quotes.funding_rate(instrument)

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        """안쪽 어댑터에 위임한다.

        Args:
            instrument: 종목.

        Returns:
            장 상태.
        """
        return await self._status_quotes().get_market_status(instrument)

    def interval_seconds(self, timeframe: Timeframe) -> int:
        """안쪽 어댑터에 위임한다.

        Args:
            timeframe: 시간축.

        Returns:
            초.
        """
        return self._quotes.interval_seconds(timeframe)

    def supported_frames(self, frames: Sequence[Timeframe]) -> tuple[Timeframe, ...]:
        """안쪽 어댑터에 위임한다.

        Args:
            frames: 원하는 시간축들.

        Returns:
            브로커가 주는 것만.
        """
        return self._quotes.supported_frames(frames)

    def candle_stream(
        self, instruments: Sequence[Instrument], timeframe: Timeframe, multiplier: Decimal
    ) -> CandleStream:
        """안쪽 어댑터의 스트림 — 스트림은 작은 창만 물으므로 캐시를 안 거친다.

        Args:
            instruments: 종목들.
            timeframe: 축.
            multiplier: 계약 승수.

        Returns:
            브로커 스트림.
        """
        return self._quotes.candle_stream(instruments, timeframe, multiplier)

    async def _tail_due(self, instrument: Instrument, key: tuple[str, Timeframe]) -> bool:
        """꼬리를 브로커에 물을 차례인가 — 열려 있으면 늘, 닫혀 있으면 간격마다."""
        try:
            status = await self._status_quotes().get_market_status(instrument)
        except Exception:
            return True
        if status.is_order_allowed:
            return True
        now = time.monotonic()
        last = self._tail_checked.get(key)
        if last is not None and now - last < CLOSED_TAIL_INTERVAL:
            return False
        self._tail_checked[key] = now
        return True

    def _status_quotes(self) -> _StatusQuotes:
        """안쪽 어댑터를 `get_market_status` 를 가진 것으로 본다 — 타입 좁히기용."""
        return self._quotes  # type: ignore[return-value]

    async def _instrument_id(self, instrument: Instrument) -> int:
        """`instruments` 행 id — 없으면 만들고, 세션 안에서는 한 번만 DB 에 묻는다.

        Args:
            instrument: 종목.

        Returns:
            DB 의 종목 id.
        """
        key = f"{instrument.market.value}:{instrument.symbol}"
        found = self._ids.get(key)
        if found is not None:
            return found
        try:
            iid, _ = await self._repo.resolve_instrument(instrument.market, instrument.symbol)
        except InstrumentNotFoundError:
            iid = await self._repo.upsert_instrument(instrument)
        self._ids[key] = iid
        return iid


def needed_frames(entry: Timeframe, step: Timeframe, *extra: Timeframe) -> tuple[Timeframe, ...]:
    """폴링 브로커에 요구할 축 — 걸음 축 · 진입 축 · 일봉 (+ 방아쇠 축).

    Args:
        entry: 매매법 진입 축.
        step: 세션 걸음 축 (`STEP_FRAME`).
        extra: 더 넣을 축 — 셋업 없는 판의 **방아쇠 축**(T250 실측: 1m 이 급전에 없어 매 걸음
            `live_trigger_step_failed` · 진입 주문이 영영 안 나갔다).

    Returns:
        중복 없이, 짧은 축부터.

    Note:
        웹소켓 브로커는 9개 축을 다 시드하지만 폴링 브로커에서 그것은 축마다 분봉 수만 개다.
        주식 저장소 생성기(T239)도 [진입 축 · 1d] 만 쓴다 — 같은 눈으로 본다.
    """
    wanted = {step, entry, Timeframe.D1, *extra}
    return tuple(sorted(wanted, key=lambda f: interval(f)))


__all__ = ["CLOSED_TAIL_INTERVAL", "HEAD_TOLERANCE", "StoredCandles", "needed_frames"]
