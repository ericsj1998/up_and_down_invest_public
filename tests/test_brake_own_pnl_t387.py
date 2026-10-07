"""T387 — 브레이크는 펀드 자기 매매 실현만 본다 · 새 매매법은 새 브레이크.

## 무엇을 막으려는 시험인가

1. 🔴 같은 거래소 계좌의 **수동 매매 손익**(앵커 교정 몫)이 브레이크 낙폭에 들어가는 것 —
   2026-10-02 수동 BTC -104 USDT 가 실계좌 브레이크 원장에 들어갔다. 실제 잔고(펀드 원장)는
   그 손실을 그대로 담아야 한다.
2. 입출금이 브레이크를 움직이는 것(흐름은 성과가 아니다).
3. 뺄 다리가 없을 때 브레이크가 펀드 원장(앵커 포함)으로 돌아가는 것.
4. 새 매매법 · 다리 개정이 옛 매매법의 고점 · 낙폭을 물려받는 것 — 열쇠가 다르면 새 브레이크,
   같으면(재기동) 그대로 이어간다. 옛 저장본(열쇠 없음)은 저장된 매매법 · 개정 번호로 열쇠를 만든다.
5. 앵커 없는 걷기(펀드 재현)의 브레이크 원장이 전과 달라지는 것.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from test_coordinator import FakePort
from updown.apps.api.rebalancer import (
    _ensure_brake_key,  # pyright: ignore[reportPrivateUsage]
)
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.orchestration.rebalancer.coordinator import brake_key, restored_brake_key
from updown.portfolio.performance import CashFlow, TwrLedger


def _coord(start: int = 1000) -> tuple[Coordinator, FakePort]:
    port = FakePort("BTC_USDT")
    basket = Basket(as_members([("BTC_USDT", Decimal(1))]))
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(start)))
    coord = Coordinator(engine=engine, ports={"BTC_USDT": port})  # type: ignore[dict-item]
    coord.tick()  # mark 를 잡는다
    coord.isolate(frozenset())
    return coord, port


class TestOwnPnlOnly:
    def test_manual_loss_in_the_account_moves_money_not_the_brake(self) -> None:
        coord, _port = _coord()
        # 같은 계좌 수동 매매 -104 — 세션 실현은 0 · 계좌 총액(앵커)만 줄었다
        coord.tick(anchor=Decimal(896))
        assert coord.engine.balance == Decimal(896)
        assert coord.engine.ledger.drawdown_pct > 0
        assert coord.brake_drawdown() == 0

    def test_own_losses_still_brake_after_a_manual_loss(self) -> None:
        coord, port = _coord()
        coord.tick(anchor=Decimal(896))
        port.earn(Decimal(-100))  # 펀드 자기 매매 손실
        coord.tick(anchor=Decimal(796))
        assert coord.engine.balance == Decimal(796)
        assert coord.brake_drawdown() == Decimal("0.1")

    def test_withdrawal_and_deposit_do_not_move_the_brake(self) -> None:
        coord, _port = _coord()
        now = datetime(2026, 10, 5, tzinfo=UTC)
        coord.tick(CashFlow(at=now, amount=Decimal(-300), note="출금"), anchor=Decimal(1000))
        coord.tick(CashFlow(at=now, amount=Decimal(500), note="입금"), anchor=Decimal(700))
        assert coord.engine.balance == Decimal(1200)
        assert coord.brake_drawdown() == 0

    def test_without_anchor_the_brake_ledger_is_unchanged(self) -> None:
        coord, port = _coord()
        port.earn(Decimal(-50))
        coord.tick()
        assert coord.engine.balance == Decimal(950)
        assert coord.core is not None and coord.core.equity == Decimal(950)
        assert coord.brake_drawdown() == coord.engine.ledger.drawdown_pct / Decimal(100)


class TestNewMethodNewBrake:
    def test_reset_starts_a_fresh_peak_from_the_balance(self) -> None:
        coord, port = _coord()
        port.earn(Decimal(-250))
        coord.tick()
        assert coord.brake_drawdown() == Decimal("0.25")
        coord.reset_brake(brake_key("새묶음", 3))
        assert coord.brake_drawdown() == 0
        assert coord.core is not None and coord.core.equity == Decimal(750)
        port.earn(Decimal(-75))
        coord.tick()
        assert coord.brake_drawdown() == Decimal("0.1")  # 새 고점(750) 기준
        assert coord.engine.ledger.drawdown_pct > Decimal(30)  # 실제 잔고는 그대로 이어진다

    def test_same_key_keeps_the_peak(self) -> None:
        coord, port = _coord()
        coord.core_key = brake_key("묶음", 2)
        port.earn(Decimal(-200))
        coord.tick()
        _ensure_brake_key(coord, "fund1", brake_key("묶음", 2), "restore")
        assert coord.brake_drawdown() == Decimal("0.2")

    def test_new_revision_inherits_the_brake(self) -> None:
        """T414(2026-10-07) — 열쇠가 바뀌어도 고점 · 낙폭을 승계한다(리셋 = 브레이크 끔)."""
        coord, port = _coord()
        coord.core_key = brake_key("묶음", 2)
        port.earn(Decimal(-200))
        coord.tick()
        _ensure_brake_key(coord, "fund1", brake_key("묶음", 3), "restore")
        assert coord.brake_drawdown() == Decimal("0.2")
        assert coord.core_key == "묶음#3"
        port.earn(Decimal(100))
        coord.tick()
        assert coord.brake_drawdown() == Decimal("0.1")  # 옛 고점(1000) 기준으로 이어진다

    def test_old_files_without_a_key_use_the_saved_revision(self) -> None:
        assert restored_brake_key(None, "묶음", 2) == "묶음#2"
        assert restored_brake_key("", "묶음", 2) == "묶음#2"
        assert restored_brake_key("옛묶음#1", "묶음", 2) == "옛묶음#1"
        # 되살리며 다리를 다시 읽어 개정 번호가 올랐다 → 열쇠가 달라진다
        assert restored_brake_key(None, "묶음", 2) != brake_key("묶음", 3)
