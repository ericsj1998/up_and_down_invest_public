"""토스 응답 ↔ 도메인 모델 변환 (spec §4.2 매핑 레이어).

이 파일이 **유일하게** 토스의 필드 이름을 안다. 어댑터·클라이언트는 도메인 타입만 다루므로
토스가 필드를 바꾸면 고칠 파일이 여기 하나다 (`upbit/mapping.py` 와 같은 구조).

실측·스펙 근거는 `docs/platform/toss_api_notes.md` 에 있다. 아래 변환은 그 문서의 함정을 흡수한다:

1. `timestamp` 에 **오프셋이 붙어 온다**(`+09:00`) → UTC 로 **변환**한다 (업비트처럼 붙이는
   것이 아니다)
2. 가격·거래량이 **문자열**이다 → `Decimal` 로 안전하게 간다
3. 응답이 **내림차순**이다 → 계약은 오름차순 (뒤집기는 어댑터가 한다)
4. `interval` 이 **`1m`/`1d` 뿐**이다 → 나머지는 어댑터가 1m 에서 합성한다
5. 심볼 규칙이 시장마다 다르다 — KRX 6자리 숫자 / US 티커
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Market, Timeframe
from updown.common.domain.market import OrderBook, OrderBookLevel, Quote
from updown.marketdata.ingest.aggregate import CandleRow

MAX_CANDLE_COUNT = 200
"""1회 요청 최대 캔들 개수 (스펙 `count` 의 maximum).

**이 값을 넘겨 보내지 않는다** — 400 이 나거나 조용히 잘린다. 잘린 것을 "전부 받았다"고
믿으면 캔들에 구멍이 생기고 지표·구조물·백테스트 전부가 오염된다 (spec §12.1).
"""

MINUTE_INTERVAL = "1m"
"""분봉 `interval` 값.

🔴 우리 `Timeframe` 에는 `1m` 이 **없다** (D-8: `{5m,15m,1h,4h,1d}`). 1m 은 분석·저장
대상이 아니라 **5m·15m·1h·4h 를 만들기 위한 원재료**이므로 도메인 enum 이 아니라 여기
문자열 상수로 둔다. 그래서 반환 타입도 `Candle` 이 아닌 `CandleRow` 다.
"""

DAILY_INTERVAL = "1d"
"""일봉 `interval` 값 — 유일하게 토스가 **그대로** 주는 우리 시간축이다."""

#: 토스가 지원하는 시장.
SUPPORTED_MARKETS: frozenset[Market] = frozenset({Market.KRX, Market.NASDAQ, Market.NYSE})


class TossMappingError(ValueError):
    """토스 응답을 도메인 모델로 옮길 수 없다.

    Note:
        응답 형식이 바뀌었다는 신호다. **조용히 기본값으로 채우지 않는다** — 값이 빠진
        캔들이 DB 에 들어가면 원인을 찾을 수 없다 (spec §7).
    """


def to_symbol(instrument: Instrument) -> str:
    """도메인 `Instrument` → 토스 심볼.

    Args:
        instrument: 대상 종목.

    Returns:
        KRX 는 6자리 숫자(`005930`), 미국은 티커(`AAPL`).

    Raises:
        TossMappingError: 토스가 다루지 않는 시장이거나 심볼 형식이 어긋난 경우.

    Note:
        변환이 사실상 통과다 — 우리 `Instrument.symbol` 이 이미 그 형식이기 때문이다.
        그래도 함수를 두는 이유는 **시장 검증**이다: 업비트 종목을 토스 어댑터에 넘기는
        실수가 여기서 잡힌다 (`upbit/mapping.to_market_code` 와 같은 역할).
    """
    if instrument.market not in SUPPORTED_MARKETS:
        raise TossMappingError(
            f"토스 어댑터에 {instrument.market} 종목이 들어왔다: {instrument.symbol} — "
            f"지원 시장: {sorted(item.value for item in SUPPORTED_MARKETS)}"
        )
    symbol = instrument.symbol.strip()
    if not symbol:
        raise TossMappingError("심볼이 비어 있다")
    if instrument.market is Market.KRX and not (symbol.isdigit() and len(symbol) == 6):
        raise TossMappingError(f"KRX 심볼은 6자리 숫자여야 한다 (스펙 `symbol` 규칙): {symbol!r}")
    return symbol


def is_native(timeframe: Timeframe) -> bool:
    """토스가 이 시간축을 **그대로** 주는가.

    Args:
        timeframe: 시간축.

    Returns:
        일봉이면 True. 나머지는 False — 호출부가 분봉에서 합성해야 한다.

    Note:
        스펙의 `interval` enum 이 `["1m", "1d"]` 뿐이라, 우리 시간축 5개 중 일봉 하나만
        네이티브다. 이 비대칭이 어댑터가 두 경로를 갖는 이유다.
    """
    return timeframe is Timeframe.D1


def parse_timestamp(raw: str) -> datetime:
    """`timestamp` 를 UTC aware datetime 으로 만든다.

    Args:
        raw: `"2026-03-25T09:00:00+09:00"` 형식. **오프셋이 붙어 온다**.

    Returns:
        UTC 로 변환한 aware datetime.

    Raises:
        TossMappingError: 파싱 불가이거나 오프셋이 없는 경우.

    Note:
        ⚠️ **업비트와 반대다.** 업비트는 오프셋 없는 naive 문자열이라 UTC 를 *붙였고*,
        토스는 오프셋이 있으니 UTC 로 *변환*한다. 오프셋이 없는 값이 오면 어느 시각인지
        알 수 없으므로 **가정하지 않고 거부**한다 (절대 규칙 #7).
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TossMappingError(f"캔들 시각을 파싱할 수 없다: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise TossMappingError(
            f"캔들 시각에 타임존이 없다: {raw!r} — 어느 시각인지 알 수 없으므로 "
            "UTC 로 가정하지 않는다 (spec §12.3)"
        )
    return parsed.astimezone(UTC)


def _decimal(payload: dict[str, Any], key: str) -> Decimal:
    """응답의 수치 필드를 `Decimal` 로 꺼낸다.

    Args:
        payload: 응답 dict.
        key: 필드 이름.

    Returns:
        Decimal 값.

    Raises:
        TossMappingError: 필드 부재 또는 수치 아님.

    Note:
        토스는 가격을 **문자열**로 준다(`"71600"`). 업비트의 JSON number 와 달리 부동소수를
        거치지 않아 오히려 안전하다. 그래도 `str()` 을 한 번 더 태우는 것은 숫자로 올 때를
        대비한 것이다.
    """
    if key not in payload:
        raise TossMappingError(f"응답에 {key!r} 필드가 없다 — 규격이 바뀌었을 수 있다")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TossMappingError(f"{key!r} 가 수치가 아니다: {value!r}")
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise TossMappingError(f"{key!r} 를 Decimal 로 만들 수 없다: {value!r}") from exc


def to_row(payload: dict[str, Any]) -> CandleRow:
    """캔들 응답 1건 → 시간축 라벨 없는 `CandleRow`.

    Args:
        payload: `result.candles[]` 의 원소.

    Returns:
        `ts` 가 UTC 인 OHLCV 한 줄.

    Raises:
        TossMappingError: 필드 부재·형식 오류.

    Note:
        분봉 경로가 쓰는 함수다. 분봉에는 붙일 `Timeframe` 이 없으므로(우리 enum 에 `1m`
        이 없다) 라벨 없는 행으로 받고, 합성 뒤 목표 시간축의 `Candle` 로 승격한다.

        OHLC 논리 검증은 여기서 하지 않는다 — 위반 봉도 적재하고 구간을
        `candle_quality_issues` 에 기록하는 것이 plan D-14 설계다 (`upbit/mapping` 과 동일).
    """
    raw_ts = payload.get("timestamp")
    if not isinstance(raw_ts, str):
        raise TossMappingError(
            f"timestamp 가 없거나 문자열이 아니다: {raw_ts!r} — 규격 변경 신호다"
        )
    return CandleRow(
        ts=parse_timestamp(raw_ts),
        open=_decimal(payload, "openPrice"),
        high=_decimal(payload, "highPrice"),
        low=_decimal(payload, "lowPrice"),
        close=_decimal(payload, "closePrice"),
        volume=_decimal(payload, "volume"),
    )


def to_candle(payload: dict[str, Any], instrument: Instrument, timeframe: Timeframe) -> Candle:
    """캔들 응답 1건 → `Candle` (네이티브 시간축 전용).

    Args:
        payload: `result.candles[]` 의 원소.
        instrument: 대상 종목.
        timeframe: 시간축. 토스가 그대로 주는 것이어야 한다 (`is_native`).

    Returns:
        도메인 캔들 (`ts` 는 UTC).

    Raises:
        TossMappingError: 필드 부재·형식 오류.
    """
    row = to_row(payload)
    return Candle(
        instrument=instrument,
        timeframe=timeframe,
        ts=row.ts,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )


def to_quote(payload: dict[str, Any], instrument: Instrument, as_of: datetime) -> Quote:
    """`/api/v1/prices` 응답 1건 → `Quote`.

    Args:
        payload: `result[]` 의 원소 (`PriceResponse`).
        instrument: 대상 종목.
        as_of: **요청 직전에 우리가 잰 시각** (spec §4.18).

    Returns:
        `bid`/`ask` 는 None 이다.

    Raises:
        TossMappingError: 필드 부재·형식 오류.

    Note:
        `bid`/`ask` 가 None 인 것은 누락이 아니라 설계다 — 토스도 업비트처럼 현재가와
        호가를 **다른 엔드포인트·다른 rate limit 그룹**으로 나눠 두었다. 시세 조회마다
        호가를 함께 받으면 예산이 두 배로 든다 (`OrderBook` docstring 과 같은 이유).

        `as_of` 에 응답의 `timestamp` 를 쓰지 않는다. 그것은 브로커 시계이고, staleness
        판정(spec §4.18)에 브로커 시계 오차가 섞이면 안 된다.
    """
    return Quote(
        instrument=instrument,
        last_price=_decimal(payload, "lastPrice"),
        bid=None,
        ask=None,
        as_of=as_of,
    )


def to_orderbook(payload: dict[str, Any], instrument: Instrument, as_of: datetime) -> OrderBook:
    """`/api/v1/orderbook` 응답 → `OrderBook`.

    Args:
        payload: `result` (`OrderbookResponse`).
        instrument: 대상 종목.
        as_of: **요청 직전에 우리가 잰 시각**.

    Returns:
        1호가부터의 단계들.

    Raises:
        TossMappingError: 필드 부재·형식 오류이거나 호가가 비어 있는 경우.

    Note:
        ⚠️ **토스는 `asks`/`bids` 를 따로 준다** — 업비트가 한 행에 묶어 주는 것과 다르다.
        우리 `OrderBookLevel` 은 한 행에 묶는 형태이므로 여기서 **인덱스로 재조립**한다.

        재조립은 조용히 틀릴 수 있는 작업이라(1호가끼리 짝이 맞지 않아도 그럴듯한 값이
        나온다) 두 가지를 지킨다: ① 두 배열을 **각각 최우선부터** 정렬해 브로커의 순서
        가정에 기대지 않는다(매수는 내림차순, 매도는 오름차순), ② 길이가 다르면 **짧은
        쪽에 맞춰 자른다** — 없는 단계를 0 으로 채우면 깊이가 과대평가되어 슬리피지가
        낙관적으로 나온다 (spec §12.7).
    """
    asks = _entries(payload, "asks")
    bids = _entries(payload, "bids")
    if not asks or not bids:
        raise TossMappingError(
            f"호가창이 비어 있다 (asks={len(asks)}, bids={len(bids)}): {instrument.symbol} — "
            "장 마감 중이거나 거래정지일 수 있다"
        )
    asks.sort(key=lambda entry: entry[0])
    bids.sort(key=lambda entry: entry[0], reverse=True)

    levels = tuple(
        OrderBookLevel(bid_price=bid[0], bid_size=bid[1], ask_price=ask[0], ask_size=ask[1])
        for bid, ask in zip(bids, asks, strict=False)
    )
    return OrderBook(instrument=instrument, levels=levels, as_of=as_of)


def _entries(payload: dict[str, Any], key: str) -> list[tuple[Decimal, Decimal]]:
    """`asks`/`bids` 배열을 `(가격, 잔량)` 목록으로 만든다.

    Args:
        payload: 호가 응답.
        key: `"asks"` 또는 `"bids"`.

    Returns:
        `(price, volume)` 목록.

    Raises:
        TossMappingError: 배열이 아니거나 원소 형식이 어긋난 경우.
    """
    raw: object = payload.get(key)
    if not isinstance(raw, list):
        raise TossMappingError(f"{key!r} 가 배열이 아니다: {type(raw).__name__}")
    entries: list[tuple[Decimal, Decimal]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            raise TossMappingError(f"{key!r} 원소가 객체가 아니다: {item!r}")
        level = cast("dict[str, Any]", item)
        entries.append((_decimal(level, "price"), _decimal(level, "volume")))
    return entries


def to_before(moment: datetime) -> str:
    """`before` 파라미터용 ISO 8601 문자열 (UTC).

    Args:
        moment: 기준 시각.

    Returns:
        `2026-03-25T09:00:00+00:00` 형식.

    Note:
        🔴 **`before` 는 inclusive 다** (업비트 `to` 는 exclusive). 이 함수는 문자열만
        만들고, **중복 제거는 어댑터의 책임**이다 — 그 사실을 잊으면 커서가 전진하지 않아
        무한 루프가 된다 (docs/platform/toss_api_notes.md 함정 ③).

        `httpx` 가 쿼리 인코딩을 하므로 `+` 를 손으로 `%2B` 로 바꾸지 않는다. 직접
        문자열을 이어 붙이는 경로를 만들면 그때 인코딩 실수가 난다.
    """
    return moment.astimezone(UTC).isoformat()
