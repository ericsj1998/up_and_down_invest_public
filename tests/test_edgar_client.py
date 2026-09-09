"""T243 — EDGAR 클라이언트(User-Agent · 재시도 · 404) 와 어댑터(티커 → CIK → 사실)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from updown.common.domain.fundamentals import load_fundamentals_config
from updown.marketdata.fundamentals.adapter import UnknownEntityError
from updown.marketdata.fundamentals.client import EdgarApiError, EdgarClient
from updown.marketdata.fundamentals.edgar import EdgarAdapter

Handler = Callable[[httpx.Request], httpx.Response]
CONFIG = load_fundamentals_config()
TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
FACTS = {
    "cik": 320193,
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "start": "2024-01-01",
                            "end": "2024-03-31",
                            "val": 100,
                            "accn": "0000320193-24-000001",
                            "fy": 2024,
                            "fp": "Q1",
                            "form": "10-Q",
                            "filed": "2024-05-01",
                        }
                    ]
                }
            }
        }
    },
}


def make_client(handler: Handler, **kwargs: Any) -> EdgarClient:
    kwargs.setdefault("rate_per_second", 100_000)
    kwargs.setdefault("max_retries", 1)
    return EdgarClient("Test test@example.com", transport=httpx.MockTransport(handler), **kwargs)


class TestClient:
    def test_user_agent_is_required(self) -> None:
        with pytest.raises(ValueError, match="User-Agent"):
            EdgarClient("   ")

    def test_user_agent_must_be_ascii(self) -> None:
        # HTTP 헤더는 라틴-1 — 한글 이름을 넣으면 httpx 가 인코딩에서 죽는다 (2026-09-10 실측 500).
        with pytest.raises(ValueError, match="ASCII"):
            EdgarClient("정성준 me@example.com")

    @pytest.mark.asyncio
    async def test_user_agent_header_is_sent(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=TICKERS)

        async with make_client(handler) as client:
            table = await client.company_tickers()
        assert table == {"AAPL": "0000320193"}
        assert seen[0].headers["User-Agent"] == "Test test@example.com"

    @pytest.mark.asyncio
    async def test_429_then_200_is_retried(self) -> None:
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json=FACTS)

        async with make_client(handler) as client:
            body = await client.company_facts("320193")
        assert calls == 2 and body["cik"] == 320193

    @pytest.mark.asyncio
    async def test_404_is_unknown_entity_and_400_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404 if "0000000009" in request.url.path else 400)

        async with make_client(handler) as client:
            with pytest.raises(UnknownEntityError):
                await client.company_facts("9")
            with pytest.raises(EdgarApiError) as caught:
                await client.company_facts("1")
        assert caught.value.status_code == 400 and calls == 2

    @pytest.mark.asyncio
    async def test_cik_path_is_zero_padded(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=FACTS)

        async with make_client(handler) as client:
            await client.company_facts("320193")
        assert seen == ["/api/xbrl/companyfacts/CIK0000320193.json"]


class TestAdapter:
    @pytest.mark.asyncio
    async def test_facts_and_filings_and_ticker_cache(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path.endswith("company_tickers.json"):
                return httpx.Response(200, json=TICKERS)
            return httpx.Response(200, json=FACTS)

        async with make_client(handler) as client:
            adapter = EdgarAdapter(client, CONFIG)
            facts = await adapter.facts("aapl")
            filings = await adapter.filings("AAPL")
            with pytest.raises(UnknownEntityError):
                await adapter.cik_of("NOPE")
        assert adapter.source == "edgar"
        assert len(facts) == 1 and facts[0].symbol == "AAPL" and facts[0].value == Decimal(100)
        assert facts[0].period_end == date(2024, 3, 31)
        assert (
            filings[0].url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/"
        )
        assert paths.count("/files/company_tickers.json") == 1, "티커 표는 한 번만 받는다"
