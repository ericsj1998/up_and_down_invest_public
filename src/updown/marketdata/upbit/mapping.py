"""업비트 응답 ↔ 도메인 모델 변환 (spec §4.2 매핑 레이어 최소형).

이 파일이 **유일하게** 업비트의 필드 이름을 안다. 어댑터·클라이언트는 도메인 타입만
다루므로, 업비트가 필드를 바꾸면 고칠 파일이 여기 하나다.

실측 규격은 `docs/platform/upbit_api_notes.md` 에 있다. 아래 변환은 그 문서의 §3 표를 코드로 옮긴
것이며, 특히 **네 가지 함정**을 여기서 흡수한다:

1. `candle_date_time_utc` 는 타임존 표기 없는 naive 문자열 → UTC 를 붙인다 (§12.3)
2. 종가 필드명이 `trade_price` 다 (`close` 가 아니다)
3. 응답이 내림차순이다 → 계약은 오름차순 (뒤집기는 어댑터가 한다)
4. `count` 상한 200 을 넘기면 **조용히 잘린다** → 상한을 여기 상수로 고정한다
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Market, Timeframe
from updown.common.domain.market import (
    MarketSession,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    Quote,
)

#: 1회 요청 최대 캔들 개수 (실측: 초과 요청은 에러 없이 200개로 잘린다).
#:
#: **이 값을 넘겨 보내는 코드 경로를 만들지 않는다.** 1000개를 요청해 200개를 받고
#: "전부 받았다"고 믿으면 캔들에 구멍이 생기고, 그 구멍은 지표·구조물·백테스트 전부를
#: 오염시킨다 (spec §12.1).
MAX_CANDLE_COUNT = 200

#: Timeframe → 분봉 unit. `1d` 는 별도 엔드포인트라 여기 없다.
#:
#: 업비트가 지원하는 unit 은 `{1,3,5,10,15,30,60,240}` 뿐이다 (실측 — 120·480 은 400).
#: 새 Timeframe 을 추가할 때 임의의 분 단위를 쓸 수 없으므로 먼저 이 표를 확인한다.
_MINUTE_UNITS: dict[Timeframe, int] = {
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
}

#: Timeframe 의 실제 간격(초). P0-8 결측 봉 판정과 정렬 검증이 쓴다.
_INTERVAL_SECONDS: dict[Timeframe, int] = {
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}


class UpbitMappingError(ValueError):
    """업비트 응답을 도메인 모델로 옮길 수 없다.

    Note:
        응답 형식이 바뀌었다는 신호다. **조용히 기본값으로 채우지 않는다** — 값이 빠진
        캔들이 DB 에 들어가면 원인을 찾을 수 없다 (spec §7).
    """


def candle_path(timeframe: Timeframe) -> str:
    """Timeframe 에 해당하는 캔들 엔드포인트 경로.

    Args:
        timeframe: 시간축.

    Returns:
        `/candles/...` 경로.

    Raises:
        UpbitMappingError: 업비트가 지원하지 않는 시간축.

    Note:
        주봉(`1w`)은 `Timeframe` 에 아직 없다 (D-8 — P3 장투 시점). 추가할 때
        `/candles/weeks` 를 여기에 붙인다.
    """
    if timeframe is Timeframe.D1:
        return "/candles/days"
    unit = _MINUTE_UNITS.get(timeframe)
    if unit is None:
        raise UpbitMappingError(f"업비트가 지원하지 않는 timeframe 이다: {timeframe}")
    return f"/candles/minutes/{unit}"


def interval_seconds(timeframe: Timeframe) -> int:
    """봉 간격을 초로 반환한다.

    Args:
        timeframe: 시간축.

    Returns:
        간격(초).

    Raises:
        UpbitMappingError: 간격이 정의되지 않은 시간축.
    """
    seconds = _INTERVAL_SECONDS.get(timeframe)
    if seconds is None:
        raise UpbitMappingError(f"간격이 정의되지 않은 timeframe 이다: {timeframe}")
    return seconds


def to_market_code(instrument: Instrument) -> str:
    """도메인 `Instrument` → 업비트 마켓 코드.

    Args:
        instrument: 대상 종목.

    Returns:
        `KRW-BTC` 형식의 마켓 코드.

    Raises:
        UpbitMappingError: 업비트 종목이 아닌 경우.

    Note:
        업비트 심볼은 이미 `KRW-BTC` 형식이라 변환이 사실상 통과다. 그래도 함수를 두는
        이유는 **시장 검증**이다 — KRX 종목을 업비트 어댑터에 넘기는 실수가 여기서 잡힌다.
        토스 어댑터가 붙으면 이 자리에서 실제 변환이 생긴다.
    """
    if instrument.market is not Market.UPBIT:
        raise UpbitMappingError(
            f"업비트 어댑터에 {instrument.market} 종목이 들어왔다: {instrument.symbol}"
        )
    return instrument.symbol


def parse_candle_ts(raw: str) -> datetime:
    """`candle_date_time_utc` 를 UTC aware datetime 으로 만든다.

    Args:
        raw: `"2026-08-03T10:45:00"` 형식. **타임존 표기가 없다** (실측).

    Returns:
        UTC aware datetime.

    Raises:
        UpbitMappingError: 파싱 불가.

    Note:
        업비트가 오프셋 없는 문자열을 주므로 `fromisoformat` 결과는 naive 다. 그대로 두면
        컨테이너 타임존에 따라 해석이 갈리고, `Candle.__post_init__` 가 예외를 던진다
        (spec §12.3, 절대 규칙 #7).

        `Z` 가 붙어서 오는 경우도 받아들인다 — 업비트가 나중에 표기를 추가해도 깨지지 않게.
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpbitMappingError(f"캔들 시각을 파싱할 수 없다: {raw!r}") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _decimal(payload: dict[str, Any], key: str) -> Decimal:
    """응답의 수치 필드를 `Decimal` 로 꺼낸다.

    Args:
        payload: 응답 dict.
        key: 필드 이름.

    Returns:
        Decimal 값.

    Raises:
        UpbitMappingError: 필드 부재 또는 수치 아님.

    Note:
        업비트는 JSON `number` 로 주므로 파이썬에서 `float` 이 된다. **`str()` 을 거쳐
        `Decimal` 로 만든다** — `Decimal(float)` 은 이진 부동소수 오차를 그대로 옮긴다
        (`Decimal(0.1)` → `0.1000000000000000055511151231257827`).
    """
    if key not in payload:
        raise UpbitMappingError(f"응답에 {key!r} 필드가 없다 — 규격이 바뀌었을 수 있다")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise UpbitMappingError(f"{key!r} 가 수치가 아니다: {value!r}")
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise UpbitMappingError(f"{key!r} 를 Decimal 로 만들 수 없다: {value!r}") from exc


def to_candle(payload: dict[str, Any], instrument: Instrument, timeframe: Timeframe) -> Candle:
    """캔들 응답 1건 → `Candle`.

    Args:
        payload: `/candles/...` 응답 배열의 원소.
        instrument: 대상 종목.
        timeframe: 시간축.

    Returns:
        도메인 캔들.

    Raises:
        UpbitMappingError: 필드 부재·형식 오류.

    Note:
        **종가는 `trade_price` 다.** 이름이 "체결가"라 현재가로 오해하기 쉽지만 캔들
        문맥에서는 종가다 (docs/platform/upbit_api_notes.md §3-②).

        OHLC 논리 검증(`high >= max(open, close)` 등)은 여기서 하지 않는다 — 위반 봉도
        일단 적재하고 구간을 `candle_quality_issues` 에 기록하는 것이 plan D-14 설계다.
    """
    raw_ts = payload.get("candle_date_time_utc")
    if not isinstance(raw_ts, str):
        raise UpbitMappingError(
            f"candle_date_time_utc 가 없거나 문자열이 아니다: {raw_ts!r} — 규격 변경 신호다"
        )
    return Candle(
        instrument=instrument,
        timeframe=timeframe,
        ts=parse_candle_ts(raw_ts),
        open=_decimal(payload, "opening_price"),
        high=_decimal(payload, "high_price"),
        low=_decimal(payload, "low_price"),
        close=_decimal(payload, "trade_price"),
        volume=_decimal(payload, "candle_acc_trade_volume"),
    )


def to_quote(payload: dict[str, Any], instrument: Instrument, as_of: datetime) -> Quote:
    """Ticker 응답 1건 → `Quote`.

    Args:
        payload: `/ticker` 응답 배열의 원소.
        instrument: 대상 종목.
        as_of: **조회 시각(UTC)**. 호출부가 재는 값이다.

    Returns:
        시세 스냅샷. `bid`/`ask` 는 항상 None (아래 Note).

    Raises:
        UpbitMappingError: 필드 부재·형식 오류.

    Note:
        **`bid`/`ask` 가 None 인 것은 누락이 아니라 사실이다.** 업비트 `/ticker` 응답에
        호가가 없다 (실측 — 필드 목록에 bid/ask 계열이 없다). 호가는 `/orderbook` 이라는
        **별도 rate limit 그룹**의 엔드포인트이며, 매 시세 조회마다 호출하면 예산이 두 배로
        든다. 호가가 실제로 필요한 곳은 집행(P2)이므로 그때 붙인다.

        `as_of` 를 응답의 `timestamp` 가 아니라 **호출부의 측정 시각**으로 받는 이유:
        staleness 는 "우리가 언제 이 값을 봤는가"의 문제다 (spec §4.18). 브로커 시계를
        신뢰하는 순간 시계 오차가 staleness 판정에 섞인다.
    """
    return Quote(
        instrument=instrument,
        last_price=_decimal(payload, "trade_price"),
        bid=None,
        ask=None,
        as_of=as_of,
    )


def to_orderbook(payload: dict[str, Any], instrument: Instrument, as_of: datetime) -> OrderBook:
    """Orderbook 응답 1건 → `OrderBook`.

    Args:
        payload: `/orderbook` 응답 배열의 원소.
        instrument: 대상 종목.
        as_of: **조회 시각(UTC)**. 호출부가 재는 값이다.

    Returns:
        호가창 스냅샷. 단계 순서는 응답 순서(최우선 호가부터)를 그대로 지킨다.

    Raises:
        UpbitMappingError: `orderbook_units` 부재·빈 배열·필드 형식 오류.

    Note:
        **응답 순서를 재정렬하지 않는다.** 업비트는 `orderbook_units` 를 최우선 호가부터
        주며(실측 — bid 내림차순·ask 오름차순이 한 행에 묶여 온다), 여기서 다시 정렬하면
        규격이 바뀌었을 때 그 변화가 조용히 흡수된다. 순서가 깨지면 `OrderBook.walk` 가
        더 나쁜 가격부터 소진해 슬리피지가 과대 계상되는데, 그것은 **드러나야 하는 오류**다.

        `timestamp`(브로커 시계, ms)는 **버린다.** staleness 는 "우리가 언제 이 값을
        봤는가"의 문제이고(spec §4.18), 브로커 시계를 섞으면 시계 오차가 비용 측정에
        들어간다 — `to_quote` 와 같은 이유다.
    """
    units = payload.get("orderbook_units")
    if not isinstance(units, list):
        raise UpbitMappingError(
            f"orderbook_units 가 없거나 배열이 아니다: {type(units).__name__} — 규격 변경 신호다"
        )
    rows = [
        cast(dict[str, Any], item) for item in cast(list[object], units) if isinstance(item, dict)
    ]
    if not rows:
        raise UpbitMappingError(
            f"orderbook_units 가 비어 있다: {instrument.symbol} — "
            "호가가 없다는 것 자체가 사건이므로 빈 호가창으로 넘기지 않는다 (spec §7)"
        )
    return OrderBook(
        instrument=instrument,
        levels=tuple(
            OrderBookLevel(
                bid_price=_decimal(row, "bid_price"),
                bid_size=_decimal(row, "bid_size"),
                ask_price=_decimal(row, "ask_price"),
                ask_size=_decimal(row, "ask_size"),
            )
            for row in rows
        ),
        as_of=as_of,
    )


def to_market_status(instrument: Instrument, as_of: datetime) -> MarketStatus:
    """코인의 장 상태 (spec §7 — 24시간 장).

    Args:
        instrument: 대상 종목.
        as_of: 조회 시각 (UTC).

    Returns:
        `ALWAYS_OPEN` 상태. 휴장 개념이 없으므로 `next_open`/`next_close` 는 None 이다.

    Note:
        **투자경고·거래정지를 담지 못한다** — `MarketStatus` 에 자리가 없다. 업비트는 그
        정보를 제공하지만(`market_event` in `/market/all`, `is_trading_suspended` in WS
        ticker), `MarketSession.HALTED` 로 접어 넣지 않았다: 투자주의·경고는 거래정지가
        아니고(계속 거래된다), 섞으면 §7 의 서로 다른 대응(재분석 vs 포지션 동결)을
        구분할 수 없다. P0-3 승인 계약이라 임의 확장하지 않는다 —
        **P2 실주문 전 확장 필수**로 기록돼 있다
        (docs/platform/upbit_api_notes.md §8, Phase02 §2-0b).
    """
    return MarketStatus(
        instrument=instrument,
        session=MarketSession.ALWAYS_OPEN,
        is_order_allowed=True,
        as_of=as_of,
        next_open=None,
        next_close=None,
    )


def to_iso_z(moment: datetime) -> str:
    """`to` 파라미터용 UTC 문자열.

    Args:
        moment: 기준 시각.

    Returns:
        `2026-08-03T10:40:00Z` 형식.

    Note:
        업비트의 `to` 는 **exclusive** 다 (실측) — `to` 자신은 결과에서 빠진다. 이 성질이
        커서 반복을 단순하게 만든다: 받은 페이지의 가장 오래된 봉의 `ts` 를 다음 `to` 로
        넘기면 그 봉이 다시 오지 않아 **중복 제거 로직이 필요 없다**.

        `Z` 를 붙여 보내는 이유는 로그 가독성이다 — 오프셋 없는 문자열은 나중에 읽을 때
        기준이 모호해진다.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
