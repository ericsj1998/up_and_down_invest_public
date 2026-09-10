"""저평가 후보 범위(전체 · SP 500 · 시장) + 이름표 별칭 + EDGAR 종류주 표기.

2026-09-10 사용자 요청.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from updown.apps.api import fundamentals as api
from updown.common.domain.instrument import Market
from updown.marketdata.fundamentals.client import EdgarClient
from updown.marketdata.fundamentals.edgar import EdgarAdapter
from updown.orchestration.ai_chat.aliases import load_aliases, parse_aliases, parse_names

ROOT = Path(__file__).resolve().parent.parent


class TestScope:
    """`market` 하나가 세 가지 범위를 뜻한다."""

    def test_all_and_sp500_cover_every_fundamentals_market(self) -> None:
        key, markets, with_candidates = api.scope_of("ALL")
        assert key == "ALL"
        assert markets == [Market.NASDAQ, Market.NYSE]
        assert with_candidates
        key, markets, with_candidates = api.scope_of("sp500")
        assert key == "SP500"
        assert with_candidates

    def test_single_market_is_itself(self) -> None:
        assert api.scope_of("NYSE") == ("NYSE", [Market.NYSE], False)

    def test_unknown_or_no_fundamentals_is_400(self) -> None:
        with pytest.raises(HTTPException) as caught:
            api.scope_of("KRX")
        assert caught.value.status_code == 400
        with pytest.raises(HTTPException):
            api.scope_of("MOON")

    def test_candidates_and_names_files_agree(self) -> None:
        candidates = api.candidates_of()
        names = api.names_of()
        assert len(candidates) > 400
        assert "ORCL" in candidates
        assert names["ORCL"]["ko"] == "오라클"
        assert names["ORCL"]["market"] == "NYSE"
        # 이름표는 후보 전부를 안다 — 시장을 모르는 종목은 AMEX 뿐이다.
        missing = [s for s in candidates if s not in names]
        assert not missing, missing[:5]
        assert {n["market"] for n in names.values()} <= {"NASDAQ", "NYSE", "AMEX"}


class TestNamesSheet:
    """토스 이름표가 두 번째 사전이다 — 손 별칭이 이긴다."""

    def test_korean_and_english_names_resolve(self) -> None:
        entries = parse_names(
            {
                "ORCL": {"ko": "오라클", "en": "Oracle", "market": "NYSE"},
                "005930": {"ko": "삼성전자", "en": "SamsungElec", "market": "KOSPI"},
                "XYZ": {"ko": "", "en": "", "market": "AMEX"},
            }
        )
        assert entries["오라클"] == ("ORCL", "foreign", "오라클")
        assert entries["oracle"] == ("ORCL", "foreign", "오라클")
        assert entries["orcl"] == ("ORCL", "foreign", "오라클")
        assert entries["삼성전자"] == ("005930", "domestic", "삼성전자")
        assert entries["xyz"] == ("XYZ", "foreign", "XYZ")

    def test_hand_aliases_win_over_the_sheet(self, tmp_path: Path) -> None:
        names = tmp_path / "names.yml"
        names.write_text(
            'AAPL: {ko: "애플 컴퓨터", en: "Apple", market: NASDAQ}\n', encoding="utf-8"
        )
        hand = tmp_path / "aliases.yml"
        hand.write_text("stocks:\n  AAPL: [애플, apple]\n", encoding="utf-8")
        book = load_aliases(hand, names)
        assert book.resolve("애플")[0].name == "애플"
        assert book.resolve("애플 컴퓨터")[0].symbol == "AAPL"

    def test_real_sheet_resolves_oracle_the_way_the_user_asked(self) -> None:
        book = load_aliases()
        found = book.resolve("오라클 종목")
        assert found and found[0].symbol == "ORCL"
        assert book.resolve("Oracle")[0].symbol == "ORCL"
        # 한 글자 종목(A · J …)이 긴 질문 안에 "들어 있다" 고 잡히면 안 된다
        # (2026-09-10 실측: JPMorgan → A)
        assert [r.symbol for r in book.resolve("JPMorgan")] == ["JPM"]
        assert book.resolve("A")[0].symbol == "A"
        # 손 별칭은 그대로
        assert parse_aliases({"stocks": {"AAPL": ["애플"]}}).resolve("애플")[0].symbol == "AAPL"


class TestEdgarClassShares:
    """`BRK.B` 는 EDGAR 에서 `BRK-B` 다."""

    @pytest.mark.asyncio
    async def test_dot_is_retried_as_dash(self) -> None:
        asked: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            if "company_tickers" in request.url.path:
                return httpx.Response(403, text="blocked")
            typed = request.url.params.get("keysTyped", "")
            asked.append(typed)
            if typed == "BRK-B":
                return httpx.Response(
                    200,
                    json={
                        "hits": {
                            "hits": [{"_id": "1067983", "_source": {"tickers": "BRK-B, BRK-A"}}]
                        }
                    },
                )
            return httpx.Response(200, json={"hits": {"hits": []}})

        client = EdgarClient(
            "Test test@example.com", transport=httpx.MockTransport(handle), max_retries=0
        )
        adapter = EdgarAdapter(client, api.load_fundamentals_config())
        try:
            assert await adapter.cik_of("BRK.B") == "0001067983"
        finally:
            await adapter.aclose()
        assert asked == ["BRK.B", "BRK-B"]
