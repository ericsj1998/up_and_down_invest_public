"""빠른 스크리닝 지표 — EDGAR `frames` 한 점 값으로 PER·PBR·PSR (T255 1단계 · 순수).

`companyfacts` 이력(2단계 · 시점 정합 · 5년 백분위)이 없는 종목에 **"지금 값" 만** 낸다.
frames 는 개념 하나 · 기간 하나에 전 회사가 한 파일이라 시장 전체를 몇 번의 호출로 훑는다.
대신 이력이 없어 백분위·점수는 없다 — 표에서 `stage = quick` 로 구분하고 "이력 받기" 로
2단계로 올린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from updown.analysis.fundamentals.ratios import market_cap, safe_div

QUICK_CONCEPTS: dict[str, tuple[tuple[str, str], ...]] = {
    "revenue": (
        ("us-gaap/Revenues", "USD"),
        ("us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
    ),
    "net_income": (("us-gaap/NetIncomeLoss", "USD"),),
    "equity": (("us-gaap/StockholdersEquity", "USD"),),
    "shares": (("dei/EntityCommonStockSharesOutstanding", "shares"),),
}
"""우리 이름 → (taxonomy/Tag, 단위) 폴백 순서.

흐름(revenue · net_income)은 연간 frame, 시점(equity · shares)은 분기말 I."""

FLOW = ("revenue", "net_income")
INSTANT = ("equity", "shares")


def annual_periods(today: date, count: int = 2) -> list[str]:
    """가장 최근에 **끝난** 달력 연도부터 `CY####` 를 count 개.

    Args:
        today: 오늘.
        count: 몇 해.

    Returns:
        예: 2026-09-10 → `["CY2025", "CY2024"]`.
    """
    return [f"CY{today.year - 1 - i}" for i in range(count)]


def instant_periods(today: date, count: int = 3) -> list[str]:
    """가장 최근에 끝난 분기부터 `CY####Q#I` 를 count 개 (최신 것이 앞).

    분기 말 뒤 공시까지 시차가 있어 첫 것이 비면 다음 것을 쓴다.

    Args:
        today: 오늘.
        count: 몇 분기.

    Returns:
        예: 2026-09-10 → `["CY2026Q2I", "CY2026Q1I", "CY2025Q4I"]`.
    """
    year, quarter = today.year, (today.month - 1) // 3  # 지금 분기의 앞 분기 = 끝난 분기
    out: list[str] = []
    for _ in range(count):
        if quarter == 0:
            year -= 1
            quarter = 4
        out.append(f"CY{year}Q{quarter}I")
        quarter -= 1
    return out


def values_by_cik(frame: dict[str, Any]) -> dict[str, Decimal]:
    """Frames 응답 → `{CIK(10자리): 값}` — 같은 CIK 가 여럿이면 가장 늦게 낸 것.

    Args:
        frame: `/api/xbrl/frames/...` JSON.

    Returns:
        CIK → 값.
    """
    out: dict[str, tuple[str, Decimal]] = {}
    rows = frame.get("data")
    if not isinstance(rows, list):
        return {}
    for raw in cast("list[object]", rows):
        if not isinstance(raw, dict):
            continue
        row = cast("dict[str, Any]", raw)
        cik = row.get("cik")
        val = row.get("val")
        if cik is None or val is None or isinstance(val, bool):
            continue
        try:
            value = Decimal(str(val))
        except ArithmeticError:
            continue
        key = str(cik).zfill(10)
        filed = str(row.get("filed") or "")
        found = out.get(key)
        if found is None or filed > found[0]:
            out[key] = (filed, value)
    return {k: v[1] for k, v in out.items()}


@dataclass(frozen=True, slots=True)
class QuickMetrics:
    """한 종목의 빠른 지표 — 값만, 백분위 없음.

    Attributes:
        price: 종가.
        shares: 발행 주식 수.
        market_cap: 시가총액.
        per: 시총 / 연간 순이익. 적자면 None.
        pbr: 시총 / 자기자본. 자본 잠식이면 None.
        psr: 시총 / 연간 매출.
        periods: 어느 frame 을 썼나 (`{"revenue": "CY2025", ...}`).
    """

    price: Decimal | None
    shares: Decimal | None
    market_cap: Decimal | None
    per: Decimal | None
    pbr: Decimal | None
    psr: Decimal | None
    periods: dict[str, str]

    def as_json(self) -> dict[str, Any]:
        """화면 모양.

        Returns:
            비율은 숫자 · 금액은 문자열 · `periods` 는 쓴 frame 이름.
        """

        def _num(v: Decimal | None) -> float | None:
            return None if v is None else float(v)

        return {
            "price": None if self.price is None else str(self.price),
            "market_cap": None if self.market_cap is None else str(self.market_cap),
            "metrics": {
                "per": {"value": _num(self.per), "percentile": None},
                "pbr": {"value": _num(self.pbr), "percentile": None},
                "psr": {"value": _num(self.psr), "percentile": None},
            },
            "periods": dict(self.periods),
        }


def quick_metrics(
    *,
    price: Decimal | None,
    shares: Decimal | None,
    revenue: Decimal | None,
    net_income: Decimal | None,
    equity: Decimal | None,
    periods: dict[str, str] | None = None,
) -> QuickMetrics:
    """값 다섯 → 지표 셋. 분모가 0·음수면 그 지표는 None (지어내지 않는다).

    Args:
        price: 종가.
        shares: 발행 주식 수.
        revenue: 연간 매출.
        net_income: 연간 순이익.
        equity: 자기자본(시점).
        periods: 쓴 frame 이름들.

    Returns:
        지표.
    """
    cap = market_cap(price, shares)
    per = safe_div(cap, net_income) if net_income is not None and net_income > 0 else None
    pbr = safe_div(cap, equity) if equity is not None and equity > 0 else None
    psr = safe_div(cap, revenue) if revenue is not None and revenue > 0 else None
    return QuickMetrics(
        price=price,
        shares=shares,
        market_cap=cap,
        per=per,
        pbr=pbr,
        psr=psr,
        periods=dict(periods or {}),
    )


__all__ = [
    "FLOW",
    "INSTANT",
    "QUICK_CONCEPTS",
    "QuickMetrics",
    "annual_periods",
    "instant_periods",
    "quick_metrics",
    "values_by_cik",
]
