"""저평가 후보 줄 세우기 — 순수 (T244 · 2026-09-09).

`GET /fundamentals/ranking` 이 종목마다 표(`FundamentalSnapshot`)를 만든 뒤 여기로 넘긴다:
줄 하나의 모양(`ranking_row`) · 근거 한 줄(`why_line`) · 60일 모멘텀(코인 rank60 과 같은 잣대) ·
정렬(`order_rows`).

🔴 **"추천" 이라는 말을 쓰지 않는다.** 점수는 정렬 기준이고, 그 점수가 수익을 가르는지는
규칙 #12(OOS · 표본 30)로 판정한 뒤 사용자가 켠다 — 그때까지 응답의 `recommended` 는 항상
거짓이고 화면은 "저평가 후보" 로 시작한다.

⛔ **재무 없는 종목은 0점이 아니라 뒤로 간다.** 0점을 주면 "가장 비싼 회사" 로 읽힌다
(`Ranking.ordered` 와 같은 원칙).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from updown.analysis.fundamentals.snapshot import FundamentalSnapshot, Metric
from updown.common.domain.fundamentals import Filing

RANK_WINDOW_DAYS = 60
"""코인 펀드의 `rank60` 과 같은 창 — 일봉 종가 기준 60일 수익."""

CARD_LABEL = "저평가 후보"
"""카드 이름 — "추천" 은 OOS 판정 뒤 사용자 결정으로 켠다 (T244 ④)."""

RECOMMENDED = False
"""T243 점수의 OOS 판정 전. 화면은 이 값이 참일 때만 "추천" 을 쓴다."""

SHOWN_METRICS = ("per", "pbr", "psr", "ev_ebitda", "fcf_yield", "dividend_yield", "debt_to_equity")
"""줄에 싣는 지표 — 나머지는 펼침(`/fundamentals/{symbol}`)에서."""

_WHY_PARTS = 2
"""근거 한 줄에 넣는 가격 지표 수 — 가장 싼 쪽 둘."""


def momentum(
    closes: Sequence[tuple[date, Decimal]], *, window: int = RANK_WINDOW_DAYS
) -> Decimal | None:
    """일봉 종가의 N일 수익 (`close[-1] / close[-1-N] - 1`).

    Args:
        closes: `(날짜, 종가)` 오름차순.
        window: 창(일봉 수).

    Returns:
        비율. 봉이 모자라면 None — 짧은 창으로 대신 재지 않는다 (`_rank_weights` 와 같은 규칙).
    """
    if len(closes) <= window:
        return None
    last = closes[-1][1]
    base = closes[-1 - window][1]
    if base <= 0:
        return None
    return last / base - 1


def _cheap_phrase(metric: Metric) -> tuple[Decimal, str] | None:
    """가격 지표 하나의 "얼마나 싼가" 와 말. 백분위 없으면 None."""
    if metric.percentile is None or metric.spec.higher_is_cheaper is None:
        return None
    pct = metric.percentile
    if metric.spec.higher_is_cheaper:
        cheapness = pct
        phrase = f"{metric.spec.label} 5년 상위 {100 - pct:.0f}%"
    else:
        cheapness = Decimal(100) - pct
        phrase = f"{metric.spec.label} 5년 하위 {pct:.0f}%"
    return cheapness, phrase


def why_line(made: FundamentalSnapshot) -> str:
    """근거 한 줄("왜 이 자리") — 가장 싼 가격 지표 둘 + 부채 깃발.

    Args:
        made: 표.

    Returns:
        예: `PER 5년 하위 12% · FCF 수익률 5년 상위 18% · 부채 깃발 없음`. 점수가 없으면 그 이유.
    """
    if made.score.score is None:
        if made.notes:
            return " · ".join(made.notes)
        return made.score.note or "재무 없음"
    phrases = sorted(
        (p for p in (_cheap_phrase(m) for m in made.metrics if m.spec.group == "price") if p),
        key=lambda p: p[0],
        reverse=True,
    )
    parts = [phrase for _, phrase in phrases[:_WHY_PARTS]]
    if made.flags:
        parts.append("부채 깃발: " + ", ".join(f.label for f in made.flags))
    else:
        parts.append("부채 깃발 없음")
    return " · ".join(parts)


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def ranking_row(
    made: FundamentalSnapshot,
    *,
    broker: str | None,
    filings: Sequence[Filing],
    closes: Sequence[tuple[date, Decimal]],
    has_facts: bool,
) -> dict[str, Any]:
    """줄 하나.

    Args:
        made: 표.
        broker: 브로커 이름 (화면 마크).
        filings: 공시 목록 — 최근 것 하나를 싣는다.
        closes: 일봉 종가 — 60일 모멘텀.
        has_facts: 사실이 있었나. 없으면 "재무 없음" 으로 뒤에 선다.

    Returns:
        화면 모양 (가격·시총은 문자열 · 비율은 숫자).
    """
    latest = max(filings, key=lambda f: f.filed_at, default=None)
    metrics = {
        m.spec.key: {"value": _num(m.value), "percentile": _num(m.percentile)}
        for m in made.metrics
        if m.spec.key in SHOWN_METRICS
    }
    return {
        "symbol": made.symbol,
        "broker": broker,
        "has_facts": has_facts,
        "price": None if made.price is None else str(made.price),
        "price_date": None if made.price_date is None else made.price_date.isoformat(),
        "market_cap": None if made.market_cap is None else str(made.market_cap),
        "score": _num(made.score.score),
        "cheapness": _num(made.score.cheapness),
        "flags": [f.label for f in made.flags],
        "metrics": metrics,
        "momentum_60d": _num(momentum(closes)),
        "history_points": made.history_points,
        "latest_filing": (
            None
            if latest is None
            else {"form": latest.form, "filed_at": latest.filed_at.isoformat(), "url": latest.url}
        ),
        "why": why_line(made) if has_facts else "재무 없음 — 공시를 아직 안 받았다",
    }


def order_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """점수 내림차순 · 점수 없는 것은 그 뒤(사실은 있음 → 사실 없음) · 같으면 종목 순.

    Args:
        rows: `ranking_row` 결과들.

    Returns:
        정렬된 새 목록.
    """

    def _key(row: dict[str, Any]) -> tuple[int, float, str]:
        score = row.get("score")
        if score is not None:
            return (0, -float(score), str(row["symbol"]))
        return (1 if row.get("has_facts") else 2, 0.0, str(row["symbol"]))

    return sorted(rows, key=_key)


SORTS: tuple[str, ...] = (
    "score",
    "per",
    "pbr",
    "psr",
    "fcf_yield",
    "debt_to_equity",
    "momentum_60d",
    "market_cap",
)
"""정렬 키 — 선언된 것만 (T255 · 모델이 임의 수식을 넘기지 못한다)."""
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class ScreenQuery:
    """스크리닝 표의 필터·정렬·쪽 (T255).

    Attributes:
        sort: 정렬 키 (`SORTS`). 모르면 `score`.
        order: `desc` · `asc`. 점수는 내림차순, 배수(PER 등)는 오름차순이 "싼 순" 이다.
        min_score: 이 점수 미만은 뺀다. None 이면 안 거른다.
        no_flags: 부채 깃발이 있으면 뺀다.
        has_facts: 참이면 2단계(이력 있음)만.
        q: 종목 코드 부분 일치.
        page: 1부터.
        size: 쪽 크기 (≤ `MAX_PAGE_SIZE`).
    """

    sort: str = "score"
    order: str = "desc"
    min_score: float | None = None
    no_flags: bool = False
    has_facts: bool = False
    q: str = ""
    page: int = 1
    size: int = DEFAULT_PAGE_SIZE


def _sort_value(row: dict[str, Any], key: str) -> float | None:
    if key == "score":
        found = row.get("score")
    elif key in ("momentum_60d", "market_cap"):
        found = row.get(key)
    else:
        metrics = cast("dict[str, Any]", row.get("metrics") or {})
        cell = cast("dict[str, Any]", metrics.get(key) or {})
        found = cell.get("value")
    if found is None:
        return None
    try:
        return float(found)
    except (TypeError, ValueError):
        return None


def screen_rows(rows: Sequence[dict[str, Any]], query: ScreenQuery) -> dict[str, Any]:
    """필터 → 정렬 → 쪽. 값이 없는 줄은 어느 정렬에서도 **뒤**에 선다.

    Args:
        rows: `ranking_row` 모양(+ `stage`).
        query: 조건.

    Returns:
        `{rows, page, pages, size, total, sort, order}`.
    """
    sort = query.sort if query.sort in SORTS else "score"
    order = "asc" if query.order == "asc" else "desc"
    size = max(1, min(int(query.size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    needle = query.q.strip().upper()
    kept: list[dict[str, Any]] = []
    for row in rows:
        if query.has_facts and not row.get("has_facts"):
            continue
        if query.no_flags and row.get("flags"):
            continue
        if query.min_score is not None:
            score = row.get("score")
            if score is None or float(score) < query.min_score:
                continue
        if needle and needle not in str(row.get("symbol", "")).upper():
            continue
        kept.append(row)

    def _key(row: dict[str, Any]) -> tuple[int, float, str]:
        value = _sort_value(row, sort)
        if value is None:
            return (1, 0.0, str(row.get("symbol")))
        return (0, -value if order == "desc" else value, str(row.get("symbol")))

    kept.sort(key=_key)
    total = len(kept)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(int(query.page or 1), pages))
    start = (page - 1) * size
    return {
        "rows": kept[start : start + size],
        "page": page,
        "pages": pages,
        "size": size,
        "total": total,
        "sort": sort,
        "order": order,
    }


__all__ = [
    "CARD_LABEL",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "RANK_WINDOW_DAYS",
    "RECOMMENDED",
    "SHOWN_METRICS",
    "SORTS",
    "ScreenQuery",
    "momentum",
    "order_rows",
    "ranking_row",
    "screen_rows",
    "why_line",
]
