"""TWR + 현금흐름 원장 (T61 · §4.18).

막아야 하는 실패:
1. 🔴 입금이 "수익"으로 둔갑하는 것 — TWR 이 입금 때문에 올라가면 성과가 오염된다.
2. 🔴 잔고와 TWR 이 안 갈리는 것 — 잔고는 입출금 포함, TWR 은 순수 성과여야 한다.
3. 🔴 깡통(0 이하)이 -100% 로 안 잡히는 것.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.portfolio.performance import CashFlow, TwrError, TwrLedger

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _dep(amount: int) -> CashFlow:
    return CashFlow(at=T0, amount=Decimal(amount))


def test_pure_growth_no_flows() -> None:
    led = TwrLedger(equity=Decimal(100))
    led.step(Decimal(110))
    assert led.twr_return == Decimal("0.1")
    assert led.balance == Decimal(110)
    assert led.contributed == Decimal(100)


def test_deposit_is_not_a_return() -> None:
    """🔴 입금은 수익이 아니다 — 100 이 110 으로 오른 뒤 100 을 넣어도 TWR 은 +10% 뿐."""
    led = TwrLedger(equity=Decimal(100))
    led.step(Decimal(110), _dep(100))
    assert led.twr_return == Decimal("0.1")  # 성과는 +10% 그대로
    assert led.balance == Decimal(210)  # 잔고는 입금 포함
    assert led.contributed == Decimal(200)
    assert led.money_gain == Decimal(10)


def test_twr_chains_across_deposits() -> None:
    """🔴 입금 뒤에도 성과는 이어붙는다 — 두 기간 +10%씩이면 TWR +21%."""
    led = TwrLedger(equity=Decimal(100))
    led.step(Decimal(110), _dep(100))  # +10%, 잔고 210
    led.step(Decimal(231))  # 210 -> 231 = +10%
    assert led.twr_return == Decimal("0.21")
    assert led.balance == Decimal(231)
    assert led.contributed == Decimal(200)
    assert led.money_gain == Decimal(31)


def test_withdrawal_reduces_balance_not_performance() -> None:
    led = TwrLedger(equity=Decimal(200))
    led.step(Decimal(220), CashFlow(at=T0, amount=Decimal(-50)))  # +10% then withdraw 50
    assert led.twr_return == Decimal("0.1")
    assert led.balance == Decimal(170)
    assert led.contributed == Decimal(150)


def test_bust_is_minus_100() -> None:
    led = TwrLedger(equity=Decimal(100))
    led.step(Decimal(0))
    assert led.twr_return == Decimal(-1)
    assert led.balance == Decimal(0)


def test_deposit_before_any_equity() -> None:
    """시작 0 에서 첫 입금 — 수익률 없이 자본만 생긴다."""
    led = TwrLedger(equity=Decimal(0))
    led.step(Decimal(0), _dep(100))
    assert led.twr_return == Decimal(0)
    assert led.balance == Decimal(100)
    assert led.contributed == Decimal(100)


def test_negative_start_raises() -> None:
    with pytest.raises(TwrError, match="음수"):
        TwrLedger(equity=Decimal(-1))


def test_flows_are_recorded() -> None:
    led = TwrLedger(equity=Decimal(100))
    led.step(Decimal(100), _dep(80))
    led.step(Decimal(180), CashFlow(at=T0, amount=Decimal(-30)))
    assert [f.amount for f in led.flows] == [Decimal(80), Decimal(-30)]
