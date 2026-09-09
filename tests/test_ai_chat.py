"""T248 — 요약 스냅샷 · 별칭 사전 · 도구 명세 · 에이전트 루프(가짜 모델) · 주문 제안 확정."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.nvidia import message_json
from updown.llm.port import (
    ChatMessage,
    ChatOutcome,
    ChatReply,
    FailureKind,
    LlmFailure,
    ToolCall,
    ToolSpec,
)
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import PROMPT_VERSION, run_chat
from updown.orchestration.ai_chat.aliases import load_aliases, parse_aliases
from updown.orchestration.ai_chat.snapshot import compress, extremes_of, summarize_frame
from updown.orchestration.ai_chat.tools import (
    TOOLS,
    ToolContext,
    coin_symbol,
    find_tool,
    market_for,
    result_text,
)

NVDA = Instrument(Market.NASDAQ, "NVDA", "NVDA", AssetType.STOCK, Currency.USD)


def _candles(
    n: int, *, start: Decimal = Decimal(100), step: Decimal = Decimal("0.5")
) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(n):
        price = start + step * i
        out.append(
            Candle(
                instrument=NVDA,
                timeframe=Timeframe.D1,
                ts=base + timedelta(days=i),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + Decimal("0.2"),
                volume=Decimal(1000),
            )
        )
    return out


class TestSnapshot:
    def test_summarize_frame_has_indicators_and_compressed_ohlc(self) -> None:
        got = summarize_frame(_candles(400), "1d")
        assert got["bars"] == 60 and got["last"] is not None
        assert got["sma20"] is not None and got["sma200"] is not None
        assert got["to_sma200_pct"] is not None and got["rsi14"] is not None
        assert len(got["ohlc"]) == 12 and got["ohlc"][-1]["close"] == got["last"]
        assert got["change_pct"] is not None and got["to_high_pct"] is not None

    def test_compress_and_empty(self) -> None:
        assert compress([]) == []
        assert summarize_frame([], "1h")["bars"] == 0

    def test_extremes_flags_near_high(self) -> None:
        got = extremes_of(_candles(300))
        assert got["days"] == 252 and got["to_high_52w_pct"] is not None
        assert any("52주 고점" in f for f in got["flags"])


class TestAliases:
    BOOK = parse_aliases(
        {"stocks": {"TSLA": ["테슬라"], "AAPL": ["애플"]}, "coins": {"BTC": ["비트코인", "비트"]}}
    )

    def test_exact_contains_fuzzy(self) -> None:
        assert self.BOOK.resolve("테슬라")[0].symbol == "TSLA"
        assert self.BOOK.resolve("테슬라 주가")[0].symbol == "TSLA"
        assert self.BOOK.resolve("aapl")[0].confidence == 1.0
        fuzzy = self.BOOK.resolve("비트코이")
        assert fuzzy and fuzzy[0].symbol == "BTC" and fuzzy[0].confidence < 1.0
        assert self.BOOK.resolve("없는이름") == []

    def test_repo_dictionary_loads(self) -> None:
        book = load_aliases()
        assert book.resolve("삼전")[0].symbol == "005930"

    def test_coin_symbol_and_market_for(self) -> None:
        assert (
            coin_symbol("BTC", Market.GATE) == "BTC_USDT"
            and coin_symbol("BTC", Market.BINANCE) == "BTCUSDT"
        )
        assert market_for("coin", ("NASDAQ", "BINANCE")) is Market.BINANCE
        assert market_for("foreign", ("NASDAQ", "BINANCE")) is Market.NASDAQ
        assert market_for("domestic", ("NASDAQ",)) is None


class TestToolSpecs:
    def test_every_tool_has_korean_description_with_synonyms(self) -> None:
        for tool in TOOLS:
            assert "유사어" in tool.spec.description, tool.spec.name
            assert tool.spec.parameters["type"] == "object"
        assert find_tool("propose_order") is not None and find_tool("nope") is None

    def test_result_text_truncates(self) -> None:
        assert len(result_text({"x": "a" * 10_000})) < 6_200


class FakeClient:
    """첫 턴은 도구 호출, 둘째 턴은 최종 답."""

    def __init__(self) -> None:
        self.turns = 0
        self.seen: list[list[ChatMessage]] = []

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
        self.turns += 1
        self.seen.append(list(messages))
        if self.turns == 1:
            return ChatReply(
                model, "", (ToolCall("c1", "symbol_resolve", {"query": "테슬라"}),), 10, 100, 5
            )
        return ChatReply(model, "TSLA 는 미국주식이다. 근거: symbol_resolve", (), 12, 120, 30)


def _ctx() -> ToolContext:
    costs = load_cost_table(DEFAULT_CONFIG_PATH)
    return ToolContext(
        provider=MarketDataProvider(),
        live_markets=("NASDAQ", "BINANCE"),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
    )


class TestAgent:
    @pytest.mark.asyncio
    async def test_loop_calls_tool_then_answers(self) -> None:
        client = FakeClient()
        lines: list[str] = []
        got = await run_chat(
            [], "테슬라 어때", client=client, model="m", ctx=_ctx(), report=lines.append
        )
        assert got.rounds == 2 and got.text.startswith("TSLA")
        assert [e.name for e in got.tool_events] == ["symbol_resolve"] and got.tool_events[0].ok
        assert got.prompt_tokens == 220 and got.completion_tokens == 35
        roles = [m.role for m in client.seen[1]]
        assert roles[0] == "system" and roles[-2:] == ["assistant", "tool"]
        assert any("도구 symbol_resolve" in line for line in lines)
        assert got.messages[0].role == "user" and got.messages[-1].role == "assistant"
        assert PROMPT_VERSION.startswith("chat-")

    @pytest.mark.asyncio
    async def test_model_failure_is_a_value(self) -> None:
        class Failing:
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
                return LlmFailure(model, FailureKind.TIMEOUT, "느리다", 5)

        got = await run_chat([], "x", client=Failing(), model="m", ctx=_ctx())
        assert got.failure and "TIMEOUT" in got.failure and "답하지 못했다" in got.text

    @pytest.mark.asyncio
    async def test_retired_model_falls_back_to_next(self) -> None:
        class Retired:
            def __init__(self) -> None:
                self.models: list[str] = []

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
                self.models.append(model)
                if model == "old":
                    return LlmFailure(model, FailureKind.UNKNOWN_MODEL, "410 Gone", 3)
                return ChatReply(model, "답", (), 5, 1, 1)

        client = Retired()
        got = await run_chat([], "x", client=client, model="old", ctx=_ctx(), fallbacks=["new"])
        assert client.models == ["old", "new"] and got.model == "new" and got.failure is None

    @pytest.mark.asyncio
    async def test_propose_order_confirms_and_never_places(self) -> None:
        tool = find_tool("propose_order")
        assert tool is not None
        got = await tool.run(
            {
                "symbol": "NVDA",
                "market": "NASDAQ",
                "entry": 224.3,
                "stop": 217.5,
                "target": 233.9,
                "short": True,
            },
            _ctx(),
        )
        assert got["ok"] is False and "숏" in got["blocked"][0], "주식은 매수만"
        got = await tool.run(
            {"symbol": "NVDA", "market": "NASDAQ", "entry": 224.3, "stop": 217.5, "target": 233.9},
            _ctx(),
        )
        assert got["ok"] is True and got["leverage"] == "1" and got["group"] == "foreign"
        assert "제안" in got["note"] and Decimal(got["stop"]) <= Decimal("217.5")


class TestMessageJson:
    def test_tool_call_round_trip_shape(self) -> None:
        call = ToolCall("c1", "market_view", {"symbol": "TSLA"})
        made: dict[str, Any] = message_json(ChatMessage("assistant", "", tool_calls=(call,)))
        assert made["tool_calls"][0]["function"]["name"] == "market_view"
        assert message_json(ChatMessage("tool", "{}", tool_call_id="c1"))["tool_call_id"] == "c1"


class TestPool:
    def test_pool_chat_block_is_read(self) -> None:
        from updown.llm.pool import load_pool

        pool = load_pool()
        assert pool.chat_model and pool.chat_timeout_seconds > 0


class TestAuto:
    def test_gate_order_and_reasons(self) -> None:
        from updown.common.security.consent import AUTO_ORDER_CONSENT_VERSION
        from updown.orchestration.ai_chat.auto import AutoState, AutoVerdict, auto_allowed

        on = AutoState(enabled=True, consent_version=AUTO_ORDER_CONSENT_VERSION)

        def gate(
            state: AutoState = on,
            *,
            placed_today: int = 0,
            new_margin: Decimal = Decimal(100),
            proposal_ok: bool = True,
        ) -> AutoVerdict:
            return auto_allowed(
                state,
                current_version=AUTO_ORDER_CONSENT_VERSION,
                placed_today=placed_today,
                exposure_now=Decimal(0),
                new_margin=new_margin,
                total=Decimal(1000),
                proposal_ok=proposal_ok,
                market_allowed=True,
                playbook_allowed=True,
            )

        assert gate().ok
        assert "꺼져" in gate(AutoState(False, None)).why
        assert "동의" in gate(AutoState(True, "old")).why
        assert "상한 3" in gate(placed_today=3).why
        assert "노출" in gate(new_margin=Decimal(400)).why
        assert "막은" in gate(proposal_ok=False).why

    def test_consent_pair_matches_screen(self) -> None:
        import re
        from pathlib import Path

        from updown.common.security.consent import (
            AUTO_ORDER_CONSENT_TEXT,
            AUTO_ORDER_CONSENT_VERSION,
        )

        source = (Path(__file__).resolve().parent.parent / "web/src/shell/disclaimer.ts").read_text(
            encoding="utf-8"
        )
        assert (
            re.search(r'AUTO_ORDER_CONSENT_VERSION = "([^"]+)"', source).group(1)
            == AUTO_ORDER_CONSENT_VERSION
        )  # type: ignore[union-attr]
        assert AUTO_ORDER_CONSENT_TEXT in source.replace("\n", "")

    def test_actor_ai_exists_and_session_buy_takes_actor(self) -> None:
        import inspect

        from updown.orchestration.walkforward.ledger import Actor
        from updown.orchestration.walkforward.session import Session

        assert Actor("AI") is Actor.AI
        assert "actor" in inspect.signature(Session.buy).parameters
