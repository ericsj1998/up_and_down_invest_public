"""리밸런싱 엔진 (T61 M1 · T285) — 배분+성과 조립. 총자본은 펀드가 들고 세션은 증분만 준다.

막아야 하는 실패:
1. 🔴 예산 합이 총자본과 안 맞는 것 (비중 배분).
2. 🔴 입금이 성과(TWR)로 둔갑하는 것.
3. 🔴 바스켓 밖 종목이 실현한 돈이 총자본에서 누락돼 사라지는 것.
4. 🔴 실현 손익이 한 번 이상 세어지는 것 (T285).
"""

from datetime import UTC, datetime
from decimal import Decimal

from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import RebalanceEngine
from updown.portfolio.performance import CashFlow, TwrLedger

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _engine(start: int, *, slots: int = 0) -> RebalanceEngine:
    basket = Basket(
        as_members(
            [("BTC_USDT", Decimal(1)), ("ETH_USDT", Decimal(1)), ("ZEC_USDT", Decimal("0.33"))]
        )
    )
    return RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(start)), slots=slots)


def test_budgets_split_current_total_by_weight() -> None:
    eng = _engine(233)
    budgets = eng.rebalance(Decimal(0))
    assert abs(sum(budgets.values(), Decimal(0)) - Decimal(233)) < Decimal("0.01")
    assert budgets["ZEC_USDT"] / budgets["BTC_USDT"] == Decimal("0.33")


def test_deposit_absorbed_not_counted_as_performance() -> None:
    eng = _engine(200)
    # 200 -> 220 (거래손익 +20) 그리고 100 입금
    budgets = eng.rebalance(Decimal(20), CashFlow(at=T0, amount=Decimal(100)))
    assert eng.twr_return == Decimal("0.1")  # 입금은 성과 아님
    assert eng.balance == Decimal(320)  # 220 + 100
    assert abs(sum(budgets.values(), Decimal(0)) - Decimal(320)) < Decimal("0.01")


def test_pnl_is_an_increment_not_a_level() -> None:
    """🔴 T285 — 증분 0 이면 총자본이 그대로다.

    예전엔 평가금액 합을 다시 넣어 누적 손익률이 또 곱혔다.
    """
    eng = _engine(100)
    eng.rebalance(Decimal(-10))
    assert eng.balance == Decimal(90)
    eng.rebalance(Decimal(0))
    eng.rebalance(Decimal(0))
    assert eng.balance == Decimal(90)


def test_slot_budgets_exceed_total_without_inflating_it() -> None:
    """자리 배분: 예산 = 총자본 ÷ 자리 를 종목마다 — 합이 총자본을 넘어도 총자본은 안 변한다."""
    eng = _engine(300, slots=3)
    budgets = eng.rebalance(Decimal(0))
    assert all(b == Decimal(100) for b in budgets.values())
    assert eng.balance == Decimal(300)


def test_twr_chains_over_periods() -> None:
    eng = _engine(100)
    eng.rebalance(Decimal(10))  # 110, +10%
    eng.rebalance(Decimal(11))  # 121, +10%
    assert eng.twr_return == Decimal("0.21")
