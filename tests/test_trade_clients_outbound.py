"""T264 3차 — 주문 클라이언트가 층 위로 간 뒤에도 **서명 바이트가 같고, 재시도가 없다**.

실주문 없이 시험한다: 전송 계층을 가로채 실제로 나간 요청(헤더 · 본문 · URL)을 붙잡고, 같은 재료로
서명을 다시 계산해 일치를 본다. 500 은 한 번만 나가고(재시도 없음), 전송 오류는 도메인 예외다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import pytest

from updown.marketdata.binance.trade_client import BinanceTradeClient, BinanceTradeError
from updown.marketdata.gate.signing import auth_headers
from updown.marketdata.gate.trade_client import GateApiError, GateTradeClient


def _capture(script: list[Any]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return httpx.MockTransport(handle), seen


class TestGateTrade:
    @pytest.mark.asyncio
    async def test_signature_covers_the_exact_bytes_sent(self) -> None:
        transport, seen = _capture([httpx.Response(201, json={"id": 1})])
        client = GateTradeClient("k", "s", transport=transport)
        body = {"contract": "BTC_USDT", "size": 1, "price": "60000", "tif": "gtc"}
        try:
            await client._request("POST", "/futures/usdt/orders", body=body)  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        req = seen[0]
        # 본문은 우리가 만든 compact JSON 그대로 — httpx 가 다시 직렬화하지 않았다
        assert req.content == json.dumps(body, separators=(",", ":")).encode()
        expected = auth_headers(
            "k",
            "s",
            "POST",
            "/futures/usdt/orders",
            params=None,
            body=req.content.decode(),
            timestamp=int(req.headers["Timestamp"]),
        )
        assert req.headers["SIGN"] == expected["SIGN"]
        assert req.headers["KEY"] == "k"
        assert req.url.path.endswith("/futures/usdt/orders")

    @pytest.mark.asyncio
    async def test_query_order_is_signed_and_sent_as_given(self) -> None:
        transport, seen = _capture([httpx.Response(200, json=[])])
        client = GateTradeClient("k", "s", transport=transport)
        params = {"contract": "BTC_USDT", "status": "open", "limit": "5"}
        try:
            await client._request("GET", "/futures/usdt/orders", params=params)  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        req = seen[0]
        assert dict(parse_qsl(req.url.query.decode())) == params
        expected = auth_headers(
            "k",
            "s",
            "GET",
            "/futures/usdt/orders",
            params=params,
            body="",
            timestamp=int(req.headers["Timestamp"]),
        )
        assert req.headers["SIGN"] == expected["SIGN"]

    @pytest.mark.asyncio
    async def test_server_error_is_not_retried(self) -> None:
        transport, seen = _capture([httpx.Response(502, text="bad gateway"), httpx.Response(200)])
        client = GateTradeClient("k", "s", transport=transport)
        try:
            with pytest.raises(GateApiError) as caught:
                await client._request("POST", "/futures/usdt/orders", body={"size": 1})  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        assert len(seen) == 1, "주문은 층이 재시도하지 않는다 (규칙 #6)"
        assert caught.value.status_code == 502

    @pytest.mark.asyncio
    async def test_transport_error_is_a_domain_error_once(self) -> None:
        transport, seen = _capture([httpx.ConnectError("down"), httpx.Response(200)])
        client = GateTradeClient("k", "s", transport=transport)
        try:
            with pytest.raises(GateApiError, match="전송 실패"):
                await client._request("GET", "/futures/usdt/accounts")  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        assert len(seen) == 1


class TestBinanceTrade:
    def _handler(self, script: list[Any]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fapi/v1/time":
                return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
            seen.append(request)
            step = script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        return httpx.MockTransport(handle), seen

    @staticmethod
    def _resign(request: httpx.Request, secret: bytes) -> tuple[str, str]:
        pairs = parse_qsl(urlsplit(str(request.url)).query, keep_blank_values=True)
        sent = dict(pairs)["signature"]
        unsigned = [(k, v) for k, v in pairs if k != "signature"]
        recomputed = hmac.new(secret, urlencode(unsigned).encode(), hashlib.sha256).hexdigest()
        return sent, recomputed

    @pytest.mark.asyncio
    async def test_signature_matches_the_query_sent(self) -> None:
        transport, seen = self._handler([httpx.Response(200, json={"ok": 1})])
        client = BinanceTradeClient("k", "s", transport=transport)
        try:
            await client._request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT", "side": "BUY"})  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        req = seen[0]
        sent, recomputed = self._resign(req, b"s")
        assert sent == recomputed
        assert req.headers["X-MBX-APIKEY"] == "k"
        query = dict(parse_qsl(urlsplit(str(req.url)).query))
        assert query["recvWindow"] == "5000"
        assert query["symbol"] == "BTCUSDT"
        assert list(query)[:2] == ["symbol", "side"], "쿼리 순서가 서명 순서다"

    @pytest.mark.asyncio
    async def test_clock_skew_is_retried_once_but_nothing_else(self) -> None:
        transport, seen = self._handler(
            [
                httpx.Response(400, text='{"code":-1021,"msg":"ahead"}'),
                httpx.Response(200, json={"ok": 1}),
            ]
        )
        client = BinanceTradeClient("k", "s", transport=transport)
        try:
            assert await client._request("GET", "/fapi/v2/account") == {"ok": 1}  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        assert len(seen) == 2

        transport, seen = self._handler([httpx.Response(503, text="busy"), httpx.Response(200)])
        client = BinanceTradeClient("k", "s", transport=transport)
        try:
            with pytest.raises(BinanceTradeError):
                await client._request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT"})  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        assert len(seen) == 1, "503 은 재시도하지 않는다 — 주문이 생겼을 수 있다"

    @pytest.mark.asyncio
    async def test_transport_error_is_a_domain_error(self) -> None:
        transport, seen = self._handler([httpx.ConnectError("down")])
        client = BinanceTradeClient("k", "s", transport=transport)
        try:
            with pytest.raises(BinanceTradeError, match="전송 실패"):
                await client._request("GET", "/fapi/v2/account")  # pyright: ignore[reportPrivateUsage]
        finally:
            await client.aclose()
        assert len(seen) == 1
