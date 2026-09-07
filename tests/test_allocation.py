"""포트폴리오 배분 (T61 · §4.7).

막아야 하는 실패:
1. 🔴 비중이 절대값처럼 취급돼 예산 합이 총자본과 안 맞는 것.
2. 🔴 위성 비중(0.33)이 코어의 1/3 이 아니게 되는 것 — 펀드식 코어+위성이 깨진다.
3. 🔴 빈/중복/음수 바스켓이 조용히 통과하는 것 (규칙 #8).
"""

from decimal import Decimal

import pytest

from updown.decision.allocation import (
    Basket,
    BasketError,
    BasketMember,
    as_members,
    rebalance_orders,
    target_budgets,
)


def _core6() -> Basket:
    return Basket(
        as_members(
            [
                ("BTC_USDT", Decimal(1)),
                ("ETH_USDT", Decimal(1)),
                ("SOL_USDT", Decimal(1)),
                ("XRP_USDT", Decimal(1)),
                ("ZEC_USDT", Decimal("0.33")),
                ("NEAR_USDT", Decimal("0.33")),
            ]
        ),
        version="v1",
    )


def test_equal_weight_splits_evenly() -> None:
    basket = Basket(as_members([("A", Decimal(1)), ("B", Decimal(1)), ("C", Decimal(1))]))
    b = target_budgets(Decimal(300), basket)
    assert b == {"A": Decimal(100), "B": Decimal(100), "C": Decimal(100)}


def test_budgets_sum_to_equity() -> None:
    b = target_budgets(Decimal(1000), _core6())
    assert sum(b.values()) == Decimal(1000)


def test_satellite_gets_one_third_of_core() -> None:
    """🔴 위성 0.33 은 코어 1.0 의 1/3 예산이어야 한다 (펀드식 코어+위성)."""
    b = target_budgets(Decimal(1000), _core6())
    ratio = b["ZEC_USDT"] / b["BTC_USDT"]
    assert ratio == Decimal("0.33")


def test_cash_signal_symbol_still_budgeted() -> None:
    """A안 = 유휴. 배분은 신호를 안 본다 — 모든 종목이 예산을 받는다(세션이 현금으로 들 뿐)."""
    b = target_budgets(Decimal(600), _core6())
    assert all(v > 0 for v in b.values())
    assert set(b) == set(_core6().symbols)


def test_rebalance_orders_are_target_minus_held() -> None:
    basket = Basket(as_members([("A", Decimal(1)), ("B", Decimal(1))]))
    held = {"A": Decimal(120), "B": Decimal(80)}
    delta = rebalance_orders(Decimal(200), basket, held)
    # 목표는 각 100 → A 는 -20(감액), B 는 +20(증액)
    assert delta == {"A": Decimal(-20), "B": Decimal(20)}


def test_rebalance_treats_missing_held_as_zero() -> None:
    basket = Basket(as_members([("A", Decimal(1)), ("B", Decimal(1))]))
    delta = rebalance_orders(Decimal(200), basket, {})
    assert delta == {"A": Decimal(100), "B": Decimal(100)}


def test_empty_basket_raises() -> None:
    with pytest.raises(BasketError, match="비었다"):
        Basket(())


def test_duplicate_symbol_raises() -> None:
    with pytest.raises(BasketError, match="중복"):
        Basket(as_members([("A", Decimal(1)), ("A", Decimal(1))]))


def test_non_positive_weight_raises() -> None:
    with pytest.raises(BasketError, match="0 이하"):
        Basket((BasketMember("A", Decimal(0)),))


def test_negative_equity_raises() -> None:
    with pytest.raises(BasketError, match="음수"):
        target_budgets(Decimal(-1), _core6())
