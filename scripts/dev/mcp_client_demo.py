"""MCP 클라이언트 시연 (T263) — 우리 `/mcp` 에 표준 클라이언트로 붙어 도구 목록을 받고 둘을 부른다.

    UPDOWN_MCP_URL=http://127.0.0.1:8000/mcp UPDOWN_MCP_TOKEN=updn_... \\
        uv run python scripts/dev/mcp_client_demo.py

어떤 MCP 클라이언트(Claude Desktop · Cursor · 랭그래프 `langchain-mcp-adapters`)도 같은 순서로
붙는다 — initialize → tools/list → tools/call. 토큰이 없으면 서버는 열리지만 도구 호출이 오류 결과로
돌아온다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, cast

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

try:  # mcp 2.x 는 httpx 의 갈래(httpx2)를 쓴다 — 없으면 보통 httpx.
    import httpx2 as httpx_lib  # pyright: ignore[reportMissingImports]
except ImportError:  # pragma: no cover
    import httpx as httpx_lib


async def main() -> int:
    """목록 → `symbol_resolve` → `macro_view(keys=[vix, usdkrw])`.

    Returns:
        0 이면 성공. 실패는 예외로 올라가 아래에서 찍는다.
    """
    url = os.environ.get("UPDOWN_MCP_URL", "http://127.0.0.1:8000/mcp")
    token = os.environ.get("UPDOWN_MCP_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = cast("Any", httpx_lib).AsyncClient(headers=headers, timeout=120)
    async with (
        http,
        streamable_http_client(url, http_client=http) as streams,
    ):
        read, write = cast("tuple[Any, Any, Any]", streams)[:2]
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print("server", info.server_info.name, info.server_info.version)
            tools = await session.list_tools()
            print("tools", len(tools.tools), [t.name for t in tools.tools])
            for name, args in (
                ("symbol_resolve", {"query": "테슬라"}),
                ("macro_view", {"keys": ["vix", "usdkrw"]}),
            ):
                result = await session.call_tool(name, args)
                first = result.content[0] if result.content else None
                text = str(getattr(first, "text", ""))
                print(name, "error" if result.is_error else "ok", text[:240].replace("\n", " "))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(json.dumps({"failed": str(exc)[:300]}, ensure_ascii=False))
        sys.exit(1)
