"""T243 — 비율 · 백분위 · 점수 · 표(시점 정합 포함) · API 모양."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from _fundamentals_fixtures import INSTANTS, QUARTER_FLOWS, company
from updown.analysis.fundamentals.percentile import month_ends, percentile_rank
from updown.analysis.fundamentals.ratios import add, cagr, safe_div
from updown.analysis.fundamentals.score import PricePercentile, value_score
from updown.analysis.fundamentals.snapshot import METRICS, build_snapshot, price_lookup
from updown.apps.api.fundamentals import snapshot_payload
from updown.common.domain.fundamentals import load_fundamentals_config
from updown.marketdata.fundamentals.edgar import filing_url, filings_of

CONFIG = load_fundamentals_config()
FLAT_PRICE = Decimal(20)


def flat(_: date) -> Decimal | None:
    return FLAT_PRICE


class TestRatios:
    def test_safe_div_refuses_non_positive_denominator(self) -> None:
        assert safe_div(Decimal(1), Decimal(0)) is None
        assert safe_div(Decimal(1), Decimal(-2)) is None
        assert safe_div(None, Decimal(2)) is None
        assert safe_div(Decimal(1), Decimal(4)) == Decimal("0.25")

    def test_add_needs_first_part(self) -> None:
        assert add(None, Decimal(1)) is None
        assert add(Decimal(1), None, Decimal(2)) == Decimal(3)

    def test_cagr(self) -> None:
        got = cagr(Decimal(100), Decimal(200), 3)
        assert got is not None and abs(got - Decimal("0.2599")) < Decimal("0.001")
        assert cagr(Decimal(-1), Decimal(200), 3) is None

    def test_percentile_rank_middle_ties(self) -> None:
        hist = [Decimal(n) for n in range(1, 11)]
        assert percentile_rank(hist, Decimal(10)) == Decimal(95)
        assert percentile_rank(hist, Decimal(0)) == Decimal(0)
        assert percentile_rank(hist, Decimal(100)) == Decimal(100)
        with pytest.raises(ValueError, match="표본"):
            percentile_rank([], Decimal(1))

    def test_month_ends_cover_window(self) -> None:
        ends = month_ends(date(2025, 6, 15), years=1)
        assert ends[-1] == date(2025, 5, 31) and ends[0] == date(2024, 6, 30)
        assert len(ends) == 12


class TestScore:
    def test_score_flips_multiples_and_penalizes_flags(self) -> None:
        pcts = [
            PricePercentile("per", Decimal(10), higher_is_cheaper=False),  # 싸다 → 90
            PricePercentile("fcf_yield", Decimal(70), higher_is_cheaper=True),  # 70
        ]
        got = value_score(pcts, ["debt_to_equity"], CONFIG.score)
        assert got.cheapness == Decimal(80) and got.score == Decimal(65)
        assert got.used == ("per", "fcf_yield") and got.flags == ("debt_to_equity",)

    def test_too_few_metrics_gives_no_score_not_zero(self) -> None:
        got = value_score(
            [PricePercentile("per", Decimal(10), higher_is_cheaper=False)], [], CONFIG.score
        )
        assert got.score is None and "최소" in got.note


class TestSnapshot:
    def test_metrics_match_hand_calculation(self) -> None:
        facts = company(range(2019, 2026))
        made = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2026, 3, 15, tzinfo=UTC),
            price_at=flat,
            config=CONFIG,
        )
        assert made.market_cap == FLAT_PRICE * INSTANTS["shares_outstanding"]  # 2000
        ni = QUARTER_FLOWS["net_income"] * 4  # 40
        assert made.metric("per").value == Decimal(2000) / ni
        assert made.metric("pbr").value == Decimal(10)
        assert made.metric("psr").value == Decimal(5)
        ebitda = (
            QUARTER_FLOWS["operating_income"] + QUARTER_FLOWS["depreciation_amortization"]
        ) * 4  # 68
        ev = Decimal(2000) + Decimal(120) - Decimal(60)
        got = made.metric("ev_ebitda").value
        assert got is not None and abs(got - ev / ebitda) < Decimal("1e-20")
        assert made.metric("fcf_yield").value == Decimal(40) / Decimal(2000) * 100
        assert made.metric("debt_to_equity").value == Decimal("1.5")
        assert made.metric("interest_coverage").value == Decimal(15)
        assert made.metric("current_ratio").value == Decimal("1.25")
        assert made.metric("roe").value == Decimal(20)
        assert made.metric("operating_margin").value == Decimal(15)
        assert made.metric("revenue_cagr_3y").value == Decimal(0)
        assert made.metric("fcf_conversion").value == Decimal(100)
        assert made.metric("shares_change_1y").value == Decimal(0)
        assert made.metric("buyback_yield").value == Decimal(12) / Decimal(2000) * 100
        assert not made.flags
        # 출처 접수 번호가 붙는다 — 복원된 Q4 는 10-K + 3개 10-Q
        assert len(made.metric("per").sources) >= 4
        assert all(m.spec.key in {s.key for s in METRICS} for m in made.metrics)

    def test_percentile_reflects_own_history(self) -> None:
        facts = company(range(2019, 2026))

        def rising(on: date) -> Decimal | None:
            return Decimal(10) + Decimal((on - date(2019, 1, 1)).days) / Decimal(100)

        made = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2026, 3, 15, tzinfo=UTC),
            price_at=rising,
            config=CONFIG,
        )
        per = made.metric("per")
        assert per.percentile is not None and per.percentile >= Decimal(95), (
            "가격만 올랐으니 PER 은 역사상 최고"
        )
        assert made.history_points >= CONFIG.score.min_history_points
        assert made.score.score is not None and made.score.score <= Decimal(10), (
            "역사상 가장 비싸다"
        )

    def test_point_in_time_excludes_unfiled_quarters(self) -> None:
        # 2025 년 값이 2배 — Q1 2025 10-Q 는 2025-05-10 공시
        facts = company(range(2022, 2026), scale={2025: Decimal(2)})
        before = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2025, 5, 10, 12, tzinfo=UTC),
            price_at=flat,
            config=CONFIG,
        )
        after = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2025, 5, 11, tzinfo=UTC),
            price_at=flat,
            config=CONFIG,
        )
        assert before.metric("psr").value == Decimal(5), "TTM 매출 400 (2024 4분기)"
        assert after.metric("psr").value == Decimal(2000) / Decimal(500), (
            "Q1 2025 = 200 이 들어온 TTM"
        )
        assert before.latest_filed_at == datetime(2025, 3, 1, tzinfo=UTC)

    def test_no_price_means_no_price_metrics_and_no_score(self) -> None:
        facts = company(range(2022, 2026))
        made = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2026, 3, 15, tzinfo=UTC),
            price_at=lambda _: None,
            config=CONFIG,
        )
        assert made.price is None and made.metric("per").value is None
        assert made.metric("debt_to_equity").value == Decimal("1.5"), "가격 없는 지표는 남는다"
        assert made.score.score is None and any("시세 없음" in n for n in made.notes)

    def test_debt_flags_and_negative_equity(self) -> None:
        risky = dict(INSTANTS)
        risky["total_liabilities"] = Decimal(900)
        risky["long_term_debt"] = Decimal(800)
        risky["current_liabilities"] = Decimal(200)
        facts = company(range(2022, 2026), instants=risky)
        made = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2026, 3, 15, tzinfo=UTC),
            price_at=flat,
            config=CONFIG,
        )
        keys = {f.key for f in made.flags}
        assert {"debt_to_equity", "net_debt_to_ebitda", "current_ratio"} <= keys
        assert made.score.penalty == CONFIG.score.penalty_per_flag * len(made.flags)

    def test_price_lookup_uses_prior_close_but_not_stale(self) -> None:
        at = price_lookup([(date(2025, 1, 3), Decimal(1)), (date(2025, 1, 10), Decimal(2))])
        assert at(date(2025, 1, 5)) == Decimal(1)
        assert at(date(2025, 1, 10)) == Decimal(2)
        assert at(date(2025, 1, 2)) is None
        assert at(date(2025, 2, 10)) is None, "한 달 지난 종가는 그날 가격이 아니다"


class TestPayload:
    def test_payload_shape_and_links(self) -> None:
        facts = company(range(2022, 2026))
        made = build_snapshot(
            facts,
            symbol="TEST",
            as_of=datetime(2026, 3, 15, tzinfo=UTC),
            price_at=flat,
            config=CONFIG,
        )
        body = snapshot_payload(made, filings_of(facts))
        assert body["symbol"] == "TEST" and body["price"] == "20" and body["market_cap"] == "2000"
        per = next(m for m in body["metrics"] if m["key"] == "per")
        assert isinstance(per["value"], float) and per["sources"]
        assert per["sources"][0]["url"] == filing_url("0000000001", per["sources"][0]["accession"])
        assert per["sources"][0]["form"] in {"10-K", "10-Q"}
        assert body["filings"] and body["filings"][0]["filed_at"] >= body["filings"][-1]["filed_at"]
        assert body["score"]["score"] is None or isinstance(body["score"]["score"], float)

    def test_filing_url_strips_zeros_and_dashes(self) -> None:
        assert (
            filing_url("0000320193", "0000320193-20-000096")
            == "https://www.sec.gov/Archives/edgar/data/320193/000032019320000096/"
        )
