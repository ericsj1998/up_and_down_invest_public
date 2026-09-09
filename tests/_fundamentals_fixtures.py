"""T243 시험용 합성 회사 — 회계연도 = 달력연도, 분기 10-Q + 연간 10-K.

Q4 는 어디에도 없다(실제 EDGAR 와 같다) — FY - 3분기로 복원돼야 한다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from updown.common.domain.fundamentals import FinancialFact

QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))
FILING_LAG_Q = timedelta(days=40)
FILING_LAG_K = timedelta(days=60)

# 분기 값 (기본 회사) — 표를 손으로 계산할 수 있게 단순하다.
QUARTER_FLOWS: dict[str, Decimal] = {
    "revenue": Decimal(100),
    "operating_income": Decimal(15),
    "net_income": Decimal(10),
    "interest_expense": Decimal(1),
    "depreciation_amortization": Decimal(2),
    "operating_cash_flow": Decimal(12),
    "capex": Decimal(2),
    "dividends_paid": Decimal(1),
    "share_repurchase": Decimal(3),
}
INSTANTS: dict[str, Decimal] = {
    "total_liabilities": Decimal(300),
    "equity": Decimal(200),
    "cash": Decimal(50),
    "short_term_investments": Decimal(10),
    "current_assets": Decimal(100),
    "current_liabilities": Decimal(80),
    "long_term_debt": Decimal(100),
    "short_term_debt": Decimal(20),
    "shares_outstanding": Decimal(100),
}


def fact(
    concept: str,
    start: date,
    end: date,
    value: Decimal,
    *,
    filed: date,
    accession: str,
    form: str,
    fy: int,
    fp: str,
    unit: str = "USD",
) -> FinancialFact:
    """사실 하나."""
    return FinancialFact(
        source="edgar",
        entity_id="0000000001",
        symbol="TEST",
        concept=concept,
        tag=f"us-gaap:{concept}",
        unit=unit,
        period_start=start,
        period_end=end,
        value=value,
        fiscal_year=fy,
        fiscal_period=fp,
        form=form,
        filed_at=datetime(filed.year, filed.month, filed.day, tzinfo=UTC),
        accession=accession,
    )


def company(
    years: range,
    *,
    flows: dict[str, Decimal] | None = None,
    instants: dict[str, Decimal] | None = None,
    scale: dict[int, Decimal] | None = None,
) -> list[FinancialFact]:
    """합성 회사의 공시 전부.

    Args:
        years: 회계연도들.
        flows: 분기 값 (없으면 기본).
        instants: 시점 값 (없으면 기본).
        scale: 연도별 배율 — 성장·급변 시험용.

    Returns:
        사실 목록.
    """
    flows = flows or QUARTER_FLOWS
    instants = instants or INSTANTS
    out: list[FinancialFact] = []
    for year in years:
        factor = (scale or {}).get(year, Decimal(1))
        for index, (month, day) in enumerate(QUARTER_ENDS):
            end = date(year, month, day)
            start = (
                date(year, 1, 1)
                if index == 0
                else date(year, QUARTER_ENDS[index - 1][0], QUARTER_ENDS[index - 1][1])
                + timedelta(days=1)
            )
            is_fy = index == 3
            filed = end + (FILING_LAG_K if is_fy else FILING_LAG_Q)
            form = "10-K" if is_fy else "10-Q"
            fp = "FY" if is_fy else f"Q{index + 1}"
            accession = f"0000000001-{year % 100:02d}-{index + 1:06d}"
            for concept, value in flows.items():
                if is_fy:
                    # 10-K 는 연간만 — 4분기 값은 없다.
                    out.append(
                        fact(
                            concept,
                            date(year, 1, 1),
                            end,
                            value * 4 * factor,
                            filed=filed,
                            accession=accession,
                            form=form,
                            fy=year,
                            fp=fp,
                        )
                    )
                else:
                    out.append(
                        fact(
                            concept,
                            start,
                            end,
                            value * factor,
                            filed=filed,
                            accession=accession,
                            form=form,
                            fy=year,
                            fp=fp,
                        )
                    )
            for concept, value in instants.items():
                unit = (
                    "shares"
                    if concept.endswith("shares") or concept == "shares_outstanding"
                    else "USD"
                )
                out.append(
                    fact(
                        concept,
                        end,
                        end,
                        value,
                        filed=filed,
                        accession=accession,
                        form=form,
                        fy=year,
                        fp=fp,
                        unit=unit,
                    )
                )
    return out
