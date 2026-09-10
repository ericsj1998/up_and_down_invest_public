"""T263 — MCP 서버: 도구 목록은 채팅 레지스트리 그대로(화면 도구 제외) · 호출자 없으면 오류 결과 ·
호출자가 있으면 같은 도구 함수가 돈다 · ASGI 끝점이 미들웨어의 호출자를 옮긴다."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import mcp.types as types
import pytest

from updown.apps.api.auth import Caller
from updown.apps.api.mcp_server import (
    _CALLER,  # pyright: ignore[reportPrivateUsage]
    HIDDEN,
    McpEndpoint,
    exported_tools,
)
from updown.apps.api.mcp_server import build_server as build_mcp_server
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market
from updown.common.security.roles import Role
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.tools import TOOLS, ToolContext

WHO = Caller(email="me@example.com", role=Role.TRADER, fresh=False, via_token=True)


@asynccontextmanager
async def _ctx(_who: Caller) -> AsyncGenerator[ToolContext, None]:
    costs = load_cost_table(DEFAULT_CONFIG_PATH)
    yield ToolContext(
        provider=MarketDataProvider(),
        live_markets=("NASDAQ", "BINANCE"),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
    )


class TestExportedTools:
    def test_registry_minus_dashboard_with_schemas(self) -> None:
        got = exported_tools()
        names = [t.name for t in got]
        assert set(names) == {t.spec.name for t in TOOLS} - HIDDEN
        assert "render_dashboard" not in names and "macro_view" in names
        for tool in got:
            assert tool.input_schema.get("type") == "object" and tool.description


def _server_handlers() -> tuple[Any, Any]:
    server = build_mcp_server(_ctx)
    # 저수준 서버는 손잡이를 method 이름으로 든다.
    list_entry = server.get_request_handler("tools/list")
    call_entry = server.get_request_handler("tools/call")
    assert list_entry is not None and call_entry is not None
    return list_entry, call_entry


def _handler(entry: Any) -> Any:
    return getattr(entry, "handler", entry)


class TestHandlers:
    @pytest.mark.asyncio
    async def test_list_tools(self) -> None:
        list_entry, _ = _server_handlers()
        result = await _handler(list_entry)(cast("Any", None), None)
        assert isinstance(result, types.ListToolsResult)
        assert len(result.tools) == len(TOOLS) - len(HIDDEN)

    @pytest.mark.asyncio
    async def test_call_without_caller_is_an_error_result(self) -> None:
        _, call_entry = _server_handlers()
        token = _CALLER.set(None)
        try:
            result = await _handler(call_entry)(
                cast("Any", None),
                types.CallToolRequestParams(name="symbol_resolve", arguments={"query": "테슬라"}),
            )
        finally:
            _CALLER.reset(token)
        assert isinstance(result, types.CallToolResult) and result.is_error
        assert "토큰" in cast("types.TextContent", result.content[0]).text

    @pytest.mark.asyncio
    async def test_call_runs_the_same_tool_and_hides_dashboard(self) -> None:
        _, call_entry = _server_handlers()
        token = _CALLER.set(WHO)
        try:
            result = await _handler(call_entry)(
                cast("Any", None),
                types.CallToolRequestParams(name="symbol_resolve", arguments={"query": "테슬라"}),
            )
            hidden = await _handler(call_entry)(
                cast("Any", None),
                types.CallToolRequestParams(name="render_dashboard", arguments={}),
            )
        finally:
            _CALLER.reset(token)
        assert isinstance(result, types.CallToolResult) and not result.is_error
        body = cast("dict[str, Any]", result.structured_content)
        assert body["candidates"][0]["symbol"] == "TSLA"
        assert isinstance(hidden, types.CallToolResult) and hidden.is_error


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_caller_travels_from_scope_state(self) -> None:
        seen: list[Caller | None] = []

        class _Manager:
            pass

        endpoint = McpEndpoint(cast("Any", _Manager()))

        async def fake_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            del scope, receive, send
            seen.append(_CALLER.get())

        endpoint._app = fake_app  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        await endpoint(
            {"type": "http", "state": {"caller": WHO}}, cast("Any", None), cast("Any", None)
        )
        await endpoint({"type": "http", "state": {}}, cast("Any", None), cast("Any", None))
        assert seen == [WHO, None]
        assert _CALLER.get() is None
