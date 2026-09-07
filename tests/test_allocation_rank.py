"""`rank_members` (T201 · 2.0.0 D2) — 랭크 비중의 결정론과 불변식."""

from decimal import Decimal

import pytest

from updown.decision.allocation import Basket, BasketError, rank_members


def test_rank_scores_by_order() -> None:
    """1등 N점 … 꼴찌 1점 — 값의 크기가 아니라 순위만 쓴다."""
    members = rank_members({"A": Decimal("0.5"), "B": Decimal("0.1"), "C": Decimal("-0.2")})
    got = {m.symbol: m.weight for m in members}
    assert got == {"A": Decimal(3), "B": Decimal(2), "C": Decimal(1)}


def test_ties_break_by_symbol() -> None:
    """같은 입력이면 같은 출력 (규칙 #5) — 동률은 사전순이 앞선다."""
    members = rank_members({"B": Decimal("0.1"), "A": Decimal("0.1")})
    got = {m.symbol: m.weight for m in members}
    assert got == {"A": Decimal(2), "B": Decimal(1)}


def test_all_weights_positive() -> None:
    """모멘텀이 음수여도 꼴찌는 1점 — Basket(비중>0) 검증을 통과해야 한다."""
    members = rank_members({"A": Decimal(0), "B": Decimal("-1")})
    Basket(members, version="v1")  # 불변식 위반이면 여기서 BasketError


def test_empty_rejected() -> None:
    with pytest.raises(BasketError):
        rank_members({})
