"""T257 — 추천 질문(starters) · 후속 질문 파싱 · 진행 줄 단계 · 근거 원문 저장."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.port import ChatMessage, ChatOutcome, ChatReply, ToolCall, ToolSpec
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import compact_json, parse_suggestions, run_chat
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.tools import TOOLS, ToolContext, starters


def _ctx() -> ToolContext:
    costs = load_cost_table(DEFAULT_CONFIG_PATH)
    return ToolContext(
        provider=MarketDataProvider(),
        live_markets=("NASDAQ", "BINANCE"),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
    )


class TestStarters:
    def test_every_tool_has_a_starter_question(self) -> None:
        got = starters()
        # render_dashboard 는 답 끝에 모델이 스스로 부르는 도구라 추천 질문이 없다 (T256).
        expected = [t for t in TOOLS if t.spec.name != "render_dashboard"]
        assert len(got) == len(expected) and all(q.endswith(("?", "줘")) for q in got)


class TestCompactJson:
    def test_stays_valid_json_and_caps_lists(self) -> None:
        import json

        big = {"rows": [{"i": i, "text": "x" * 500} for i in range(200)], "note": "n"}
        text = compact_json(big, 3_000)
        got = json.loads(text)
        assert len(text) <= 3_000 and got["note"] == "n"
        assert isinstance(got["rows"], list) and str(got["rows"][-1]).startswith("… 외 ")
        assert compact_json({"a": 1}, 100) == '{"a": 1}'


class TestSuggestions:
    def test_parse_array_with_noise_and_limits_to_three(self) -> None:
        text = '물론입니다:\n["다음 A?", "다음 B?", "다음 C?", "다음 D?"]\n끝'
        assert parse_suggestions(text) == ["다음 A?", "다음 B?", "다음 C?"]
        assert parse_suggestions("배열이 없다") == []
        assert parse_suggestions("[1, 2]") == []
        assert parse_suggestions('["a", ') == []

    @pytest.mark.asyncio
    async def test_suggest_adds_one_call_and_never_breaks_the_answer(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls = 0

            async def chat(
                self,
                model: str,
                messages: Sequence[ChatMessage],
                *,
                tools: Sequence[ToolSpec],
                temperature: float,
                timeout_seconds: float,
            ) -> ChatOutcome:
                del messages, temperature, timeout_seconds
                self.calls += 1
                if self.calls == 1:
                    return ChatReply(
                        model, "", (ToolCall("c1", "symbol_resolve", {"query": "테슬라"}),), 1, 1, 1
                    )
                if self.calls == 2:
                    return ChatReply(model, "답", (), 1, 1, 1)
                assert not tools, "후속 질문 호출엔 도구가 없다"
                return ChatReply(model, '["그럼 애플은?", "지금 살까?"]', (), 1, 1, 1)

        client = Client()
        lines: list[str] = []
        got = await run_chat(
            [],
            "테슬라 어때",
            client=client,
            model="m",
            ctx=_ctx(),
            report=lines.append,
            suggest=True,
        )
        assert got.text == "답" and got.rounds == 2 and client.calls == 3
        assert got.suggestions == ["그럼 애플은?", "지금 살까?"]
        assert got.tool_events[0].result.startswith("{")
        assert any("계획" in line for line in lines) and any("완료" in line for line in lines)
        assert any("다음 질문" in line for line in lines)

    @pytest.mark.asyncio
    async def test_without_suggest_no_extra_call(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.calls = 0

            async def chat(
                self,
                model: str,
                messages: Sequence[ChatMessage],
                *,
                tools: Sequence[ToolSpec],
                temperature: float,
                timeout_seconds: float,
            ) -> ChatOutcome:
                del messages, tools, temperature, timeout_seconds
                self.calls += 1
                return ChatReply(model, "답", (), 1, 1, 1)

        client = Client()
        got = await run_chat([], "x", client=client, model="m", ctx=_ctx())
        assert client.calls == 1 and got.suggestions == []
