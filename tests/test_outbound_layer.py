"""아웃바운드 HTTP 층 (T264) — 재시도 정책 · 예산 · 훅 · **원시 클라이언트 래칫**.

래칫: `httpx.AsyncClient(` 를 직접 만드는 파일 목록을 못 박는다. 새 파일이 원시 클라이언트를 만들면
실패하고, 옮긴 파일이 목록에 남아 있어도 실패한다 — 목록은 줄어들기만 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

from updown.common.http import (
    NO_RETRY,
    Outbound,
    OutboundError,
    RequestBudgetExceededError,
    RetryPolicy,
    retry_after_seconds,
)
from updown.marketdata import adapter as marketdata_adapter

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "updown"
FAST = RetryPolicy(max_retries=2, base_delay_s=0.0, jitter_s=0.0)

#: 아직 원시 `httpx.AsyncClient(` 를 직접 만드는 파일 — T264 2차 이관 대상. 옮기면 여기서 뺀다.
RAW_CLIENT_ALLOWLIST = frozenset(
    {
        "src/updown/common/http/outbound.py",  # 층 자체
        "src/updown/marketdata/binance/ws.py",  # 웹소켓 핸드셰이크 — 층은 HTTP 만
    }
)


def raw_client_sites(source: str, rel_path: str) -> list[int]:
    """`httpx.AsyncClient(...)` 호출이 있는 줄 번호.

    Args:
        source: 파이썬 소스.
        rel_path: 파일 이름(파싱 오류 메시지용).

    Returns:
        줄 번호 목록.
    """
    tree = ast.parse(source, filename=rel_path)
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "AsyncClient":
            if isinstance(func.value, ast.Name) and func.value.id == "httpx":
                found.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id == "AsyncClient":
            found.append(node.lineno)
    return found


def _handler(script: list[Any]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return httpx.MockTransport(handle), seen


class TestRetryPolicy:
    """정책은 순수 함수다."""

    def test_retry_after_is_read_as_seconds_with_cap(self) -> None:
        assert retry_after_seconds({"Retry-After": "3"}) == 3.0
        assert retry_after_seconds({"Retry-After": "0"}) == 0.0
        assert retry_after_seconds({"Retry-After": "999"}) is None
        assert retry_after_seconds({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
        assert retry_after_seconds({}) is None

    def test_delay_prefers_server_value_without_jitter(self) -> None:
        policy = RetryPolicy(base_delay_s=0.5, jitter_s=0.25)
        assert policy.delay(0, {"Retry-After": "7"}) == 7.0
        got = policy.delay(2)
        assert 2.0 <= got <= 2.25

    def test_retriable_set_and_5xx_switch(self) -> None:
        assert RetryPolicy().is_retriable(429)
        assert RetryPolicy().is_retriable(503)
        assert RetryPolicy().is_retriable(599)
        assert not RetryPolicy().is_retriable(404)
        assert RetryPolicy(retriable=frozenset({403}), retry_5xx=False).is_retriable(403)
        assert not RetryPolicy(retriable=frozenset({403}), retry_5xx=False).is_retriable(502)
        assert NO_RETRY.max_retries == 0


class TestOutbound:
    """전송 루프 — 네트워크 없이 `MockTransport` 로."""

    @pytest.mark.asyncio
    async def test_retriable_status_then_success(self) -> None:
        transport, seen = _handler(
            [httpx.Response(503, text="busy"), httpx.Response(200, json={"ok": 1})]
        )
        client = Outbound("T", timeout=1, policy=FAST, transport=transport)
        try:
            assert await client.get_json("https://x.test/a?k=v") == {"ok": 1}
        finally:
            await client.aclose()
        assert len(seen) == 2
        assert client.requests == 2

    @pytest.mark.asyncio
    async def test_non_retriable_status_is_returned_not_retried(self) -> None:
        transport, seen = _handler([httpx.Response(404, text="nope")])
        client = Outbound("T", timeout=1, policy=FAST, transport=transport)
        try:
            response = await client.request("GET", "https://x.test/missing")
        finally:
            await client.aclose()
        assert response.status_code == 404
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_404_raises_with_status_for_domain_mapping(self) -> None:
        transport, _ = _handler([httpx.Response(404, text="nope")])
        client = Outbound("T", timeout=1, policy=FAST, transport=transport)
        try:
            with pytest.raises(OutboundError) as caught:
                await client.get_json("https://x.test/missing")
        finally:
            await client.aclose()
        assert caught.value.status_code == 404
        assert caught.value.venue == "T"
        assert "nope" in caught.value.body

    @pytest.mark.asyncio
    async def test_exhaustion_raises_with_last_status(self) -> None:
        transport, seen = _handler([httpx.Response(429)] * 3)
        client = Outbound("T", timeout=1, policy=FAST, transport=transport)
        try:
            with pytest.raises(OutboundError) as caught:
                await client.request("GET", "https://x.test/r")
        finally:
            await client.aclose()
        assert len(seen) == 3
        assert caught.value.status_code == 429

    @pytest.mark.asyncio
    async def test_transport_error_is_retried_then_raised(self) -> None:
        transport, seen = _handler(
            [httpx.ConnectError("down"), httpx.ConnectError("down"), httpx.ConnectError("down")]
        )
        client = Outbound("T", timeout=1, policy=FAST, transport=transport)
        try:
            with pytest.raises(OutboundError) as caught:
                await client.get_json("https://x.test/a")
        finally:
            await client.aclose()
        assert len(seen) == 3
        assert caught.value.status_code is None
        assert "ConnectError" in str(caught.value)

    @pytest.mark.asyncio
    async def test_non_json_200_is_an_error_with_status_200(self) -> None:
        transport, _ = _handler([httpx.Response(200, text="<html>")])
        client = Outbound("T", timeout=1, policy=NO_RETRY, transport=transport)
        try:
            with pytest.raises(OutboundError) as caught:
                await client.get_json("https://x.test/a")
        finally:
            await client.aclose()
        assert caught.value.status_code == 200

    @pytest.mark.asyncio
    async def test_budget_blocks_before_sending(self) -> None:
        transport, seen = _handler([httpx.Response(200, json={}), httpx.Response(200, json={})])
        client = Outbound("T", timeout=1, policy=NO_RETRY, transport=transport)
        try:
            with client.budget(1):
                await client.get_json("https://x.test/1")
                with pytest.raises(RequestBudgetExceededError):
                    await client.get_json("https://x.test/2")
            # 블록 밖은 무제한
            await client.get_json("https://x.test/3")
        finally:
            await client.aclose()
        assert len(seen) == 2
        assert client.requests == 3  # 막힌 요청도 센다 — 예산이 "보내기 전"에 걸렸다는 뜻

    @pytest.mark.asyncio
    async def test_throttle_and_response_hooks_are_called(self) -> None:
        waited: list[str] = []
        observed: list[tuple[str, str]] = []

        class _Throttle:
            async def acquire(self) -> None:
                waited.append("acquired")

        transport, _ = _handler([httpx.Response(200, json={}, headers={"X-Used": "7"})])
        client = Outbound(
            "T",
            timeout=1,
            policy=NO_RETRY,
            throttle_of=lambda path: _Throttle() if path.startswith("/v1") else None,
            on_response=lambda path, headers: observed.append((path, headers.get("X-Used", ""))),
            transport=transport,
        )
        try:
            await client.get_json("https://x.test/v1/x?secret=1")
        finally:
            await client.aclose()
        assert waited == ["acquired"]
        assert observed == [("/v1/x", "7")]  # 쿼리는 로그·훅에 안 실린다

    def test_budget_error_is_the_same_class_marketdata_exports(self) -> None:
        assert marketdata_adapter.RequestBudgetExceededError is RequestBudgetExceededError


class TestRawClientRatchet:
    """원시 `httpx.AsyncClient(` 는 허용 목록 밖에 생길 수 없고, 목록은 줄어들기만 한다."""

    def test_checker_detects_both_spellings(self) -> None:
        assert raw_client_sites("import httpx\nc = httpx.AsyncClient(timeout=1)\n", "a.py") == [2]
        assert raw_client_sites("from httpx import AsyncClient\nc = AsyncClient()\n", "b.py") == [2]
        assert raw_client_sites("x = other.AsyncClient()\n", "c.py") == []

    def test_no_raw_client_outside_the_allowlist(self) -> None:
        offenders: list[str] = []
        stale: list[str] = []
        seen: set[str] = set()
        for path in sorted(SRC_ROOT.rglob("*.py")):
            rel = path.relative_to(SRC_ROOT.parent.parent).as_posix()
            lines = raw_client_sites(path.read_text(encoding="utf-8"), rel)
            if not lines:
                continue
            seen.add(rel)
            if rel not in RAW_CLIENT_ALLOWLIST:
                offenders.extend(f"{rel}:{line}" for line in lines)
        stale = sorted(RAW_CLIENT_ALLOWLIST - seen)
        assert not offenders, (
            "원시 httpx.AsyncClient 는 common/http.Outbound 를 통한다 (T264): "
            + ", ".join(offenders)
        )
        assert not stale, f"이관이 끝난 파일은 목록에서 뺀다: {stale}"

    def test_migrated_clients_have_no_raw_client(self) -> None:
        for rel in (
            "src/updown/marketdata/fundamentals/client.py",
            "src/updown/marketdata/macro/client.py",
        ):
            source = (SRC_ROOT.parent.parent / rel).read_text(encoding="utf-8")
            assert raw_client_sites(source, rel) == []
