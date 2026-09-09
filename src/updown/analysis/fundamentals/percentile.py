"""자기 역사 백분위 — "이 회사치고 싼가" (T243).

절대 PER 20 이 싼지는 업종마다 다르지만, **그 회사의 5년 중 하위 10%** 는 뜻이 분명하다.
표본은 월말이다 —
일별로 재면 최근 달이 60 배 과대표집되고, 분기별로 재면 20 점뿐이라 백분위가 5 단위로 끊긴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from updown.common.numeric import fixed_context

_HUNDRED = Decimal(100)


def month_ends(until: date, *, years: int) -> list[date]:
    """`until` 이전 `years` 년의 월말 날짜 (오름차순 · `until` 자체는 제외).

    Args:
        until: 기준일.
        years: 년 수.

    Returns:
        월말 날짜들.
    """
    out: list[date] = []
    first_of_month = until.replace(day=1)
    cursor = first_of_month - timedelta(days=1)  # 지난달 말
    floor = until - timedelta(days=365 * years + years // 4)
    while cursor >= floor:
        out.append(cursor)
        cursor = cursor.replace(day=1) - timedelta(days=1)
    out.reverse()
    return out


def percentile_rank(history: Sequence[Decimal], current: Decimal) -> Decimal:
    """`current` 가 역사에서 몇 % 위치인가 (작을수록 낮은 값 · 0~100).

    Args:
        history: 과거 값 (순서 무관 · 비어 있으면 안 된다).
        current: 지금 값.

    Returns:
        `current` 이하인 과거 값의 비율 x 100. 동률은 절반만 센다 (중앙 순위).
    """
    if not history:
        raise ValueError("백분위는 표본 없이 정의되지 않는다")
    below = sum(1 for v in history if v < current)
    equal = sum(1 for v in history if v == current)
    with fixed_context():
        return (Decimal(below) + Decimal(equal) / 2) / Decimal(len(history)) * _HUNDRED


__all__ = ["month_ends", "percentile_rank"]
