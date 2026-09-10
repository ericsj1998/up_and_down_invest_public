"""위저드-인-채팅 (T271) — 카드 · 막는 이유 · 답 합치기 · 도구."""

from __future__ import annotations

from typing import Any

import pytest

from updown.apps.api import assistant as assistant_api
from updown.orchestration.ai_chat import wizard
from updown.orchestration.ai_chat.tools import find_tool
from updown.orchestration.ai_chat.wizard import (
    STEPS,
    action_line,
    blockers,
    card_for,
    merge_answers,
    next_step,
    prev_step,
    text_for,
)

PREVIEW: dict[str, Any] = {
    "market": "NASDAQ",
    "chosen": "b",
    "note": "숫자는 과거 창 실측이다.",
    "candidates": [
        {
            "id": "a",
            "label": "A 매매법",
            "recommended": False,
            "store": {"years": 1.7, "total_pct": -3.8, "mdd_pct": 5.0, "trades_count": 68},
        },
        {"id": "b", "label": "B 매매법", "recommended": True, "store": None},
    ],
}


class TestSteps:
    def test_steps_match_the_api(self) -> None:
        assert STEPS == assistant_api.STEPS
        assert next_step("consent") == "capital"
        assert next_step("done") == "done"
        assert prev_step("consent") == "consent"
        assert prev_step("review") == "setup"

    def test_blockers_follow_the_web_rules(self) -> None:
        assert blockers("consent", {}, consented=False) == ["동의가 필요하다"]
        assert blockers("consent", {}, consented=True) == []
        assert blockers("capital", {"capital": 0}, consented=True) == [
            "시작 금액은 0 보다 커야 한다"
        ]
        assert blockers("profile", {"group": "moon"}, consented=True) == [
            "종목 갈래를 고른다",
            "성향을 고른다",
        ]
        assert blockers("setup", {}, consented=True) == ["매매법을 고른다"]
        assert blockers("setup", {"playbook": "a"}, consented=True) == []

    def test_merge_keeps_known_keys_and_coerces_numbers(self) -> None:
        got = merge_answers(
            {"group": "coin", "junk": 1},
            {"capital": "2000000", "years": "3", "tier": "safe", "made_up": "x", "label": ""},
        )
        assert got == {"group": "coin", "capital": 2000000.0, "years": 3.0, "tier": "safe"}


class TestCards:
    def test_consent_card_switches_button_after_consent(self) -> None:
        before = card_for(
            "consent", {}, consented=False, disclaimer_text="문구", disclaimer_version="v3"
        )
        after = card_for(
            "consent", {}, consented=True, disclaimer_text="문구", disclaimer_version="v3"
        )
        assert before["actions"][0]["action"] == "consent"
        assert after["actions"][0]["action"] == "next"
        assert before["version"] == "v3"
        assert "v3" in text_for(before)

    def test_setup_card_lists_candidates_with_default_and_selection(self) -> None:
        card = card_for(
            "setup",
            {"group": "foreign", "tier": "balanced"},
            consented=True,
            disclaimer_text="",
            disclaimer_version="v3",
            preview=PREVIEW,
        )
        opts = {o["value"]: o for o in card["options"]}
        assert opts["b"]["default"] and opts["b"]["selected"] and opts["b"]["recommended"]
        assert "손익 -3.8%" in opts["a"]["hint"] and "MDD +5.0%" in opts["a"]["hint"]
        assert opts["b"]["hint"] == "저장소 없음"
        assert "NASDAQ" in card["text"]
        chosen = card_for(
            "setup",
            {"playbook": "a"},
            consented=True,
            disclaimer_text="",
            disclaimer_version="v3",
            preview=PREVIEW,
        )
        assert {o["value"]: o["selected"] for o in chosen["options"]} == {"a": True, "b": False}

    def test_review_card_summarises_and_asks_confirmation(self) -> None:
        answers = {
            "capital": 2000000.0,
            "contribution": 100000.0,
            "cadence": "month",
            "years": 3.0,
            "group": "foreign",
            "tier": "balanced",
            "playbook": "a",
        }
        card = card_for(
            "review",
            answers,
            consented=True,
            disclaimer_text="",
            disclaimer_version="v3",
            preview=PREVIEW,
        )
        assert card["summary"][0].startswith("시작 금액 2,000,000")
        assert "미국주식" in card["summary"][1] and "균형 투자" in card["summary"][1]
        assert "A 매매법" in card["summary"][2] and "NASDAQ" in card["summary"][2]
        create = next(a for a in card["actions"] if a["action"] == "create")
        assert create["confirm"] is True
        assert "[5/5 검토]" in text_for(card)

    def test_done_card_names_the_fund(self) -> None:
        card = card_for(
            "done",
            {},
            consented=True,
            disclaimer_text="",
            disclaimer_version="v3",
            fund_id="fund_x",
        )
        assert "fund_x" in card["text"] and card["fund_id"] == "fund_x"

    def test_action_lines_read_like_a_person(self) -> None:
        assert action_line("consent", "consent", {}) == "동의: 동의함"
        assert (
            action_line("next", "profile", {"group": "coin", "tier": "safe"})
            == "성향: 갈래=코인 · 성향=안전 투자 → 다음"
        )
        assert (
            action_line("next", "capital", {"capital": 2000000.0, "years": 2.0})
            == "자본: 시작 금액 2,000,000 · 2년 → 다음"
        )
        assert action_line("create", "review", {}) == "검토: 펀드 만들기"


class TestTool:
    @pytest.mark.asyncio
    async def test_tool_returns_the_card_from_the_api_callback(self) -> None:
        from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
        from updown.common.domain.instrument import Market
        from updown.decision.risk.policy import load_settings as load_risk_settings
        from updown.marketdata.provider import MarketDataProvider
        from updown.orchestration.ai_chat.aliases import load_aliases
        from updown.orchestration.ai_chat.tools import ToolContext

        seen: list[str] = []

        async def fake_wizard(action: str) -> dict[str, Any]:
            seen.append(action)
            return {
                "card": card_for(
                    "consent", {}, consented=False, disclaimer_text="문구", disclaimer_version="v3"
                ),
                "step": "consent",
            }

        costs = load_cost_table(DEFAULT_CONFIG_PATH)
        ctx = ToolContext(
            provider=MarketDataProvider(),
            live_markets=("NASDAQ",),
            aliases=load_aliases(),
            risk=load_risk_settings(),
            round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
            wizard=fake_wizard,
        )
        tool = find_tool("profile_wizard")
        assert tool is not None
        got = await tool.run({"action": "start"}, ctx)
        assert seen == ["start"]
        assert got["card"]["kind"] == "wizard" and got["step"] == "consent"
        assert "카드" in got["note"]
        bare = await tool.run(
            {},
            ToolContext(
                provider=ctx.provider,
                live_markets=ctx.live_markets,
                aliases=ctx.aliases,
                risk=ctx.risk,
                round_trip=ctx.round_trip,
            ),
        )
        assert "note" in bare and "card" not in bare

    def test_module_is_pure(self) -> None:
        import inspect

        source = inspect.getsource(wizard)
        assert "apps.api" not in source and "sqlalchemy" not in source
