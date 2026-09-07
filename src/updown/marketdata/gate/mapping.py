"""Gate.io v4 무기한 선물 응답 ↔ 도메인 변환 (순수 함수).

## 왜 매핑을 따로 두는가

업비트와 같은 이유다 — **응답 규격이 바뀌면 여기 한 곳만 깨진다.** 어댑터가 dict 를
직접 헤집으면 규격 변경이 여러 곳에서 조용히 다르게 터진다.

## 🔴 Gate 선물이 업비트와 다른 세 가지

1. **거래량 단위가 계약(contract)이다.** `v: 2132098` 은 BTC 수량이 아니라 계약 수이며,
   `quanto_multiplier` (BTC_USDT 는 **0.0001**) 를 곱해야 BTC 수량이 된다.

   ⚠️ 이것을 놓치면 거래량이 **1만 배**로 들어간다. 거래량 기준선(judgement_spec 부록 B
   1번)이 그 값을 쓰므로 조용히 전부 틀린다 — 예외가 나지 않는 종류의 사고다.

   ⭐ `sum` 은 USDT 거래대금이다 (업비트 `candle_acc_trade_price` 에 해당). 계약 수와
     혼동하면 안 되므로 둘을 다른 함수로 뽑는다.

2. **시각이 초 단위 유닉스**다. 업비트는 ISO 문자열이었다. 그리고 `t` 는 **봉 시작**
   시각이다.

3. **`v` 만 문자열이 아니다.** OHLC 와 `sum` 은 문자열인데 `v` 는 정수로 온다
   (실측 2026-08-17). 그래서 `_decimal` 을 한 벌로 쓸 수 없다.

## 심볼

도메인 심볼이 `BTC_USDT` 면 그대로 쓴다. 업비트 표기(`KRW-BTC`)를 여기 넣으면
**다른 상품**이다 — KRW 현물과 USDT 무기한은 가격·수수료·펀딩이 전부 다르므로
섞이면 조용히 틀린다. 그래서 형식을 검사하고 아니면 예외를 던진다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Market, Timeframe

_INTERVALS: Final[dict[Timeframe, str]] = {
    Timeframe.S10: "10s",
    Timeframe.S30: "30s",
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.H8: "8h",
    Timeframe.D1: "1d",
}
"""시간축 → Gate `interval` 문자열.

⭐ 값이 우연히 `Timeframe` 값과 같지만 **표를 유지한다.** `timeframe.value` 를 그대로
보내면, 우리가 나중에 Gate 에 없는 축(`1w` 등)을 더했을 때 조용히 400 이 돌아온다.

✅ **실측으로 채웠다** (2026-08-18 · `futures/usdt/candlesticks`): 10s·30s·1m·5m·15m·
30m·1h·4h·8h·1d 전부 200 이고 봉 간격도 맞다. Gate 는 `7d·30d` 도 주지만 우리 열거형에
없다.

⚠️ **과거 깊이는 10,000봉까지다** — 30s 는 3.5일, 1m 은 6.9일뿐이다. 하위 축을 백테스트에
쓸 수 없는 이유가 이것이고, 그래서 **보기 전용**이다 (사용자 확정 2026-08-18).
"""

_INTERVAL_SECONDS: Final[dict[Timeframe, int]] = {
    Timeframe.S10: 10,
    Timeframe.S30: 30,
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.H8: 28800,
    Timeframe.D1: 86400,
}

CANDLE_LIMIT: Final = 2000
"""한 번에 받을 수 있는 최대 봉 수 (선물). 초과하면 400 이다.

⚠️ 현물(`/spot/candlesticks`)은 1000 이다. 같은 값으로 두지 않는다.
"""

SETTLE: Final = "usdt"
"""정산 통화. `BTC_USDT` 무기한은 USDT 정산이다."""

HISTORY_LIMIT = 10000
"""시간축별로 받을 수 있는 **최대 과거 봉 수**.

🔴 하드 제약이다 (실측 2026-08-17):

    400 INVALID_PARAM_VALUE
    "Candlestick too long ago. Maximum 10000 points recently are allowed"

시간축마다 실제 기간이 다르다:

    5m   약   34일
    15m  약  104일
    1h   약  416일
    4h   약  4.5년
    1d   약   27년

⚠️ **업비트와 다르다.** 업비트는 2022-01 까지 적재해 뒀지만 Gate 는 최근 창만 준다 —
   4년 백테스트를 Gate 데이터로 할 수 없다는 뜻이다. 그래서 판정은 여전히 적재된
   업비트 데이터로 하고, Gate 는 **라이브·최근 구간**을 맡는다.

⛔ 이 제약을 조용히 넘기지 않는다. 넘기면 500 이 나고, 500 은 "서버가 깨졌다" 로
   읽힌다 — 실제로는 요청이 사정거리 밖인 것이다.
"""


def earliest_servable(timeframe: Timeframe, now: datetime) -> datetime:
    """이 시간축으로 받을 수 있는 **가장 이른** 시각.

    Args:
        timeframe: 시간축.
        now: 기준 시각 (보통 지금).

    Returns:
        이보다 이른 구간은 Gate 가 거부한다.

    Raises:
        GateMappingError: 지원하지 않는 시간축.

    Note:
        경계를 정확히 맞추려 하지 않는다 — 거래소가 세는 방식이 우리와 다를 수 있으므로
        **여유를 두지 않고** 계산하고, 실패하면 그 응답을 그대로 전한다. 여기서 하려는
        것은 "왜 안 되는지" 를 미리 말하는 것뿐이다.
    """
    return now - timedelta(seconds=interval_seconds(timeframe) * HISTORY_LIMIT)


class GateMappingError(ValueError):
    """Gate 응답을 도메인으로 옮길 수 없을 때.

    Note:
        규격 변경·필드 부재를 **조용히 넘기지 않는다** (절대 규칙 #8). 캔들 하나가
        None 으로 빠지면 그 구간만 비어 있는 시리즈가 만들어지고, 그것은 "거래가
        없었다"와 구별되지 않는다.
    """


def interval_of(timeframe: Timeframe) -> str:
    """시간축 → Gate `interval` 문자열.

    Args:
        timeframe: 시간축.

    Returns:
        `"15m"` 같은 Gate 표기.

    Raises:
        GateMappingError: Gate 가 지원하지 않는 시간축.
    """
    found = _INTERVALS.get(timeframe)
    if found is None:
        raise GateMappingError(
            f"Gate 선물이 지원하지 않는 timeframe 이다: {timeframe} — "
            f"지원: {sorted(item.value for item in _INTERVALS)}"
        )
    return found


def interval_seconds(timeframe: Timeframe) -> int:
    """봉 간격(초).

    Args:
        timeframe: 시간축.

    Returns:
        간격(초).

    Raises:
        GateMappingError: 간격이 정의되지 않은 시간축.
    """
    found = _INTERVAL_SECONDS.get(timeframe)
    if found is None:
        raise GateMappingError(f"간격이 정의되지 않은 timeframe 이다: {timeframe}")
    return found


def to_contract(instrument: Instrument) -> str:
    """도메인 `Instrument` → Gate 계약 이름.

    Args:
        instrument: 대상 종목. `market` 이 `GATE` 여야 한다.

    Returns:
        `"BTC_USDT"`.

    Raises:
        GateMappingError: 시장이 GATE 가 아니거나 심볼 형식이 다른 경우.

    Note:
        🔴 **업비트 표기를 받지 않는다.** `KRW-BTC` 를 여기 넣으면 KRW 현물을 USDT
        무기한으로 착각하는 것이고, 두 상품은 가격·수수료·펀딩이 전부 다르다.
        형식이 다르면 예외를 던져 그 혼동이 런타임까지 살아남지 못하게 한다.
    """
    if instrument.market is not Market.GATE:
        raise GateMappingError(
            f"GATE 어댑터에 {instrument.market.value} 종목이 들어왔다: {instrument.symbol} — "
            "같은 BTC 라도 다른 상품이다 (업비트는 KRW 현물, 여기는 USDT 무기한)"
        )
    symbol = instrument.symbol.strip().upper()
    if "_" not in symbol or symbol.startswith("_") or symbol.endswith("_"):
        raise GateMappingError(
            f"Gate 계약 이름 형식이 아니다: {instrument.symbol!r} — 'BTC_USDT' 처럼 "
            "밑줄로 기준통화와 정산통화를 잇는다 (업비트의 'KRW-BTC' 가 아니다)"
        )
    return symbol


def parse_seconds(raw: object, field: str) -> datetime:
    """Gate 의 초 단위 유닉스 시각 → tz-aware UTC.

    Args:
        raw: `1786957200` · `"1786957200"` · `1786957200.285` 중 무엇이든.
        field: 오류 문구에 쓸 필드 이름.

    Returns:
        UTC `datetime`.

    Raises:
        GateMappingError: 숫자로 읽을 수 없는 경우.

    Note:
        Gate 문서가 *"소수점 있는 숫자로 파싱하라"* 고 못박고 있다 — 같은 필드가
        int·float·문자열로 오는 것이 규격이다. 그래서 형을 좁게 잡지 않는다.

        🔴 **반드시 tz-aware 로 만든다** (절대 규칙 #7 · spec §12.3). naive 를
        돌려주면 도메인·영속화 가드가 나중에 거부하는데, 그 지점이 여기서 멀다.
    """
    try:
        moment = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise GateMappingError(
            f"{field} 를 시각으로 읽을 수 없다: {raw!r} — 규격 변경 신호다"
        ) from exc
    return datetime.fromtimestamp(moment, tz=UTC)


def _decimal(raw: object, field: str) -> Decimal:
    """문자열·숫자 → `Decimal`.

    Args:
        raw: 응답 값.
        field: 오류 문구에 쓸 필드 이름.

    Returns:
        `Decimal`.

    Raises:
        GateMappingError: 없거나 숫자로 읽히지 않는 경우.

    Note:
        `float` 을 거치지 않는다 — 가격을 float 로 받으면 정밀도가 조용히 깎이고,
        그것이 손절가가 0.1 틀리는 원인이다.
    """
    if raw is None:
        raise GateMappingError(f"{field} 가 없다 — 규격 변경 신호다")
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise GateMappingError(f"{field} 를 수로 읽을 수 없다: {raw!r}") from exc


def base_volume(payload: dict[str, Any], multiplier: Decimal) -> Decimal:
    """계약 수 → **기준통화 수량** (BTC).

    Args:
        payload: 캔들 응답 원소. `v` 를 읽는다.
        multiplier: 계약 승수 (`quanto_multiplier`). BTC_USDT 는 `0.0001`.

    Returns:
        BTC 수량.

    Raises:
        GateMappingError: `v` 가 없거나 수가 아닌 경우, 또는 승수가 0 이하인 경우.

    Note:
        🔴 **이 함수가 없으면 거래량이 1만 배로 들어간다.** `v: 2132098` 은 계약 수이고
        BTC 로는 213.2 다. 거래량 기준선이 그 값을 쓰므로 조용히 전부 틀린다 —
        예외가 나지 않는 종류의 사고다.

        ⛔ 승수를 상수로 박지 않는다. 계약마다 다르고 거래소가 바꿀 수 있으므로
        `/futures/{settle}/contracts/{contract}` 에서 읽어 넘긴다. 박아 두면
        ETH·SOL 을 추가하는 순간 조용히 틀린다.
    """
    if multiplier <= 0:
        raise GateMappingError(
            f"계약 승수가 {multiplier} 다 — 0 이하면 거래량이 0 이나 음수가 된다. "
            "명세를 못 읽었을 때 기본값으로 넘기지 않는다"
        )
    return _decimal(payload.get("v"), "v(계약 수)") * multiplier


def quote_volume(payload: dict[str, Any]) -> Decimal:
    """USDT 거래대금 (`sum`).

    Args:
        payload: 캔들 응답 원소.

    Returns:
        거래대금.

    Raises:
        GateMappingError: `sum` 이 없거나 수가 아닌 경우.

    Note:
        업비트 `candle_acc_trade_price` 에 해당한다. `base_volume` 과 **다른 값**이며
        섞으면 거래량 판정이 통화 단위로 흔들린다.
    """
    return _decimal(payload.get("sum"), "sum(거래대금)")


def to_candle(
    payload: dict[str, Any],
    instrument: Instrument,
    timeframe: Timeframe,
    multiplier: Decimal,
) -> Candle:
    """캔들 응답 1건 → `Candle`.

    Args:
        payload: `/futures/{settle}/candlesticks` 응답 배열의 원소.
        instrument: 대상 종목.
        timeframe: 시간축.
        multiplier: 계약 승수 — 거래량을 BTC 수량으로 옮기는 데 쓴다.

    Returns:
        도메인 캔들. `ts` 는 **봉 시작** 시각(UTC)이다.

    Raises:
        GateMappingError: 필드 부재·형식 오류.

    Note:
        OHLC 논리 검증(`high >= max(open, close)` 등)은 하지 않는다 — 위반 봉도 일단
        적재하고 구간을 기록하는 것이 D-14 설계다. 업비트 매핑과 같은 방침이다.
    """
    return Candle(
        instrument=instrument,
        timeframe=timeframe,
        ts=parse_seconds(payload.get("t"), "t"),
        open=_decimal(payload.get("o"), "o"),
        high=_decimal(payload.get("h"), "h"),
        low=_decimal(payload.get("l"), "l"),
        close=_decimal(payload.get("c"), "c"),
        volume=base_volume(payload, multiplier),
    )


def to_multiplier(payload: dict[str, Any]) -> Decimal:
    """계약 명세 → 계약 승수.

    Args:
        payload: `/futures/{settle}/contracts/{contract}` 응답.

    Returns:
        `quanto_multiplier`.

    Raises:
        GateMappingError: 없거나 0 이하인 경우.

    Note:
        ⛔ 기본값으로 되돌리지 않는다. 승수를 모르면 거래량을 **모르는** 것이고,
        모르는 값을 1 로 채우면 1만 배 틀린 시리즈가 조용히 만들어진다.
    """
    found = _decimal(payload.get("quanto_multiplier"), "quanto_multiplier")
    if found <= 0:
        raise GateMappingError(f"quanto_multiplier 가 {found} 다 — 거래량 환산이 불가능하다")
    return found
