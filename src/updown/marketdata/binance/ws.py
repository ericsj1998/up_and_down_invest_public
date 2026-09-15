"""바이낸스 캔들 웹소켓 — 러너 급전용 (T62 P2b).

`GateCandleStream` 과 같은 계약을 지킨다: `stream()` 이 `LiveCandle(candle, closed)` 을
계속 내고, 끊기면 재연결하며 `reconnects` 로 센다. 소비처(LiveRunner)는 덕 타이핑으로
둘을 같은 자리에 꽂는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import httpx
import websockets

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.logging.setup import get_logger
from updown.marketdata.binance.mapping import interval_of, to_symbol
from updown.marketdata.stream import LiveCandle

_logger = get_logger("marketdata.binance.ws")

LIVE_WS_URL = "wss://fstream.binance.com/ws"
LIVE_REST_URL = "https://fapi.binance.com"
SILENT_AFTER = 60.0
"""이 초 동안 kline 이 한 건도 없으면 소켓이 죽은 것이다 — kline 스트림은 시간축과
무관하게 진행 중 봉을 초 단위로 밀어 주므로, 60초 침묵은 정상일 수 없다."""
POLL_SECONDS = 20.0
"""폴링 주기 — 4h 진입 축에서 봉 마감을 최대 이만큼 늦게 본다 (판정은 봉 마감 기준이라
지연만 있고 왜곡은 없다)."""


class BinanceWebSocketError(RuntimeError):
    """구독 형식·규격 오류 — 재연결로 덮지 않고 던진다 (규칙 #8)."""


class BinanceCandleStream:
    """kline 채널을 끊기지 않게 듣는다 (Gate 스트림과 같은 계약).

    Note:
        ⚠️ 데이터는 **라이브 소켓**이다 (조회는 라이브 원칙 — testnet 시세로 판단하면
        다른 시장을 분석하는 셈). 주문만 testnet 으로 나간다.
    """

    def __init__(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        multiplier: Decimal,
        *,
        url: str = LIVE_WS_URL,
        ping_interval: float = 20.0,
    ) -> None:
        """스트림을 만든다.

        Args:
            instruments: 구독할 종목들 — 전부 BINANCE 여야 한다.
            timeframe: 시간축.
            multiplier: 계약 승수 (stepSize). ⚠️ 바이낸스 거래량은 이미 코인 단위라
                변환에 안 쓰지만, Gate 스트림과 시그니처를 맞춘다 (같은 자리에 꽂힌다).
            url: 웹소켓 URL.
            ping_interval: 핑 간격(초).

        Raises:
            BinanceWebSocketError: 종목이 없는 경우.
            BinanceMappingError: BINANCE 종목이 아닌 것이 섞인 경우.
        """
        del multiplier  # 시그니처 호환용 — 바이낸스 거래량은 이미 코인 단위다 (위 Args)
        if not instruments:
            raise BinanceWebSocketError("구독할 종목이 없다")
        self._by_symbol = {to_symbol(item): item for item in instruments}
        self._timeframe = timeframe
        self._interval = interval_of(timeframe)
        self._url = url
        self._ping_interval = ping_interval
        self.reconnects = 0
        """재연결 횟수 — 0 이 아니면 봉에 구멍이 있을 수 있다."""

    @property
    def contracts(self) -> list[str]:
        """구독 중인 심볼들."""
        return sorted(self._by_symbol)

    @property
    def is_testnet(self) -> bool:
        """데이터가 testnet 인가 — 라이브 소켓이므로 거짓이다."""
        return "testnet" in self._url or "binancefuture" in self._url

    def _params(self) -> list[str]:
        return [f"{symbol.lower()}@kline_{self._interval}" for symbol in self.contracts]

    def _to_live(self, data: dict[str, Any]) -> LiveCandle | None:
        """웹소켓 kline 프레임 하나를 `LiveCandle` 로.

        Args:
            data: `e == "kline"` 인 프레임 — 심볼은 `s`, 봉은 `k` 에 있다.

        Returns:
            봉. 구독하지 않은 심볼이거나 `k` 가 비어 있으면 None (버린다). `closed` 는 `x` 다.

        Raises:
            BinanceWebSocketError: `k` 안의 열쇠·숫자가 규격과 다른 경우 — 형식 변경은 재연결로
                덮지 않고 던진다 (클래스 docstring · 규칙 #8).
        """
        symbol = str(data.get("s", ""))
        instrument = self._by_symbol.get(symbol)
        kline = cast("dict[str, Any]", data.get("k") or {})
        if instrument is None or not kline:
            return None
        try:
            candle = Candle(
                instrument=instrument,
                timeframe=self._timeframe,
                ts=datetime.fromtimestamp(int(kline["t"]) / 1000, tz=UTC),
                open=Decimal(str(kline["o"])),
                high=Decimal(str(kline["h"])),
                low=Decimal(str(kline["l"])),
                close=Decimal(str(kline["c"])),
                volume=Decimal(str(kline["v"])),
            )
        except (KeyError, ArithmeticError, ValueError) as exc:
            raise BinanceWebSocketError(f"kline 프레임을 못 읽었다: {kline!r}") from exc
        return LiveCandle(candle=candle, closed=bool(kline.get("x")))

    async def stream(self) -> AsyncGenerator[LiveCandle]:
        """봉을 계속 낸다 — 웹소켓이 침묵하면 **REST 폴링으로 전환**한다.

        Yields:
            봉. `closed` 로 마감 여부를 가린다 — 원장은 마감만, 화면은 진행 중도 쓴다.

        Note:
            🔴 2026-08-25 실측: 이 네트워크에서 바이낸스 **선물** WS 는 연결·구독까지
            되고 데이터가 **0건**이다 (현물 WS 는 정상 · REST 는 정상 — 지역 차단으로
            보인다). 그래서 침묵을 재연결로 덮지 않는다 — `SILENT_AFTER` 안에 kline 이
            한 건도 없으면 REST 폴링으로 내려간다. 폴링은 마감을 최대 `POLL_SECONDS`
            늦게 볼 뿐 왜곡이 없다 (판정은 봉 마감 기준 · 절대 규칙 #5).

            소켓·프로토콜 오류로 kline 을 받던 중 끊기면 재연결한다. 형식 오류는
            던진다 — 규격 변경을 재연결로 덮으면 영원히 빈 스트림을 듣는다.
        """
        import json

        while True:
            got_kline = False
            try:
                async with websockets.connect(
                    self._url, ping_interval=self._ping_interval
                ) as socket:
                    await socket.send(
                        json.dumps({"method": "SUBSCRIBE", "params": self._params(), "id": 1})
                    )
                    _logger.info(
                        "binance_ws_subscribed",
                        payload={
                            "url": self._url,
                            "timeframe": self._timeframe.value,
                            "symbols": self.contracts,
                        },
                    )
                    while True:
                        raw = await asyncio.wait_for(socket.recv(), timeout=SILENT_AFTER)
                        frame = cast("dict[str, Any]", json.loads(raw))
                        if frame.get("e") != "kline":
                            continue  # 구독 응답(result) 등은 조용히 넘긴다
                        item = self._to_live(frame)
                        if item is not None:
                            got_kline = True
                            yield item
            except TimeoutError:
                if not got_kline:
                    break  # 🔴 연결은 됐는데 침묵 — 폴링으로 내려간다
                self.reconnects += 1  # 받다가 멎었다 — 소켓을 다시 연다
            except (OSError, websockets.WebSocketException) as exc:
                self.reconnects += 1
                _logger.warning(
                    "binance_ws_reconnect",
                    payload={
                        "reason": str(exc)[:200],
                        "reconnects": self.reconnects,
                        "symbols": self.contracts,
                        "note": "이 사이 봉에 구멍이 있을 수 있다 — REST 로 메워야 한다",
                    },
                )
                if not got_kline:
                    break  # 연결조차 안 된다 — 폴링으로 내려간다

        _logger.error(
            "binance_ws_silent_polling",
            payload={
                "symbols": self.contracts,
                "poll_seconds": POLL_SECONDS,
                "note": "선물 WS 가 데이터를 안 준다 (지역 차단 추정) — REST 폴링으로 돈다. "
                "봉 마감을 최대 20초 늦게 본다",
            },
        )
        async for item in self._poll():
            yield item

    async def _poll(self) -> AsyncGenerator[LiveCandle]:
        """REST 로 봉을 나른다 — 마감봉은 새것만, 진행봉은 매번 (화면용).

        Note:
            klines 원소는 배열이다: `[openTime, o, h, l, c, v, closeTime, ...]`.
            마지막 원소가 진행 중 봉이고 그 앞이 마감봉들이다.
        """
        seen: dict[str, int] = {}
        async with httpx.AsyncClient(base_url=LIVE_REST_URL, timeout=10.0) as http:
            while True:
                for symbol in self.contracts:
                    try:
                        response = await http.get(
                            "/fapi/v1/klines",
                            params={"symbol": symbol, "interval": self._interval, "limit": 2},
                        )
                        response.raise_for_status()
                        rows = cast("list[list[Any]]", response.json())
                    except (httpx.HTTPError, ValueError) as exc:
                        self.reconnects += 1
                        _logger.warning(
                            "binance_poll_failed",
                            payload={"symbol": symbol, "error": str(exc)[:140]},
                        )
                        continue
                    for row in rows[:-1]:  # 마감봉 — 새것만 낸다
                        opened = int(row[0])
                        if opened <= seen.get(symbol, -1):
                            continue
                        item = self._row_live(symbol, row, closed=True)
                        if item is not None:
                            seen[symbol] = opened
                            yield item
                    if rows:  # 진행 중 봉 — 화면이 쓴다 (원장은 closed 만 본다)
                        item = self._row_live(symbol, rows[-1], closed=False)
                        if item is not None:
                            yield item
                await asyncio.sleep(POLL_SECONDS)

    def _row_live(self, symbol: str, row: list[Any], *, closed: bool) -> LiveCandle | None:
        """REST kline 배열 한 줄을 LiveCandle 로."""
        instrument = self._by_symbol.get(symbol)
        if instrument is None or len(row) < 6:
            return None
        try:
            candle = Candle(
                instrument=instrument,
                timeframe=self._timeframe,
                ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
            )
        except (ArithmeticError, ValueError) as exc:
            raise BinanceWebSocketError(f"kline 행을 못 읽었다: {row!r}") from exc
        return LiveCandle(candle=candle, closed=closed)
