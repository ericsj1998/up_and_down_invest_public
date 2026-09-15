"""한 종목의 재무 표 — 지표 · 자기 역사 백분위 · 깃발 · 점수 (T243 · 순수).

`build_snapshot(facts, as_of, price_at, config)` 하나가 입구다. 안에서 하는 일:

1. `known_facts` 로 `as_of` 에 알 수 있던 사실만 남긴다 (시점 정합).
2. `compute_metrics` 로 지표를 낸다 — 가격 지표는 `as_of` 의 종가와 최신 주식수로 시총을 만든 뒤.
3. 자기 역사: 지난 N 년의 **월말마다** 1·2 를 되풀이해 그때의 지표를 만들고, 지금 값의 백분위를
   잰다.
   그때의 사실·그때의 가격만 쓰므로 역사 자체도 시점 정합이다.
4. 깃발(부채 문턱)과 점수.

가격이 없으면(봉 없음) 가격 지표는 전부 None 이고 점수도 None 이다 — 가격을 지어내지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from updown.analysis.fundamentals.percentile import month_ends, percentile_rank
from updown.analysis.fundamentals.ratios import (
    add,
    as_percent,
    cagr,
    change_ratio,
    enterprise_value,
    market_cap,
    safe_div,
)
from updown.analysis.fundamentals.score import PricePercentile, ValueScore, value_score
from updown.analysis.fundamentals.series import (
    Point,
    annual_flows,
    instant_history,
    known_facts,
    latest_instant,
    quarterly_flows,
    ttm,
    value_at,
)
from updown.common.domain.fundamentals import FinancialFact, FundamentalsConfig

PriceAt = Callable[[date], Decimal | None]
"""날짜 → 그날(또는 직전 거래일) 종가. 없으면 None."""

PRICE_STALE_DAYS = 7
"""이보다 오래된 종가는 "그날 가격" 으로 안 친다 — 상장폐지·결측 구간에 옛 가격을 끌어오지
않는다."""

CAGR_YEARS = 3
_ONE_YEAR = timedelta(days=365)


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """지표 정의 — 표에 나가는 이름과 성질.

    Attributes:
        key: 키.
        label: 화면 이름.
        group: `price` · `debt` · `earning` · `dilution`.
        unit: `x`(배수) · `%`.
        higher_is_cheaper: 가격 지표의 방향. 가격 지표가 아니면 None.
    """

    key: str
    label: str
    group: str
    unit: str
    higher_is_cheaper: bool | None = None


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("per", "PER(TTM)", "price", "x", False),
    MetricSpec("pbr", "PBR", "price", "x", False),
    MetricSpec("psr", "PSR(TTM)", "price", "x", False),
    MetricSpec("ev_ebitda", "EV/EBITDA(TTM)", "price", "x", False),
    MetricSpec("fcf_yield", "FCF 수익률", "price", "%", True),
    MetricSpec("dividend_yield", "배당수익률", "price", "%", True),
    MetricSpec("debt_to_equity", "부채비율(총부채/자본)", "debt", "x"),
    MetricSpec("net_debt_to_ebitda", "순부채/EBITDA", "debt", "x"),
    MetricSpec("interest_coverage", "이자보상배율", "debt", "x"),
    MetricSpec("current_ratio", "유동비율", "debt", "x"),
    MetricSpec("roe", "ROE(TTM)", "earning", "%"),
    MetricSpec("operating_margin", "영업이익률(TTM)", "earning", "%"),
    MetricSpec("revenue_cagr_3y", "매출 성장(3y CAGR)", "earning", "%"),
    MetricSpec("net_income_cagr_3y", "순이익 성장(3y CAGR)", "earning", "%"),
    MetricSpec("fcf_conversion", "FCF 전환율(FCF/순이익)", "earning", "%"),
    MetricSpec("shares_change_1y", "발행주식수 1년 변화", "dilution", "%"),
    MetricSpec("buyback_yield", "자사주 매입 수익률", "dilution", "%"),
)
METRIC_BY_KEY: Mapping[str, MetricSpec] = {m.key: m for m in METRICS}


@dataclass(frozen=True, slots=True)
class MetricValue:
    """지표 값 하나와 출처.

    Attributes:
        value: 값. 없으면 None.
        sources: 공시 접수 번호.
        note: 없는 이유 (적자 · 자료 없음).
    """

    value: Decimal | None
    sources: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class Metric:
    """표 한 줄.

    Attributes:
        spec: 정의.
        value: 값.
        percentile: 자기 역사 백분위. 없으면 None.
        sources: 공시 접수 번호.
        note: 비고.
    """

    spec: MetricSpec
    value: Decimal | None
    percentile: Decimal | None
    sources: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class Flag:
    """부채 위험 깃발.

    Attributes:
        key: 키.
        label: 화면 말.
        value: 걸린 값.
        threshold: 문턱.
    """

    key: str
    label: str
    value: Decimal | None
    threshold: Decimal | None


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    """한 종목의 재무 표.

    Attributes:
        symbol: 종목.
        as_of: 기준 시각.
        price: 그 시점 종가. 없으면 None.
        price_date: 종가 날짜.
        market_cap: 시총.
        metrics: 지표들 (`METRICS` 순).
        flags: 깃발.
        score: 저평가 점수.
        latest_filed_at: 쓰인 사실 중 가장 늦은 공시일.
        history_points: 백분위 표본 수 (가장 많은 지표 기준).
        notes: 표 전체 비고 (가격 없음 등).
    """

    symbol: str
    as_of: datetime
    price: Decimal | None
    price_date: date | None
    market_cap: Decimal | None
    metrics: tuple[Metric, ...]
    flags: tuple[Flag, ...]
    score: ValueScore
    latest_filed_at: datetime | None
    history_points: int
    notes: tuple[str, ...]

    def metric(self, key: str) -> Metric:
        """키로 지표를 찾는다.

        Args:
            key: 지표 키.

        Returns:
            지표.

        Raises:
            KeyError: 모르는 키.
        """
        for metric in self.metrics:
            if metric.spec.key == key:
                return metric
        raise KeyError(key)


def price_lookup(
    closes: Sequence[tuple[date, Decimal]], *, stale_days: int = PRICE_STALE_DAYS
) -> PriceAt:
    """일봉 종가 목록 → `PriceAt`.

    Args:
        closes: `(날짜, 종가)` (순서 무관).
        stale_days: 직전 거래일이 이보다 멀면 None.

    Returns:
        조회 함수.
    """
    table = sorted(closes)

    def _at(on: date) -> Decimal | None:
        """기준일 이하의 가장 최근 종가 — 휴장일이면 직전 거래일로 내려간다.

        Args:
            on: 기준일.

        Returns:
            종가. 직전 거래일이 `stale_days` 보다 멀면 None — 상장폐지·결측 구간에 옛 가격을
            "그날 가격" 으로 끌어오지 않는다 (`PRICE_STALE_DAYS`).
        """
        found: tuple[date, Decimal] | None = None
        for row in table:
            if row[0] <= on:
                found = row
            else:
                break
        if found is None or (on - found[0]).days > stale_days:
            return None
        return found[1]

    return _at


def _sources(*points: Point | None) -> tuple[str, ...]:
    """지표에 쓰인 점들의 공시 접수 번호 — 중복 없이, 처음 나온 순서대로."""
    return tuple(dict.fromkeys(s for p in points if p is not None for s in p.sources))


def _val(point: Point | None) -> Decimal | None:
    """없는 점은 None 값으로 — 비율 함수(`safe_div` 등)가 None 을 자료 없음으로 받는다."""
    return None if point is None else point.value


def compute_metrics(
    known: Sequence[FinancialFact], price: Decimal | None, on: date
) -> tuple[dict[str, MetricValue], Decimal | None]:
    """지표를 계산한다 (백분위 없이).

    Args:
        known: 시점 정합이 끝난 사실.
        price: 그 시점 종가. None 이면 가격 지표는 전부 None.
        on: 기준일 — 시점 값·연간 값의 "그때" 를 고르는 데 쓴다.

    Returns:
        `(키 → 값, 시총)`.
    """
    flows: dict[str, list[Point]] = {
        c: quarterly_flows(known, c)
        for c in (
            "revenue",
            "operating_income",
            "net_income",
            "interest_expense",
            "depreciation_amortization",
            "operating_cash_flow",
            "capex",
            "dividends_paid",
            "share_repurchase",
        )
    }
    trailing = {c: ttm(q) for c, q in flows.items()}
    instants = {
        c: latest_instant(known, c)
        for c in (
            "total_liabilities",
            "equity",
            "cash",
            "short_term_investments",
            "current_assets",
            "current_liabilities",
            "long_term_debt",
            "short_term_debt",
            "commercial_paper",
            "shares_outstanding",
        )
    }
    revenue = trailing["revenue"]
    op_income = trailing["operating_income"]
    net_income = trailing["net_income"]
    interest = trailing["interest_expense"]
    da = trailing["depreciation_amortization"]
    ocf = trailing["operating_cash_flow"]
    capex = trailing["capex"]
    dividends = trailing["dividends_paid"]
    buyback = trailing["share_repurchase"]
    equity = instants["equity"]
    liabilities = instants["total_liabilities"]
    shares = instants["shares_outstanding"]

    cap = market_cap(price, _val(shares))
    ebitda = add(_val(op_income), _val(da))
    total_debt = add(
        _val(instants["long_term_debt"]),
        _val(instants["short_term_debt"]),
        _val(instants["commercial_paper"]),
    )
    cash_like = add(_val(instants["cash"]), _val(instants["short_term_investments"]))
    ev = enterprise_value(cap, total_debt, cash_like)
    fcf = None if ocf is None else ocf.value - (capex.value if capex is not None else Decimal(0))
    net_debt = None if total_debt is None else total_debt - (cash_like or Decimal(0))

    out: dict[str, MetricValue] = {}

    def _put(key: str, value: Decimal | None, *points: Point | None, note: str = "") -> None:
        out[key] = MetricValue(value, _sources(*points), note if value is None else "")

    loss = "적자(TTM 순이익 ≤ 0)" if net_income is not None and net_income.value <= 0 else ""
    _put("per", safe_div(cap, _val(net_income)), net_income, shares, note=loss or "자료 없음")
    _put("pbr", safe_div(cap, _val(equity)), equity, shares, note="자료 없음")
    _put("psr", safe_div(cap, _val(revenue)), revenue, shares, note="자료 없음")
    _put(
        "ev_ebitda",
        safe_div(ev, ebitda),
        op_income,
        da,
        shares,
        instants["long_term_debt"],
        instants["cash"],
        note="EBITDA ≤ 0 또는 차입 자료 없음",
    )
    _put("fcf_yield", as_percent(safe_div(fcf, cap)), ocf, capex, shares, note="자료 없음")
    _put(
        "dividend_yield",
        as_percent(safe_div(_val(dividends), cap)) if dividends is not None else None,
        dividends,
        shares,
        note="배당 없음/자료 없음",
    )

    _put(
        "debt_to_equity",
        safe_div(_val(liabilities), _val(equity)),
        liabilities,
        equity,
        note="자본잠식 또는 자료 없음",
    )
    _put(
        "net_debt_to_ebitda",
        safe_div(net_debt, ebitda) if net_debt is not None and net_debt > 0 else None,
        instants["long_term_debt"],
        instants["cash"],
        op_income,
        da,
        note="순현금(빚보다 현금이 많음) 또는 자료 없음",
    )
    _put(
        "interest_coverage",
        safe_div(_val(op_income), _val(interest)),
        op_income,
        interest,
        note="이자 자료 없음",
    )
    _put(
        "current_ratio",
        safe_div(_val(instants["current_assets"]), _val(instants["current_liabilities"])),
        instants["current_assets"],
        instants["current_liabilities"],
        note="자료 없음",
    )

    _put(
        "roe",
        as_percent(safe_div(_val(net_income), _val(equity))),
        net_income,
        equity,
        note="자료 없음",
    )
    _put(
        "operating_margin",
        as_percent(safe_div(_val(op_income), _val(revenue))),
        op_income,
        revenue,
        note="자료 없음",
    )
    rev_years = annual_flows(known, "revenue")
    ni_years = annual_flows(known, "net_income")
    rev_now = value_at(rev_years, on)
    rev_then = value_at(rev_years, on - _ONE_YEAR * CAGR_YEARS)
    ni_now = value_at(ni_years, on)
    ni_then = value_at(ni_years, on - _ONE_YEAR * CAGR_YEARS)
    _put(
        "revenue_cagr_3y",
        as_percent(cagr(_val(rev_then), _val(rev_now), CAGR_YEARS)),
        rev_then,
        rev_now,
        note="3년 전 연간 자료 없음",
    )
    _put(
        "net_income_cagr_3y",
        as_percent(cagr(_val(ni_then), _val(ni_now), CAGR_YEARS)),
        ni_then,
        ni_now,
        note="3년 전 연간 자료 없음 또는 적자",
    )
    _put(
        "fcf_conversion",
        as_percent(safe_div(fcf, _val(net_income))),
        ocf,
        capex,
        net_income,
        note=loss or "자료 없음",
    )

    share_hist = instant_history(known, "shares_outstanding")
    shares_then = value_at(share_hist, on - _ONE_YEAR)
    _put(
        "shares_change_1y",
        as_percent(change_ratio(_val(shares_then), _val(shares))),
        shares_then,
        shares,
        note="1년 전 주식수 자료 없음",
    )
    _put(
        "buyback_yield",
        as_percent(safe_div(_val(buyback), cap)) if buyback is not None else None,
        buyback,
        shares,
        note="매입 없음/자료 없음",
    )
    return out, cap


def _flags(metrics: Mapping[str, MetricValue], config: FundamentalsConfig) -> tuple[Flag, ...]:
    """부채 깃발 — 설정 문턱(`config.score.debt`)을 넘은 지표만 든다.

    Args:
        metrics: `compute_metrics` 가 낸 지표.
        config: 문턱이 들어 있는 설정.

    Returns:
        넘은 지표의 깃발 (부채비율 · 순부채/EBITDA · 이자보상 · 유동비율 순). 값이 None 인
        지표는 판정하지 않는다 — "자료 없음" 을 "위험" 으로 읽지 않는다.
    """
    debt = config.score.debt
    out: list[Flag] = []
    d_e = metrics["debt_to_equity"].value
    if d_e is not None and d_e > debt.debt_to_equity_max:
        out.append(Flag("debt_to_equity", "부채비율 높음", d_e, debt.debt_to_equity_max))
    nd = metrics["net_debt_to_ebitda"].value
    if nd is not None and nd > debt.net_debt_to_ebitda_max:
        out.append(
            Flag("net_debt_to_ebitda", "순부채/EBITDA 높음", nd, debt.net_debt_to_ebitda_max)
        )
    ic = metrics["interest_coverage"].value
    if ic is not None and ic < debt.interest_coverage_min:
        out.append(Flag("interest_coverage", "이자보상 낮음", ic, debt.interest_coverage_min))
    cr = metrics["current_ratio"].value
    if cr is not None and cr < debt.current_ratio_min:
        out.append(Flag("current_ratio", "유동비율 낮음", cr, debt.current_ratio_min))
    return tuple(out)


def _negative_equity(known: Sequence[FinancialFact]) -> Flag | None:
    """자본잠식 깃발 — 최신 자본이 0 이하면. 자본 자료가 없으면 깃발도 없다."""
    equity = latest_instant(known, "equity")
    if equity is not None and equity.value <= 0:
        return Flag("negative_equity", "자본잠식", equity.value, Decimal(0))
    return None


def build_snapshot(
    facts: Iterable[FinancialFact],
    *,
    symbol: str,
    as_of: datetime,
    price_at: PriceAt,
    config: FundamentalsConfig,
) -> FundamentalSnapshot:
    """표를 만든다.

    Args:
        facts: 그 종목의 사실 전부 (시점 정합은 여기서 건다).
        symbol: 종목.
        as_of: 기준 시각 (UTC aware).
        price_at: 날짜 → 종가.
        config: 매핑 + 점수 규칙.

    Returns:
        표.
    """
    all_facts = list(facts)
    on = as_of.date()
    known = known_facts(all_facts, as_of)
    price = price_at(on)
    price_date = _price_date(price_at, on) if price is not None else None
    values, cap = compute_metrics(known, price, on)

    # 자기 역사 — 월말마다 그때의 사실·그때의 가격으로.
    history: dict[str, list[Decimal]] = {m.key: [] for m in METRICS if m.group == "price"}
    for month_end in month_ends(on, years=config.score.percentile_years):
        then = datetime(month_end.year, month_end.month, month_end.day, tzinfo=UTC)
        past_price = price_at(month_end)
        if past_price is None:
            continue
        past_values, _ = compute_metrics(known_facts(all_facts, then), past_price, month_end)
        for key, bucket in history.items():
            value = past_values[key].value
            if value is not None:
                bucket.append(value)

    metrics: list[Metric] = []
    percentiles: list[PricePercentile] = []
    points = 0
    for spec in METRICS:
        current = values[spec.key]
        pct: Decimal | None = None
        if spec.group == "price" and current.value is not None:
            sample = history[spec.key]
            points = max(points, len(sample))
            if len(sample) >= config.score.min_history_points:
                pct = percentile_rank(sample, current.value)
                percentiles.append(PricePercentile(spec.key, pct, bool(spec.higher_is_cheaper)))
        metrics.append(Metric(spec, current.value, pct, current.sources, current.note))

    flags = list(_flags(values, config))
    neg = _negative_equity(known)
    if neg is not None:
        flags.append(neg)
    score = value_score(percentiles, [f.key for f in flags], config.score)

    notes: list[str] = []
    if not known:
        notes.append("이 시점에 알 수 있던 공시가 없다")
    if price is None:
        notes.append("시세 없음 — 가격 지표·점수 없음")
    latest = max((f.filed_at for f in known), default=None)
    return FundamentalSnapshot(
        symbol=symbol,
        as_of=as_of,
        price=price,
        price_date=price_date,
        market_cap=cap,
        metrics=tuple(metrics),
        flags=tuple(flags),
        score=score,
        latest_filed_at=latest,
        history_points=points,
        notes=tuple(notes),
    )


def _price_date(price_at: PriceAt, on: date) -> date:
    """`price_at` 이 준 종가의 날짜 — 직전 거래일을 되짚는다."""
    cursor = on
    for _ in range(PRICE_STALE_DAYS + 1):
        earlier = cursor - timedelta(days=1)
        if price_at(earlier) != price_at(on) or earlier < on - timedelta(days=PRICE_STALE_DAYS):
            return cursor
        cursor = earlier
    return cursor


__all__ = [
    "METRICS",
    "METRIC_BY_KEY",
    "Flag",
    "FundamentalSnapshot",
    "Metric",
    "MetricSpec",
    "MetricValue",
    "PriceAt",
    "build_snapshot",
    "compute_metrics",
    "price_lookup",
]
