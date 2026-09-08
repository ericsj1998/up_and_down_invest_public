# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
# 공유 클라이언트의 내부(_request · _client · _shared_gate)를 직접 두드려야 시험되는 성질이다.
"""공유 Gate 클라이언트는 누가 닫아도 다음 요청에서 살아난다 (2026-09-05 실계좌 첫 펀드 사고)."""

from __future__ import annotations

import asyncio

import httpx

from updown.common.domain.instrument import Market
from updown.marketdata.gate.client import GateClient
from updown.marketdata.gate.trade_client import GateTradeClient
from updown.marketdata.provider import MarketDataProvider


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True, "path": req.url.path})
    )


def test_public_client_reopens_after_aclose() -> None:
    async def run() -> None:
        c = GateClient(transport=_ok_transport())
        await c.aclose()
        body = await c.get_json("/futures/usdt/contracts/BTC_USDT")
        assert isinstance(body, dict) and body.get("ok")

    asyncio.run(run())


def test_trade_client_reopens_after_aclose() -> None:
    async def run() -> None:
        c = GateTradeClient("k", "s", transport=_ok_transport())
        await c.aclose()
        body = await c._request("GET", "/futures/usdt/accounts")
        assert isinstance(body, dict) and body.get("ok")

    asyncio.run(run())


def test_provider_aclose_leaves_shared_gate_alive() -> None:
    async def run() -> None:
        async with MarketDataProvider() as p:
            p.adapter_for(Market.GATE)
        shared = MarketDataProvider._shared_gate
        assert shared is not None
        assert not shared._client.is_closed, "provider 종료가 공유 클라이언트를 닫으면 안 된다"

    asyncio.run(run())
