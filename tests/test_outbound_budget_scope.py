"""요청 예산은 **그 작업이 낸 요청만** 센다 (2026-09-11).

Note:
    🔴 사용자 신고: *"지금 거래 콘솔 주식에서 펀드 생성이 실패해"* — 화면에는
    `TOSS 요청이 상한 300 을 넘었다 (/api/v1/candles)` 만 떴다.

    예전 예산은 클라이언트의 **누계**를 재서, 같은 시각에 도는 야간 예열·콘솔 폴링·다른 판의
    30초 점검이 모두 그 상한을 먹었다. 사람이 누른 펀드 만들기는 자기가 쓴 요청이 몇 개든
    남의 일 때문에 실패할 수 있었고, 화면에서는 고칠 방법이 없는 오류로만 보였다.

    지금은 `contextvars` 로 작업마다 따로 센다. 요율 자체는 스로틀이 지키고, 이 상한이
    지키는 것은 **한 작업이 너무 비싼가**이다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from updown.common.http import Outbound, RequestBudgetExceededError, RetryPolicy


def _client() -> Outbound:
    """무엇을 물어도 200 을 주는 층 — 요청 수만 센다."""
    return Outbound(
        "TOSS",
        timeout=1.0,
        base_url="https://example.invalid",
        policy=RetryPolicy(max_retries=0),
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={})),
    )


@pytest.mark.asyncio
async def test_budget_counts_only_this_task() -> None:
    """배경 작업이 같은 클라이언트로 백 번을 불러도 내 상한을 먹지 않는다."""
    client = _client()

    async def background() -> None:
        for _ in range(50):
            await client.request("GET", "/api/v1/candles")

    task = asyncio.create_task(background())
    await asyncio.sleep(0)
    with client.budget(5):
        for _ in range(5):
            await client.request("GET", "/api/v1/candles")
    await task
    assert client.requests >= 55, "누계는 남의 요청까지 센다 — 요율 눈금은 그대로다"


@pytest.mark.asyncio
async def test_budget_still_stops_one_expensive_task() -> None:
    """제 요청이 상한을 넘으면 그대로 멈춘다 — 상한을 없앤 것이 아니다."""
    client = _client()
    with pytest.raises(RequestBudgetExceededError) as caught, client.budget(3):
        for _ in range(4):
            await client.request("GET", "/api/v1/candles")
    assert "상한 3" in str(caught.value)
    assert "/api/v1/candles" in str(caught.value)


@pytest.mark.asyncio
async def test_budget_used_reads_this_task_only() -> None:
    """눈금은 블록 안에서만 값이 있고, 그 값이 로그로 나간다 (`run_start_requests`)."""
    client = _client()
    assert client.budget_used is None
    with client.budget(10):
        await client.request("GET", "/api/v1/candles")
        await client.request("GET", "/api/v1/candles")
        assert client.budget_used == 2
    assert client.budget_used is None


@pytest.mark.asyncio
async def test_nested_budget_inner_wins_and_outer_returns() -> None:
    """중첩은 안쪽이 이기고, 나가면 바깥 상한으로 돌아간다."""
    client = _client()
    with client.budget(10):
        await client.request("GET", "/a")
        with client.budget(1):
            await client.request("GET", "/b")
            with pytest.raises(RequestBudgetExceededError):
                await client.request("GET", "/b")
        assert client.budget_used == 1, "바깥 눈금에 안쪽 요청은 안 들어간다"
        await client.request("GET", "/c")
        assert client.budget_used == 2
