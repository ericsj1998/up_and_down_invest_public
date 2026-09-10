"""MCP 서버 (T263 · 사용자 채택 2026-09-10) — 채팅 도구를 MCP 표준으로 내보낸다.

앱 안 채팅이 쓰는 도구 레지스트리(`orchestration/ai_chat/tools.TOOLS`)를 그대로 MCP 도구 목록으로
낸다. 이름·설명·JSON 스키마는 한 벌이고, 실행도 같은 함수다 — 모델만 바깥(Claude Desktop · Cursor ·
ChatGPT)이 된다. 붙는 자리는 기존 FastAPI 의 `/mcp` 한 경로(Streamable HTTP · 무상태 · JSON 응답)라
프로세스도 메모리도 늘지 않는다.

지키는 선:
- 인증은 앱의 문(`auth.guard`)이 그대로 본다. `Authorization: Bearer <개인 토큰>` →
  `Caller.via_token`. 로그인이 없으면 도구 호출은 오류 결과로 답한다(서버는 열리지만 도구는
  안 돈다).
- 주문을 내는 도구는 없다(규칙 #2). `propose_order` 는 RiskManager 를 거친 **제안**이다.
- `render_dashboard` 는 우리 화면 명세 도구라 뺀다 — MCP 클라이언트는 자기 화면이 있다.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import mcp.types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Receive, Scope, Send

from updown.apps.api import ai_chat
from updown.apps.api.auth import Caller, on_real_money
from updown.common.logging.setup import get_logger
from updown.common.security.caps import Cap
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import EVIDENCE_CHARS, compact_json
from updown.orchestration.ai_chat.tools import TOOLS, Tool, ToolContext, find_tool

_logger = get_logger("api.mcp")

HIDDEN: frozenset[str] = frozenset({"render_dashboard", "profile_wizard"})
ACCOUNT_TOOLS: frozenset[str] = frozenset(
    {"positions", "portfolio_exposure", "propose_order", "recommend_by_budget"}
)
"""거래소 잔고·포지션·펀드를 읽는 도구 — HTTP 로 치면 `LIVE/DEMO_ACCOUNT_READ` 가 필요한 것과
같은 값이다.

🔴 보안 점검(2026-09-10): `/mcp` 는 로그인만 요구하는 경로라, 승인 대기 계정이 토큰 하나로
실계좌 잔고를
읽을 수 있었다(같은 사람이 `GET /exchange/state` 를 치면 403). 도구마다 같은 기능을
요구한다.
"""
RUNS_TOOLS: frozenset[str] = frozenset({"trade_journal"})
"""판·원장을 읽는 도구 — `LIVE/DEMO_RUNS_READ`."""


def required_cap_of(tool_name: str) -> Cap | None:
    """도구가 요구하는 기능 — HTTP 경로 판정(`caps.required_cap`)과 같은 값.

    Args:
        tool_name: 도구 이름.

    Returns:
        기능, 또는 None(로그인만 하면 되는 도구 — 종목 풀기 · 시장 구조 · 재무 · 거시).
    """
    live = on_real_money()
    if tool_name in ACCOUNT_TOOLS:
        return Cap.LIVE_ACCOUNT_READ if live else Cap.DEMO_ACCOUNT_READ
    if tool_name in RUNS_TOOLS:
        return Cap.LIVE_RUNS_READ if live else Cap.DEMO_RUNS_READ
    return None


"""MCP 로 내보내지 않는 도구 — 우리 화면 전용."""

INSTRUCTIONS = (
    "업 앤 다운 — 주식·코인 자동 투자 관리의 조회·분석 도구. "
    "값은 실제 시세·원장·재무 출처에서 온다. "
    "도구는 근거와 제안만 낸다 — 주문을 내는 도구는 없고, 사는 것은 사람이 정한다. "
    "숫자를 지어내지 말고 도구 결과를 그대로 인용한다."
)

_CALLER: contextvars.ContextVar[Caller | None] = contextvars.ContextVar("mcp_caller", default=None)
"""이번 요청의 호출자 — `McpEndpoint` 가 미들웨어가 둔 `scope.state.caller` 를 옮겨 담는다."""

ContextFactory = Callable[[Caller], AbstractAsyncContextManager[ToolContext]]


def app_version() -> str:
    """패키지 버전 — MCP `serverInfo` 에 실린다.

    Returns:
        설치된 `updown` 버전. 메타데이터가 없으면 "0".
    """
    try:
        return version("updown")
    except PackageNotFoundError:
        return "0"


def exported_tools(tools: tuple[Tool, ...] = TOOLS) -> list[types.Tool]:
    """레지스트리 → MCP 도구 목록 (순수).

    Args:
        tools: 채팅 도구 레지스트리.

    Returns:
        `HIDDEN` 을 뺀 도구들 — 이름·설명·입력 스키마는 채팅과 같은 것.
    """
    return [
        types.Tool(name=t.spec.name, description=t.spec.description, input_schema=t.spec.parameters)
        for t in tools
        if t.spec.name not in HIDDEN
    ]


@asynccontextmanager
async def _default_context(who: Caller) -> AsyncGenerator[ToolContext, None]:
    """채팅과 같은 도구 자원 — 호출마다 시세 제공자를 열고 닫는다."""
    async with MarketDataProvider() as provider:
        yield ai_chat._context(who, lambda _: None, provider)  # pyright: ignore[reportPrivateUsage]


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def build_server(context_factory: ContextFactory | None = None) -> Server[Any]:
    """MCP 서버 — 도구 목록·호출 두 손잡이만 단다.

    Args:
        context_factory: 호출자 → 도구 자원(컨텍스트 매니저). None 이면 채팅과 같은 것.
            시험은 가짜를 준다.

    Returns:
        저수준 `Server`. 전송(HTTP)은 `build_manager` 가 붙인다.
    """
    factory = context_factory or _default_context

    async def on_list_tools(
        _ctx: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        """도구 목록.

        Args:
            _ctx: 요청 컨텍스트(안 쓴다).
            _params: 페이지 인자(안 쓴다 — 도구가 적다).

        Returns:
            `exported_tools()`.
        """
        return types.ListToolsResult(tools=exported_tools())

    async def on_call_tool(
        _ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        """도구 호출 — 호출자가 없거나 모르는 도구면 오류 결과(예외가 아니라).

        Args:
            _ctx: 요청 컨텍스트(안 쓴다).
            params: 도구 이름과 인자.

        Returns:
            글(JSON 요약)과 구조화 결과. 실패는 `is_error=True`.
        """
        who = _CALLER.get()
        if who is None:
            return _error(
                "로그인이 필요하다 — Authorization: Bearer <개인 토큰> (화면 /tokens 에서 만든다)"
            )
        tool = find_tool(params.name)
        if tool is None or params.name in HIDDEN:
            return _error(f"모르는 도구다: {params.name}")
        need = required_cap_of(params.name)
        if need is not None and not who.has(need):
            _logger.warning(
                "mcp_tool_forbidden",
                payload={"tool": params.name, "email": who.email, "cap": need.value},
            )
            return _error(f"{params.name} 은 '{need.value}' 권한이 필요하다 — 관리자에게 요청한다")
        started = time.perf_counter()
        try:
            async with factory(who) as tool_ctx:
                result = await tool.run(dict(params.arguments or {}), tool_ctx)
        except Exception as exc:
            _logger.warning(
                "mcp_tool_failed",
                payload={"tool": params.name, "email": who.email, "detail": str(exc)[:200]},
            )
            return _error(f"{params.name} 실패: {str(exc)[:300]}")
        _logger.info(
            "mcp_tool_call",
            payload={
                "tool": params.name,
                "email": who.email,
                "ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=compact_json(result, EVIDENCE_CHARS))],
            structured_content=result,
        )

    return Server(
        "updown",
        version=app_version(),
        title="업 앤 다운",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def build_manager(server: Server[Any]) -> StreamableHTTPSessionManager:
    """전송 — 무상태 · JSON 응답. 세션·SSE 를 안 두므로 프로세스가 재시작돼도 잃을 것이 없다.

    Args:
        server: `build_server` 의 결과.

    Returns:
        세션 관리자. 앱 lifespan 이 `run()` 을 감싸야 한다.

    Note:
        DNS 리바인딩 보호는 끈다 — 이 앱은 nginx 뒤 도메인으로 서비스되고 인증은 우리 문이 본다.
    """
    return StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class McpEndpoint:
    """`/mcp` ASGI 끝점 — 미들웨어가 둔 호출자를 도구 호출까지 옮긴다.

    문(`auth.guard`)이 `request.state.caller` 에 둔 값은 `scope["state"]` 에 있다. 이것을 컨텍스트
    변수에 담고 MCP 앱에 넘기면, 같은 요청 안에서 도는 도구 호출이 `_CALLER.get()` 으로 사람을 안다.
    """

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        """끝점을 만든다.

        Args:
            manager: `build_manager` 의 결과.
        """
        self._app = StreamableHTTPASGIApp(manager)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 진입 — 호출자를 옮기고 MCP 앱에 넘긴다."""
        state: dict[str, Any] = scope.get("state") or {}
        who = state.get("caller")
        token = _CALLER.set(who if isinstance(who, Caller) else None)
        try:
            await self._app(scope, receive, send)
        finally:
            _CALLER.reset(token)


__all__ = [
    "HIDDEN",
    "INSTRUCTIONS",
    "ContextFactory",
    "McpEndpoint",
    "app_version",
    "build_manager",
    "build_server",
    "exported_tools",
]
