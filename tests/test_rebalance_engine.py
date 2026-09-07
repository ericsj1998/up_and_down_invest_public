"""리밸런싱 엔진 (T61 M1) — 배분+성과 조립.

막아야 하는 실패:
1. 🔴 예산 합이 총자본과 안 맞는 것.
2. 🔴 입금이 성과(TWR)로 둔갑하는 것.
3. 🔴 바스켓 밖 종목의 돈이 총자본에서 누락돼 사라지는 것.
"""

from datetime import UTC, datetime
from decimal import Decimal

from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import RebalanceEngine
from updown.portfolio.performance import CashFlow, TwrLedger

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _engine(start: int) -> RebalanceEngine:
    basket = Basket(
        as_members(
            [("BTC_USDT", Decimal(1)), ("ETH_USDT", Decimal(1)), ("ZEC_USDT", Decimal("0.33"))]
        )
    )
    return RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(start)))


def test_budgets_split_current_total_by_weight() -> None:
    eng = _engine(233)
    budgets = eng.rebalance(
        {"BTC_USDT": Decimal(100), "ETH_USDT": Decimal(100), "ZEC_USDT": Decimal(33)}
    )
    assert abs(sum(budgets.values(), Decimal(0)) - Decimal(233)) < Decimal("0.01")
    assert budgets["ZEC_USDT"] / budgets["BTC_USDT"] == Decimal("0.33")


def test_deposit_absorbed_not_counted_as_performance() -> None:
    eng = _engine(200)
    # 200 -> 220 (거래손익 +10%) 그리고 100 입금
    budgets = eng.rebalance(
        {"BTC_USDT": Decimal(110), "ETH_USDT": Decimal(110)},
        CashFlow(at=T0, amount=Decimal(100)),
    )
    assert eng.twr_return == Decimal("0.1")  # 입금은 성과 아님
    assert eng.balance == Decimal(320)  # 220 + 100
    assert abs(sum(budgets.values(), Decimal(0)) - Decimal(320)) < Decimal("0.01")


def test_money_outside_basket_is_not_lost() -> None:
    """🔴 바스켓 밖(제거 예정) 종목의 돈도 총자본에 들어가 남은 종목으로 재분배된다."""
    eng = _engine(300)
    # DOGE 는 바스켓에 없지만 아직 세션에 돈이 있다
    budgets = eng.rebalance(
        {
            "BTC_USDT": Decimal(100),
            "ETH_USDT": Decimal(100),
            "ZEC_USDT": Decimal(33),
            "DOGE_USDT": Decimal(67),
        }
    )
    assert eng.balance == Decimal(300)  # DOGE 67 포함
    assert "DOGE_USDT" not in budgets  # 예산은 바스켓 구성원에만
    assert abs(sum(budgets.values(), Decimal(0)) - Decimal(300)) < Decimal("0.01")


def test_twr_chains_over_periods() -> None:
    eng = _engine(100)
    eng.rebalance(
        {"BTC_USDT": Decimal(55), "ETH_USDT": Decimal(55), "ZEC_USDT": Decimal(0)}
    )  # 110, +10%
    eng.rebalance(
        {"BTC_USDT": Decimal(60), "ETH_USDT": Decimal(61), "ZEC_USDT": Decimal(0)}
    )  # 121, +10%
    assert eng.twr_return == Decimal("0.21")
