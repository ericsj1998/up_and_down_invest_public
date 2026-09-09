"""사실 → 시계열: 시점 정합 · 분기 복원 · TTM (T243).

## 시점 정합

`known_facts(facts, as_of)` 만이 "그때 알 수 있던 것" 을 준다. 공시 당일은 장 마감 뒤 접수가
흔하므로 **하루 지연**
(`AVAILABILITY_LAG`)을 둔다 — 당일 값을 당일 아침에 쓰는 낙관을 막는다.

## 분기 복원 — 10-K 에는 4분기가 없다

미국 공시는 Q1~Q3 를 10-Q 로, 연간을 10-K 로 낸다. 4분기 값은 어디에도 없고
**FY - (Q1+Q2+Q3)** 로 만든다. 10-Q 가 분기 값 대신 누적(YTD)만 준 회사는 **누적 차분**으로
분기를 만든다. 둘 다 안 되면 그 분기는 없고, TTM 도 없다 —
빈칸을 0 으로 메우면 PER 이 그럴듯하게 틀린다.

같은 기간이 여러 공시에 실리면(원본 · 정정 · 다음 해 비교 열) **가장 늦게 공시된 값**을 쓴다 —
정정이 정정 전보다 맞다.
`known_facts` 가 먼저 걸러져 있으므로 "늦게" 는 그 시점까지 중 늦게다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from updown.common.domain.fundamentals import FactKind, FinancialFact

AVAILABILITY_LAG = timedelta(days=1)
"""공시일 뒤 이만큼 지나야 "알 수 있던 값" 으로 친다."""

QUARTER_DAYS = 91
"""분기 길이의 기준. 누적 길이를 이 값으로 나눠 반올림하면 1(분기)·2(반기)·3(9개월)·4(연간)."""
QUARTER_MIN_DAYS = 80
QUARTER_MAX_DAYS = 100
YEAR_MIN_DAYS = 350
YEAR_MAX_DAYS = 380
_MAX_CUMULATIVE = 4
_TTM_QUARTERS = 4


@dataclass(frozen=True, slots=True)
class Point:
    """시계열 한 점.

    Attributes:
        period_end: 기간 끝.
        value: 값.
        sources: 이 값을 만든 공시 접수 번호(들). 복원된 분기는 여럿이다.
    """

    period_end: date
    value: Decimal
    sources: tuple[str, ...]


def known_facts(
    facts: Iterable[FinancialFact], as_of: datetime, *, lag: timedelta = AVAILABILITY_LAG
) -> list[FinancialFact]:
    """`as_of` 시점에 알 수 있던 사실만.

    Args:
        facts: 사실.
        as_of: 기준 시각 (UTC aware).
        lag: 공시일 뒤 가용 지연.

    Returns:
        `filed_at + lag <= as_of` 인 사실.
    """
    return [f for f in facts if f.filed_at + lag <= as_of]


def _latest_by_period(facts: Iterable[FinancialFact]) -> dict[tuple[date, date], FinancialFact]:
    """같은 기간은 가장 늦게 공시된 것 하나."""
    out: dict[tuple[date, date], FinancialFact] = {}
    for fact in facts:
        key = (fact.period_start, fact.period_end)
        prior = out.get(key)
        if prior is None or fact.filed_at > prior.filed_at:
            out[key] = fact
    return out


def latest_instant(facts: Iterable[FinancialFact], concept: str) -> Point | None:
    """시점 값의 최신 — 기간 끝이 가장 늦은 것.

    Args:
        facts: (시점 정합이 끝난) 사실.
        concept: 우리 이름.

    Returns:
        점. 없으면 None.
    """
    rows = _latest_by_period(
        f for f in facts if f.concept == concept and f.kind is FactKind.INSTANT
    )
    if not rows:
        return None
    best = max(rows.values(), key=lambda f: (f.period_end, f.filed_at))
    return Point(best.period_end, best.value, (best.accession,))


def instant_history(facts: Iterable[FinancialFact], concept: str) -> list[Point]:
    """시점 값의 전체 역사 (기간 끝 오름차순).

    Args:
        facts: 사실.
        concept: 우리 이름.

    Returns:
        점 목록.
    """
    rows = _latest_by_period(
        f for f in facts if f.concept == concept and f.kind is FactKind.INSTANT
    )
    return sorted(
        (Point(f.period_end, f.value, (f.accession,)) for f in rows.values()),
        key=lambda p: p.period_end,
    )


def quarterly_flows(facts: Iterable[FinancialFact], concept: str) -> list[Point]:
    """분기 값 시계열 — 보고된 분기 + 복원된 분기 (기간 끝 오름차순).

    Args:
        facts: (시점 정합이 끝난) 사실.
        concept: 우리 이름.

    Returns:
        분기 점. 복원 불가 분기는 빠진다.
    """
    rows = _latest_by_period(f for f in facts if f.concept == concept and f.kind is FactKind.FLOW)
    quarters: dict[date, Point] = {}
    cumulative: dict[int, dict[date, FinancialFact]] = {
        k: {} for k in range(2, _MAX_CUMULATIVE + 1)
    }
    for fact in rows.values():
        days = fact.duration_days
        if QUARTER_MIN_DAYS <= days <= QUARTER_MAX_DAYS:
            quarters[fact.period_end] = Point(fact.period_end, fact.value, (fact.accession,))
            continue
        bucket = round(days / QUARTER_DAYS)
        if 2 <= bucket <= _MAX_CUMULATIVE:
            cumulative[bucket][fact.period_end] = fact

    # 누적 → 분기: 짧은 누적부터 채워야 긴 누적이 그 결과를 쓸 수 있다.
    for bucket in range(2, _MAX_CUMULATIVE + 1):
        for end, fact in sorted(cumulative[bucket].items()):
            if end in quarters:
                continue
            derived = _derive_quarter(fact, bucket, quarters, cumulative)
            if derived is not None:
                quarters[end] = derived
    return [quarters[end] for end in sorted(quarters)]


def _derive_quarter(
    fact: FinancialFact,
    bucket: int,
    quarters: dict[date, Point],
    cumulative: dict[int, dict[date, FinancialFact]],
) -> Point | None:
    """누적 값에서 마지막 분기를 뺀다 — 앞 분기들의 합 또는 한 단계 짧은 누적."""
    end = fact.period_end
    start = fact.period_start
    # ① 앞 (bucket-1) 개 분기가 전부 있으면 그 합을 뺀다.
    prior = [
        q
        for q in quarters.values()
        if start <= q.period_end < end and (end - q.period_end).days < bucket * QUARTER_MAX_DAYS
    ]
    prior.sort(key=lambda p: p.period_end)
    if len(prior) == bucket - 1:
        total = sum((p.value for p in prior), Decimal(0))
        sources = tuple(dict.fromkeys((fact.accession, *[s for p in prior for s in p.sources])))
        return Point(end, fact.value - total, sources)
    # ② 한 단계 짧은 누적이 같은 시작일로 있으면 그 차이.
    if bucket - 1 >= 2:
        for shorter in cumulative[bucket - 1].values():
            if (
                shorter.period_start == start
                and QUARTER_MIN_DAYS <= (end - shorter.period_end).days <= QUARTER_MAX_DAYS
            ):
                return Point(end, fact.value - shorter.value, (fact.accession, shorter.accession))
    return None


def annual_flows(facts: Iterable[FinancialFact], concept: str) -> list[Point]:
    """연간 값 시계열 (10-K 의 FY 행 · 기간 끝 오름차순).

    Args:
        facts: 사실.
        concept: 우리 이름.

    Returns:
        연간 점.
    """
    rows = _latest_by_period(
        f
        for f in facts
        if f.concept == concept
        and f.kind is FactKind.FLOW
        and YEAR_MIN_DAYS <= f.duration_days <= YEAR_MAX_DAYS
    )
    return sorted(
        (Point(f.period_end, f.value, (f.accession,)) for f in rows.values()),
        key=lambda p: p.period_end,
    )


def ttm(quarters: Sequence[Point]) -> Point | None:
    """최근 4분기 합 — 분기가 **이어져야** 한다.

    Args:
        quarters: `quarterly_flows` 결과.

    Returns:
        합 (기간 끝은 마지막 분기). 4분기가 없거나 사이가 비면 None — 3분기 합을 4분기라 부르지
        않는다.
    """
    if len(quarters) < _TTM_QUARTERS:
        return None
    tail = list(quarters[-_TTM_QUARTERS:])
    for earlier, later in pairwise(tail):
        gap = (later.period_end - earlier.period_end).days
        if not QUARTER_MIN_DAYS <= gap <= QUARTER_MAX_DAYS:
            return None
    total = sum((p.value for p in tail), Decimal(0))
    sources = tuple(dict.fromkeys(s for p in tail for s in p.sources))
    return Point(tail[-1].period_end, total, sources)


def value_at(history: Sequence[Point], on: date) -> Point | None:
    """`on` 이전(포함) 마지막 점.

    Args:
        history: 기간 끝 오름차순 점.
        on: 기준일.

    Returns:
        점. 없으면 None.
    """
    found: Point | None = None
    for point in history:
        if point.period_end <= on:
            found = point
        else:
            break
    return found


__all__ = [
    "AVAILABILITY_LAG",
    "Point",
    "annual_flows",
    "instant_history",
    "known_facts",
    "latest_instant",
    "quarterly_flows",
    "ttm",
    "value_at",
]
