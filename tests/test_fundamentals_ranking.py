"""T244 — 저평가 후보 줄 세우기: 근거 한 줄 · 60일 모멘텀 · 정렬(재무 없음은 뒤)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from _fundamentals_fixtures import company
from updown.analysis.fundamentals.snapshot import build_snapshot
from updown.apps.api.fundamentals_rank import (
    RECOMMENDED,
    momentum,
    order_rows,
    ranking_row,
    why_line,
)
from updown.common.domain.fundamentals import load_fundamentals_config
from updown.marketdata.fundamentals.edgar import filings_of

CONFIG = load_fundamentals_config()
AS_OF = datetime(2026, 3, 15, tzinfo=UTC)


def _closes(days: int, *, start: Decimal = Decimal(10), step: Decimal = Decimal("0.1")):
    first = AS_OF.date() - timedelta(days=days)
    return [(first + timedelta(days=i), start + step * i) for i in range(days + 1)]


class TestMomentum:
    def test_sixty_day_return_and_short_history(self) -> None:
        closes = _closes(80)
        got = momentum(closes)
        assert got is not None
        assert got == closes[-1][1] / closes[-61][1] - 1
        assert momentum(closes[:50]) is None, "짧은 창으로 대신 재지 않는다"


class TestWhyLine:
    def test_mentions_cheapest_two_and_flags(self) -> None:
        facts = company(range(2019, 2026))
        table = dict(_closes(365 * 7))
        made = build_snapshot(
            facts, symbol="TEST", as_of=AS_OF, price_at=lambda d: table.get(d), config=CONFIG
        )
        line = why_line(made)
        assert "5년" in line and "부채 깃발 없음" in line
        assert line.count("·") == 2, "가격 지표 둘 + 깃발"

    def test_no_price_explains_itself(self) -> None:
        made = build_snapshot(
            company(range(2022, 2026)),
            symbol="TEST",
            as_of=AS_OF,
            price_at=lambda _: None,
            config=CONFIG,
        )
        assert "시세 없음" in why_line(made)


class TestRows:
    def test_row_shape_and_order(self) -> None:
        facts = company(range(2019, 2026))
        closes = _closes(365 * 7)
        table = dict(closes)
        made = build_snapshot(
            facts, symbol="TEST", as_of=AS_OF, price_at=lambda d: table.get(d), config=CONFIG
        )
        scored = ranking_row(
            made, broker="toss", filings=filings_of(facts), closes=closes, has_facts=True
        )
        assert scored["score"] is not None and scored["broker"] == "toss"
        assert scored["latest_filing"] and scored["latest_filing"]["url"]
        assert scored["momentum_60d"] is not None and "per" in scored["metrics"]
        assert isinstance(scored["price"], str)

        empty = build_snapshot(
            [], symbol="NONE", as_of=AS_OF, price_at=lambda d: table.get(d), config=CONFIG
        )
        no_facts = ranking_row(empty, broker="toss", filings=[], closes=closes, has_facts=False)
        assert no_facts["score"] is None and "재무 없음" in no_facts["why"]

        unpriced = build_snapshot(
            facts, symbol="ZZZ", as_of=AS_OF, price_at=lambda _: None, config=CONFIG
        )
        facts_no_score = ranking_row(
            unpriced, broker="toss", filings=filings_of(facts), closes=[], has_facts=True
        )
        better = dict(scored, symbol="AAA", score=99.0)
        ordered = order_rows([no_facts, facts_no_score, scored, better])
        assert [r["symbol"] for r in ordered] == ["AAA", "TEST", "ZZZ", "NONE"]
        assert RECOMMENDED is False


def test_fixture_dates_are_utc() -> None:
    assert AS_OF.tzinfo is UTC and isinstance(AS_OF.date(), date)
