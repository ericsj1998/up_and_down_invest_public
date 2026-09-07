"""T21 ⑤⑥ — 수익선·천장을 **증거금 초과분**(% 또는 금액)으로 받는다 (사용자 확정 2026-08-22).

절대값으로 받던 때 폼 기본값(200·300)이 증거금 1000 보다 낮아 첫 정산에서 판이 멈췄다.
상대값이면 그 함정이 구조적으로 없다 — 그리고 0 과 빈 칸은 다른 뜻이다.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from updown.apps.api.walkforward import over_margin

MARGIN = Decimal(1000)


def test_percent_and_amount_both_sit_above_the_margin() -> None:
    """`20%` 는 증거금의 20% 위, `200` 은 200 위 — 둘 다 증거금보다 낮을 수 없다."""
    assert over_margin("20%", MARGIN, "수익선") == Decimal(1200)
    assert over_margin("200", MARGIN, "수익선") == Decimal(1200)
    assert over_margin(" 100 % ", MARGIN, "한계") == Decimal(2000)
    assert over_margin(0, MARGIN, "수익선") == MARGIN, (
        "0 은 '증거금 바로 위' 지 '안 걸었다' 가 아니다"
    )


def test_empty_means_not_set() -> None:
    """칸을 비우면 None — 판정 경로가 "제한 없음" 으로 읽는다."""
    assert over_margin("", MARGIN, "수익선") is None
    assert over_margin(None, MARGIN, "수익선") is None


@pytest.mark.parametrize("bad", ["-5%", "-10", "abc", "%"])
def test_negative_or_unreadable_is_refused_loudly(bad: str) -> None:
    """음수·못 읽는 값은 400 — 조용히 None 으로 떨어뜨리면 '안 걸었다' 와 구별이 안 된다."""
    with pytest.raises(HTTPException) as caught:
        over_margin(bad, MARGIN, "수익선")
    assert caught.value.status_code == 400
