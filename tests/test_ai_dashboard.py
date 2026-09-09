"""T256 — 대시보드 명세: 참조 해석 · 리터럴 거부 · 모르는 부품 버림 · 표 채우기 · 에이전트 부착."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.port import ChatMessage, ChatOutcome, ChatReply, ToolCall, ToolSpec
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import run_chat
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.dashboard import lookup, resolve
from updown.orchestration.ai_chat.tools import ToolContext, find_tool

RESULTS: dict[str, Any] = {
    "market_view": {"symbol": "TSLA", "frames": {"1d": {"last": 316.22, "rsi14": 50.2}}},
    "screen": {
        "rows": [
            {"symbol": "PYPL", "metrics": {"per": {"value": 8.6}}},
            {"symbol": "HON", "metrics": {"per": {"value": 13.8}}},
        ]
    },
}


class TestLookup:
    def test_paths(self) -> None:
        assert lookup(RESULTS, "market_view.frames.1d.last") == (False, None), (
            "숫자로 시작하는 키는 문법 밖"
        )
        assert lookup(RESULTS, "market_view.symbol") == (True, "TSLA")
        assert lookup(RESULTS, "screen.rows[1].symbol") == (True, "HON")
        assert lookup(RESULTS, "screen.rows[9].symbol") == (False, None)
        assert lookup(RESULTS, "nope.x") == (False, None)
        assert lookup(RESULTS, "bad path!") == (False, None)


class TestResolve:
    def test_cards_table_and_refusals(self) -> None:
        spec: dict[str, Any] = {
            "title": "테슬라",
            "blocks": [
                {
                    "kind": "cards",
                    "items": [
                        {"label": "종목", "value": {"from": "market_view.symbol"}},
                        {"label": "지어낸 값", "value": 123},
                        {"label": "없는 참조", "value": {"from": "market_view.frames.x"}},
                    ],
                },
                {
                    "kind": "table",
                    "title": "PER 낮은 순",
                    "from": "screen.rows",
                    "columns": [
                        {"key": "symbol", "label": "종목"},
                        {"key": "metrics.per.value", "label": "PER"},
                    ],
                },
                {"kind": "hologram", "items": []},
                {"kind": "text", "text": "예상이 아니다"},
            ],
        }
        got = resolve(spec, RESULTS)
        cards = got.spec["blocks"][0]["items"]
        assert (
            cards[0]["value"] == "TSLA" and cards[1]["value"] is None and cards[2]["value"] is None
        )
        assert got.missing == ["market_view.frames.x"]
        assert any("리터럴 숫자" in d for d in got.dropped) and any(
            "hologram" in d for d in got.dropped
        )
        table = got.spec["blocks"][1]
        assert table["columns"][1]["label"] == "PER" and table["rows"] == [
            ["PYPL", 8.6],
            ["HON", 13.8],
        ]
        assert got.spec["blocks"][2]["kind"] == "text" and len(got.spec["blocks"]) == 3

    def test_missing_list_reference(self) -> None:
        got = resolve(
            {"blocks": [{"kind": "table", "from": "screen.nope", "columns": ["symbol"]}]}, RESULTS
        )
        assert got.missing == ["screen.nope"] and got.spec["blocks"][0]["rows"] == []


def _ctx() -> ToolContext:
    costs = load_cost_table(DEFAULT_CONFIG_PATH)
    return ToolContext(
        provider=MarketDataProvider(),
        live_markets=("NASDAQ", "BINANCE"),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
    )


class TestAgentAttachesDashboard:
    @pytest.mark.asyncio
    async def test_render_dashboard_uses_this_turn_results(self) -> None:
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
                if self.calls == 1:
                    return ChatReply(
                        model, "", (ToolCall("c1", "symbol_resolve", {"query": "테슬라"}),), 1, 1, 1
                    )
                if self.calls == 2:
                    spec: dict[str, Any] = {
                        "title": "테슬라 코드",
                        "blocks": [
                            {
                                "kind": "cards",
                                "items": [
                                    {
                                        "label": "코드",
                                        "value": {"from": "symbol_resolve.candidates[0].symbol"},
                                    },
                                    {"label": "없음", "value": {"from": "symbol_resolve.zzz"}},
                                ],
                            }
                        ],
                    }
                    return ChatReply(
                        model, "", (ToolCall("c2", "render_dashboard", {"spec": spec}),), 1, 1, 1
                    )
                return ChatReply(model, "답", (), 1, 1, 1)

        got = await run_chat([], "테슬라 코드", client=Client(), model="m", ctx=_ctx())
        assert got.dashboard is not None
        assert got.dashboard["blocks"][0]["items"][0]["value"] == "TSLA"
        assert got.dashboard_missing == ["symbol_resolve.zzz"]
        assert find_tool("render_dashboard") is not None
