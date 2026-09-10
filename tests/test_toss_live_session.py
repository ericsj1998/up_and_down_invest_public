"""T259 — 달력이 "열림" 이어도 종목이 거래정지·정리매매·VI 면 주문 불가.

토스 `/api/v1/stocks`(상장 상태 · 거래정지) + `/api/v1/stocks/{symbol}/warnings`(VI)를
`get_market_status` 가 장중에만 묻고 60초 기억한다. 못 물으면 달력 답 그대로(계측 실패가 조회를 막지
않는다).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.common.domain.market import MarketSession
from updown.common.domain.session import Tradability
from updown.marketdata.toss.adapter import TossAdapter
from updown.marketdata.toss.client import TossClient

SAMSUNG = Instrument(Market.KRX, "005930", "삼성전자", AssetType.STOCK, Currency.KRW)
APPLE = Instrument(Market.NASDAQ, "AAPL", "애플", AssetType.STOCK, Currency.USD)
TOKEN_BODY = {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}


class _OpenCalendar:
    """정규장 — 달력은 열렸다고 답한다."""

    def tradability(self, market: Market, moment: datetime) -> tuple[Tradability, str]:  # noqa: ARG002
        return Tradability.OPEN, "정규장"

    def session_at(self, market: Market, moment: datetime) -> MarketSession:  # noqa: ARG002
        return MarketSession.REGULAR

    def next_events(self, market: Market, moment: datetime) -> tuple[None, None]:  # noqa: ARG002
        return None, None


def _adapter(
    info: dict[str, Any] | None, warnings: list[dict[str, Any]], *, fail: bool = False
) -> tuple[TossAdapter, list[str]]:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        sent.append(path)
        if path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        if fail:
            return httpx.Response(500, json={"error": {"code": "boom", "message": "x"}})
        if path == "/api/v1/stocks":
            return httpx.Response(200, json={"result": [] if info is None else [info]})
        if path.endswith("/warnings"):
            return httpx.Response(200, json={"result": warnings})
        return httpx.Response(404, json={"error": {"code": "nf", "message": "?"}})

    client = TossClient(
        SecretStr("id"),
        SecretStr("secret"),
        transport=httpx.MockTransport(handler),
        rate_per_second=100_000,
        max_retries=0,
    )
    made = TossAdapter(client, calendar=_OpenCalendar())  # type: ignore[arg-type]
    return made, sent


KR_ACTIVE = {
    "symbol": "005930",
    "status": "ACTIVE",
    "koreanMarketDetail": {"krxTradingSuspended": False, "liquidationTrading": False},
}


class TestLiveSessionOf:
    def test_pure_rules(self) -> None:
        today = date(2026, 9, 10)
        assert TossAdapter.live_session_of(KR_ACTIVE, [], today) is None
        assert TossAdapter.live_session_of({**KR_ACTIVE, "status": "DELISTED"}, [], today) is (
            MarketSession.HALTED
        )
        suspended = {**KR_ACTIVE, "koreanMarketDetail": {"krxTradingSuspended": True}}
        assert TossAdapter.live_session_of(suspended, [], today) is MarketSession.HALTED
        vi = [{"warningType": "VI_STATIC", "startDate": "2026-09-10", "endDate": None}]
        assert TossAdapter.live_session_of(KR_ACTIVE, vi, today) is MarketSession.VI
        # 끝난 VI 는 제약이 아니다 · 정리매매 유의사항은 정지다.
        old = [{"warningType": "VI_DYNAMIC", "startDate": "2026-09-01", "endDate": "2026-09-02"}]
        assert TossAdapter.live_session_of(KR_ACTIVE, old, today) is None
        liq = [{"warningType": "LIQUIDATION_TRADING", "startDate": None, "endDate": None}]
        assert TossAdapter.live_session_of(KR_ACTIVE, liq, today) is MarketSession.HALTED
        # 미국 종목(koreanMarketDetail 없음)은 상장 상태만.
        assert (
            TossAdapter.live_session_of({"symbol": "AAPL", "status": "ACTIVE"}, [], today) is None
        )


class TestMarketStatus:
    @pytest.mark.asyncio
    async def test_open_calendar_but_vi_blocks_orders_and_is_cached(self) -> None:
        vi = [{"warningType": "VI_STATIC_AND_DYNAMIC", "startDate": None, "endDate": None}]
        made, sent = _adapter(KR_ACTIVE, vi)
        status = await made.get_market_status(SAMSUNG)
        assert status.session is MarketSession.VI and status.is_order_allowed is False
        again = await made.get_market_status(SAMSUNG)
        assert again.session is MarketSession.VI
        # 60초 안에는 다시 묻지 않는다 — 토큰 1 + 정보 1 + 유의사항 1.
        assert sent.count("/api/v1/stocks") == 1 and sum(p.endswith("/warnings") for p in sent) == 1

    @pytest.mark.asyncio
    async def test_us_symbol_asks_info_only(self) -> None:
        made, sent = _adapter({"symbol": "AAPL", "status": "ACTIVE"}, [])
        status = await made.get_market_status(APPLE)
        assert status.session is MarketSession.REGULAR and status.is_order_allowed is True
        assert not any(p.endswith("/warnings") for p in sent)

    @pytest.mark.asyncio
    async def test_broker_failure_keeps_the_calendar_answer(self) -> None:
        made, _sent = _adapter(KR_ACTIVE, [], fail=True)
        status = await made.get_market_status(SAMSUNG)
        assert status.session is MarketSession.REGULAR and status.is_order_allowed is True
        assert status.as_of.tzinfo is UTC or status.as_of.utcoffset() is not None

    @pytest.mark.asyncio
    async def test_stock_info_and_prices_batch(self) -> None:
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/token":
                return httpx.Response(200, json=TOKEN_BODY)
            asked.append(request.url.params.get("symbols", ""))
            if request.url.path == "/api/v1/stocks":
                rows = [{"symbol": s, "status": "ACTIVE"} for s in asked[-1].split(",")]
                return httpx.Response(200, json={"result": rows})
            rows = [{"symbol": s, "lastPrice": "10.5"} for s in asked[-1].split(",")]
            return httpx.Response(200, json={"result": rows})

        client = TossClient(
            SecretStr("id"),
            SecretStr("secret"),
            transport=httpx.MockTransport(handler),
            rate_per_second=100_000,
            max_retries=0,
        )
        made = TossAdapter(client, calendar=_OpenCalendar())  # type: ignore[arg-type]
        symbols = [f"S{i}" for i in range(250)]
        info = await made.stock_info(symbols)
        prices = await made.last_prices(symbols)
        assert len(info) == 250 and len(prices) == 250 and str(prices["S7"]) == "10.5"
        # 200개씩 두 번 x 두 엔드포인트.
        assert len(asked) == 4 and len(asked[0].split(",")) == 200
