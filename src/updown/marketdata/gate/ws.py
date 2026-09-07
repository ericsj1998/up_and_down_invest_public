"""Gate.io 무기한 선물 WebSocket — 실시간 봉·시세.

## 🔴 왜 필요한가

지금 모의 라이브는 `SealedFeed`(봉인된 과거)로만 돈다. 라이브로 가려면 **다음 봉이
언제 닫혔는지**를 알아야 하고, 그걸 REST 폴링으로 하면 15m 봉 하나를 놓치지 않으려고
매초 두드려야 한다 — 레이트리밋을 태우고도 봉 마감 시점이 흐릿하다.

## 프로토콜 (실측 규격 · Gate v4)

    보낼 것:  {"time": <초>, "channel": "futures.candlesticks",
               "event": "subscribe", "payload": ["15m", "BTC_USDT"]}

    받을 것:  {"channel": "...", "event": "update", "result": [{...}]}

⚠️ **캔들 채널의 `n` 은 `"15m_BTC_USDT"` 꼴**이다 (interval 과 계약이 한 문자열로
   붙어 온다). REST 캔들의 `t/o/h/l/c/v` 와 달리 계약 이름을 따로 주지 않으므로,
   여러 계약을 한 연결로 구독하면 이 필드로 갈라야 한다.

## 🔴 미마감 봉을 진짜 봉으로 쓰지 않는다

Gate 는 **진행 중인 봉도** `update` 로 계속 보낸다. 그것을 확정 봉으로 취급하면
같은 시각의 봉이 여러 번 다른 값으로 들어오고, 지표가 매 틱 흔들린다 —
백테스트에서는 있을 수 없는 상태이므로 라이브만 다른 판단을 하게 된다.

⇒ `closed` 플래그를 함께 낸다. 소비처가 **마감된 것만** 원장에 넣는다.
   판단 기준은 `봉 시작 + 간격 <= 지금` 이다 — Gate 가 마감 여부를 따로 알려주지
   않으므로 시각으로 판정한다.
"""

import json
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, cast

import websockets

from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.trade_tick import TradeSide, TradeTick
from updown.common.logging.setup import get_logger
from updown.marketdata.gate.mapping import (
    interval_of,
    interval_seconds,
    to_candle,
    to_contract,
)

# LiveCandle 은 거래소 무관 계약이라 중립 모듈이 소유한다 (T63 §2b) — 여기서의
# re-export 는 하위 호환이다.
from updown.marketdata.stream import LiveCandle

LIVE_WS_URL: Final = "wss://fx-ws.gateio.ws/v4/ws/usdt"
"""라이브 선물 웹소켓 (USDT 정산)."""

TESTNET_WS_URL: Final = "wss://fx-ws-testnet.gateio.ws/v4/ws/usdt"
"""테스트넷 선물 웹소켓.

⛔ 여기서 오는 봉으로 **판단하지 않는다.** 호가창이 라이브와 다르므로 다른 시장이다.
   testnet 은 주문 경로 검증용이고 분석은 라이브 데이터로 한다.
"""

CANDLE_CHANNEL: Final = "futures.candlesticks"

_logger = get_logger("marketdata.gate.ws")


class GateWebSocketError(RuntimeError):
    """웹소켓 프레임을 해석할 수 없다.

    Note:
        조용히 넘기지 않는다 (절대 규칙 #8). 프레임 하나를 버리면 그 봉이 없는 것과
        구별되지 않고, 라이브에서 봉 하나가 비면 지표가 조용히 틀린다.
    """


def build_subscription(
    timeframe: Timeframe, contracts: Sequence[str], *, at: datetime | None = None
) -> list[str]:
    """캔들 채널 구독 프레임들.

    Args:
        timeframe: 시간축.
        contracts: 계약 이름들 (`BTC_USDT`).
        at: 프레임에 실을 시각. None 이면 지금.

    Returns:
        보낼 JSON 문자열 목록.

    Raises:
        GateMappingError: 지원하지 않는 시간축.
        GateWebSocketError: 계약 목록이 빈 경우.

    Note:
        🔴 **캔들 채널은 payload 가 `[interval, contract]` 이고 계약 하나씩만 받는다.**
        티커처럼 배열로 여러 개를 못 넣는다 — 그래서 계약 수만큼 프레임을 만든다.
        한 프레임에 몰아 넣으면 구독이 조용히 하나만 걸린다.
    """
    if not contracts:
        raise GateWebSocketError("구독할 계약이 없다 — 빈 구독은 연결만 열고 아무것도 안 받는다")
    interval = interval_of(timeframe)
    stamp = int((at or datetime.now(UTC)).timestamp())
    return [
        json.dumps(
            {
                "time": stamp,
                "channel": CANDLE_CHANNEL,
                "event": "subscribe",
                "payload": [interval, contract],
            }
        )
        for contract in contracts
    ]


def decode_frame(raw: bytes | str) -> dict[str, Any]:
    """프레임을 dict 로.

    Args:
        raw: 수신 프레임.

    Returns:
        파싱된 객체.

    Raises:
        GateWebSocketError: JSON 이 아니거나 객체가 아닌 경우.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        parsed: object = json.loads(text)
    except ValueError as exc:
        raise GateWebSocketError(f"프레임이 JSON 이 아니다: {text[:200]}") from exc
    if not isinstance(parsed, dict):
        raise GateWebSocketError(f"프레임이 객체가 아니다: {type(parsed).__name__}")
    return cast("dict[str, Any]", parsed)


def split_name(name: str) -> tuple[str, str]:
    """`"15m_BTC_USDT"` → `("15m", "BTC_USDT")`.

    Args:
        name: 캔들 프레임의 `n` 필드.

    Returns:
        `(interval, contract)`.

    Raises:
        GateWebSocketError: 형식이 다른 경우.

    Note:
        🔴 **계약 이름 자체에 밑줄이 있다** (`BTC_USDT`). 그래서 첫 밑줄에서만 자른다 —
        `split("_")` 로 통째 쪼개면 `USDT` 를 잃는다.
    """
    interval, _, contract = name.partition("_")
    if not interval or not contract:
        raise GateWebSocketError(
            f"캔들 이름 형식이 아니다: {name!r} — '15m_BTC_USDT' 꼴이어야 한다"
        )
    return interval, contract


def frame_to_candles(
    frame: dict[str, Any],
    instruments: dict[str, Instrument],
    timeframe: Timeframe,
    multiplier: Decimal,
    *,
    now: datetime | None = None,
) -> list[LiveCandle]:
    """`update` 프레임 → 봉들.

    Args:
        frame: 수신 프레임.
        instruments: 계약 이름 → 종목.
        timeframe: 시간축.
        multiplier: 계약 승수 (`Decimal`). 거래량을 BTC 로 옮기는 데 쓴다.
        now: 마감 판정 기준 시각. None 이면 지금.

    Returns:
        봉 목록. 이 프레임이 캔들 갱신이 아니면 빈 리스트다.

    Raises:
        GateWebSocketError: 형식이 깨진 경우.
        GateMappingError: 봉을 도메인으로 옮길 수 없는 경우.

    Note:
        ⭐ **구독 확인·에러 프레임은 조용히 건너뛴다** — 그것은 봉이 아니라 제어
        메시지이고, 예외로 만들면 정상 흐름이 예외로 돈다. 대신 `error` 가 실려 오면
        로그에 남긴다 (조용히 무시하면 구독이 안 걸린 것을 모른다).

        🔴 **모르는 계약은 버리지 않고 예외다.** 구독하지 않은 계약이 오는 것은 규격
        변경이거나 우리 구독이 틀린 것이고, 조용히 버리면 "봉이 안 온다"로만 보인다.
    """
    if frame.get("channel") != CANDLE_CHANNEL:
        return []
    error = frame.get("error")
    if error:
        _logger.error("gate_ws_error", payload={"error": error, "event": frame.get("event")})
        return []
    if frame.get("event") != "update":
        return []
    result = frame.get("result")
    if not isinstance(result, list):
        raise GateWebSocketError(f"캔들 result 가 배열이 아니다: {type(result).__name__}")

    moment = now or datetime.now(UTC)
    span = timedelta(seconds=interval_seconds(timeframe))
    out: list[LiveCandle] = []
    rows = cast("list[object]", result)
    for row in rows:
        if not isinstance(row, dict):
            raise GateWebSocketError(f"캔들 원소가 객체가 아니다: {type(row).__name__}")
        item = cast("dict[str, Any]", row)
        name = item.get("n")
        if not isinstance(name, str):
            raise GateWebSocketError(f"캔들에 n 이 없다: {item!r} — 계약을 가릴 수 없다")
        _, contract = split_name(name)
        instrument = instruments.get(contract)
        if instrument is None:
            raise GateWebSocketError(
                f"구독하지 않은 계약이 왔다: {contract} — 구독이 틀렸거나 규격이 바뀌었다"
            )
        candle = to_candle(item, instrument, timeframe, multiplier)
        out.append(LiveCandle(candle=candle, closed=candle.ts + span <= moment))
    return out


TRADE_CHANNEL: Final = "futures.trades"
"""체결 채널 — **델타 볼륨의 원천**이다 (T23).

🔴 **봉에는 총량만 있고 매수/매도 분리가 없다.** 그래서 *"이 돌파를 누가 만들었나"* 를
봉으로는 영영 못 묻는다. 과거 체결도 받을 수 없어 **백테스트가 불가능**하고, 그래서
지금부터 쌓는 것 말고는 방법이 없다 (T28 판정 기준 참고).

⚠️ 캔들 채널과 달리 payload 가 **계약 배열**이라 한 프레임에 여러 계약을 넣는다.
"""


def build_trade_subscription(contracts: Sequence[str], *, at: datetime | None = None) -> list[str]:
    """체결 채널 구독 프레임.

    Args:
        contracts: 계약 이름들 (`BTC_USDT`).
        at: 프레임에 실을 시각. None 이면 지금.

    Returns:
        보낼 JSON 문자열 목록. **한 개**다 — 계약을 배열로 넣는다.

    Raises:
        GateWebSocketError: 계약 목록이 빈 경우.

    Note:
        ⭐ **캔들과 규격이 다르다.** 캔들은 `[interval, contract]` 라 계약마다 프레임이
        하나씩 필요한데, 체결은 계약 배열을 그대로 받는다. 캔들 방식을 복사하면 구독이
        여러 번 걸려 같은 체결이 중복으로 온다.
    """
    if not contracts:
        raise GateWebSocketError("구독할 계약이 없다 — 빈 구독은 연결만 열고 아무것도 안 받는다")
    stamp = int((at or datetime.now(UTC)).timestamp())
    return [
        json.dumps(
            {
                "time": stamp,
                "channel": TRADE_CHANNEL,
                "event": "subscribe",
                "payload": list(contracts),
            }
        )
    ]


def frame_to_trades(
    frame: dict[str, Any],
    instruments: dict[str, Instrument],
    multipliers: dict[str, Decimal],
) -> list[TradeTick]:
    """`update` 프레임 → 체결들.

    Args:
        frame: 수신 프레임.
        instruments: 계약 이름 → 종목.
        multipliers: 계약 이름 → 승수. 계약 수를 기준통화로 옮긴다.

    Returns:
        체결 목록. 이 프레임이 체결 갱신이 아니면 빈 리스트다.

    Raises:
        GateWebSocketError: 형식이 깨졌거나 구독하지 않은 계약이 온 경우.

    Note:
        🔴 **방향이 `size` 의 부호에 있다** (실측 2026-08-21):

        ```
        {"id": 814154097, "contract": "BTC_USDT", "size": -5910, "price": "77393.5"}
        {"id": 814154096, "contract": "BTC_USDT", "size": 10, "price": "77389.5"}
        ```

        **양수면 매수 체결**(테이커가 샀다), 음수면 매도 체결이다. 업비트의 `ask_bid`
        와 달리 별도 필드가 없으므로 부호를 잃으면 **델타가 통째로 0** 이 된다 —
        값이 그럴듯해서 안 보이는 종류의 사고다.

        ⛔ **`size: 0` 은 예외다.** 방향이 없는 체결은 규격 위반이고, 조용히 한쪽으로
        떨어뜨리면 부호가 틀린 채 그럴듯해 보인다 (절대 규칙 #8).

        🔴 **계약 수를 기준통화로 옮긴다.** 안 하면 봉 거래량과 단위가 달라 대조가
        무의미해진다 — `base_volume` 이 캔들에서 하는 일과 같다.

        시각은 **체결 시각**(`create_time_ms`)을 쓴다. 봉에 접어 넣을 값이므로 수신
        시각이 아니라 실제로 체결된 때여야 한다.
    """
    if frame.get("channel") != TRADE_CHANNEL:
        return []
    error = frame.get("error")
    if error:
        _logger.error("gate_ws_error", payload={"error": error, "event": frame.get("event")})
        return []
    if frame.get("event") != "update":
        return []
    result = frame.get("result")
    if not isinstance(result, list):
        raise GateWebSocketError(f"체결 result 가 배열이 아니다: {type(result).__name__}")

    out: list[TradeTick] = []
    rows = cast("list[object]", result)
    for row in rows:
        if not isinstance(row, dict):
            raise GateWebSocketError(f"체결 원소가 객체가 아니다: {type(row).__name__}")
        item = cast("dict[str, Any]", row)
        contract = item.get("contract")
        if not isinstance(contract, str):
            raise GateWebSocketError(f"체결에 contract 가 없다: {item!r}")
        instrument = instruments.get(contract)
        if instrument is None:
            raise GateWebSocketError(
                f"구독하지 않은 계약이 왔다: {contract} — 구독이 틀렸거나 규격이 바뀌었다"
            )
        multiplier = multipliers.get(contract)
        if multiplier is None or multiplier <= 0:
            raise GateWebSocketError(
                f"{contract} 의 계약 승수를 모른다 — 계약 수를 수량으로 못 옮긴다"
            )

        contracts = _number(item.get("size"), "size", item)
        if contracts == 0:
            # 🔴 **실측 정정 (2026-08-22 라이브 소켓)**: Gate 는 `size: 0` 체결을 실제로
            #    보낸다 — ETH_USDT 에서 몇 초마다. 규격 위반이라 **던졌더니** 소켓이
            #    계속 재연결해 수집이 통째로 깨졌다. 방향이 없는 체결을 한쪽으로
            #    떨어뜨리면 안 된다는 원칙은 그대로다 — 그래서 **버리되 남긴다**.
            #    델타에는 안 들어가고, 로그가 건수를 말한다.
            # ⛔ 여기서 로그를 찍지 않는다 — 몇 초마다 오는 것이라 몇 주 도는 수집기의
            #    로그를 하루 10만 줄씩 불린다. 순수 함수는 건너뛰기만 하고, "거래가
            #    없었다 vs 못 받았다"는 수집기가 봉마다 남기는 trades 건수로 가른다.
            continue

        out.append(
            TradeTick(
                instrument=instrument,
                ts=_traded_at(item),
                price=_number(item.get("price"), "price", item),
                volume=abs(contracts) * multiplier,
                side=TradeSide.BUY if contracts > 0 else TradeSide.SELL,
            )
        )
    return out


#: `create_time_ms` 가 **진짜 밀리초**인지 가리는 하한 (2001-09-09 = 1e12 ms).
#:
#: 🔴 **WS 와 REST 가 이름이 같고 단위가 다르다** (실측 2026-08-21):
#:
#: ```
#: WS    "create_time": 1787320055,      초
#:       "create_time_ms": 1787320055943  진짜 밀리초 (13자리)
#: REST  "create_time": 1787319911.612,      초 (소수)
#:       "create_time_ms": 1787319911.612    🔴 초다 — 이름과 다르다
#: ```
#:
#: REST 값을 1000 으로 나누면 **1970년**이 나오고, 예외 없이 조용히 통과한다.
#: 그러면 델타가 전부 1970 봉으로 접혀 **한 봉도 안 맞는데 에러가 없다.**
_MILLIS_FLOOR = 1_000_000_000_000


def _number(value: object, field: str, item: dict[str, Any]) -> Decimal:
    """체결 필드 하나를 `Decimal` 로 — **못 읽으면 멈춘다**.

    Args:
        value: 원본 값.
        field: 필드 이름 (오류 문구용).
        item: 체결 원소 전체 (오류 문구용).

    Returns:
        수치.

    Raises:
        GateWebSocketError: 수치가 아니거나 해석할 수 없는 경우.

    Note:
        ⚠️ **`bool` 을 먼저 막는다.** 파이썬에서 `True` 는 `int` 라 그냥 두면 1 계약이 된다.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise GateWebSocketError(f"체결의 {field} 가 수치가 아니다: {value!r}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise GateWebSocketError(f"체결의 {field} 를 수로 못 읽는다: {value!r} · {item!r}") from exc


def _traded_at(item: dict[str, Any]) -> datetime:
    """체결 시각 — **단위를 확인하고** 옮긴다.

    Args:
        item: 체결 원소.

    Returns:
        UTC aware 시각.

    Raises:
        GateWebSocketError: 필드가 없거나 밀리초로 보이지 않는 경우.

    Note:
        🔴 **`_MILLIS_FLOOR` 검사가 이 함수의 이유다.** 값이 초 단위인데 1000 으로
        나누면 1970년이 나오는데, 그것은 예외가 아니라 **그럴듯한 시각**이라 조용히
        통과한다 (절대 규칙 #8). 그러면 모든 델타가 1970 봉으로 접힌다.

        ⛔ **`create_time`(초)로 대신 떨어지지 않는다.** 초로 떨어지면 밀리초 정밀도를
        잃고, 10초봉에서 그것은 봉 경계를 넘나드는 오차다. 규격이 바뀌면 **멈춘다.**
    """
    stamp = item.get("create_time_ms")
    if isinstance(stamp, bool) or not isinstance(stamp, int | float | str):
        raise GateWebSocketError(f"체결에 create_time_ms 가 없다: {item!r}")
    try:
        millis = float(stamp)
    except ValueError as exc:
        raise GateWebSocketError(f"create_time_ms 를 수로 못 읽는다: {stamp!r}") from exc
    if millis < _MILLIS_FLOOR:
        raise GateWebSocketError(
            f"create_time_ms 가 밀리초로 안 보인다: {stamp!r} — "
            "REST 는 같은 이름으로 **초**를 준다. 1000 으로 나누면 1970년이 되고 "
            "예외 없이 통과해 모든 델타가 한 봉으로 접힌다"
        )
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


class GateCandleStream:
    """캔들 채널 하나를 끊기지 않게 듣는다.

    Note:
        🔴 **끊기면 다시 붙는다.** 라이브에서 연결이 한 번 끊기면 그 사이 봉이 비고,
        빈 봉은 "거래가 없었다"와 구별되지 않는다. 재연결 뒤에는 REST 로 **구멍을
        메워야** 하며 그것은 소비처의 일이다 — 이 클래스는 끊김을 **알린다**.

        ⚠️ 재연결이 조용하면 안 된다. 로그에 남기고 `reconnects` 로 센다.
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
            instruments: 구독할 종목들. 전부 `Market.GATE` 여야 한다.
            timeframe: 시간축.
            multiplier: 계약 승로 (`Decimal`) — 어댑터가 계약 명세에서 읽어 넘긴다.
            url: 웹소켓 URL. testnet 은 `TESTNET_WS_URL`.
            ping_interval: 핑 간격(초).

        Raises:
            GateWebSocketError: 종목이 없는 경우.
            GateMappingError: GATE 종목이 아닌 것이 섞인 경우.
        """
        if not instruments:
            raise GateWebSocketError("구독할 종목이 없다")
        self._by_contract = {to_contract(item): item for item in instruments}
        self._timeframe = timeframe
        self._multiplier = multiplier
        self._url = url
        self._ping_interval = ping_interval
        self.reconnects = 0
        """재연결 횟수 — **0 이 아니면 봉에 구멍이 있을 수 있다.**"""

    @property
    def contracts(self) -> list[str]:
        """구독 중인 계약 이름들."""
        return sorted(self._by_contract)

    @property
    def is_testnet(self) -> bool:
        """테스트넷에 붙어 있는가."""
        return self._url == TESTNET_WS_URL

    async def stream(self) -> AsyncGenerator[LiveCandle]:
        """봉을 계속 낸다 (끊기면 재연결).

        Yields:
            봉. `closed` 로 마감 여부를 가린다.

        Note:
            ⛔ 예외를 삼키지 않는다. 재연결 가능한 오류(소켓·프로토콜)만 다시 붙고,
            형식 오류(`GateWebSocketError`)는 **던진다** — 규격이 바뀐 것을 조용히
            재연결로 덮으면 영원히 빈 스트림을 듣게 된다.

            🔴 **제너레이터를 중첩하지 않는다.** 예전에는 `stream → _once → _receive`
            로 셋을 겹쳤는데, 소비처가 `break` 로 빠져나오면 정리가 터졌다 (실측
            2026-08-17):

                RuntimeError: aclose(): asynchronous generator is already running

            안쪽 제너레이터가 `send` 중일 때 바깥이 닫히면서 난 것이다. 종료는
            **실제로 쓰이는 경로**이므로(재생 중지·앱 종료) 한 겹으로 펴서 없앴다.
        """
        while True:
            try:
                async with websockets.connect(
                    self._url, ping_interval=self._ping_interval
                ) as socket:
                    for payload in build_subscription(self._timeframe, self.contracts):
                        await socket.send(payload)
                    _logger.info(
                        "gate_ws_subscribed",
                        payload={
                            "url": self._url,
                            "testnet": self.is_testnet,
                            "timeframe": self._timeframe.value,
                            "contracts": self.contracts,
                        },
                    )
                    async for raw in socket:
                        for item in frame_to_candles(
                            decode_frame(raw),
                            self._by_contract,
                            self._timeframe,
                            self._multiplier,
                        ):
                            yield item
            except (OSError, websockets.WebSocketException) as exc:
                self.reconnects += 1
                _logger.warning(
                    "gate_ws_reconnect",
                    payload={
                        "reason": str(exc)[:200],
                        "reconnects": self.reconnects,
                        "contracts": self.contracts,
                        "note": "이 사이 봉에 구멍이 있을 수 있다 — REST 로 메워야 한다",
                    },
                )
