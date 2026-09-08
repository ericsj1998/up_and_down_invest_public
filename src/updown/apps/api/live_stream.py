"""차트 주문의 **실시간 봉** — 거래소 웹소켓을 브라우저까지 잇는다 (2026-08-30).

## 왜 만들었나

사용자: *"실제 거래소처럼 동적이게 움직이게 하고 싶은건데... 이러면 사실상 못써먹겠는데?"*

폴링으로 여기까지 왔다: 1초마다 봉 하나를 REST 로 받았다. 깜빡임은 다 잡았지만
**부드러워지지는 않는다** — 1초에 한 번 계단식으로 값이 바뀌는 것이 한계이고, 그것은
다듬어서 될 일이 아니다. 거래소 차트가 부드러운 이유는 **체결마다** 값이 오기 때문이다.

⇒ 서버는 이미 거래소 웹소켓을 듣고 있었다 (`gate/ws.py` · `binance/ws.py`). 없던 것은
  **서버에서 브라우저로 미는 통로** 하나뿐이다.

## 이 파일이 하는 일

    거래소 WS  ──▶  이 파일(구독 하나)  ──SSE──▶  브라우저 여럿

## 🔴 구독을 **종목·축마다 하나만** 연다

같은 종목을 두 창에서 봐도 거래소 연결은 하나다. 안 그러면 창을 열 때마다 연결이
늘고, 그 비용은 조용하다 — 이 프로젝트는 조용한 비용으로 IP 밴까지 갔다.

## ⛔ 판정에 쓰이지 않는다

미마감 봉이 구조물·셋업 계산에 들어가면 같은 상황에서 매 틱 다른 답이 난다
(절대 규칙 #5). 이 값은 **화면에만** 간다 — `/analysis/frame` 은 여전히 마감 봉만 쓴다.

## ⚠️ SSE 를 쓴다 (웹소켓이 아니라)

브라우저로 **내려보내기만** 하면 되고, `EventSource` 는 끊기면 스스로 다시 붙는다.
양방향이 필요 없는 곳에 웹소켓을 놓으면 재연결·하트비트를 우리가 다시 짜야 한다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from updown.common.domain.instrument import Market, Timeframe
from updown.common.logging.setup import get_logger
from updown.common.wire import candle_json
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.provider import MarketDataProvider
from updown.marketdata.stream import CandleStream

_logger = get_logger("api.live_stream")

router = APIRouter(prefix="/analysis", tags=["analysis"])

HTTP_BAD_REQUEST = 400

QUEUE_MAX = 8
"""한 시청자가 밀려도 되는 봉 수.

⚠️ 넘치면 **가장 오래된 것을 버린다.** 느린 브라우저 하나가 큐를 채우면 그 뒤로 전부
밀리는데, 지금 가격을 보는 화면에서 **늦은 값은 값이 아니다** — 최신만 남기면 된다.
"""

IDLE_S = 30.0
"""아무도 안 볼 때 구독을 접기까지 기다리는 시간(초).

⚠️ 즉시 접으면 화면을 새로고침할 때마다 연결이 끊겼다 붙는다. 너무 길게 두면 아무도
안 보는 연결이 남는다.
"""

HEARTBEAT_S = 15.0
"""아무 봉도 안 올 때 보내는 빈 줄 간격(초).

🔴 프록시(nginx)는 조용한 연결을 끊는다. 거래소가 조용한 것과 우리가 죽은 것을
브라우저가 구별할 수 없으므로, 살아 있다는 신호를 보낸다.
"""


Seat = asyncio.Queue[dict[str, Any]]
"""시청자 한 명의 우편함 — 이름을 붙여야 서명이 읽힌다."""


class _Room:
    """종목·축 하나를 듣는 방 — **구독 하나에 시청자 여럿**.

    Attributes:
        key: `시장:종목:축`.
        seats: 시청자별 큐.
        last: 마지막으로 받은 봉 — 새로 들어온 사람에게 **즉시** 준다.

    Note:
        🔴 `last` 가 없으면 새로 연 창이 **다음 체결까지 빈 화면**이다. 10초봉이면
        그것이 몇 초일 수 있고, 사람은 고장으로 읽는다.
    """

    __slots__ = ("first_at", "key", "last", "pushed", "seats", "sent", "task")

    def __init__(self, key: str) -> None:
        self.key = key
        self.seats: set[Seat] = set()
        self.last: dict[str, Any] | None = None
        self.task: asyncio.Task[None] | None = None
        self.pushed = 0
        """거래소에서 받은 봉 수."""
        self.sent = 0
        """시청자 큐에 실제로 넣은 횟수 — `pushed` 와 갈리면 아무도 안 듣는 것이다."""
        self.first_at = 0.0
        """첫 봉이 온 단조 시각 — 구독이 붙는 데 걸린 시간을 잰다."""


_ROOMS: dict[str, _Room] = {}
_LOCK = asyncio.Lock()


async def _listen(room: _Room, market: Market, symbol: str, frame: Timeframe) -> None:
    """거래소 웹소켓을 듣고 방에 뿌린다.

    Args:
        room: 방.
        market: 거래소.
        symbol: 종목.
        frame: 시간축.

    Note:
        ⛔ **예외로 죽지 않는다.** 이 태스크가 조용히 끝나면 화면은 "안 움직인다" 로만
        보이고 원인이 어디에도 안 남는다 (절대 규칙 #8). 스트림 자체가 재연결을 하지만,
        그것마저 끝나면 남기고 접는다.
    """
    from updown.apps.api.admin import instrument_of

    instrument = instrument_of(symbol, market)
    try:
        async with MarketDataProvider() as provider:
            adapter = provider.adapter_for(market)
            # ⭐ 구체 클래스를 나열하지 않고 **계약**으로 묻는다 (T63 §2b) — 새 거래소는
            #   `QuoteAdapter` 를 지키는 순간 여기서 자동으로 돈다.
            if not isinstance(adapter, QuoteAdapter):
                raise TypeError(f"{market.value} 는 실시간 봉을 주지 않는다")
            live: CandleStream = adapter.candle_stream([instrument], frame, Decimal(1))
            opened = time.monotonic()
            _logger.info(
                "live_stream_opened",
                payload={"key": room.key, "seats": len(room.seats)},
            )
            async for item in live.stream():
                body = candle_json(item.candle, closed=item.closed)
                room.last = body
                room.pushed += 1
                # 🔴 **첫 봉이 언제 왔는지 남긴다.** 구독이 붙는 데 몇 초가 걸리는데,
                #    그동안 화면은 "안 움직인다" 로 보인다 — 그 몇 초를 모르면 고장과
                #    구별할 수 없다 (사용자 신고 2026-08-30: *"꼬리가 안자라는데?"*).
                if room.pushed == 1:
                    room.first_at = time.monotonic()
                    _logger.info(
                        "live_stream_first",
                        payload={
                            "key": room.key,
                            "waited_s": f"{room.first_at - opened:.2f}",
                            "seats": len(room.seats),
                        },
                    )
                for seat in list(room.seats):
                    room.sent += 1
                    # ⚠️ 밀린 시청자의 **가장 오래된 것을 버린다** — 지금 가격을 보는
                    #    화면에서 늦은 값은 값이 아니다.
                    if seat.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            seat.get_nowait()
                    with contextlib.suppress(asyncio.QueueFull):
                        seat.put_nowait(body)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _logger.warning(
            "live_stream_ended",
            payload={"key": room.key, "error": str(exc)[:200], "note": "구독이 끊겼다"},
        )


async def _join(market: Market, symbol: str, frame: Timeframe) -> tuple[_Room, Seat]:
    """방에 들어간다 — 없으면 만들고 **구독을 하나만** 연다."""
    key = f"{market.value}:{symbol}:{frame.value}"
    async with _LOCK:
        room = _ROOMS.get(key)
        if room is None:
            room = _Room(key)
            _ROOMS[key] = room
        seat: Seat = asyncio.Queue(maxsize=QUEUE_MAX)
        room.seats.add(seat)
        if room.task is None or room.task.done():
            room.task = asyncio.create_task(
                _listen(room, market, symbol, frame), name=f"live-stream-{key}"
            )
        return room, seat


async def _leave(room: _Room, seat: Seat) -> None:
    """자리를 뜬다 — 아무도 없으면 잠시 뒤 구독을 접는다."""
    async with _LOCK:
        room.seats.discard(seat)
        if room.seats:
            return
    await asyncio.sleep(IDLE_S)
    async with _LOCK:
        # ⚠️ 기다리는 동안 누가 들어왔을 수 있다 — 그러면 접지 않는다.
        if room.seats:
            return
        if room.task is not None:
            room.task.cancel()
            room.task = None
        _ROOMS.pop(room.key, None)
    # ⚠️ **성적을 남긴다.** `pushed` 가 0 이면 거래소가 안 준 것이고, `sent` 가 0 이면
    #    받았는데 아무도 안 들은 것이다 — 두 고장은 화면에서 똑같이 "멈춤" 으로 보인다.
    _logger.info(
        "live_stream_closed",
        payload={"key": room.key, "pushed": room.pushed, "sent": room.sent},
    )


@router.get("/stream")
async def stream(
    symbol: str,
    timeframe: str = "10s",
    market: Market = Market.BINANCE,
) -> StreamingResponse:
    """**거래소가 미는 봉**을 그대로 브라우저로 (SSE).

    Args:
        symbol: 종목 코드.
        timeframe: 시간축.
        market: 거래소.

    Returns:
        `text/event-stream`. 이벤트마다 봉 하나 (`/analysis/tick` 과 같은 모양 + `closed`).

    Raises:
        HTTPException: 축을 못 읽거나 거래소가 그 축을 안 주면 400.

    Note:
        🔴 **이것이 "거래소처럼 움직이는" 것의 실체다.** 폴링은 1초에 한 번 계단식으로
        값을 바꾸는데, 거래소 차트가 부드러운 이유는 **체결마다** 값이 오기 때문이다.
        다듬어서 될 일이 아니라 통로가 달라야 했다.

        ⛔ **판정에 쓰이지 않는다** — 미마감 봉이 구조물 계산에 들어가면 같은 상황에서
        매 틱 다른 답이 난다 (절대 규칙 #5). `/analysis/frame` 은 여전히 마감 봉만 쓴다.

        ⚠️ **끊기면 브라우저가 스스로 다시 붙는다** (`EventSource` 의 성질). 그동안
        화면은 폴링 값으로 버틴다 — 스트림이 유일한 통로면 한 번 끊길 때 멈춘다.
    """
    try:
        frame = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"모르는 시간축이다: {timeframe}") from exc

    provider = MarketDataProvider()
    adapter = provider.adapter_for(market)
    served = getattr(adapter, "supported_frames", None)
    if served is not None and frame not in served([frame]):
        raise HTTPException(HTTP_BAD_REQUEST, f"{market.value} 는 {timeframe} 봉을 주지 않는다")

    room, seat = await _join(market, symbol, frame)

    async def pump() -> AsyncGenerator[str]:
        """방의 좌석에서 봉을 받아 SSE 로 흘린다.

        Yields:
            마지막 봉(입장 즉시) · 새 봉 · 조용할 때의 `: ping`. 끝나면 좌석을 반납한다.
        """
        try:
            # 🔴 **들어오자마자 마지막 값을 준다.** 없으면 다음 체결까지 빈 화면이고,
            #    10초봉이면 그것이 몇 초다 — 사람은 고장으로 읽는다.
            _logger.info(
                "live_stream_watcher",
                payload={
                    "key": room.key,
                    "seats": len(room.seats),
                    "warm": room.last is not None,
                },
            )
            if room.last is not None:
                yield f"data: {json.dumps(room.last)}\n\n"
            while True:
                try:
                    body = await asyncio.wait_for(seat.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    # ⚠️ 조용한 연결은 프록시가 끊는다. 거래소가 조용한 것과 우리가
                    #    죽은 것을 브라우저가 구별할 수 없으므로 살아 있다고 말한다.
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(body)}\n\n"
        finally:
            await _leave(room, seat)

    return StreamingResponse(
        pump(),
        media_type="text/event-stream",
        # ⚠️ `X-Accel-Buffering` 이 없으면 nginx 가 응답을 모아 두고 안 보낸다 —
        #    그러면 스트림이 도는데 화면은 멈춘 것처럼 보인다.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
