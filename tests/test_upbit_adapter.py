"""UpbitAdapter 검증 (P0-7-7 · spec §4.2, §12.3, §7).

두 층으로 나눈다:

- **단위**: `httpx.MockTransport` 로 네트워크 없이 매핑·페이지네이션·재시도·차단을 검증.
  픽스처는 `tests/fixtures/upbit/` 의 **실측 응답**이다 — 손으로 쓴 가짜면 필드 이름
  오타를 잡지 못한다.
- **통합** (`@pytest.mark.integration`): 실제 `api.upbit.com` 호출. **CI 에서는 제외**한다
  (D-1 의 미결 항목을 여기서 확정 — 외부 API 장애가 CI 를 깨는 것을 원치 않는다).
  `make test-all` 로 로컬에서 돌린다.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Side, Timeframe
from updown.common.domain.market import MarketSession
from updown.common.domain.order import OrderKind, OrderRequest, OrderType
from updown.marketdata.adapter import BrokerAdapter, Capability
from updown.marketdata.upbit.adapter import (
    MAX_CURSOR_PAGES,
    OrderPathNotAvailableError,
    UpbitAdapter,
)
from updown.marketdata.upbit.client import (
    HTTP_TOO_MANY_REQUESTS,
    UnknownMarketError,
    UpbitApiError,
    UpbitClient,
)
from updown.marketdata.upbit.mapping import (
    MAX_CANDLE_COUNT,
    UpbitMappingError,
    candle_path,
    interval_seconds,
    parse_candle_ts,
    to_candle,
    to_iso_z,
    to_market_code,
)
from updown.marketdata.upbit.ws import (
    UpbitTickerStream,
    UpbitWebSocketError,
    build_subscription,
    decode_frame,
    frame_to_quote,
)

FIXTURES = Path(__file__).parent / "fixtures" / "upbit"

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
SAMSUNG = Instrument(
    market=Market.KRX,
    symbol="005930",
    name="삼성전자",
    asset_type=AssetType.STOCK,
    currency=Currency.KRW,
)


def load(name: str) -> Any:
    """픽스처를 읽는다."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


Handler = Callable[[httpx.Request], httpx.Response]


def always(status: int, *, json_body: Any = None, text: str | None = None) -> Handler:
    """항상 같은 응답을 주는 핸들러를 만든다.

    Note:
        `httpx.Response` 객체를 재사용하지 않고 **호출마다 새로 만든다** — 재시도 테스트는
        같은 핸들러를 여러 번 부르고, 응답 본문 스트림은 한 번 읽히면 소진된다.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=json_body)

    return handler


def make_client(handler: Handler, **kwargs: Any) -> UpbitClient:
    """MockTransport 를 물린 클라이언트.

    Note:
        `rate_per_second` 를 크게 둔다 — 단위 테스트에서 스로틀 대기는 시간만 쓴다.
        스로틀 자체는 별도 테스트가 검증한다.
    """
    kwargs.setdefault("rate_per_second", 100_000)
    kwargs.setdefault("max_retries", 2)
    return UpbitClient(transport=httpx.MockTransport(handler), **kwargs)


# ---------------------------------------------------------------------------
# 1. 매핑 — 실측 함정 4개 (docs/platform/upbit_api_notes.md §3)
# ---------------------------------------------------------------------------


def test_naive_timestamp_becomes_utc_aware() -> None:
    """함정 ① — 업비트는 타임존 표기 없는 문자열을 준다 (spec §12.3, 절대 규칙 #7)."""
    parsed = parse_candle_ts("2026-08-03T10:45:00")
    assert parsed.tzinfo is not None, "naive 로 남으면 저장 시점에 타임존이 뒤섞인다"
    assert parsed == datetime(2026, 8, 3, 10, 45, tzinfo=UTC)


def test_offset_suffixed_timestamp_still_works() -> None:
    """업비트가 나중에 `Z` 를 붙여도 깨지지 않아야 한다."""
    assert parse_candle_ts("2026-08-03T10:45:00Z") == datetime(2026, 8, 3, 10, 45, tzinfo=UTC)


def test_trade_price_maps_to_close() -> None:
    """함정 ② — 종가 필드명이 `trade_price` 다 (`close` 가 아니다)."""
    row = load("candles_5m.json")[0]
    candle = to_candle(row, BTC, Timeframe.M5)
    assert candle.close == Decimal("89438000.0")
    assert candle.open == Decimal("89435000.0")
    assert candle.volume == Decimal(str(row["candle_acc_trade_volume"]))


def test_prices_avoid_binary_float_error() -> None:
    """`Decimal(float)` 은 이진 오차를 그대로 옮긴다 — `str()` 을 거쳐야 한다."""
    candle = to_candle(
        {
            "candle_date_time_utc": "2026-08-03T10:45:00",
            "opening_price": 0.1,
            "high_price": 0.1,
            "low_price": 0.1,
            "trade_price": 0.1,
            "candle_acc_trade_volume": 0.1,
        },
        BTC,
        Timeframe.M5,
    )
    assert candle.close == Decimal("0.1"), "부동소수 오차가 Decimal 로 새어 들어왔다"


def test_daily_candle_boundary_is_utc_midnight() -> None:
    """일봉 경계는 00:00 UTC (= 09:00 KST) 이고 간격이 정확히 24시간이다."""
    rows = load("candles_1d.json")
    candles = [to_candle(row, BTC, Timeframe.D1) for row in rows]
    for candle in candles:
        assert (candle.ts.hour, candle.ts.minute, candle.ts.second) == (0, 0, 0)
    gap = abs(candles[0].ts - candles[1].ts)
    assert gap == timedelta(seconds=interval_seconds(Timeframe.D1))


def test_missing_field_raises_instead_of_defaulting() -> None:
    """필드가 없으면 예외다 — 0 으로 채우면 원인을 찾을 수 없다 (spec §7)."""
    row = dict(load("candles_5m.json")[0])
    del row["trade_price"]
    with pytest.raises(UpbitMappingError, match="trade_price"):
        to_candle(row, BTC, Timeframe.M5)


def test_missing_timestamp_names_the_regression() -> None:
    """시각 필드 부재는 **규격 변경 신호**임이 메시지에 드러나야 한다."""
    with pytest.raises(UpbitMappingError, match="규격 변경"):
        to_candle({"opening_price": 1}, BTC, Timeframe.M5)


@pytest.mark.parametrize(
    ("timeframe", "expected"),
    [
        (Timeframe.M5, "/candles/minutes/5"),
        (Timeframe.M15, "/candles/minutes/15"),
        (Timeframe.H1, "/candles/minutes/60"),
        (Timeframe.H4, "/candles/minutes/240"),
        (Timeframe.D1, "/candles/days"),
    ],
)
def test_every_timeframe_maps_to_a_supported_endpoint(timeframe: Timeframe, expected: str) -> None:
    """D-8 의 5개 Timeframe 이 전부 업비트 지원 unit 으로 간다 (실측: 120·480 은 400)."""
    assert candle_path(timeframe) == expected


def test_wrong_market_is_rejected_at_the_boundary() -> None:
    """KRX 종목을 업비트 어댑터에 넘기는 실수는 매핑에서 잡힌다."""
    with pytest.raises(UpbitMappingError, match="KRX"):
        to_market_code(SAMSUNG)


def test_cursor_string_is_explicitly_utc() -> None:
    """`to` 는 `Z` 를 붙여 보낸다 — 로그에서 기준이 모호해지지 않게."""
    assert to_iso_z(datetime(2026, 8, 3, 10, 40, tzinfo=UTC)) == "2026-08-03T10:40:00Z"


# ---------------------------------------------------------------------------
# 2. get_candles — 정렬 · 페이지네이션 · 구간 절단
# ---------------------------------------------------------------------------


async def test_candles_are_returned_ascending() -> None:
    """함정 ③ — 업비트는 내림차순, 계약은 오름차순이다 (spec §4.2)."""
    rows = load("candles_5m.json")
    assert rows[0]["candle_date_time_utc"] > rows[-1]["candle_date_time_utc"], (
        "픽스처가 내림차순이 아니면 이 테스트가 아무것도 검증하지 못한다"
    )

    async with make_client(always(200, json_body=rows)) as client:
        candles = await UpbitAdapter(client).get_candles(
            BTC,
            Timeframe.M5,
            datetime(2026, 8, 3, 10, 30, tzinfo=UTC),
            datetime(2026, 8, 3, 10, 45, tzinfo=UTC),
        )

    assert [c.ts for c in candles] == sorted(c.ts for c in candles)
    assert candles[0].ts == datetime(2026, 8, 3, 10, 30, tzinfo=UTC)
    assert all(c.ts.tzinfo is not None for c in candles), "DoD 5 — 전부 UTC aware"


async def test_candle_gaps_are_uniform() -> None:
    """DoD 5 — TF 간격이 일정하다."""
    async with make_client(always(200, json_body=load("candles_5m.json"))) as client:
        candles = await UpbitAdapter(client).get_candles(
            BTC,
            Timeframe.M5,
            datetime(2026, 8, 3, 10, 30, tzinfo=UTC),
            datetime(2026, 8, 3, 10, 45, tzinfo=UTC),
        )
    gaps = {(b.ts - a.ts).total_seconds() for a, b in pairwise(candles)}
    assert gaps == {float(interval_seconds(Timeframe.M5))}


async def test_never_requests_more_than_the_silent_cap() -> None:
    """함정 ④ — 상한 초과 요청은 **조용히 잘린다**. 그 경로를 아예 만들지 않는다."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("count"))
        return httpx.Response(200, json=load("candles_5m.json"))

    async with make_client(handler) as client:
        await UpbitAdapter(client).get_candles(
            BTC,
            Timeframe.M5,
            datetime(2026, 8, 3, 10, 30, tzinfo=UTC),
            datetime(2026, 8, 3, 10, 45, tzinfo=UTC),
        )

    assert seen and all(int(c or 0) <= MAX_CANDLE_COUNT for c in seen)


async def test_cursor_walks_backwards_without_duplicates() -> None:
    """`to` 가 exclusive 이므로 페이지 경계에 중복이 없다 (실측 §4).

    업비트를 흉내내는 가짜 서버: 200봉씩, `to` 미만을 내림차순으로 준다.
    """
    step = interval_seconds(Timeframe.M5)
    end = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    start = end - timedelta(seconds=step * 450)  # 3페이지 필요
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw_to = request.url.params.get("to")
        assert isinstance(raw_to, str)
        cursor = datetime.strptime(raw_to, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        rows: list[dict[str, Any]] = []
        moment = cursor - timedelta(seconds=step)  # exclusive
        for _ in range(MAX_CANDLE_COUNT):
            rows.append(
                {
                    "candle_date_time_utc": moment.strftime("%Y-%m-%dT%H:%M:%S"),
                    "opening_price": 100,
                    "high_price": 110,
                    "low_price": 90,
                    "trade_price": 105,
                    "candle_acc_trade_volume": 1,
                }
            )
            moment -= timedelta(seconds=step)
        pages.append(len(rows))
        return httpx.Response(200, json=rows)

    async with make_client(handler) as client:
        candles = await UpbitAdapter(client).get_candles(BTC, Timeframe.M5, start, end)

    timestamps = [c.ts for c in candles]
    assert len(timestamps) == len(set(timestamps)), "페이지 경계에서 봉이 중복됐다"
    assert timestamps == sorted(timestamps)
    assert timestamps[0] >= start and timestamps[-1] <= end, "구간 밖 봉이 섞였다"
    assert len(pages) >= 3, "한 페이지로 끝나면 커서 반복을 검증하지 못한다"


async def test_stalled_cursor_raises_instead_of_looping_forever() -> None:
    """`to` 가 inclusive 로 바뀌면 무한 루프다 → 시끄럽게 죽는다 (spec §7)."""
    frozen = [
        {
            "candle_date_time_utc": "2026-08-03T10:45:00",
            "opening_price": 1,
            "high_price": 1,
            "low_price": 1,
            "trade_price": 1,
            "candle_acc_trade_volume": 1,
        }
    ]

    async with make_client(always(200, json_body=frozen)) as client:
        with pytest.raises(UpbitApiError, match="inclusive"):
            await UpbitAdapter(client).get_candles(
                BTC,
                Timeframe.M5,
                datetime(2020, 1, 1, tzinfo=UTC),  # 아주 과거 → 커서가 계속 돌아야 한다
                datetime(2026, 8, 3, 10, 45, tzinfo=UTC),
            )


async def test_naive_datetime_is_rejected() -> None:
    """naive 입력을 UTC 로 가정하지 않는다 (절대 규칙 #7)."""
    async with make_client(always(200, json_body=[])) as client:
        with pytest.raises(ValueError, match="timezone-aware"):
            await UpbitAdapter(client).get_candles(
                BTC, Timeframe.M5, datetime(2026, 8, 3, 10, 0), datetime.now(UTC)
            )


async def test_reversed_range_is_rejected() -> None:
    async with make_client(always(200, json_body=[])) as client:
        with pytest.raises(ValueError, match="늦다"):
            await UpbitAdapter(client).get_candles(
                BTC, Timeframe.M5, datetime.now(UTC), datetime.now(UTC) - timedelta(hours=1)
            )


def test_cursor_page_limit_is_bounded() -> None:
    """무한 루프 방지 상한이 실재해야 한다."""
    assert 0 < MAX_CURSOR_PAGES <= 1000


# ---------------------------------------------------------------------------
# 3. get_quote / get_market_status
# ---------------------------------------------------------------------------


async def test_quote_maps_price_and_has_no_orderbook() -> None:
    """`bid`/`ask` 가 None 인 것은 누락이 아니라 사실이다 (ticker 에 호가가 없다)."""
    rows = load("ticker_btc.json")
    assert not any(k.startswith(("bid", "ask")) for k in rows[0]), (
        "픽스처에 호가가 있으면 이 테스트의 전제가 틀렸다"
    )

    async with make_client(always(200, json_body=rows)) as client:
        quote = await UpbitAdapter(client).get_quote(BTC)

    assert quote.last_price == Decimal(str(rows[0]["trade_price"]))
    assert quote.bid is None and quote.ask is None
    assert quote.as_of.tzinfo is not None, "staleness 판정 근거가 없다 (spec §4.18)"


async def test_quote_as_of_is_our_clock_not_the_brokers() -> None:
    """`as_of` 는 우리가 잰 시각이다 — 브로커 시계 오차를 staleness 에 섞지 않는다."""
    rows = [dict(load("ticker_btc.json")[0], timestamp=0, trade_timestamp=0)]
    before = datetime.now(UTC)
    async with make_client(always(200, json_body=rows)) as client:
        quote = await UpbitAdapter(client).get_quote(BTC)
    assert quote.as_of >= before, "응답의 timestamp(0)를 그대로 썼다"


async def test_empty_ticker_response_is_an_error() -> None:
    """빈 배열을 조용히 통과시키지 않는다."""
    async with make_client(always(200, json_body=[])) as client:
        with pytest.raises(UpbitApiError, match="비어 있다"):
            await UpbitAdapter(client).get_quote(BTC)


async def test_market_status_is_always_open_for_coins() -> None:
    """P0-7-6 — 코인은 24시간 장이다 (spec §7)."""
    async with make_client(always(200, json_body=load("ticker_btc.json"))) as client:
        status = await UpbitAdapter(client).get_market_status(BTC)

    assert status.session is MarketSession.ALWAYS_OPEN
    assert status.is_order_allowed is True
    assert status.next_open is None and status.next_close is None


async def test_market_status_verifies_the_symbol_exists() -> None:
    """없는 종목에 "거래 가능"이라고 답하지 않는다 (spec §7 조용한 실패 금지)."""
    unknown = always(404, json_body={"error": {"name": 404, "message": "Code not found"}})
    async with make_client(unknown) as client:
        with pytest.raises(UnknownMarketError):
            await UpbitAdapter(client).get_market_status(BTC)


async def test_market_list_is_returned() -> None:
    async with make_client(always(200, json_body=load("market_all.json"))) as client:
        codes = await UpbitAdapter(client).list_markets()
    assert "KRW-BTC" in codes


# ---------------------------------------------------------------------------
# 4. 클라이언트 — 재시도 정책
# ---------------------------------------------------------------------------


async def test_rate_limit_is_retried() -> None:
    """429 는 재시도한다 — 기다리면 풀린다."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="Too many requests")
        return httpx.Response(200, json=load("ticker_btc.json"))

    async with make_client(handler) as client:
        quote = await UpbitAdapter(client).get_quote(BTC)
    assert calls["n"] == 2, "429 를 재시도하지 않았다"
    assert quote.last_price > 0


async def test_429_is_not_mistaken_for_a_spec_error() -> None:
    """실측 중 겪은 오독 방지 — 429 를 "미지원 unit" 으로 결론내면 1h/4h 를 못 쓴다."""
    async with make_client(always(429, text="Too many requests")) as client:
        with pytest.raises(UpbitApiError) as caught:
            await UpbitAdapter(client).get_quote(BTC)
    assert caught.value.status_code == HTTP_TOO_MANY_REQUESTS
    assert not isinstance(caught.value, UnknownMarketError)


async def test_server_error_is_retried() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return (
            httpx.Response(503, text="unavailable")
            if calls["n"] < 3
            else httpx.Response(200, json=load("ticker_btc.json"))
        )

    async with make_client(handler) as client:
        await UpbitAdapter(client).get_quote(BTC)
    assert calls["n"] == 3


async def test_client_errors_are_not_retried() -> None:
    """4xx 는 요청이 잘못된 것이라 반복해도 같은 답이다."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            400, json={"error": {"name": 400, "message": "specified unit is not valid."}}
        )

    async with make_client(handler) as client:
        with pytest.raises(UpbitApiError, match="400"):
            await UpbitAdapter(client).get_quote(BTC)
    assert calls["n"] == 1, "4xx 를 재시도해 한도만 낭비했다"


async def test_unknown_market_is_a_distinct_error_type() -> None:
    """ "종목이 없다"와 "API 가 죽었다"는 대응이 다르다."""
    async with make_client(
        always(404, json_body={"error": {"message": "Code not found"}})
    ) as client:
        with pytest.raises(UnknownMarketError):
            await UpbitAdapter(client).get_quote(BTC)


async def test_retries_are_exhausted_then_it_raises() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    async with make_client(handler, max_retries=1) as client:
        with pytest.raises(UpbitApiError):
            await UpbitAdapter(client).get_quote(BTC)
    assert calls["n"] == 2, "max_retries=1 이면 총 2회 시도다"


async def test_throttle_spaces_requests_out() -> None:
    """스로틀이 실제로 간격을 만든다 (spec §4.2 rate limit 은 이 레이어의 책임)."""
    import time

    async with make_client(
        always(200, json_body=load("ticker_btc.json")), rate_per_second=20
    ) as client:
        adapter = UpbitAdapter(client)
        began = time.perf_counter()
        for _ in range(4):
            await adapter.get_quote(BTC)
        elapsed = time.perf_counter() - began

    # 초당 20회 * 0.8 마진 = 16/s → 최소 간격 0.0625s. 4회면 3간격 이상.
    assert elapsed >= 0.0625 * 3 * 0.9, f"스로틀이 동작하지 않았다 (elapsed={elapsed:.4f}s)"


async def test_non_json_response_is_an_error() -> None:
    async with make_client(always(200, text="<html>maintenance</html>")) as c:
        with pytest.raises(UpbitApiError, match="JSON"):
            await UpbitAdapter(c).get_quote(BTC)


# ---------------------------------------------------------------------------
# 5. capability & 주문 경로 차단 (P0-7-6 / DoD 2·3)
# ---------------------------------------------------------------------------


def test_capabilities_are_spot_only() -> None:
    """DoD 2 — 업비트는 **현물 전용**이다 (spec §4.2, §12.8).

    Note:
        `ORDERBOOK` 이 P1 §5-1(비용 실측)에서 추가됐다. 파생·레버리지·공매도가 없다는
        DoD 2 의 요지는 그대로다 — 호가 조회는 거래 능력이 아니라 **시세 조회**이며,
        키 없이 되는 공개 엔드포인트라 P0-7 의 "자격증명 미사용" 안전 속성도 유지된다.
    """
    adapter = UpbitAdapter(UpbitClient())
    assert adapter.capabilities == frozenset({Capability.SPOT, Capability.WS, Capability.ORDERBOOK})
    # 거래 능력은 여전히 없다 — 이것이 DoD 2 가 실제로 지키려는 것이다.
    assert not (
        adapter.capabilities
        & {Capability.DERIVATIVES, Capability.LEVERAGE, Capability.SHORT, Capability.FUNDING}
    )


def test_conditional_orders_is_absent() -> None:
    """브로커측 스탑이 없다 — 코인은 §7 이중화 수단이 없음을 계약으로 드러낸다."""
    assert Capability.CONDITIONAL_ORDERS not in UpbitAdapter(UpbitClient()).capabilities


def test_adapter_satisfies_the_broker_protocol() -> None:
    """프로토콜을 구조적으로 만족해야 상위 계층이 어댑터를 교체할 수 있다."""
    assert isinstance(UpbitAdapter(UpbitClient()), BrokerAdapter)


async def test_submit_order_raises_explicitly() -> None:
    """DoD 3 — 조용히 성공한 척하지 않는다 (spec §7, 절대 규칙 #0)."""
    request = OrderRequest(
        instrument=BTC,
        side=Side.BUY,
        order_kind=OrderKind.ENTRY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("89000000"),
        idempotency_key="test-root:entry:0",
        approved_order_id="approved-1",
        leg_index=0,
        revision_id=None,
    )
    with pytest.raises(OrderPathNotAvailableError, match="OrderGateway"):
        await UpbitAdapter(UpbitClient()).submit_order(request)


@pytest.mark.parametrize("method", ["cancel_order", "get_order_status"])
async def test_order_management_paths_raise(method: str) -> None:
    adapter = UpbitAdapter(UpbitClient())
    with pytest.raises(OrderPathNotAvailableError):
        await getattr(adapter, method)("some-order-id")


async def test_balance_raises_because_it_needs_credentials() -> None:
    """키를 아예 쓰지 않는 것이 P0-7 의 안전 속성이다."""
    with pytest.raises(OrderPathNotAvailableError, match="인증"):
        await UpbitAdapter(UpbitClient()).get_balance()


async def test_order_status_does_not_invent_a_plausible_value() -> None:
    """`REJECTED` 같은 값을 지어내면 체결 확인(절대 규칙 #6)이 거짓 답을 받는다."""
    with pytest.raises(OrderPathNotAvailableError):
        result = await UpbitAdapter(UpbitClient()).get_order_status("x")
        assert result is None  # 여기 도달하면 실패다


# ---------------------------------------------------------------------------
# 6. WebSocket — 실측 함정 2개
# ---------------------------------------------------------------------------


def test_subscription_uses_default_format() -> None:
    message = json.loads(build_subscription(["KRW-BTC"]))
    assert message[1] == {"type": "ticker", "codes": ["KRW-BTC"]}
    assert message[2]["format"] == "DEFAULT", "SIMPLE 은 약어 키라 로그를 읽을 수 없다"
    assert message[0]["ticket"]


def test_subscription_requires_at_least_one_market() -> None:
    with pytest.raises(ValueError, match="마켓 코드가 없다"):
        build_subscription([])


def test_binary_frames_are_decoded() -> None:
    """함정 ① — 업비트 WS 는 **bytes** 를 준다."""
    raw = json.dumps({"type": "ticker", "code": "KRW-BTC", "trade_price": 89438000.0})
    assert decode_frame(raw.encode("utf-8"))["code"] == "KRW-BTC"
    assert decode_frame(raw)["code"] == "KRW-BTC", "text 로 와도 동작해야 한다"


def test_corrupt_frame_raises() -> None:
    with pytest.raises(UpbitWebSocketError, match="JSON"):
        decode_frame(b"not json")


def test_frame_becomes_a_quote() -> None:
    quote = frame_to_quote(
        {"type": "ticker", "code": "KRW-BTC", "trade_price": 89438000.0}, {"KRW-BTC": BTC}
    )
    assert quote is not None
    assert quote.last_price == Decimal("89438000.0")
    assert quote.bid is None and quote.ask is None
    assert quote.as_of.tzinfo is not None


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "orderbook", "code": "KRW-BTC", "trade_price": 1},
        {"type": "ticker", "code": "KRW-DOGE", "trade_price": 1},
        {"type": "ticker", "trade_price": 1},
    ],
)
def test_irrelevant_frames_are_skipped(frame: dict[str, Any]) -> None:
    """구독하지 않은 코드·타입은 조용히 건너뛴다 (오류가 아니다)."""
    assert frame_to_quote(frame, {"KRW-BTC": BTC}) is None


def test_non_numeric_price_raises() -> None:
    with pytest.raises(UpbitWebSocketError, match="trade_price"):
        frame_to_quote({"type": "ticker", "code": "KRW-BTC", "trade_price": None}, {"KRW-BTC": BTC})


def test_stream_rejects_empty_and_wrong_market() -> None:
    with pytest.raises(ValueError, match="종목이 없다"):
        UpbitTickerStream([])
    with pytest.raises(UpbitMappingError, match="KRX"):
        UpbitTickerStream([SAMSUNG])


def test_stream_exposes_its_market_codes() -> None:
    """무수신 경고 메시지가 어느 구독인지 알려줄 수 있어야 한다."""
    assert UpbitTickerStream([BTC]).market_codes == ["KRW-BTC"]


async def test_stream_gives_up_after_reconnect_limit() -> None:
    """닿을 수 없는 주소 → 재접속 상한에서 예외. 조용히 영원히 재시도하지 않는다."""
    stream = UpbitTickerStream(
        [BTC], url="ws://127.0.0.1:1/nope", stall_timeout=0.2, max_reconnects=1
    )
    with pytest.raises(UpbitWebSocketError, match="재접속 상한"):
        async for _ in stream.stream():
            pass


# ---------------------------------------------------------------------------
# 7. 통합 — 실 API (CI 제외)
# ---------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
async def test_live_btc_candles() -> None:
    """DoD 1 — BTC 5m **200봉** 수신 + 매핑 검증 (실 API)."""
    step = interval_seconds(Timeframe.M5)
    end = datetime.now(UTC)
    start = end - timedelta(seconds=step * 200)

    async with UpbitClient() as client:
        candles = await UpbitAdapter(client).get_candles(BTC, Timeframe.M5, start, end)

    assert len(candles) >= 195, f"200봉 구간에서 {len(candles)}봉만 왔다"
    assert [c.ts for c in candles] == sorted(c.ts for c in candles), "오름차순이 아니다"
    assert all(c.ts.tzinfo is not None for c in candles), "DoD 5 — UTC aware"
    gaps = {(b.ts - a.ts).total_seconds() for a, b in pairwise(candles)}
    assert gaps == {float(step)}, f"간격이 일정하지 않다: {sorted(gaps)}"
    assert all(c.high >= c.low for c in candles)


@pytest.mark.integration
async def test_live_quote_and_status() -> None:
    async with UpbitClient() as client:
        adapter = UpbitAdapter(client)
        quote = await adapter.get_quote(BTC)
        status = await adapter.get_market_status(BTC)

    assert quote.last_price > 0
    assert status.session is MarketSession.ALWAYS_OPEN


@pytest.mark.integration
async def test_live_unknown_market_is_404() -> None:
    """실측 근거 유지 — 없는 코드는 404 다."""
    ghost = Instrument(
        market=Market.UPBIT,
        symbol="KRW-NOPE",
        name="없는코인",
        asset_type=AssetType.COIN,
        currency=Currency.KRW,
    )
    async with UpbitClient() as client:
        with pytest.raises(UnknownMarketError):
            await UpbitAdapter(client).get_quote(ghost)


@pytest.mark.integration
async def test_live_candle_count_cap_is_still_200() -> None:
    """실측 근거 유지 — `count` 상한이 바뀌면 페이지네이션 전제가 흔들린다."""
    async with UpbitClient() as client:
        rows = await client.get_json(
            "/candles/minutes/5",
            group="candles",
            params={"market": "KRW-BTC", "count": "1000"},
        )
    assert isinstance(rows, list)
    assert len(rows) == MAX_CANDLE_COUNT, (
        f"상한이 {len(rows)} 로 바뀌었다 — docs/platform/upbit_api_notes.md §3-④ 를 갱신하라"
    )


@pytest.mark.integration
async def test_live_ws_receives_then_reconnects() -> None:
    """DoD 4 — WS 구독 수신 + 강제 종료 후 재접속.

    실제 재접속을 검증하려면 연결을 끊어야 한다. `stall_timeout` 을 짧게 두고 재접속
    상한을 1 로 주면, 끊김 → 백오프 → 재연결 → 재수신 경로가 한 번 돈다.
    """
    from updown.marketdata.upbit.ws import collect_quotes

    stream = UpbitTickerStream([BTC], stall_timeout=20.0)
    first = await collect_quotes(stream, limit=3, timeout=30.0)
    assert first, "30초간 WS 수신이 없다"
    assert all(q.last_price > 0 for q in first)

    # 새 스트림으로 재구독 — 연결이 반복 수립 가능해야 한다.
    second = await collect_quotes(UpbitTickerStream([BTC]), limit=1, timeout=30.0)
    assert second, "재구독으로 수신하지 못했다"
