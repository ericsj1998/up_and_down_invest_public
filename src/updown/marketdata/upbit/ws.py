"""업비트 WebSocket 티커 스트림 (spec §4.2, P0-7-5).

**근거**: 코인은 WS 로 실시간 시세를 받으므로 **폴링 예산을 소모하지 않는다** (spec §4.2
적응형 폴링 표). 토스가 REST-only 인 것과 대비되는 구조적 이점이며, 그래서 코인 단타의
감시 비용이 주식보다 싸다.

## 실측에서 나온 함정 2개 (docs/platform/upbit_api_notes.md §7)

1. **프레임이 binary 다.** `recv()` 가 `bytes` 를 준다 → `.decode("utf-8")` 필요.
   text 로 가정하면 첫 프레임에서 깨진다.
2. **잘못된 코드는 조용히 무시된다.** 없는 마켓을 구독하면 에러도 응답도 없어,
   "거래가 없어 조용한 것"과 구별되지 않는다 → §7 "조용한 실패 금지" 위반이 되기 쉽다.
   그래서 무수신 감시(`stall_timeout`)를 둔다.
"""

import asyncio
import contextlib
import json
import random
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from updown.common.domain.instrument import Instrument
from updown.common.domain.market import Quote
from updown.common.domain.trade_tick import TradeSide, TradeTick
from updown.common.logging.setup import get_logger

WS_URL = "wss://api.upbit.com/websocket/v1"

#: 무수신 경고 임계값(초). 이 시간 동안 프레임이 없으면 경고를 남기고 재접속한다.
#:
#: 업비트는 잘못된 코드를 조용히 무시하므로(실측) "구독은 됐지만 아무것도 오지 않는" 상태를
#: 스스로 알아채야 한다. KRW-BTC 는 초당 여러 건이 오므로 이 값이면 충분히 여유롭다.
DEFAULT_STALL_TIMEOUT = 30.0

#: 재접속 백오프 상한(초).
MAX_RECONNECT_DELAY = 30.0

_logger = get_logger("marketdata.upbit.ws")


class UpbitWebSocketError(RuntimeError):
    """WS 스트림이 복구 불가능하게 실패했다."""


def build_subscription(market_codes: Sequence[str], *, ticket: str | None = None) -> str:
    """구독 메시지를 만든다.

    Args:
        market_codes: `["KRW-BTC", "KRW-ETH"]` 형식.
        ticket: 구독 식별자. None 이면 UUID 로 생성한다.

    Returns:
        전송할 JSON 문자열.

    Raises:
        ValueError: 마켓 코드가 비었을 때.

    Note:
        `format` 을 `DEFAULT` 로 둔다. `SIMPLE` 은 키를 약어로 주는데(`tp`, `ttms`, `mw` …)
        로그 가독성이 떨어지고 매핑 표 없이는 필드 의미를 읽을 수 없다. 대역폭 절약은
        이 규모에서 의미가 없다.
    """
    if not market_codes:
        raise ValueError("구독할 마켓 코드가 없다")
    return json.dumps(
        [
            {"ticket": ticket or f"updown-{uuid.uuid4().hex[:12]}"},
            {"type": "ticker", "codes": list(market_codes)},
            {"format": "DEFAULT"},
        ]
    )


def decode_frame(raw: bytes | str) -> dict[str, Any]:
    """WS 프레임을 dict 로 파싱한다.

    Args:
        raw: 수신 프레임. 업비트는 **bytes** 로 준다 (실측).

    Returns:
        파싱된 메시지.

    Raises:
        UpbitWebSocketError: 파싱 불가 또는 예상 밖 구조.

    Note:
        `bytes` 와 `str` 을 모두 받는다 — 업비트가 나중에 text 로 바꿔도 깨지지 않게.
    """
    payload = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        parsed: object = json.loads(payload)
    except ValueError as exc:
        raise UpbitWebSocketError(f"WS 프레임이 JSON 이 아니다: {payload[:200]}") from exc
    if not isinstance(parsed, dict):
        raise UpbitWebSocketError(f"WS 프레임이 객체가 아니다: {type(parsed).__name__}")
    return parsed  # pyright: ignore[reportUnknownVariableType]


def frame_to_quote(frame: dict[str, Any], instruments: dict[str, Instrument]) -> Quote | None:
    """티커 프레임 → `Quote`.

    Args:
        frame: `decode_frame` 결과.
        instruments: 마켓 코드 → `Instrument` 맵.

    Returns:
        해당 종목의 시세. 구독하지 않은 코드거나 ticker 타입이 아니면 None.

    Raises:
        UpbitWebSocketError: `trade_price` 가 수치가 아니다 — 시세를 지어내지 않는다.

    Note:
        `as_of` 를 **수신 시각**으로 둔다. 프레임의 `trade_timestamp` 는 체결 시각이고
        `timestamp` 는 업비트 서버 시각인데, staleness 는 "우리가 언제 이 값을 봤는가"의
        문제다 (spec §4.18).

        `bid`/`ask` 는 None 이다 — ticker 스트림에 호가가 없다. 호가가 필요하면 `orderbook`
        타입을 별도 구독해야 하며, 그것은 집행(P2) 시점의 일이다.
    """
    if frame.get("type") != "ticker":
        return None
    code = frame.get("code")
    if not isinstance(code, str):
        return None
    instrument = instruments.get(code)
    if instrument is None:
        return None

    price = frame.get("trade_price")
    if isinstance(price, bool) or not isinstance(price, int | float | str):
        raise UpbitWebSocketError(f"WS 프레임의 trade_price 가 수치가 아니다: {price!r}")

    return Quote(
        instrument=instrument,
        last_price=Decimal(str(price)),
        bid=None,
        ask=None,
        as_of=datetime.now(UTC),
    )


class UpbitTickerStream:
    """업비트 티커 WS 구독 — 자동 재접속 포함.

    Note:
        재접속을 **스트림 안에서** 처리한다. 소비자가 끊김을 몰라도 되게 하는 것이 목적이다
        — 소비자마다 재접속 로직을 쓰면 백오프 정책이 갈라진다.
    """

    def __init__(
        self,
        instruments: Sequence[Instrument],
        *,
        url: str = WS_URL,
        stall_timeout: float = DEFAULT_STALL_TIMEOUT,
        max_reconnects: int | None = None,
    ) -> None:
        """스트림을 만든다.

        Args:
            instruments: 구독할 종목. 업비트 종목이어야 한다.
            url: WS 엔드포인트.
            stall_timeout: 무수신 허용 시간(초). 넘으면 경고 후 재접속한다.
            max_reconnects: 재접속 상한. None 이면 무한(운영 기본). 테스트가 유한값을 준다.

        Raises:
            ValueError: 종목이 비었거나 업비트 종목이 아닌 경우.
        """
        from updown.marketdata.upbit.mapping import to_market_code

        if not instruments:
            raise ValueError("구독할 종목이 없다")
        self._instruments = {to_market_code(inst): inst for inst in instruments}
        self._url = url
        self._stall_timeout = stall_timeout
        self._max_reconnects = max_reconnects

    @property
    def market_codes(self) -> list[str]:
        """구독 중인 마켓 코드."""
        return list(self._instruments)

    async def stream(self) -> AsyncGenerator[Quote]:
        """시세를 계속 내보낸다.

        Yields:
            수신 순서대로의 `Quote`.

        Raises:
            UpbitWebSocketError: 재접속 상한을 넘겼을 때.

        Note:
            구독 **전에 심볼을 검증하지 않는다** — 검증은 `UpbitAdapter.list_markets()` 의
            일이고, 여기서 HTTP 를 호출하면 WS 스트림이 REST 클라이언트에 의존하게 된다.
            대신 `stall_timeout` 이 "구독은 됐는데 아무것도 오지 않는" 상태를 잡는다.

            **조기 종료하는 소비자는 `contextlib.aclosing` 으로 감싼다.** `async for` 를
            `break`/`return` 으로 빠져나오면 이 제너레이터의 `finally` 가 즉시 돌지 않아
            **WS 연결이 닫히지 않는다** (GC 시점에 "Task was destroyed but it is pending"
            경고로 드러난다). `collect_quotes()` 가 그 예다.
        """
        attempt = 0
        while True:
            try:
                async with aclosing(self._stream_once()) as quotes:
                    async for quote in quotes:
                        attempt = 0  # 정상 수신 → 백오프 초기화
                        yield quote
            except (OSError, UpbitWebSocketError, websockets.WebSocketException) as exc:
                reason = f"{type(exc).__name__}: {exc}"
            else:
                reason = "스트림이 정상 종료됐다 (서버가 연결을 닫음)"

            if self._max_reconnects is not None and attempt >= self._max_reconnects:
                raise UpbitWebSocketError(
                    f"WS 재접속 상한({self._max_reconnects})을 넘겼다: {reason}"
                )

            delay = min(MAX_RECONNECT_DELAY, (2**attempt) * 0.5) + random.uniform(0, 0.5)
            _logger.warning(
                "upbit_ws_reconnecting",
                payload={
                    "reason": reason,
                    "attempt": attempt,
                    "delay_seconds": round(delay, 3),
                    "markets": self.market_codes,
                },
            )
            await asyncio.sleep(delay)
            attempt += 1

    async def _stream_once(self) -> AsyncGenerator[Quote]:
        """한 번의 연결 동안 수신한다.

        Yields:
            `Quote`.

        Raises:
            UpbitWebSocketError: 무수신 시간 초과 또는 프레임 파싱 실패.
        """
        async with websockets.connect(self._url, ping_interval=20) as connection:
            await connection.send(build_subscription(self.market_codes))
            _logger.info("upbit_ws_subscribed", payload={"markets": self.market_codes})
            async for quote in self._receive(connection):
                yield quote

    async def _receive(self, connection: ClientConnection) -> AsyncGenerator[Quote]:
        """연결에서 프레임을 읽어 `Quote` 로 내보낸다.

        Args:
            connection: 열린 WS 연결.

        Yields:
            `Quote`.

        Raises:
            UpbitWebSocketError: `stall_timeout` 동안 무수신.
        """
        while True:
            try:
                raw = await asyncio.wait_for(connection.recv(), timeout=self._stall_timeout)
            except TimeoutError as exc:
                # 잘못된 코드를 구독하면 업비트는 조용하다 (실측). 침묵을 정상으로 두면
                # 그것이 곧 조용한 실패다 (spec §7).
                raise UpbitWebSocketError(
                    f"{self._stall_timeout}초간 WS 수신이 없다 — 구독 코드가 잘못됐거나 "
                    f"연결이 죽었다 (markets={self.market_codes})"
                ) from exc

            quote = frame_to_quote(decode_frame(raw), self._instruments)
            if quote is not None:
                yield quote


async def collect_quotes(stream: UpbitTickerStream, *, limit: int, timeout: float) -> list[Quote]:
    """스트림에서 정해진 개수만 모은다 (테스트·수동 확인용).

    Args:
        stream: 구독할 스트림.
        limit: 모을 개수.
        timeout: 전체 제한 시간(초).

    Returns:
        모은 시세. 시간이 다 되면 그때까지 모은 것만 반환한다.

    Note:
        `stream()` 은 무한 제너레이터라 테스트에서 그대로 쓸 수 없다. 여기서 끊는 책임을
        분리해 두면 스트림 쪽에 테스트용 종료 조건을 심지 않아도 된다.

        **`aclosing` 이 필수다.** 이것 없이 `async for` 를 `return` 으로 빠져나오면
        제너레이터 정리가 GC 로 미뤄지고 **WS 연결이 열린 채 남는다** — 파이썬이
        "Task was destroyed but it is pending" 경고로 알려 준다. 조회 경로의 누수는
        rate limit 예산을 조용히 잡아먹으므로 여기서 확실히 닫는다.
    """
    collected: list[Quote] = []

    async def _pump() -> None:
        async with aclosing(stream.stream()) as quotes:
            async for quote in quotes:
                collected.append(quote)
                if len(collected) >= limit:
                    return

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(_pump(), timeout=timeout)
    return collected


# ---------------------------------------------------------------------------
# 체결 스트림 — 델타 볼륨의 원천 (2026-08-15)
# ---------------------------------------------------------------------------
#
# 🔴 **왜 REST 가 아니라 WS 인가**: 업비트 `/trades/ticks` 의 `to` 는 `HH:mm:ss` 만
#    받아 날짜를 못 넘긴다(실측). 500건이 BTC 기준 9분치라 과거를 파고들 수 없다.
#    즉 체결 이력은 **지금부터 받아 쌓는 수밖에 없다.**


def subscribe_trades_payload(market_codes: Sequence[str], ticket: str | None = None) -> str:
    """체결 스트림 구독 메시지.

    Args:
        market_codes: 구독할 마켓 코드.
        ticket: 구독 티켓. 생략하면 무작위로 만든다.

    Returns:
        전송할 JSON 문자열.

    Raises:
        ValueError: 마켓 코드가 비었을 때.

    Note:
        `ticker` 와 **같은 연결에서 같이 구독할 수도 있지만 나눈다.** 둘은 소비처가
        다르고(시세 vs 델타), 한쪽이 끊겼을 때 다른 쪽까지 재접속시키면 원인 추적이
        어려워진다.
    """
    if not market_codes:
        raise ValueError("구독할 마켓 코드가 없다")
    return json.dumps(
        [
            {"ticket": ticket or f"updown-trade-{uuid.uuid4().hex[:12]}"},
            {"type": "trade", "codes": list(market_codes)},
            {"format": "DEFAULT"},
        ]
    )


def frame_to_trade(frame: dict[str, Any], instruments: dict[str, Instrument]) -> TradeTick | None:
    """체결 프레임 → `TradeTick`.

    Args:
        frame: `decode_frame` 결과.
        instruments: 마켓 코드 → `Instrument` 맵.

    Returns:
        체결. 구독하지 않은 코드거나 trade 타입이 아니면 None.

    Raises:
        UpbitWebSocketError: 필수 필드가 없거나 `ask_bid` 가 예상 밖 값인 경우.

    Note:
        🔴 **`ask_bid` 는 체결을 일으킨 주문의 종류다.** `BID` 면 매수 주문이 매도
        호가를 때린 것이라 **매수 우위**로 센다. 뒤집으면 델타의 부호가 통째로 반대가
        되는데, 값이 그럴듯해서 안 보인다.

        ⛔ 모르는 `ask_bid` 값을 매수로도 매도로도 떨어뜨리지 않는다. 규격이 바뀌면
        **그 자리에서 멈춘다** (절대 규칙 #8).

        시각은 **체결 시각**(`trade_timestamp`)을 쓴다 — 봉에 접어 넣을 값이므로
        수신 시각이 아니라 실제로 체결된 때여야 한다.
    """
    if frame.get("type") != "trade":
        return None
    code = frame.get("code")
    if not isinstance(code, str):
        return None
    instrument = instruments.get(code)
    if instrument is None:
        return None

    side_raw = frame.get("ask_bid")
    if side_raw == "BID":
        side = TradeSide.BUY
    elif side_raw == "ASK":
        side = TradeSide.SELL
    else:
        raise UpbitWebSocketError(
            f"ask_bid 가 BID/ASK 가 아니다: {side_raw!r} — 규격 변경 신호다. "
            f"조용히 한쪽으로 떨어뜨리면 델타 부호가 틀린 채 그럴듯해 보인다"
        )

    price = frame.get("trade_price")
    volume = frame.get("trade_volume")
    stamp = frame.get("trade_timestamp")
    for name, value in (("trade_price", price), ("trade_volume", volume)):
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise UpbitWebSocketError(f"WS 체결 프레임의 {name} 가 수치가 아니다: {value!r}")
    if not isinstance(stamp, int):
        raise UpbitWebSocketError(f"trade_timestamp 가 정수가 아니다: {stamp!r}")

    return TradeTick(
        instrument=instrument,
        ts=datetime.fromtimestamp(stamp / 1000, tz=UTC),
        price=Decimal(str(price)),
        volume=Decimal(str(volume)),
        side=side,
    )
