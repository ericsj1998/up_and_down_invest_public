"""채팅 시험 엔진 — 사례가 도구를 전부 덮나 · 판정(순수) · 가짜 모델로 한 바퀴 · 커버리지."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.port import ChatMessage, ChatOutcome, ChatReply, ToolCall, ToolSpec
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import ChatResult, ToolEvent
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.evaluate import CASES, EvalCase, judge, run_eval
from updown.orchestration.ai_chat.tools import TOOLS, ToolContext


class TestCases:
    def test_every_tool_has_a_case(self) -> None:
        covered = {name for case in CASES for name in case.expect_tools}
        # render_dashboard 는 규칙 8 로 답 끝에 스스로 불린다 — expect_dashboard 사례가 잰다.
        assert {t.spec.name for t in TOOLS} - {"render_dashboard"} <= covered
        assert any(c.expect_dashboard for c in CASES)


class TestJudge:
    def test_pass_and_fail_reasons(self) -> None:
        case = EvalCase("x", "q", ("symbol_resolve",), expect_dashboard=True)
        ok = ChatResult(
            text="답",
            tool_events=[
                ToolEvent("symbol_resolve", {}, True, 5, "d"),
                ToolEvent("render_dashboard", {}, True, 1, ""),
            ],
            rounds=3,
            dashboard={"title": "", "blocks": [{"kind": "text", "title": "", "text": "x"}]},
            dashboard_missing=["a.b"],
        )
        got = judge(case, ok, 1234)
        assert (
            got.passed and got.hit and got.tools_ok and got.dashboard and got.dashboard_missing == 1
        )
        no_dash = ChatResult(text="답", tool_events=[ToolEvent("symbol_resolve", {}, True, 5, "d")])
        assert judge(case, no_dash, 1).passed is False and judge(case, no_dash, 1).hit
        failed_tool = ChatResult(
            text="답", tool_events=[ToolEvent("symbol_resolve", {}, False, 5, "", "boom")]
        )
        assert judge(EvalCase("y", "q", ("symbol_resolve",)), failed_tool, 1).tools_ok is False
        model_dead = ChatResult(text="모델이 답하지 못했다", failure="TIMEOUT: x")
        assert judge(EvalCase("z", "q", ("symbol_resolve",)), model_dead, 1).answered is False


def _ctx() -> ToolContext:
    costs = load_cost_table(DEFAULT_CONFIG_PATH)
    return ToolContext(
        provider=MarketDataProvider(),
        live_markets=("NASDAQ", "BINANCE"),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
    )


class TestRunEval:
    @pytest.mark.asyncio
    async def test_fake_model_round_and_coverage(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.turn = 0

            async def chat(
                self,
                model: str,
                messages: Sequence[ChatMessage],
                *,
                tools: Sequence[ToolSpec],
                temperature: float,
                timeout_seconds: float,
            ) -> ChatOutcome:
                del tools, temperature, timeout_seconds
                self.turn += 1
                last = messages[-1]
                if last.role == "user" and "테슬라" in last.content:
                    return ChatReply(
                        model, "", (ToolCall("c", "symbol_resolve", {"query": "테슬라"}),), 1, 1, 1
                    )
                return ChatReply(model, "답", (), 1, 1, 1)

        cases = (
            EvalCase("symbol_resolve", "테슬라 종목 코드가 뭐야?", ("symbol_resolve",)),
            EvalCase("market_view", "비트코인 추세", ("market_view",)),
        )
        lines: list[str] = []
        got = await run_eval(
            cases,
            client=Client(),
            model="m",
            ctx_factory=_ctx,
            prompt_version="chat-x",
            report=lines.append,
        )
        assert [c.passed for c in got.cases] == [True, False]
        cov = got.coverage(["symbol_resolve", "market_view", "valuation"])
        assert cov == {"symbol_resolve": "passed", "market_view": "missed", "valuation": "untested"}
        body = got.as_json(["symbol_resolve"])
        assert (
            body["n"] == 2
            and body["passed"] == 1
            and body["cases"][0]["called"] == ["symbol_resolve"]
        )
        assert any("[1/2]" in line for line in lines)
