"""T243 — 설정 · EDGAR 매핑 · 시점 정합 · 분기 복원 · TTM."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from _fundamentals_fixtures import company, fact
from updown.analysis.fundamentals.series import (
    AVAILABILITY_LAG,
    annual_flows,
    known_facts,
    latest_instant,
    quarterly_flows,
    ttm,
)
from updown.common.domain.fundamentals import (
    FactKind,
    FundamentalsConfigError,
    load_fundamentals_config,
    parse_fundamentals_config,
)
from updown.marketdata.fundamentals.mapping import parse_company_facts

CONFIG = load_fundamentals_config()


class TestConfig:
    def test_repo_config_loads_and_has_core_concepts(self) -> None:
        for name in ("revenue", "net_income", "equity", "shares_outstanding", "long_term_debt"):
            assert name in CONFIG.concepts
        assert CONFIG.concepts["revenue"].kind is FactKind.FLOW
        assert CONFIG.concepts["equity"].kind is FactKind.INSTANT
        assert CONFIG.concepts["shares_outstanding"].tags[0].startswith("dei:")
        assert CONFIG.score.debt.debt_to_equity_max == Decimal("2.0")

    def test_bad_tag_and_missing_score_are_loud(self) -> None:
        with pytest.raises(FundamentalsConfigError, match="taxonomy:Tag"):
            parse_fundamentals_config(
                {
                    "concepts": {"x": {"kind": "flow", "unit": "USD", "tags": ["Revenues"]}},
                    "score": {},
                }
            )
        with pytest.raises(FundamentalsConfigError, match="score"):
            parse_fundamentals_config(
                {"concepts": {"x": {"kind": "flow", "unit": "USD", "tags": ["us-gaap:Revenues"]}}}
            )

    def test_missing_file_is_loud(self, tmp_path: Path) -> None:
        with pytest.raises(FundamentalsConfigError, match="없다"):
            load_fundamentals_config(tmp_path / "nope.yml")


def _companyfacts() -> dict[str, Any]:
    def row(
        start: str | None, end: str, val: float, accn: str, filed: str, fp: str, form: str
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "end": end,
            "val": val,
            "accn": accn,
            "fy": 2020,
            "fp": fp,
            "form": form,
            "filed": filed,
        }
        if start is not None:
            out["start"] = start
        return out

    return {
        "cik": 320193,
        "entityName": "Test Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            row(
                                "2019-01-01", "2019-12-31", 400, "A-19", "2020-02-28", "FY", "10-K"
                            ),
                            # 같은 기간이 두 태그로 — 앞 태그(Revenues)가 이긴다
                            row(
                                "2020-01-01",
                                "2020-03-31",
                                100,
                                "A-20-1",
                                "2020-05-10",
                                "Q1",
                                "10-Q",
                            ),
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            row(
                                "2020-01-01",
                                "2020-03-31",
                                999,
                                "A-20-1",
                                "2020-05-10",
                                "Q1",
                                "10-Q",
                            ),
                            row(
                                "2020-04-01",
                                "2020-06-30",
                                110,
                                "A-20-2",
                                "2020-08-10",
                                "Q2",
                                "10-Q",
                            ),
                            # 단위가 다르면 버린다
                        ],
                        "EUR": [
                            row("2020-04-01", "2020-06-30", 1, "A-20-2", "2020-08-10", "Q2", "10-Q")
                        ],
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [row(None, "2020-06-30", 5000, "A-20-2", "2020-08-10", "Q2", "10-Q")]
                    }
                },
                # 기간 값인데 30일짜리 조각 — 버린다
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            row("2020-06-01", "2020-06-30", 5, "A-20-2", "2020-08-10", "Q2", "10-Q")
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            row(None, "2020-07-15", 16000000, "A-20-2", "2020-08-10", "Q2", "10-Q")
                        ]
                    }
                }
            },
        },
    }


class TestMapping:
    def test_parse_company_facts_maps_tags_units_and_fallback_order(self) -> None:
        facts = parse_company_facts(_companyfacts(), symbol="TEST", config=CONFIG)
        by = {(f.concept, f.period_end): f for f in facts}
        assert by[("revenue", date(2020, 3, 31))].value == Decimal(100), "앞 태그가 이긴다"
        assert by[("revenue", date(2020, 3, 31))].tag == "us-gaap:Revenues"
        assert by[("revenue", date(2020, 6, 30))].value == Decimal(110), (
            "다른 기간은 폴백 태그로 이어진다"
        )
        assert by[("total_assets", date(2020, 6, 30))].kind is FactKind.INSTANT
        shares = by[("shares_outstanding", date(2020, 7, 15))]
        assert shares.unit == "shares" and shares.tag.startswith("dei:")
        assert ("net_income", date(2020, 6, 30)) not in by, "30일 조각은 버린다"
        assert all(f.entity_id == "0000320193" for f in facts)
        assert by[("revenue", date(2019, 12, 31))].filed_at == datetime(2020, 2, 28, tzinfo=UTC)


class TestPointInTime:
    def test_known_facts_applies_one_day_lag(self) -> None:
        one = fact(
            "revenue",
            date(2025, 1, 1),
            date(2025, 3, 31),
            Decimal(1),
            filed=date(2025, 5, 10),
            accession="X",
            form="10-Q",
            fy=2025,
            fp="Q1",
        )
        assert AVAILABILITY_LAG.days == 1
        assert not known_facts([one], datetime(2025, 5, 10, 12, tzinfo=UTC)), (
            "공시 당일은 아직 모른다"
        )
        assert known_facts([one], datetime(2025, 5, 11, tzinfo=UTC)) == [one]

    def test_latest_instant_prefers_latest_period_then_latest_filing(self) -> None:
        early = fact(
            "equity",
            date(2024, 12, 31),
            date(2024, 12, 31),
            Decimal(200),
            filed=date(2025, 3, 1),
            accession="K24",
            form="10-K",
            fy=2024,
            fp="FY",
        )
        restated = fact(
            "equity",
            date(2024, 12, 31),
            date(2024, 12, 31),
            Decimal(210),
            filed=date(2026, 3, 1),
            accession="K25",
            form="10-K",
            fy=2025,
            fp="FY",
        )
        found = latest_instant([early, restated], "equity")
        assert found is not None and found.value == Decimal(210), "정정(늦은 공시)이 이긴다"


class TestQuarterRecovery:
    def test_q4_is_fy_minus_three_quarters(self) -> None:
        facts = company(range(2023, 2025), scale={2024: Decimal(2)})
        quarters = quarterly_flows(facts, "revenue")
        ends = [q.period_end for q in quarters]
        assert date(2023, 12, 31) in ends and date(2024, 12, 31) in ends, "Q4 가 복원된다"
        q4 = next(q for q in quarters if q.period_end == date(2024, 12, 31))
        assert q4.value == Decimal(200), "FY 800 - (200+200+200)"
        assert len(q4.sources) == 4, "출처 = 10-K + 3개 10-Q"

    def test_ytd_difference_recovers_quarter(self) -> None:
        # 분기 값 없이 누적(6개월 · 9개월)만 낸 회사
        h1 = fact(
            "revenue",
            date(2024, 1, 1),
            date(2024, 6, 30),
            Decimal(230),
            filed=date(2024, 8, 9),
            accession="Q2",
            form="10-Q",
            fy=2024,
            fp="Q2",
        )
        q1 = fact(
            "revenue",
            date(2024, 1, 1),
            date(2024, 3, 31),
            Decimal(100),
            filed=date(2024, 5, 9),
            accession="Q1",
            form="10-Q",
            fy=2024,
            fp="Q1",
        )
        nine = fact(
            "revenue",
            date(2024, 1, 1),
            date(2024, 9, 30),
            Decimal(370),
            filed=date(2024, 11, 9),
            accession="Q3",
            form="10-Q",
            fy=2024,
            fp="Q3",
        )
        quarters = {q.period_end: q.value for q in quarterly_flows([h1, q1, nine], "revenue")}
        assert quarters[date(2024, 6, 30)] == Decimal(130)
        assert quarters[date(2024, 9, 30)] == Decimal(140)

    def test_ttm_requires_four_contiguous_quarters(self) -> None:
        facts = company(range(2024, 2025))  # Q1~Q3 + FY → Q4 복원 → 4분기
        trailing = ttm(quarterly_flows(facts, "revenue"))
        assert trailing is not None and trailing.value == Decimal(400)
        assert ttm(quarterly_flows(facts, "revenue")[:3]) is None, "3분기는 TTM 이 아니다"
        # 사이가 빈 4분기
        gap = [
            q
            for q in quarterly_flows(company(range(2023, 2025)), "revenue")
            if q.period_end != date(2024, 6, 30)
        ]
        assert ttm(gap[-4:]) is None

    def test_annual_flows_take_only_fy_rows(self) -> None:
        years = annual_flows(company(range(2022, 2025)), "net_income")
        assert [y.period_end.year for y in years] == [2022, 2023, 2024]
        assert all(y.value == Decimal(40) for y in years)
