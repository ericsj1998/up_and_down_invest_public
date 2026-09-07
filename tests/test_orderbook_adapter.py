"""호가창 조회 경로 검증 (P1 §5-1 · spec §4.2 확장, §12.7).

`test_upbit_adapter.py` 와 같은 두 층 구조다 — `httpx.MockTransport` 단위 테스트 +
`@pytest.mark.integration` 실 API 테스트(CI 제외).

여기서 지키는 계약 셋:

1. **`Capability.ORDERBOOK` 선언과 실제 능력이 일치**한다. 선언만 하고 못 하거나,
   할 수 있는데 선언하지 않으면 상위 계층의 능력 분기가 거짓말이 된다.
2. **응답 순서를 재정렬하지 않는다.** 업비트가 최우선 호가부터 주므로 그대로 담아야
   `walk` 가 좋은 가격부터 소진한다.
3. **조회 어댑터를 `MarketDataProvider` 로 얻는다** (절대 규칙 #0).
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.marketdata.adapter import BrokerAdapter, Capability, OrderBookAdapter
from updown.marketdata.provider import MarketDataProvider
from updown.marketdata.upbit.adapter import UpbitAdapter
from updown.marketdata.upbit.client import UpbitApiError, UpbitClient
from updown.marketdata.upbit.mapping import UpbitMappingError, to_orderbook

FIXTURES = Path("tests/fixtures/upbit")

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

Handler = Callable[[httpx.Request], httpx.Response]


def fixture() -> Any:
    """실측 `/orderbook` 응답."""
    return json.loads((FIXTURES / "orderbook_btc.json").read_text(encoding="utf-8"))


def make_adapter(handler: Handler) -> UpbitAdapter:
    """MockTransport 를 물린 어댑터."""
    return UpbitAdapter(
        UpbitClient(transport=httpx.MockTransport(handler), rate_per_second=100_000)
    )


def always(status: int, *, json_body: Any = None) -> Handler:
    """항상 같은 응답을 주는 핸들러."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body)

    return handler


# ---------------------------------------------------------------------------
# 1. 능력 선언
# ---------------------------------------------------------------------------


def test_upbit_declares_the_orderbook_capability() -> None:
    adapter = make_adapter(always(200, json_body=fixture()))
    assert Capability.ORDERBOOK in adapter.capabilities


def test_upbit_adapter_satisfies_the_orderbook_protocol() -> None:
    """선언과 실제 구현이 일치하는가 — 이 대입이 pyright 검사 지점이다."""
    adapter: OrderBookAdapter = make_adapter(always(200, json_body=fixture()))
    assert isinstance(adapter, OrderBookAdapter)


def test_orderbook_adapter_is_not_required_of_every_broker() -> None:
    """`BrokerAdapter` 의 7종 계약(spec §4.2)에 호가가 들어가지 않았는지 확인한다.

    필수로 올리면 호가 없는 브로커가 전부 "예외를 던지는 구현"을 갖게 되고,
    그 순간 `capabilities` 선언이 무의미해진다.
    """
    assert "get_orderbook" not in set(BrokerAdapter.__protocol_attrs__)  # type: ignore[attr-defined]
    assert "get_orderbook" in set(OrderBookAdapter.__protocol_attrs__)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 2. 매핑
# ---------------------------------------------------------------------------


async def test_orderbook_has_thirty_levels() -> None:
    """실측 근거 유지 — 업비트는 30단계를 준다."""
    adapter = make_adapter(always(200, json_body=fixture()))
    book = await adapter.get_orderbook(BTC)
    assert len(book.levels) == 30


async def test_levels_keep_the_response_order() -> None:
    """재정렬하지 않는다 — `levels[0]` 이 최우선 호가여야 `walk` 가 옳다."""
    payload = fixture()
    adapter = make_adapter(always(200, json_body=payload))
    book = await adapter.get_orderbook(BTC)
    units = payload[0]["orderbook_units"]
    for level, unit in zip(book.levels, units, strict=True):
        assert level.bid_price == Decimal(str(unit["bid_price"]))
        assert level.ask_price == Decimal(str(unit["ask_price"]))


async def test_best_bid_is_below_best_ask_in_real_data() -> None:
    adapter = make_adapter(always(200, json_body=fixture()))
    book = await adapter.get_orderbook(BTC)
    assert book.best_bid < book.best_ask


async def test_as_of_is_our_clock_not_the_brokers() -> None:
    """spec §4.18 — 브로커 시계(`timestamp`)를 쓰면 시계 오차가 비용 측정에 섞인다."""
    payload = fixture()
    payload[0]["timestamp"] = 0  # 1970년. 이 값을 쓰면 티가 난다.
    adapter = make_adapter(always(200, json_body=payload))
    before = datetime.now(UTC)
    book = await adapter.get_orderbook(BTC)
    assert before <= book.as_of <= datetime.now(UTC)


def test_prices_avoid_binary_float_error() -> None:
    """JSON number 는 float 이 된다 — `str()` 을 거치지 않으면 이진 오차가 남는다."""
    payload = {
        "orderbook_units": [{"bid_price": 0.1, "bid_size": 0.1, "ask_price": 0.3, "ask_size": 0.1}]
    }
    book = to_orderbook(payload, BTC, datetime.now(UTC))
    assert book.best_bid == Decimal("0.1")


def test_missing_units_raises_instead_of_defaulting() -> None:
    with pytest.raises(UpbitMappingError, match="orderbook_units"):
        to_orderbook({"market": "KRW-BTC"}, BTC, datetime.now(UTC))


def test_empty_units_raises() -> None:
    """호가가 없다는 것 자체가 사건이다 (spec §7)."""
    with pytest.raises(UpbitMappingError, match="비어 있다"):
        to_orderbook({"orderbook_units": []}, BTC, datetime.now(UTC))


def test_missing_price_field_names_the_regression() -> None:
    payload = {"orderbook_units": [{"bid_price": 1, "bid_size": 1, "ask_size": 1}]}
    with pytest.raises(UpbitMappingError, match="ask_price"):
        to_orderbook(payload, BTC, datetime.now(UTC))


async def test_empty_response_array_is_an_error() -> None:
    adapter = make_adapter(always(200, json_body=[]))
    with pytest.raises(UpbitApiError, match="비어 있다"):
        await adapter.get_orderbook(BTC)


async def test_orderbook_uses_its_own_rate_limit_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """업비트는 그룹별로 한도를 관리한다 — `ticker` 를 쓰면 시세 조회가 호가에 밀린다.

    스로틀 그룹은 요청에 드러나지 않으므로 클라이언트 호출을 가로채 확인한다.
    """
    seen: list[tuple[str, str]] = []
    original = UpbitClient.get_json

    async def spy(
        self: UpbitClient, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> Any:
        seen.append((path, group))
        return await original(self, path, group=group, params=params)

    monkeypatch.setattr(UpbitClient, "get_json", spy)
    adapter = make_adapter(always(200, json_body=fixture()))
    await adapter.get_orderbook(BTC)
    assert seen == [("/orderbook", "orderbook")]


# ---------------------------------------------------------------------------
# 3. 획득 경로 (절대 규칙 #0)
# ---------------------------------------------------------------------------


def test_provider_returns_an_orderbook_capable_adapter() -> None:
    """조회 경로는 `MarketDataProvider` 하나다 — 어댑터를 직접 만들지 않는다."""
    provider = MarketDataProvider()
    adapter = provider.adapter_for(Market.UPBIT)
    assert Capability.ORDERBOOK in adapter.capabilities
    assert isinstance(adapter, OrderBookAdapter)


async def test_orderbook_path_still_cannot_place_orders() -> None:
    """호가를 열어도 주문은 여전히 막혀 있다 — 이 파일이 게이트를 약화시키지 않는다."""
    from updown.marketdata.upbit.adapter import OrderPathNotAvailableError

    adapter = make_adapter(always(200, json_body=fixture()))
    with pytest.raises(OrderPathNotAvailableError):
        await adapter.get_balance()


# ---------------------------------------------------------------------------
# 4. 실 API (CI 제외)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_live_orderbook_is_thirty_levels_without_credentials() -> None:
    """키 없이 30단계가 오는가 — P0-7 의 안전 속성(자격증명 미사용)이 유지되는 근거다."""
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(Market.UPBIT)
        assert isinstance(adapter, OrderBookAdapter)
        book = await adapter.get_orderbook(BTC)
    assert len(book.levels) == 30
    assert book.best_bid < book.best_ask
