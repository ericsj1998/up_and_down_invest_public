"""T248 3차 — 예산 추천 · 비중 분석 · 매매일지 도구(가짜 콜백) · 근거별 적중 · 봉 캐시 경로."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.report import AiTrade, journal_rows, reason_hits
from updown.orchestration.ai_chat.tools import TOOLS, ToolContext, find_tool

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _ctx(**extra: Any) -> ToolContext:
    costs = load_cost_table(DEFAULT_CONFIG_PATH)
    return ToolContext(
        provider=MarketDataProvider(),
        live_markets=("NASDAQ", "BINANCE"),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
        **extra,
    )


async def _candidates(group: str, tier: str) -> dict[str, Any]:
    return {
        "tier_label": tier,
        "market": "NASDAQ" if group == "foreign" else "BINANCE",
        "chosen": "a",
        "candidates": [
            {"id": "a", "label": "A", "recommended": False, "store": {"risk_tier_label": "균형"}},
            {"id": "b", "label": "B", "recommended": False, "store": {"risk_tier_label": "공격적"}},
        ],
    }


async def _ranking(market: str) -> dict[str, Any]:
    return {
        "rows": [{"symbol": "AAPL", "score": 70, "per": 20, "pbr": 5, "flags": []}],
        "note": market,
    }


class TestRecommendByBudget:
    @pytest.mark.asyncio
    async def test_chosen_alternatives_min_unit_and_value_candidates(self) -> None:
        tool = find_tool("recommend_by_budget")
        assert tool is not None
        got = await tool.run(
            {"budget": 2_000_000, "currency": "krw", "group": "foreign"},
            _ctx(candidates=_candidates, ranking=_ranking),
        )
        assert got["chosen"]["id"] == "a" and [a["id"] for a in got["alternatives"]] == ["b"]
        assert got["currency"] == "KRW" and "정수 주" in got["min_unit"]
        assert got["value_candidates"][0]["symbol"] == "AAPL" and "예상이 아니다" in got["note"]

    @pytest.mark.asyncio
    async def test_budget_required_and_coin_min_unit(self) -> None:
        tool = find_tool("recommend_by_budget")
        assert tool is not None
        with pytest.raises(ValueError):
            await tool.run({}, _ctx(candidates=_candidates))
        got = await tool.run({"budget": 100, "group": "coin"}, _ctx(candidates=_candidates))
        assert "50 USDT" in got["min_unit"] and "value_candidates" not in got


class TestPortfolioExposure:
    @pytest.mark.asyncio
    async def test_shares_warnings_and_opposite_pick(self) -> None:
        async def open_runs() -> list[dict[str, Any]]:
            return [
                {"market": "NASDAQ", "playbook_id": "b", "margin": "400", "meta": {"ai": {}}},
                {"market": "BINANCE", "playbook_id": "a", "margin": "100", "meta": {}},
            ]

        async def exchange_state(symbol: str, market: str) -> dict[str, Any]:
            del symbol
            del market
            return {"balance": {"total": "500"}}

        async def evidence(playbook: str) -> dict[str, Any]:
            return {"risk_tier": "aggressive" if playbook == "b" else "balanced"}

        tool = find_tool("portfolio_exposure")
        assert tool is not None
        got = await tool.run(
            {},
            _ctx(
                open_runs=open_runs,
                exchange_state=exchange_state,
                evidence=evidence,
                candidates=_candidates,
            ),
        )
        assert got["total"] == "1000" and got["by_group"]["foreign"]["share_pct"] == "40.0"
        assert got["ai_share_pct"] == "40.0"
        assert any("공격적" in w for w in got["warnings"])
        assert got["opposite_tier_pick"] == {"tier": "safe", "chosen": "a"}

    @pytest.mark.asyncio
    async def test_without_ledger_says_so(self) -> None:
        tool = find_tool("portfolio_exposure")
        assert tool is not None
        assert "없다" in (await tool.run({}, _ctx()))["note"]


class TestJournal:
    def test_reason_hits_and_rows(self) -> None:
        trades = [
            AiTrade(
                "k", T0, Decimal(2), Decimal(1), True, "목표 익절", "NVDA", ("RSI 30", "지지"), "r1"
            ),
            AiTrade(
                "k",
                T0 + timedelta(hours=1),
                Decimal(-1),
                Decimal(-1),
                False,
                "손절",
                "NVDA",
                ("RSI 30",),
                "r2",
            ),
        ]
        hits = reason_hits(trades)
        assert hits[0] == {"reason": "RSI 30", "n": 2, "wins": 1, "hit_rate": "50.00"}
        assert hits[1]["reason"] == "지지" and hits[1]["hit_rate"] == "100.00"
        rows = journal_rows(reversed(trades))
        assert [r["run_key"] for r in rows] == ["r1", "r2"] and rows[0]["reasons"] == [
            "RSI 30",
            "지지",
        ]

    @pytest.mark.asyncio
    async def test_tool_limits_rows(self) -> None:
        async def journal() -> dict[str, Any]:
            return {"n": 3, "rows": [{"i": 1}, {"i": 2}, {"i": 3}], "reason_hits": []}

        tool = find_tool("trade_journal")
        assert tool is not None
        got = await tool.run({"limit": 2}, _ctx(journal=journal))
        assert got["rows"] == [{"i": 2}, {"i": 3}] and got["n"] == 3


class TestRegistry:
    def test_new_tools_have_synonyms(self) -> None:
        names = {t.spec.name for t in TOOLS}
        assert {"recommend_by_budget", "portfolio_exposure", "trade_journal"} <= names
        for name in ("recommend_by_budget", "portfolio_exposure", "trade_journal"):
            tool = find_tool(name)
            assert tool is not None and "유사어" in tool.spec.description
