"""호가 검사의 표시가는 **지금 값**이어야 한다 (T970 · 2026-11-22).

Note:
    계약 명세는 한 시간 기억한다(`speccache` · 호가 단위 · 승수 때문에 옳다). 그런데 그 안의
    `mark_price` 도 같이 한 시간 묵어, 호가 검사가 지금 호가를 한 시간 전 값과 견줬다.
    실계좌 2026-11-21:

    ```
    TAO    표시가 184.6 (한 시간 전)   시장 174.4   → "매수호가 5.2% 떨어졌다" 로 진입 막힘
    RIVER  표시가 0.646               시장 0.618   → 표시가 ±5% 안 매수 깊이 109 < 756
    RIVER  표시가 0.598               시장 0.568   → 153 < 765
    ZEC    표시가 689.62              시장 733.46  → "매도호가 6.4% 떨어졌다"(매수 깊이는 34,122)
    ```

    값이 빨리 움직일 때만 생겨 급락 되돌림 다리가 정통으로 맞았다(놓친 진입 3 건).
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

import httpx
import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.execution import gate_paper
from updown.marketdata.gate.trade_client import GateApiError, GateTradeClient
from updown.orchestration import liquidity
from updown.orchestration.liquidity import live_spec, probe_book

TAO = Instrument(
    market=Market.GATE,
    symbol="TAO_USDT",
    name="TAO",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
STALE = {"mark_price": "184.6", "quanto_multiplier": "0.01", "order_price_round": "0.1"}
"""한 시간 전에 기억한 명세 — 그때 시장가가 184.6 이었다."""

BOOK_NOW = {
    "bids": [{"p": "174.3", "s": "500000"}, {"p": "174.2", "s": "500000"}],
    "asks": [{"p": "174.5", "s": "500000"}, {"p": "174.6", "s": "500000"}],
}
"""지금 호가 — 시장은 174.4 근처이고 양쪽이 두껍다(멀쩡하다)."""


class _Orders:
    """명세(묵은 값) · 호가 · (있으면) 지금 표시가만 답하는 가짜 주문 어댑터."""

    def __init__(self, mark: Decimal | Exception | None) -> None:
        self._mark = mark
        self.marks_asked = 0

    async def contract_spec(self, _instrument: Any) -> dict[str, Any]:
        return dict(STALE)

    async def book_here(self, _instrument: Any, _limit: int = 20) -> dict[str, Any]:
        return dict(BOOK_NOW)


class _FreshOrders(_Orders):
    async def mark_now(self, _instrument: Any) -> Decimal:
        self.marks_asked += 1
        if isinstance(self._mark, Exception):
            raise self._mark
        assert self._mark is not None
        return self._mark


class TestTheProbeUsesTheMarkNow:
    async def test_stale_mark_alone_blocks_a_healthy_book(self) -> None:
        """사고 재현 — 지금 표시가를 못 주는 어댑터는 옛 동작(묵은 값)이다."""
        got = await probe_book(_Orders(None), TAO, Decimal(2000))
        assert not got.ok
        assert "팔 곳이 없다" in got.why

    async def test_the_mark_now_lets_a_healthy_book_pass(self) -> None:
        """🔴 고친 뒤 — 같은 호가 · 같은 명세라도 지금 표시가로 견주면 멀쩡하다."""
        orders = _FreshOrders(Decimal("174.4"))
        got = await probe_book(orders, TAO, Decimal(2000))
        assert got.ok, got.why
        assert got.mark == Decimal("174.4")
        assert orders.marks_asked == 1

    async def test_a_really_dry_book_is_still_blocked(self) -> None:
        """⛔ 고친다고 검사가 무뎌지면 안 된다 — 지금 표시가에서 정말 먼 호가는 그대로 막는다."""
        orders = _FreshOrders(Decimal("195.0"))
        got = await probe_book(orders, TAO, Decimal(2000))
        assert not got.ok

    async def test_an_unreadable_mark_does_not_block(self) -> None:
        """⛔ 지금 표시가를 못 읽으면 막지 않는다(묵은 값으로 되돌아가 막지도 않는다 · §1.2.1)."""
        got = await probe_book(_FreshOrders(RuntimeError("티커 응답 없음")), TAO, Decimal(2000))
        assert got.ok
        assert not got.read

    async def test_the_spec_keeps_its_other_fields(self) -> None:
        """호가 단위 · 승수는 명세 그대로다 — 바뀌는 것은 표시가 하나."""
        spec = await live_spec(_FreshOrders(Decimal("174.4")), TAO)
        assert spec["mark_price"] == "174.4"
        assert spec["quanto_multiplier"] == STALE["quanto_multiplier"]
        assert spec["order_price_round"] == STALE["order_price_round"]

    def test_every_mark_reader_goes_through_live_spec(self) -> None:
        """탈출가 화면 · 거는 쪽 · 1 계약 문도 같은 길 — 한쪽만 고치면 화면과 서버가 갈린다."""
        from updown.apps.api import exchange

        readers = (
            exchange.escape_view,
            exchange.escape_place,
            exchange.sizing_of,
            liquidity.probe_book,
        )
        for fn in readers:
            assert "live_spec(" in inspect.getsource(fn), fn.__name__


class TestTheGateAdapterAsksFresh:
    async def test_mark_now_reads_the_ticker_not_the_spec_box(self) -> None:
        class _Trade:
            def __init__(self) -> None:
                self.asked: list[str] = []

            async def ticker(self, contract: str) -> dict[str, Any]:
                self.asked.append(contract)
                return {"contract": contract, "mark_price": "174.41", "last": "174.4"}

            async def contract(self, _contract: str) -> dict[str, Any]:
                raise AssertionError("지금 표시가를 한 시간 기억통에서 읽는다")

        adapter = gate_paper.GatePaperAdapter.__new__(gate_paper.GatePaperAdapter)
        trade = _Trade()
        adapter._trade = trade  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        assert await adapter.mark_now(TAO) == Decimal("174.41")
        assert trade.asked == ["TAO_USDT"]

    async def test_a_zero_mark_is_an_error(self) -> None:
        class _Trade:
            async def ticker(self, contract: str) -> dict[str, Any]:
                return {"contract": contract, "mark_price": "0", "last": ""}

        adapter = gate_paper.GatePaperAdapter.__new__(gate_paper.GatePaperAdapter)
        adapter._trade = _Trade()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        with pytest.raises(GateApiError):
            await adapter.mark_now(TAO)


def _client(rows: list[Any]) -> tuple[GateTradeClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=rows)

    return GateTradeClient("k", "s", transport=httpx.MockTransport(handle)), seen


class TestTheTickerCall:
    async def test_it_is_not_remembered(self) -> None:
        """두 번 물으면 두 번 나간다 — 표시가는 기억통을 안 거친다."""
        client, seen = _client([{"contract": "TAO_USDT", "mark_price": "174.4"}])
        try:
            assert (await client.ticker("TAO_USDT"))["mark_price"] == "174.4"
            await client.ticker("TAO_USDT")
        finally:
            await client.aclose()
        assert len(seen) == 2
        assert seen[0].url.path.endswith("/futures/usdt/tickers")
        assert dict(seen[0].url.params) == {"contract": "TAO_USDT"}

    @pytest.mark.parametrize(
        "rows",
        [[], [{"contract": "BTC_USDT", "mark_price": "60000"}], ["x"]],
    )
    async def test_a_wrong_answer_is_refused(self, rows: list[Any]) -> None:
        """⛔ 빈 응답 · 남의 계약 값으로 판단하지 않는다."""
        client, _ = _client(rows)
        try:
            with pytest.raises(GateApiError):
                await client.ticker("TAO_USDT")
        finally:
            await client.aclose()
