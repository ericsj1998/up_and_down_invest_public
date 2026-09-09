"""T255 — frames 기간 이름 · CIK 별 최신 값 · 빠른 지표 · 스크리닝 필터/정렬/페이지."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from updown.analysis.fundamentals.quick import (
    annual_periods,
    instant_periods,
    quick_metrics,
    values_by_cik,
)
from updown.apps.api.fundamentals_rank import ScreenQuery, screen_rows


class TestPeriods:
    def test_annual_and_instant(self) -> None:
        assert annual_periods(date(2026, 9, 10)) == ["CY2025", "CY2024"]
        assert instant_periods(date(2026, 9, 10)) == ["CY2026Q2I", "CY2026Q1I", "CY2025Q4I"]
        assert instant_periods(date(2026, 1, 15), 2) == ["CY2025Q4I", "CY2025Q3I"]


class TestValuesByCik:
    def test_latest_filed_wins_and_bad_rows_skipped(self) -> None:
        frame = {
            "data": [
                {"cik": 320193, "val": 100, "filed": "2026-01-01"},
                {"cik": 320193, "val": 120, "filed": "2026-05-01"},
                {"cik": 1045810, "val": None},
                {"cik": 789019, "val": "x"},
                "junk",
            ]
        }
        assert values_by_cik(frame) == {"0000320193": Decimal(120)}
        assert values_by_cik({"data": "nope"}) == {}


class TestQuickMetrics:
    def test_ratios_and_refusals(self) -> None:
        got = quick_metrics(
            price=Decimal(100),
            shares=Decimal(10),
            revenue=Decimal(500),
            net_income=Decimal(50),
            equity=Decimal(200),
            periods={"revenue": "CY2025"},
        )
        assert got.market_cap == Decimal(1000) and got.per == Decimal(20)
        assert got.pbr == Decimal(5) and got.psr == Decimal(2)
        assert got.as_json()["metrics"]["per"] == {"value": 20.0, "percentile": None}
        loss = quick_metrics(
            price=Decimal(100),
            shares=Decimal(10),
            revenue=None,
            net_income=Decimal(-5),
            equity=Decimal(0),
        )
        assert loss.per is None and loss.pbr is None and loss.psr is None


def _row(
    symbol: str,
    score: float | None,
    per: float | None,
    flags: list[str] | None = None,
    stage: str = "history",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "has_facts": stage == "history",
        "stage": stage,
        "score": score,
        "flags": flags or [],
        "metrics": {"per": {"value": per, "percentile": None}},
        "momentum_60d": None,
        "market_cap": None,
    }


ROWS = [
    _row("A", 90, 10),
    _row("B", 40, 30, ["부채비율 높음"]),
    _row("C", None, 15, stage="quick"),
    _row("D", 70, None),
    _row("E", 10, 50),
]


class TestScreenRows:
    def test_filter_sort_page(self) -> None:
        got = screen_rows(
            ROWS, ScreenQuery(min_score=30, no_flags=True, sort="score", page=1, size=2)
        )
        assert [r["symbol"] for r in got["rows"]] == ["A", "D"] and got["total"] == 2
        got = screen_rows(ROWS, ScreenQuery(sort="per", order="asc", size=10))
        assert [r["symbol"] for r in got["rows"]][:3] == ["A", "C", "B"], "값 없는 것은 뒤"
        got = screen_rows(ROWS, ScreenQuery(has_facts=True, size=10))
        assert all(r["stage"] == "history" for r in got["rows"]) and got["total"] == 4
        got = screen_rows(ROWS, ScreenQuery(q="c", size=10))
        assert [r["symbol"] for r in got["rows"]] == ["C"]

    def test_page_bounds(self) -> None:
        got = screen_rows(ROWS, ScreenQuery(size=2, page=3))
        assert got["page"] == 3 and len(got["rows"]) == 1 and got["pages"] == 3
        got = screen_rows(ROWS, ScreenQuery(size=2, page=99))
        assert got["page"] == 3 and len(got["rows"]) == 1, "넘는 쪽은 마지막 쪽"
        got = screen_rows(ROWS, ScreenQuery(size=500))
        assert got["size"] == 50, "쪽 크기 상한"
