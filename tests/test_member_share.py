"""펀드 종목 예산 — 몫의 합이 총액을 넘지 않는다.

2026-09-05 실계좌 첫 펀드에서 잡힌 Decimal 반올림 꼬리.
"""

from __future__ import annotations

from decimal import Decimal

from updown.apps.api.rebalancer import member_share


def test_shares_never_sum_above_total() -> None:
    total = Decimal("300")
    weights = [Decimal(w) for w in ("2", "2", "1", "1", "1")]  # BTC·ETH 2 · 알트 1 — 합 7
    wsum = sum(weights, Decimal(0))
    shares = [member_share(total, w, wsum) for w in weights]
    assert sum(shares, Decimal(0)) <= total
    assert shares[0] == Decimal("85.71") and shares[2] == Decimal("42.85")


def test_share_is_cents_and_rounds_down() -> None:
    assert member_share(Decimal("100"), Decimal("1"), Decimal("3")) == Decimal("33.33")
    assert member_share(Decimal("1"), Decimal("1"), Decimal("1")) == Decimal("1.00")
