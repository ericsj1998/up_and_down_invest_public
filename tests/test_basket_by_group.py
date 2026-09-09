"""기본 바스켓은 시장 묶음별이다 — 주식 펀드가 코인 종목을 받지 않는다 (2026-09-10 실측 수정)."""

from __future__ import annotations

from updown.apps.api.rebalancer import (  # pyright: ignore[reportPrivateUsage]
    _default_basket,
    basket_block_of,
)


class TestBasketByGroup:
    def test_block_by_market_group(self) -> None:
        assert basket_block_of("GATE") == "default"
        assert basket_block_of("BINANCE") == "default"
        assert basket_block_of("NASDAQ") == "foreign_stock"
        assert basket_block_of("KRX") == "domestic_stock"
        assert basket_block_of("NOPE") == "default"

    def test_stock_fund_gets_stock_symbols_only(self) -> None:
        members, missing = _default_basket("NASDAQ")
        symbols = [m["symbol"] for m in members]
        assert symbols and all("_USDT" not in s for s in symbols) and missing == []
        coin, _ = _default_basket("BINANCE")
        assert all(m["symbol"].endswith("_USDT") for m in coin)
