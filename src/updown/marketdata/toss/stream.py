"""토스 **폴링** 캔들 스트림 — 웹소켓이 없는 브로커를 러너에 꽂는다 (T240).

토스 Open API 에는 WebSocket 이 없다("추후 지원 예정" · `docs/providers/toss.json`). 러너의
`CandleStream` 계약(`reconnects` · `is_testnet` · `stream()`)은 그대로 지키되, 안에서는
REST 캔들을 주기적으로 읽어 **바뀐 봉만** 낸다.

## 장이 닫혀 있으면 아무것도 내지 않는다

봉이 없으면 러너는 걷지 않는다 — 장 시간 스케줄러가 따로 필요 없는 이유다. 캘린더가
`OPEN` 이 아니면(휴장·시간외·유효 구간 밖 `UNKNOWN`) 조회조차 하지 않고 느리게 잔다.
다음 거래일 첫 봉이 오면 세션이 야간 갭을 그 봉에서 판정한다(`Session._gap_stop_fill`).

## 마감 판정

한 봉은 두 경우에 `closed=True` 로 나간다 — 더 새 봉이 보였거나, 시계가 그 봉의 끝을
지났거나. 뒤의 경우가 없으면 하루의 **마지막 봉**은 영원히 안 닫힌다(다음 봉이 다음날
아침에야 오므로).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.session import MarketCalendar, Tradability, regular_only
from updown.common.logging.setup import get_logger
from updown.marketdata.ingest.timeframes import floor_to_interval, interval
from updown.marketdata.stream import LiveCandle

_logger = get_logger("marketdata.toss.stream")

CLOSED_POLL_SECONDS = 60.0
"""장이 닫혀 있을 때 캘린더를 다시 보는 간격 — 조회는 안 하므로 요율과 무관하다."""
MIN_POLL_SECONDS = 5.0
MAX_POLL_SECONDS = 30.0
ERROR_WAIT_SECONDS = 5.0


class CandleSource(Protocol):
    """스트림이 조회 어댑터에 요구하는 것 — 캔들 하나뿐."""

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """기간 내 캔들.

        Args:
            instrument: 종목.
            timeframe: 봉 간격.
            start: 구간 시작 (UTC).
            end: 구간 끝 (UTC).

        Returns:
            `ts` 오름차순 봉.
        """
        ...


def poll_seconds_for(timeframe: Timeframe) -> float:
    """시간축에 맞는 폴링 간격 — 봉 길이의 1/6, 5~30초 사이.

    Args:
        timeframe: 스트림이 내는 봉의 축.

    Returns:
        초.

    Note:
        차트 조회는 `MARKET_DATA_CHART` 요율 그룹이다. 1h 봉을 30초마다 물으면 종목당
        분당 2회 — 러너의 `_keep_fresh` 가 같은 축을 TTL 로 한 번 더 묻는 것을 더해도
        한도와 멀다.
    """
    sixth = interval(timeframe).total_seconds() / 6
    return max(MIN_POLL_SECONDS, min(MAX_POLL_SECONDS, sixth))


class TossCandleStream:
    """REST 폴링으로 `CandleStream` 계약을 채운다.

    Note:
        `reconnects` 는 **조회 실패 뒤 다시 시도한 횟수**다 — 소켓이 없으니 재연결도
        없지만, 러너가 데이터 신뢰도로 읽는 뜻(끊겼다 붙었다)은 같다.
    """

    def __init__(
        self,
        quotes: CandleSource,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        *,
        calendar: MarketCalendar,
        poll_seconds: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """스트림을 만든다.

        Args:
            quotes: 캔들을 주는 조회 어댑터.
            instruments: 볼 종목들.
            timeframe: 낼 봉의 축.
            calendar: 장 시간 판정. 열려 있을 때만 조회한다.
            poll_seconds: 조회 간격. None 이면 축에서 정한다.
            clock: 현재 시각 함수 (시험용 · 규칙 #5). None 이면 UTC now.
        """
        self._quotes = quotes
        self._instruments = tuple(instruments)
        self._timeframe = timeframe
        self._calendar = calendar
        self._poll = poll_seconds_for(timeframe) if poll_seconds is None else poll_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._span = interval(timeframe)
        self.reconnects = 0
        self._last: dict[str, Candle] = {}
        self._closed: dict[str, datetime] = {}
        self._was_open: dict[str, bool] = {}

    @property
    def is_testnet(self) -> bool:
        """실데이터다 — 페이퍼 체결이어도 시세는 진짜다."""
        return False

    def contracts(self) -> list[str]:
        """보는 종목 코드들 (Gate 스트림과 같은 모양).

        Returns:
            종목 코드 목록.
        """
        return [item.symbol for item in self._instruments]

    async def stream(self) -> AsyncGenerator[LiveCandle]:
        """봉을 계속 낸다 — 장중에만, 바뀐 것만.

        Yields:
            열린 봉(`closed=False`)은 값이 바뀔 때마다, 마감은 한 번.

        Raises:
            asyncio.CancelledError: 러너가 멈출 때 — 조회 실패는 삼키고 세지만 취소는 그대로 올린다.
        """
        while True:
            now = self._clock()
            opened = False
            for instrument in self._instruments:
                if not self._is_open(instrument, now):
                    continue
                opened = True
                try:
                    rows = await self._fetch(instrument, now)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.reconnects += 1
                    _logger.warning(
                        "toss_stream_poll_failed",
                        payload={
                            "symbol": instrument.symbol,
                            "error": str(exc)[:140],
                            "reconnects": self.reconnects,
                        },
                    )
                    await asyncio.sleep(ERROR_WAIT_SECONDS)
                    continue
                for item in self._advance(instrument, rows, now):
                    yield item
            await asyncio.sleep(self._poll if opened else CLOSED_POLL_SECONDS)

    def _is_open(self, instrument: Instrument, now: datetime) -> bool:
        state, why = self._calendar.tradability(instrument.market, now)
        is_open = state is Tradability.OPEN
        if self._was_open.get(instrument.symbol) != is_open:
            self._was_open[instrument.symbol] = is_open
            _logger.info(
                "toss_stream_market_open" if is_open else "toss_stream_market_closed",
                payload={"symbol": instrument.symbol, "why": why, "at": now.isoformat()},
            )
        return is_open

    async def _fetch(self, instrument: Instrument, now: datetime) -> list[Candle]:
        start = floor_to_interval(now, self._timeframe) - self._span * 2
        rows = await self._quotes.get_candles(instrument, self._timeframe, start, now)
        # ⚠️ 시간외 봉을 거른다 — 정규장만 걷는 세션(T239 `regular_only`)과 같은 눈.
        return regular_only(sorted(rows, key=lambda c: c.ts), self._calendar)

    def _advance(
        self, instrument: Instrument, rows: list[Candle], now: datetime
    ) -> list[LiveCandle]:
        key = instrument.symbol
        out: list[LiveCandle] = []
        for candle in rows:
            last = self._last.get(key)
            if last is None or candle.ts > last.ts:
                if last is not None and self._closed.get(key) != last.ts:
                    self._closed[key] = last.ts
                    out.append(LiveCandle(candle=last, closed=True))
                self._last[key] = candle
                out.append(LiveCandle(candle=candle, closed=False))
            elif candle.ts == last.ts and candle != last:
                self._last[key] = candle
                out.append(LiveCandle(candle=candle, closed=False))
        last = self._last.get(key)
        if last is not None and self._closed.get(key) != last.ts and now >= last.ts + self._span:
            self._closed[key] = last.ts
            out.append(LiveCandle(candle=last, closed=True))
        return out


__all__ = ["TossCandleStream", "poll_seconds_for"]
