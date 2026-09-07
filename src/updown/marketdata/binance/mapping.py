"""바이낸스 표기 변환 — 심볼·시간축·캔들 (T62).

내부 규약은 `BTC_USDT`(Gate 와 동일)이고 바이낸스 표기(`BTCUSDT`)는 이 경계에서만
만든다 — 상위 계층이 거래소 표기를 알면 시장 추가마다 온 코드가 바뀐다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Market, Timeframe


class BinanceMappingError(RuntimeError):
    """바이낸스 응답·표기가 기대와 다르다 — 조용히 넘기지 않는다 (규칙 #8)."""


#: 바이낸스 klines interval 표기. ⚠️ 10초봉은 없다 — 방아쇠 축이 10s 인 룰은
#: 여기서 못 돌고, 요청하면 즉시 예외다 (조용히 다른 축으로 바꾸지 않는다).
_INTERVALS: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}

_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1_800,
    Timeframe.H1: 3_600,
    Timeframe.H4: 14_400,
    Timeframe.D1: 86_400,
}


def to_symbol(instrument: Instrument) -> str:
    """도메인 종목 → 바이낸스 심볼 (`BTC_USDT` → `BTCUSDT`).

    Args:
        instrument: BINANCE 종목.

    Returns:
        `_` 를 뺀 바이낸스 표기.

    Raises:
        BinanceMappingError: BINANCE 종목이 아니거나 표기가 규약 밖인 경우.
    """
    if instrument.market is not Market.BINANCE:
        raise BinanceMappingError(
            f"{instrument.market.value} 종목을 바이낸스 표기로 바꿀 수 없다: {instrument.symbol}"
        )
    if "_" not in instrument.symbol:
        raise BinanceMappingError(f"심볼 규약(BASE_QUOTE) 밖이다: {instrument.symbol!r}")
    return instrument.symbol.replace("_", "")


#: 바이낸스 무기한선물 견적통화 — 역변환 시 여기서 경계를 찾는다 (긴 것부터).
_QUOTES = ("USDT", "USDC", "BUSD", "USD")


def from_binance(symbol: str) -> str:
    """바이낸스 심볼 → 도메인 표기 (`BTCUSDT` → `BTC_USDT`).

    Args:
        symbol: 바이낸스 표기 (또는 이미 내부 표기).

    Returns:
        `BASE_QUOTE`. 견적통화를 모르면 입력 그대로.

    Note:
        🔴 **경계 문제** (2026-09-01 §4): 거래소 포지션 조회는 바이낸스 표기(`BTCUSDT`)를
        주는데 내부·`Instrument` 는 `BASE_QUOTE` 를 요구한다. 이 경계에서 `_` 를 되꽂는다.

        ⚠️ 이미 `_` 가 있으면(내부/Gate 표기) 그대로 돌려준다 — 하위호환. 아는
        견적통화가 아니면 그대로 둔다(조용히 틀린 값을 만들지 않는다).
    """
    if "_" in symbol:
        return symbol
    for quote in _QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[: -len(quote)]}_{quote}"
    return symbol


def interval_of(timeframe: Timeframe) -> str:
    """시간축 → klines interval 표기.

    Args:
        timeframe: 시간축.

    Returns:
        `1m`·`15m`·`1h` 같은 바이낸스 표기.

    Raises:
        BinanceMappingError: 바이낸스에 없는 축 (10s 등).
    """
    try:
        return _INTERVALS[timeframe]
    except KeyError as exc:
        raise BinanceMappingError(f"바이낸스에 {timeframe.value} 봉이 없다") from exc


def interval_seconds(timeframe: Timeframe) -> int:
    """시간축의 초 길이 — 페이징 커서 전진에 쓴다.

    Args:
        timeframe: 시간축.

    Returns:
        초.

    Raises:
        BinanceMappingError: 지원하지 않는 축.
    """
    try:
        return _SECONDS[timeframe]
    except KeyError as exc:
        raise BinanceMappingError(f"바이낸스에 {timeframe.value} 봉이 없다") from exc


def to_candle(row: list[Any], instrument: Instrument, timeframe: Timeframe) -> Candle:
    """Klines 행 → 도메인 캔들.

    Args:
        row: klines 응답의 한 행 (고정 위치 배열).
        instrument: 종목.
        timeframe: 봉 간격.

    Returns:
        UTC aware 캔들.

    Note:
        행 형식(고정 위치 배열): `[openTime(ms), open, high, low, close, volume, ...]`.
        ⭐ **거래량이 이미 기초자산 수량이다** — Gate 처럼 계약수x승수 변환이 없다.
        시각은 openTime(UTC ms) → aware UTC (절대 규칙 #7).

    Raises:
        BinanceMappingError: 행이 짧거나 수로 읽히지 않는 경우.
    """
    if len(row) < 6:
        raise BinanceMappingError(f"kline 행이 짧다({len(row)}): {row!r}")
    try:
        return Candle(
            instrument=instrument,
            timeframe=timeframe,
            ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
        )
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise BinanceMappingError(f"kline 행을 못 읽었다: {row!r}") from exc


def spec_with_compat(found: dict[str, Any]) -> dict[str, Any]:
    """ExchangeInfo 심볼 항목 → **Gate 호환 키**를 얹은 명세.

    `quanto_multiplier`(=stepSize) · `order_size_min`(=minQty/stepSize · 정수) ·
    `order_price_round`(=tickSize). "계약 1개 = stepSize 코인" 으로 읽으면 러너의
    정수 계약 사이징이 두 거래소에서 같은 산수가 된다 (T62 결정).

    Args:
        found: `exchangeInfo.symbols[]` 의 한 항목.

    Returns:
        원문 + 호환 키 세 개.

    Raises:
        BinanceMappingError: 필터가 빠진 경우.
    """
    tick = step = min_qty = None
    for item in cast("list[dict[str, Any]]", found.get("filters", [])):
        kind = str(item.get("filterType"))
        if kind == "PRICE_FILTER":
            tick = Decimal(str(item["tickSize"]))
        elif kind == "LOT_SIZE":
            step = Decimal(str(item["stepSize"]))
            min_qty = Decimal(str(item["minQty"]))
    if tick is None or step is None or min_qty is None:
        raise BinanceMappingError(f"필터가 빠졌다: tick={tick} step={step} min={min_qty}")
    return {
        **found,
        "quanto_multiplier": str(step),
        "order_size_min": int(min_qty / step),
        "order_price_round": str(tick),
    }


def price_text(price: Decimal) -> str:
    """지정가 전송용 문자열.

    Args:
        price: 가격.

    Returns:
        꼬리 0 을 턴 십진 표기 (지수 표기 없음). 소수점 과다가 400 을 부른다 — Gate 와 같은 사상.
    """
    return format(price.normalize(), "f")
