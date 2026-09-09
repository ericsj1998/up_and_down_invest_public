"""TossAdapter 검증 (spec §4.2, §12.3, §7 · docs/platform/toss_api_notes.md).

`httpx.MockTransport` 로 네트워크 없이 검증한다. 실제 토스 호출은 자격증명이 필요하고
CI 에서 돌릴 수 없으므로 **통합 테스트를 두지 않는다** — 대신 응답 픽스처를 스펙
(`docs/providers/toss.json`)의 `example` 값에서 그대로 가져와, 손으로 지어낸 필드 이름이
섞이지 않게 했다.

검증의 축은 업비트와 **다른 지점**들이다:

| 축 | 업비트 | 토스 |
|---|---|---|
| 커서 | `to` exclusive | `before` **inclusive** + `nextBefore` |
| 시각 | naive → UTC 부착 | 오프셋 有 → UTC **변환** |
| 가격 | JSON number | **문자열** |
| 시간축 | 5개 전부 네이티브 | `1d` 만 네이티브, 나머지 **합성** |
| 호가 | 한 행에 bid/ask | **배열 2개** |
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Side, Timeframe
from updown.common.domain.market import MarketSession
from updown.common.domain.order import OrderKind, OrderRequest, OrderType
from updown.common.domain.session import SessionConfigError, Tradability
from updown.marketdata.adapter import BrokerAdapter, Capability
from updown.marketdata.ingest.aggregate import AggregationError, CandleRow, merge_rows
from updown.marketdata.toss.adapter import (
    CHART_GROUP,
    MarketCalendarRequiredError,
    OrderPathNotAvailableError,
    TossAdapter,
)
from updown.marketdata.toss.client import TossApiError, TossClient
from updown.marketdata.toss.mapping import (
    TossMappingError,
    is_native,
    parse_timestamp,
    to_orderbook,
    to_quote,
    to_row,
    to_symbol,
)

SAMSUNG = Instrument(
    market=Market.KRX,
    symbol="005930",
    name="삼성전자",
    asset_type=AssetType.STOCK,
    currency=Currency.KRW,
)
APPLE = Instrument(
    market=Market.NASDAQ,
    symbol="AAPL",
    name="애플",
    asset_type=AssetType.STOCK,
    currency=Currency.USD,
)
BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

Handler = Callable[[httpx.Request], httpx.Response]

TOKEN_BODY = {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}


def make_client(handler: Handler, **kwargs: Any) -> TossClient:
    """MockTransport 를 물린 클라이언트.

    Note:
        `rate_per_second` 를 크게 둔다 — 단위 테스트에서 스로틀 대기는 시간만 쓴다.
    """
    kwargs.setdefault("rate_per_second", 100_000)
    kwargs.setdefault("max_retries", 1)
    return TossClient(
        SecretStr("id"),
        SecretStr("secret"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def candle(ts: str, *, close: str = "72000", volume: str = "1000") -> dict[str, str]:
    """스펙 `Candle` 스키마 그대로의 응답 1건."""
    return {
        "timestamp": ts,
        "openPrice": "71600",
        "highPrice": "72300",
        "lowPrice": "71500",
        "closePrice": close,
        "volume": volume,
        "currency": "KRW",
    }


def routed(pages: list[dict[str, Any]], *, record: list[httpx.Request] | None = None) -> Handler:
    """토큰 발급 + 캔들 페이지를 순서대로 돌려주는 핸들러.

    Args:
        pages: `/api/v1/candles` 가 순서대로 돌려줄 `result` 값들.
        record: 넘기면 캔들 요청을 여기에 기록한다 (파라미터 검증용).
    """
    remaining = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        if record is not None:
            record.append(request)
        empty: dict[str, Any] = {"candles": [], "nextBefore": None}
        result = remaining.pop(0) if remaining else empty
        return httpx.Response(200, json={"result": result})

    return handler


# ---------------------------------------------------------------------------
# 1. 매핑 — 토스 고유 함정
# ---------------------------------------------------------------------------


def test_offset_timestamp_is_converted_not_attached() -> None:
    """함정 ① — 오프셋이 붙어 오므로 UTC 로 **변환**해야 한다 (업비트와 반대)."""
    parsed = parse_timestamp("2026-03-25T09:00:00+09:00")
    assert parsed == datetime(2026, 3, 25, 0, 0, tzinfo=UTC), (
        "오프셋을 무시하고 UTC 를 붙이면 9시간이 통째로 어긋난다"
    )
    assert parsed.tzinfo is not None


def test_naive_timestamp_is_rejected_not_assumed() -> None:
    """오프셋이 없으면 **가정하지 않고 거부**한다 (절대 규칙 #7)."""
    with pytest.raises(TossMappingError, match="타임존이 없다"):
        parse_timestamp("2026-03-25T09:00:00")


def test_string_prices_become_exact_decimals() -> None:
    """함정 ② — 가격이 문자열이다. 부동소수를 거치지 않아야 한다."""
    row = to_row(candle("2026-03-25T09:00:00+09:00", close="72000.15"))
    assert row.close == Decimal("72000.15")
    assert row.open == Decimal("71600")


def test_missing_field_raises_instead_of_defaulting() -> None:
    """필드가 빠지면 0 으로 채우지 않고 터진다 (spec §7)."""
    broken = candle("2026-03-25T09:00:00+09:00")
    del broken["closePrice"]
    with pytest.raises(TossMappingError, match="closePrice"):
        to_row(broken)


def test_only_daily_is_native() -> None:
    """함정 ④ — `interval` 이 `1m`/`1d` 뿐이라 일봉만 네이티브다."""
    assert is_native(Timeframe.D1)
    assert not any(
        is_native(tf) for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4)
    )


def test_krx_symbol_must_be_six_digits() -> None:
    """KRX 심볼 형식을 경계에서 막는다."""
    assert to_symbol(SAMSUNG) == "005930"
    assert to_symbol(APPLE) == "AAPL"
    bad = Instrument(
        market=Market.KRX,
        symbol="SAMSUNG",
        name="삼성전자",
        asset_type=AssetType.STOCK,
        currency=Currency.KRW,
    )
    with pytest.raises(TossMappingError, match="6자리"):
        to_symbol(bad)


def test_coin_instrument_is_refused_by_toss_mapping() -> None:
    """업비트 종목을 토스에 넘기는 실수를 매핑이 잡는다."""
    with pytest.raises(TossMappingError, match="지원 시장"):
        to_symbol(BTC)


def test_orderbook_reassembles_two_arrays_into_paired_levels() -> None:
    """함정 ⑤ — 토스는 asks/bids 를 **따로** 준다. 한 행으로 재조립해야 한다."""
    book = to_orderbook(
        {
            "currency": "KRW",
            # 일부러 어긋난 순서로 준다 — 브로커 정렬 가정에 기대면 안 된다.
            "asks": [{"price": "72200", "volume": "5"}, {"price": "72100", "volume": "8"}],
            "bids": [{"price": "72000", "volume": "3"}, {"price": "72050", "volume": "9"}],
        },
        SAMSUNG,
        datetime(2026, 3, 25, tzinfo=UTC),
    )
    assert book.levels[0].ask_price == Decimal("72100"), "매도 1호가는 가장 낮은 가격이다"
    assert book.levels[0].bid_price == Decimal("72050"), "매수 1호가는 가장 높은 가격이다"
    assert book.levels[0].ask_size == Decimal("8")
    assert book.levels[0].bid_size == Decimal("9")


def test_orderbook_truncates_to_shorter_side() -> None:
    """단계 수가 다르면 짧은 쪽에 맞춘다 — 0 으로 채우면 깊이가 과대평가된다."""
    book = to_orderbook(
        {
            "currency": "KRW",
            "asks": [{"price": "72100", "volume": "8"}, {"price": "72200", "volume": "5"}],
            "bids": [{"price": "72000", "volume": "3"}],
        },
        SAMSUNG,
        datetime(2026, 3, 25, tzinfo=UTC),
    )
    assert len(book.levels) == 1


def test_empty_orderbook_is_an_event_not_a_blank() -> None:
    """장 마감 중 빈 호가는 **사건**이다 — 조용히 빈 결과를 주지 않는다."""
    with pytest.raises(TossMappingError, match="비어 있다"):
        to_orderbook(
            {"currency": "KRW", "asks": [], "bids": []},
            SAMSUNG,
            datetime(2026, 3, 25, tzinfo=UTC),
        )


def test_quote_leaves_bid_ask_none_by_design() -> None:
    """현재가 엔드포인트에는 호가가 없다 — 지어내지 않는다."""
    as_of = datetime(2026, 3, 25, tzinfo=UTC)
    quote = to_quote({"symbol": "005930", "lastPrice": "72000", "currency": "KRW"}, SAMSUNG, as_of)
    assert quote.last_price == Decimal("72000")
    assert quote.bid is None
    assert quote.ask is None
    assert quote.as_of == as_of, "브로커 시계가 아니라 우리가 잰 시각이어야 한다"


# ---------------------------------------------------------------------------
# 2. 합성 — 우리 시간축 4개가 분봉에서 나온다
# ---------------------------------------------------------------------------


def rows(count: int, *, start: datetime) -> list[CandleRow]:
    """1분 간격 행 `count` 개."""
    return [
        CandleRow(
            ts=start + timedelta(minutes=index),
            open=Decimal(100 + index),
            high=Decimal(110 + index),
            low=Decimal(90 + index),
            close=Decimal(105 + index),
            volume=Decimal(10),
        )
        for index in range(count)
    ]


def test_merge_rows_uses_standard_ohlcv_rule() -> None:
    """시가=첫 봉 · 종가=마지막 봉 · 고저=극값 · 거래량=합."""
    start = datetime(2026, 3, 25, 0, 0, tzinfo=UTC)
    result = merge_rows(
        rows(5, start=start), Timeframe.M5, SAMSUNG, source_interval=timedelta(minutes=1)
    )
    assert len(result.candles) == 1
    bar = result.candles[0]
    assert bar.ts == start
    assert bar.open == Decimal(100)
    assert bar.close == Decimal(109)
    assert bar.high == Decimal(114)
    assert bar.low == Decimal(90)
    assert bar.volume == Decimal(50)
    assert bar.timeframe is Timeframe.M5
    assert result.incomplete == 0


def test_merge_rows_counts_incomplete_instead_of_dropping() -> None:
    """장 마감 경계의 짧은 버킷을 버리지도 숨기지도 않는다 (절대 규칙 #8)."""
    start = datetime(2026, 3, 25, 0, 0, tzinfo=UTC)
    result = merge_rows(
        rows(7, start=start), Timeframe.M5, SAMSUNG, source_interval=timedelta(minutes=1)
    )
    assert len(result.candles) == 2
    assert result.incomplete == 1, "두 번째 버킷은 2봉뿐이라는 사실이 보여야 한다"


def test_merge_rows_refuses_non_multiple() -> None:
    """정확한 배수가 아니면 합성이 근사가 된다 — 경계에서 막는다."""
    start = datetime(2026, 3, 25, 0, 0, tzinfo=UTC)
    with pytest.raises(AggregationError, match="배수"):
        merge_rows(
            rows(3, start=start), Timeframe.M5, SAMSUNG, source_interval=timedelta(minutes=3)
        )


# ---------------------------------------------------------------------------
# 3. 어댑터 — 페이지네이션과 두 경로
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_path_asks_broker_directly() -> None:
    """일봉은 합성하지 않고 `interval=1d` 로 받는다."""
    seen: list[httpx.Request] = []
    client = make_client(
        routed(
            [{"candles": [candle("2026-03-25T09:00:00+09:00")], "nextBefore": None}],
            record=seen,
        )
    )
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2026, 3, 20, tzinfo=UTC),
            datetime(2026, 3, 26, tzinfo=UTC),
        )
    assert len(result) == 1
    assert result[0].timeframe is Timeframe.D1
    assert result[0].ts == datetime(2026, 3, 25, 0, 0, tzinfo=UTC)
    assert seen[0].url.params["interval"] == "1d"


@pytest.mark.asyncio
async def test_native_path_returns_ascending_despite_descending_response() -> None:
    """함정 ③ — 토스는 내림차순으로 준다. 계약은 **오름차순**이다.

    Note:
        실호출 스모크에서 실제로 걸린 회귀다. 합성 경로는 `merge_rows` 가 정렬해 주지만
        네이티브 경로에는 그 단계가 없어, 정렬을 빼먹으면 조용히 역순이 나간다. 역순
        캔들은 지표·구조물 계산을 통째로 뒤집는다.
    """
    descending = [
        candle("2026-03-25T09:00:00+09:00", close="300"),
        candle("2026-03-24T09:00:00+09:00", close="200"),
        candle("2026-03-23T09:00:00+09:00", close="100"),
    ]
    client = make_client(routed([{"candles": descending, "nextBefore": None}]))
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2026, 3, 22, tzinfo=UTC),
            datetime(2026, 3, 26, tzinfo=UTC),
        )
    assert [bar.ts for bar in result] == sorted(bar.ts for bar in result)
    assert [bar.close for bar in result] == [Decimal(100), Decimal(200), Decimal(300)]


@pytest.mark.asyncio
async def test_intraday_path_synthesizes_from_minutes() -> None:
    """5m 은 브로커가 주지 않는다 — 1m 을 받아 합성한다."""
    seen: list[httpx.Request] = []
    minutes = [candle(f"2026-03-25T09:0{index}:00+09:00") for index in range(5)]
    client = make_client(routed([{"candles": minutes, "nextBefore": None}], record=seen))
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.M5,
            datetime(2026, 3, 25, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 25, 0, 4, tzinfo=UTC),
        )
    assert seen[0].url.params["interval"] == "1m", "호출부는 5m 을 요청했지만 분봉을 받아야 한다"
    assert len(result) == 1
    assert result[0].timeframe is Timeframe.M5, "합성 결과는 요청한 시간축을 달아야 한다"
    assert result[0].ts == datetime(2026, 3, 25, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_adjusted_prices_are_requested_explicitly() -> None:
    """액면분할 왜곡 방지 — 기본값에 기대지 않고 명시적으로 보낸다."""
    seen: list[httpx.Request] = []
    client = make_client(
        routed(
            [{"candles": [candle("2026-03-25T09:00:00+09:00")], "nextBefore": None}], record=seen
        )
    )
    async with client:
        adapter = TossAdapter(client)
        await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2026, 3, 25, tzinfo=UTC),
            datetime(2026, 3, 25, tzinfo=UTC),
        )
    assert seen[0].url.params["adjusted"] == "true"


@pytest.mark.asyncio
async def test_inclusive_cursor_does_not_duplicate_boundary_bar() -> None:
    """🔴 `before` 가 inclusive 라 경계 봉이 두 페이지에 걸친다 — 중복 제거가 필수다."""
    shared = candle("2026-03-25T09:02:00+09:00")
    pages = [
        {
            "candles": [candle("2026-03-25T09:03:00+09:00"), shared],
            "nextBefore": "2026-03-25T09:02:00+09:00",
        },
        # inclusive 이므로 커서로 준 봉이 **다시** 온다.
        {"candles": [shared, candle("2026-03-25T09:01:00+09:00")], "nextBefore": None},
    ]
    client = make_client(routed(pages))
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2026, 3, 25, 0, 1, tzinfo=UTC),
            datetime(2026, 3, 25, 0, 3, tzinfo=UTC),
        )
    stamps = [bar.ts for bar in result]
    assert len(stamps) == len(set(stamps)), "inclusive 커서의 중복이 그대로 새면 봉이 두 번 센다"
    assert len(result) == 3


@pytest.mark.asyncio
async def test_empty_page_does_not_end_history() -> None:
    """🔴 빈 페이지가 곧 히스토리 끝은 아니다 — 커서를 밀고 재시도해야 한다.

    Note:
        실측 회귀다. 미국 종목은 `before` 가 **정확히 UTC 자정**일 때 양쪽에 데이터가
        있는데도 빈 페이지를 준다. 백필 창 경계가 전부 UTC 자정이라 매 창의 첫 요청이
        그 구멍에 떨어졌고, 빈 페이지에서 즉시 멈춘 탓에 **AAPL 5m 이 4,702봉**만
        적재됐다 (정상은 40,000봉 이상).
    """
    pages: list[dict[str, Any]] = [
        {"candles": [], "nextBefore": None},  # 구멍 — 여기서 멈추면 안 된다
        {
            "candles": [candle("2026-03-25T09:00:00+09:00")],
            "nextBefore": None,
        },
    ]
    client = make_client(routed(pages))
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2026, 3, 20, tzinfo=UTC),
            datetime(2026, 3, 26, tzinfo=UTC),
        )
    assert len(result) == 1, "빈 페이지에서 멈추면 그 뒤의 데이터를 통째로 잃는다"


@pytest.mark.asyncio
async def test_persistent_empty_pages_do_terminate() -> None:
    """빈 페이지가 계속되면 결국 멈춘다 — 재시도가 무한 루프가 되면 안 된다."""
    client = make_client(routed([]))  # 항상 빈 페이지
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2026, 3, 26, tzinfo=UTC),
        )
    assert result == []


@pytest.mark.asyncio
async def test_null_next_before_jumps_to_previous_segment() -> None:
    """🔴 `nextBefore=null` 은 "히스토리 끝"이 아니라 "이 세그먼트 끝"이다.

    Note:
        실측 회귀다. 미국 종목 분봉은 **거래일마다** `nextBefore` 가 끊긴다 —
        AAPL 은 하루를 걷다 장 시작에서 null 을 주고, 005930 은 날짜를 넘어 계속 간다.
        null 을 끝으로 읽으면 미국 종목이 **하루치만** 적재된다 (AAPL 1h 62봉).

        받은 것 중 가장 오래된 봉 직전으로 커서를 밀어 다음 세그먼트로 넘어가야 한다.
    """
    pages = [
        # 세그먼트 1 — 하루치를 주고 끊긴다
        {"candles": [candle("2026-03-25T09:00:00+09:00")], "nextBefore": None},
        # 세그먼트 2 — 커서를 밀면 그 앞날이 나온다
        {"candles": [candle("2026-03-24T09:00:00+09:00")], "nextBefore": None},
    ]
    client = make_client(routed(pages))
    async with client:
        adapter = TossAdapter(client)
        result = await adapter.get_candles(
            SAMSUNG,
            Timeframe.D1,
            datetime(2026, 3, 23, tzinfo=UTC),
            datetime(2026, 3, 26, tzinfo=UTC),
        )
    assert len(result) == 2, "세그먼트 경계에서 멈추면 그 이전 거래일을 전부 잃는다"


@pytest.mark.asyncio
async def test_repeated_cursor_raises_instead_of_looping() -> None:
    """같은 커서가 다시 오면 무한 루프다 — 조용히 돌지 않고 터진다 (spec §7)."""
    stuck = {
        "candles": [candle("2026-03-25T09:00:00+09:00")],
        "nextBefore": "2026-03-25T09:00:00+09:00",
    }
    client = make_client(routed([stuck, stuck, stuck]))
    async with client:
        adapter = TossAdapter(client)
        with pytest.raises(TossApiError, match="커서가 전진하지 않는다"):
            await adapter.get_candles(
                SAMSUNG,
                Timeframe.D1,
                datetime(2020, 1, 1, tzinfo=UTC),
                datetime(2026, 3, 26, tzinfo=UTC),
            )


@pytest.mark.asyncio
async def test_naive_datetime_is_refused_at_the_boundary() -> None:
    """naive 를 UTC 로 가정하면 호출부의 실수가 조용히 통과한다 (절대 규칙 #7)."""
    client = make_client(routed([]))
    async with client:
        adapter = TossAdapter(client)
        with pytest.raises(ValueError, match="timezone-aware"):
            await adapter.get_candles(
                SAMSUNG,
                Timeframe.D1,
                datetime(2026, 3, 25),
                datetime(2026, 3, 26, tzinfo=UTC),
            )


@pytest.mark.asyncio
async def test_prices_endpoint_returns_an_array_not_an_object() -> None:
    """`/api/v1/prices` 의 `result` 는 **배열**이다 — dict 로 강제하면 죽는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        return httpx.Response(
            200,
            json={"result": [{"symbol": "005930", "lastPrice": "72000", "currency": "KRW"}]},
        )

    client = make_client(handler)
    async with client:
        quote = await TossAdapter(client).get_quote(SAMSUNG)
    assert quote.last_price == Decimal("72000")


# ---------------------------------------------------------------------------
# 4. 차단 — 주문 경로와 장 상태
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_retry_honours_retry_after() -> None:
    """429 에 `Retry-After` 가 있으면 **서버가 준 값**을 쓴다.

    Note:
        지수 백오프는 헤더가 없을 때의 대비책이다. 서버가 "N초 뒤에 오라"고 했는데 더
        일찍 가면 한도를 또 넘긴다.
    """
    attempts: list[float] = []
    real_sleep = asyncio.sleep

    async def record(delay: float, *args: Any, **kwargs: Any) -> None:
        del args, kwargs  # asyncio.sleep 시그니처를 맞추기만 한다
        attempts.append(delay)
        await real_sleep(0)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"error": {"code": "rate-limit-exceeded"}}, headers={"Retry-After": "7"}
            )
        return httpx.Response(200, json={"result": {"candles": [], "nextBefore": None}})

    client = make_client(handler, max_retries=2)
    with patch("updown.marketdata.toss.client.asyncio.sleep", record):
        async with client:
            await client.get_result("/api/v1/candles", group=CHART_GROUP)

    assert 7.0 in attempts, f"Retry-After 를 무시하고 자체 백오프를 썼다: {attempts}"


@pytest.mark.asyncio
async def test_absurd_retry_after_falls_back_to_backoff() -> None:
    """헤더 하나로 백필이 몇 시간 멈추면 안 된다 — 상한을 넘으면 지수 백오프다."""
    attempts: list[float] = []
    real_sleep = asyncio.sleep

    async def record(delay: float, *args: Any, **kwargs: Any) -> None:
        del args, kwargs  # asyncio.sleep 시그니처를 맞추기만 한다
        attempts.append(delay)
        await real_sleep(0)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "99999"})
        return httpx.Response(200, json={"result": {"candles": [], "nextBefore": None}})

    client = make_client(handler, max_retries=2)
    with patch("updown.marketdata.toss.client.asyncio.sleep", record):
        async with client:
            await client.get_result("/api/v1/candles", group=CHART_GROUP)

    assert attempts, "재시도 자체가 안 됐다"
    assert all(delay < 10 for delay in attempts), f"말도 안 되는 값을 그대로 썼다: {attempts}"


def test_capabilities_exclude_conditional_orders_and_ws() -> None:
    """없는 이중화를 있다고 선언하지 않는다 (spec §7, §12.6)."""
    caps = TossAdapter(make_client(routed([]))).capabilities
    assert caps == frozenset({Capability.SPOT, Capability.ORDERBOOK})
    assert Capability.CONDITIONAL_ORDERS not in caps, (
        "선언하면 상위 계층이 서버 다운 중 손절이 걸린다고 믿는다 — 이 어댑터는 낼 수 없다"
    )
    assert Capability.WS not in caps


def test_adapter_satisfies_broker_protocol_structurally() -> None:
    """프로토콜을 어기면 상위 계층이 어댑터를 교체할 수 없다."""
    adapter: BrokerAdapter = TossAdapter(make_client(routed([])))
    assert adapter is not None


@pytest.mark.asyncio
async def test_order_paths_raise_rather_than_pretending() -> None:
    """주문 경로 전부가 명시적으로 막힌다 (절대 규칙 #0 의 두 번째 방어선)."""
    adapter = TossAdapter(make_client(routed([])))
    request = OrderRequest(
        instrument=SAMSUNG,
        side=Side.BUY,
        order_kind=OrderKind.ENTRY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(1),
        price=Decimal(72000),
        idempotency_key="k",
        approved_order_id="approval-1",
        leg_index=0,
        revision_id="rev-1",
    )
    with pytest.raises(OrderPathNotAvailableError):
        await adapter.submit_order(request)
    with pytest.raises(OrderPathNotAvailableError):
        await adapter.cancel_order("x")
    with pytest.raises(OrderPathNotAvailableError):
        await adapter.get_order_status("x")
    with pytest.raises(OrderPathNotAvailableError):
        await adapter.get_balance()


class _UnknownCalendar:
    """유효 구간 밖 — 캘린더가 모른다고 답하는 경우."""

    def tradability(self, market: Market, moment: datetime) -> tuple[Tradability, str]:  # noqa: ARG002
        return Tradability.UNKNOWN, "휴장일 목록이 없는 해"

    def session_at(self, market: Market, moment: datetime) -> MarketSession:  # noqa: ARG002
        return MarketSession.CLOSED

    def next_events(self, market: Market, moment: datetime) -> tuple[None, None]:  # noqa: ARG002
        return None, None


@pytest.mark.asyncio
async def test_market_status_outside_calendar_coverage_is_not_orderable() -> None:
    """캘린더가 모르는 날은 `REGULAR` 을 지어내지 않는다 (C2-3·C2-4, 절대 규칙 #8 · T240)."""
    adapter = TossAdapter(make_client(routed([])), calendar=_UnknownCalendar())  # type: ignore[arg-type]
    status = await adapter.get_market_status(SAMSUNG)
    assert status.is_order_allowed is False and status.session is MarketSession.CLOSED


@pytest.mark.asyncio
async def test_market_status_without_a_readable_calendar_is_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """캘린더 파일을 못 읽으면 예외 — 조용히 열린 척하지 않는다."""

    def _broken() -> None:
        raise SessionConfigError("없다")

    monkeypatch.setattr("updown.marketdata.toss.adapter.load_calendar", _broken)
    adapter = TossAdapter(make_client(routed([])))
    with pytest.raises(MarketCalendarRequiredError, match="마켓 캘린더"):
        await adapter.get_market_status(SAMSUNG)
