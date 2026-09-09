"""주식 페이퍼 경로 프로브 (T240) — api 컨테이너 안에서 돈다. 시크릿은 찍지 않는다.

보는 것: ① 이 API 가 연결한 시장 목록에 주식 시장이 있나 ② 토스 조회 어댑터가 러너 계약을 지키나
③ 관문이 주식 페이퍼 어댑터를 주나 ④ 페이퍼 계좌(DB `stock_paper_accounts`)가 열리고 잔고가 읽히나
⑤ 장 상태(캘린더)와 명세(호가단위·시세)가 나오나.
"""

from __future__ import annotations

import asyncio
import os
import sys

from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.execution.gateway import order_adapter
from updown.execution.stock_paper import DbStateStore, attach_state_store
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.provider import MarketDataProvider

AAPL = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)


async def main() -> int:
    settings = load_settings()
    print("APP_ENV", settings.app_env.value, "STOCK_LIVE_ORDERS", settings.stock_live_orders)
    print("UPDOWN_MARKETS", os.environ.get("UPDOWN_MARKETS", "(없음 = 전부)"))
    provider = MarketDataProvider()
    markets = provider.live_markets()
    print("live_markets", markets)
    if Market.NASDAQ.value not in markets:
        print("NASDAQ 가 목록에 없다 — 토스 조회 자격증명 또는 UPDOWN_MARKETS 를 본다")
        return 1
    quotes = provider.adapter_for(Market.NASDAQ)
    print("quotes", type(quotes).__name__, "QuoteAdapter", isinstance(quotes, QuoteAdapter))
    engine = create_engine(settings.database_url)
    attach_state_store(DbStateStore(create_session_factory(engine)))
    try:
        orders = order_adapter(quotes, user_id="probe")
        print("orders", type(orders).__name__, "testnet", getattr(orders, "is_testnet", None))
        balance = await orders.get_balance()
        print(
            "balance",
            balance.broker,
            balance.currency.value,
            "cash",
            balance.cash,
            "locked",
            balance.positions_value,
        )
        status = await quotes.get_market_status(AAPL)
        print(
            "market_status",
            status.session.value,
            "orderable",
            status.is_order_allowed,
            "next_open",
            status.next_open,
            "next_close",
            status.next_close,
        )
        spec = await orders.contract_spec(AAPL)  # type: ignore[attr-defined]
        print(
            "spec", {k: spec[k] for k in ("quanto_multiplier", "order_price_round", "mark_price")}
        )
        snap = await orders.position_snapshot(AAPL)  # type: ignore[attr-defined]
        print("position", snap or "없음")
        stops = await orders.open_stops(AAPL)  # type: ignore[attr-defined]
        print("stops", len(stops))
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
