"""T263 — 개인 API 토큰: 값 모양·해시(순수) · 경로 분류(`/mcp` · `/auth/tokens` 는 로그인만) ·
토큰 호출자는 읽기와 MCP 만."""

from __future__ import annotations

import pytest

from updown.apps.api.auth import TOKEN_PREFIX, hash_token, new_token, token_allowed, token_mode
from updown.common.security.caps import Access, required_cap
from updown.common.security.roles import Need, need_for


class TestTokenValue:
    def test_prefix_randomness_and_hash(self) -> None:
        a, b = new_token(), new_token()
        assert a.startswith(TOKEN_PREFIX) and b.startswith(TOKEN_PREFIX) and a != b
        assert len(a) > 40
        assert hash_token(a) == hash_token(a) and len(hash_token(a)) == 64
        assert hash_token(a) != hash_token(b)


class TestPaths:
    def test_mcp_and_tokens_need_only_sign_in(self) -> None:
        for method in ("GET", "POST", "DELETE"):
            assert required_cap(method, "/mcp", live=False) is Access.SIGNED_IN
            assert required_cap(method, "/mcp", live=True) is Access.SIGNED_IN
            assert need_for(method, "/mcp") is Need.READ
        assert required_cap("POST", "/auth/tokens", live=False) is Access.SIGNED_IN
        assert required_cap("DELETE", "/auth/tokens/abc", live=True) is Access.SIGNED_IN
        assert need_for("POST", "/auth/tokens") is Need.READ
        # 다른 새 POST 는 여전히 거래 권한이다 — 구멍이 아니다.
        assert need_for("POST", "/mcpx/other") is Need.TRADE

    def test_token_callers_read_or_mcp_only(self) -> None:
        assert token_allowed("GET", "/walkforward/sessions")
        assert token_allowed("POST", "/mcp") and token_allowed("DELETE", "/mcp/")
        assert not token_allowed("POST", "/walkforward/live")
        assert not token_allowed("POST", "/auth/tokens")
        assert not token_allowed("DELETE", "/rebalancer/abc")


class TestTokenMode:
    """토큰 값에 행선지를 새긴다 (2026-09-14) — MCP 요청은 쿠키가 없어 nginx 가 이걸로 보낸다."""

    def test_new_token_carries_mode_and_old_tokens_have_none(self) -> None:
        live, demo = new_token("live"), new_token("demo")
        assert live.startswith("updn_live_") and demo.startswith("updn_demo_")
        assert token_mode(live) == "live" and token_mode(demo) == "demo"
        assert token_mode("updn_" + "x" * 43) is None  # 옛 토큰
        assert token_mode("other_live_abc") is None
        # 접두어를 붙여도 우리 토큰 머리(`updn_`)로 시작한다 — caller_of 의 첫 판별 그대로.
        assert live.startswith(TOKEN_PREFIX)
        with pytest.raises(ValueError):
            new_token("paper")
