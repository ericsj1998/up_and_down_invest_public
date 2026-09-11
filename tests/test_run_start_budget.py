"""T253 — 판 시작의 브로커 요청을 세고 상한을 건다.

토스 클라이언트가 요청을 세는지(토큰 발급 포함) · `budget` 블록이 상한을 넘는 요청을 **보내기 전에**
막는지 · 어댑터가 `RequestCounting` 계약을 만족하는지 · 러너의 `seed_within_budget` 가 쓴 요청 수를
돌려주고 상한에서 던지는지. 세지 못하는 어댑터(웹소켓 거래소)는 None 이다.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.adapter import QuoteAdapter, RequestBudgetExceededError, RequestCounting
from updown.marketdata.toss.adapter import TossAdapter
from updown.marketdata.toss.client import TossClient
from updown.orchestration.walkforward.live_runner import seed_within_budget

TOKEN_BODY = {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}
APPLE = Instrument(
    market=Market.NASDAQ,
    symbol="AAPL",
    name="Apple",
    asset_type=AssetType.STOCK,
    currency=Currency.USD,
)


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/oauth2/token":
        return httpx.Response(200, json=TOKEN_BODY)
    return httpx.Response(200, json={"result": {"candles": [], "nextBefore": None}})


def _client() -> TossClient:
    return TossClient(
        SecretStr("id"),
        SecretStr("secret"),
        transport=httpx.MockTransport(_handler),
        rate_per_second=100_000,
        max_retries=0,
    )


class TestTossClientCounts:
    @pytest.mark.asyncio
    async def test_counts_token_and_data_requests(self) -> None:
        async with _client() as client:
            assert client.requests == 0
            await client.get_result("/api/v1/candles", group="CHART", params={})
            await client.get_result("/api/v1/candles", group="CHART", params={})
            # 토큰 1 + 데이터 2 — 토큰은 한 번만 받는다(캐시).
            assert client.requests == 3

    @pytest.mark.asyncio
    async def test_budget_blocks_before_sending_and_restores(self) -> None:
        sent: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request.url.path)
            return _handler(request)

        async with TossClient(
            SecretStr("id"),
            SecretStr("secret"),
            transport=httpx.MockTransport(handler),
            rate_per_second=100_000,
            max_retries=0,
        ) as client:
            with client.budget(3):
                await client.get_result("/a", group="G", params={})  # 토큰 + 1 = 2
                await client.get_result("/b", group="G", params={})  # 3
                with pytest.raises(RequestBudgetExceededError, match="상한 3"):
                    await client.get_result("/c", group="G", params={})
            # 던진 요청은 **보내지 않았다** — 상한은 요율을 지키는 것이지 기록만 하는 것이 아니다.
            assert sent == ["/oauth2/token", "/a", "/b"]
            # 블록을 나가면 상한이 풀린다.
            await client.get_result("/d", group="G", params={})
            assert client.requests == 5

    @pytest.mark.asyncio
    async def test_zero_cap_means_unlimited_and_nesting_restores_outer(self) -> None:
        async with _client() as client:
            with client.budget(0):
                for _ in range(5):
                    await client.get_result("/x", group="G", params={})
            with client.budget(10):
                with client.budget(1):
                    await client.get_result("/y", group="G", params={})
                    with pytest.raises(RequestBudgetExceededError):
                        await client.get_result("/y", group="G", params={})
                # 바깥 예산(10)으로 돌아왔다 — 안쪽에서 던졌어도 바깥은 산다.
                await client.get_result("/z", group="G", params={})

    @pytest.mark.asyncio
    async def test_adapter_satisfies_request_counting(self) -> None:
        async with _client() as client:
            adapter = TossAdapter(client)
            assert isinstance(adapter, RequestCounting)
            with adapter.budget(1), pytest.raises(RequestBudgetExceededError):
                await client.get_result("/a", group="G", params={})
                await client.get_result("/b", group="G", params={})
            assert adapter.requests == client.requests


def _bars(instrument: Instrument, timeframe: Timeframe, n: int = 3) -> list[Candle]:
    """시드 봉 몇 개 — 급전은 빈 시드를 거부하고 마지막 봉을 버리므로 둘 이상이어야 한다."""
    base = datetime(2026, 9, 1, tzinfo=UTC)
    return [
        Candle(
            instrument=instrument,
            timeframe=timeframe,
            ts=base + timedelta(hours=i),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
        )
        for i in range(n)
    ]


class _Counting:
    """요청을 세는 가짜 조회 어댑터 — 봉 요청 하나가 HTTP 3회다(합성 흉내)."""

    def __init__(self, per_call: int = 3) -> None:
        self.requests = 0
        self._per_call = per_call
        self._cap = 0
        self._used: int | None = None

    @property
    def budget_used(self) -> int | None:
        """블록 안에서 이 작업이 쓴 수 — 층과 같은 뜻이다 (2026-09-11)."""
        return self._used

    @contextlib.contextmanager
    def budget(self, cap: int) -> Generator[None, None, None]:
        was = (self._cap, self._used)
        self._cap, self._used = cap, 0
        try:
            yield
        finally:
            self._cap, self._used = was

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        del start, end
        for _ in range(self._per_call):
            self.requests += 1
            if self._used is not None:
                self._used += 1
                if self._cap > 0 and self._used > self._cap:
                    raise RequestBudgetExceededError("cap")
        return _bars(instrument, timeframe)


class _Plain:
    """세지 못하는 어댑터(웹소켓 거래소) — 요청 수는 None 이어야 한다."""

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        del start, end
        return _bars(instrument, timeframe)


class TestSeedWithinBudget:
    @pytest.mark.asyncio
    async def test_reports_requests_used(self) -> None:
        quotes = _Counting()
        _feed, used = await seed_within_budget(
            cast("QuoteAdapter", quotes), APPLE, (Timeframe.H1, Timeframe.D1), Timeframe.H1, cap=100
        )
        assert used == 6

    @pytest.mark.asyncio
    async def test_counter_can_be_the_inner_adapter(self) -> None:
        inner = _Counting()
        _feed, used = await seed_within_budget(
            cast("QuoteAdapter", inner), APPLE, (Timeframe.H1,), Timeframe.H1, cap=0, counter=inner
        )
        assert used == 3

    @pytest.mark.asyncio
    async def test_raises_when_over_cap(self) -> None:
        quotes = _Counting()
        with pytest.raises(RequestBudgetExceededError):
            await seed_within_budget(
                cast("QuoteAdapter", quotes),
                APPLE,
                (Timeframe.H1, Timeframe.D1),
                Timeframe.H1,
                cap=5,
            )
        # 다섯 개까지는 갔고 여섯 번째에서 멈췄다 — 상한을 넘겨 계속 부르지 않는다.
        assert quotes.requests == 6

    @pytest.mark.asyncio
    async def test_non_counting_adapter_reports_none(self) -> None:
        _feed, used = await seed_within_budget(
            cast("QuoteAdapter", _Plain()), APPLE, (Timeframe.H1,), Timeframe.H1, cap=5
        )
        assert used is None
