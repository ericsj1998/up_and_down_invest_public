"""T240 — 주식 페이퍼 어댑터: 정수 주 · 숏 없음 · 배율 없음 · 지정가 대기 · 갭 손절 · 상태 파일."""
# ruff: noqa: ARG002 — 가짜 어댑터는 프로토콜 서명을 그대로 지킨다

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Side,
    Timeframe,
)
from updown.common.domain.market import (
    MarketSession,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    Quote,
)
from updown.common.domain.order import OrderKind, OrderRequest, OrderStatus, OrderType
from updown.execution.stock_paper import (
    BROKER_NAME,
    FileStateStore,
    StockPaperAdapter,
    StockPaperRejectedError,
)

AAPL = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)
SEED = {Market.NASDAQ: Decimal(10_000)}


class FakeQuotes:
    """시세만 흉내낸다 — `last` 를 시험이 바꾼다."""

    def __init__(self, last: str) -> None:
        self.last = Decimal(last)
        self.open_market = True

    async def get_quote(self, instrument: Instrument) -> Quote:
        return Quote(
            instrument=instrument,
            last_price=self.last,
            bid=None,
            ask=None,
            as_of=datetime.now(UTC),
        )

    async def get_orderbook(self, instrument: Instrument) -> OrderBook:
        level = OrderBookLevel(
            bid_price=self.last - Decimal("0.01"),
            bid_size=Decimal(500),
            ask_price=self.last + Decimal("0.01"),
            ask_size=Decimal(500),
        )
        return OrderBook(instrument=instrument, levels=(level,), as_of=datetime.now(UTC))

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        return []

    async def get_market_status(self, instrument: Instrument) -> MarketStatus:
        return MarketStatus(
            instrument=instrument,
            session=MarketSession.REGULAR if self.open_market else MarketSession.CLOSED,
            is_order_allowed=self.open_market,
            as_of=datetime.now(UTC),
            next_open=None,
            next_close=None,
        )

    async def contract_spec(self, instrument: Instrument) -> dict[str, Any]:
        return {"quanto_multiplier": "1", "order_size_min": 1, "order_price_round": "0.01"}


def _order(
    side: Side,
    qty: str,
    *,
    price: str | None = None,
    kind: OrderKind = OrderKind.ENTRY,
    key: str = "k1",
) -> OrderRequest:
    return OrderRequest(
        instrument=AAPL,
        side=side,
        order_kind=kind,
        order_type=OrderType.MARKET if price is None else OrderType.LIMIT,
        quantity=Decimal(qty),
        price=None if price is None else Decimal(price),
        idempotency_key=key,
        approved_order_id="a",
        leg_index=0,
        revision_id=None,
    )


def _adapter(tmp_path: Path, quotes: FakeQuotes) -> StockPaperAdapter:
    return StockPaperAdapter(quotes, store=FileStateStore(tmp_path), seed_cash=SEED)


class TestCapabilitiesAreEnforced:
    @pytest.mark.asyncio
    async def test_fractional_shares_are_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(StockPaperRejectedError, match="정수"):
            await _adapter(tmp_path, FakeQuotes("100")).submit_order(_order(Side.BUY, "1.5"))

    @pytest.mark.asyncio
    async def test_selling_more_than_held_is_a_short_and_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(StockPaperRejectedError, match="숏"):
            await _adapter(tmp_path, FakeQuotes("100")).submit_order(_order(Side.SELL, "1"))

    @pytest.mark.asyncio
    async def test_leverage_other_than_one_is_rejected(self, tmp_path: Path) -> None:
        adapter = _adapter(tmp_path, FakeQuotes("100"))
        await adapter.set_leverage(AAPL, Decimal(1))
        with pytest.raises(StockPaperRejectedError, match="배율"):
            await adapter.set_leverage(AAPL, Decimal(2))

    @pytest.mark.asyncio
    async def test_closed_market_takes_no_orders(self, tmp_path: Path) -> None:
        quotes = FakeQuotes("100")
        quotes.open_market = False
        with pytest.raises(StockPaperRejectedError, match="닫혀"):
            await _adapter(tmp_path, quotes).submit_order(_order(Side.BUY, "1"))

    @pytest.mark.asyncio
    async def test_cash_limit_blocks_a_buy_it_cannot_afford(self, tmp_path: Path) -> None:
        with pytest.raises(StockPaperRejectedError, match="현금"):
            await _adapter(tmp_path, FakeQuotes("100")).submit_order(
                _order(Side.BUY, "200", price="100")
            )


class TestFills:
    @pytest.mark.asyncio
    async def test_market_buy_fills_with_slippage_and_fee(self, tmp_path: Path) -> None:
        adapter = _adapter(tmp_path, FakeQuotes("100"))
        result = await adapter.submit_order(_order(Side.BUY, "10"))
        assert result.status is OrderStatus.FILLED and result.filled_quantity == 10
        assert result.average_price is not None and result.average_price >= Decimal(100)
        snap = await adapter.position_snapshot(AAPL)
        assert snap["size"] == "10" and snap["leverage"] == "1"
        balance = await adapter.get_balance()
        assert balance.broker == BROKER_NAME and balance.currency is Currency.USD
        # 현금 = 시드 - 매입금 - 수수료 → 시드보다 작고 매입금은 묶인 돈에 있다
        assert balance.cash < Decimal(10_000) - Decimal(1_000)
        assert balance.positions_value == Decimal(snap["margin"])

    @pytest.mark.asyncio
    async def test_limit_buy_waits_then_fills_when_price_reaches(self, tmp_path: Path) -> None:
        quotes = FakeQuotes("100")
        adapter = _adapter(tmp_path, quotes)
        result = await adapter.submit_order(_order(Side.BUY, "5", price="95"))
        assert result.status is OrderStatus.SUBMITTED
        assert [row["price"] for row in await adapter.open_orders(AAPL)] == ["95"]
        assert (await adapter.margins())["order_margin"] == "475"
        quotes.last = Decimal("94.5")
        adapter._quote_cache.clear()  # pyright: ignore[reportPrivateUsage]
        assert await adapter.open_orders(AAPL) == []
        recent = await adapter.recent_orders(AAPL)
        assert recent[0]["finish_as"] == "filled" and recent[0]["fill_price"] == "95"
        assert (await adapter.position_snapshot(AAPL))["entry_price"] == "95"

    @pytest.mark.asyncio
    async def test_close_writes_a_position_close_row_with_fee_split(self, tmp_path: Path) -> None:
        quotes = FakeQuotes("100")
        adapter = _adapter(tmp_path, quotes)
        await adapter.submit_order(_order(Side.BUY, "10", price="100"))
        quotes.last = Decimal(110)
        adapter._quote_cache.clear()  # pyright: ignore[reportPrivateUsage]
        await adapter.submit_order(
            _order(Side.SELL, "10", price="110", kind=OrderKind.TAKE_PROFIT, key="tp")
        )
        assert await adapter.position_snapshot(AAPL) == {}
        (row,) = await adapter.position_closes(AAPL)
        assert row["pnl_pnl"] == "100" and Decimal(row["pnl_fee"]) < 0 and row["pnl_fund"] == "0"
        assert Decimal(row["pnl"]) == Decimal(row["pnl_pnl"]) + Decimal(row["pnl_fee"])
        assert row["text"] == "tp" and row["side"] == "long" and row["max_size"] == "10"


class TestStops:
    @pytest.mark.asyncio
    async def test_stop_triggers_at_the_gap_price_not_the_trigger(self, tmp_path: Path) -> None:
        quotes = FakeQuotes("100")
        adapter = _adapter(tmp_path, quotes)
        await adapter.submit_order(_order(Side.BUY, "10", price="100"))
        made = await adapter.stops_for(AAPL, Decimal(95), long=True)
        assert made is not None
        assert await adapter.stops_for(AAPL, Decimal(95), long=True) is None, (
            "같은 값이면 안 다시 건다"
        )
        assert [row["trigger_price"] for row in await adapter.open_stops(AAPL)] == ["95"]
        quotes.last = Decimal(90)  # 야간 갭 — 손절선 95 를 건너뛰고 90 에서 열렸다
        adapter._quote_cache.clear()  # pyright: ignore[reportPrivateUsage]
        assert await adapter.open_stops(AAPL) == []
        assert await adapter.position_snapshot(AAPL) == {}
        (row,) = await adapter.position_closes(AAPL)
        assert Decimal(row["short_price"]) <= Decimal(90), "트리거(95)가 아니라 시가(90) 이하"

    @pytest.mark.asyncio
    async def test_restop_replaces_the_old_trigger(self, tmp_path: Path) -> None:
        adapter = _adapter(tmp_path, FakeQuotes("100"))
        await adapter.submit_order(_order(Side.BUY, "10", price="100"))
        await adapter.stops_for(AAPL, Decimal(95), long=True)
        await adapter.stops_for(AAPL, Decimal(97), long=True)
        assert [row["trigger_price"] for row in await adapter.open_stops(AAPL)] == ["97"]


class TestStateSurvivesRestart:
    @pytest.mark.asyncio
    async def test_positions_and_stops_are_reloaded(self, tmp_path: Path) -> None:
        quotes = FakeQuotes("100")
        first = _adapter(tmp_path, quotes)
        await first.submit_order(_order(Side.BUY, "3", price="100"))
        await first.stops_for(AAPL, Decimal(95), long=True)
        again = _adapter(tmp_path, quotes)
        assert (await again.position_snapshot(AAPL))["size"] == "3"
        assert [row["trigger_price"] for row in await again.open_stops(AAPL)] == ["95"]
        assert (await again.get_balance()).cash == (await first.get_balance()).cash
        assert (tmp_path / "NASDAQ.json").exists()
