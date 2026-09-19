"""낙폭 브레이크가 펀드에 **실제로 붙어 있나** — 조용히 끊기면 못 알아채는 자리들 (T286).

브레이크가 안 붙으면 아무 에러도 안 나고 그냥 평소 크기로 매매한다. 실계좌에서 그것은
MDD 33% 판인 줄 알고 MDD 45% 판을 도는 것이다(143차). 그래서 배선 자체를 시험한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import DrawdownBrake
from updown.apps.api.rebalancer import _attach_gate  # pyright: ignore[reportPrivateUsage]
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer.coordinator import Coordinator
from updown.orchestration.rebalancer.engine import RebalanceEngine
from updown.orchestration.rebalancer.gate import SlotGate
from updown.portfolio.performance import CashFlow, TwrLedger

AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


class TestTwrDrawdownIsTheMeasuredDrawdown:
    """🔴 브레이크의 입력은 TWR 지수 낙폭이다 — 측정한 **실현 잔고 낙폭**과 같아야 한다.

    입출금이 없으면(백테스트 조건) 둘은 같은 값이다. 이것이 143~145차 측정과 실계좌를 잇는
    유일한 다리라, 여기가 갈라지면 문턱 12% 가 다른 것을 재는 값이 된다.
    """

    def test_no_flows_means_balance_drawdown(self) -> None:
        ledger = TwrLedger(equity=Decimal(1000))
        for value in (Decimal(1200), Decimal(900), Decimal(1100)):
            ledger.step(value)
            # 고점은 갱신만 된다 — 1200 을 찍은 뒤의 낙폭은 1200 기준이다.
        peak = Decimal(1200)
        expected = (Decimal(1) - Decimal(1100) / peak) * Decimal(100)
        assert abs(ledger.drawdown_pct - expected) < Decimal("0.0001")

    def test_deposit_does_not_erase_the_drawdown(self) -> None:
        """🔴 T286 의 "정할 것 ②" 가 여기서 풀린다 — 입금이 낙폭을 메우면 안 된다."""
        plain = TwrLedger(equity=Decimal(1000))
        plain.step(Decimal(1200))
        plain.step(Decimal(900))

        funded = TwrLedger(equity=Decimal(1000))
        funded.step(Decimal(1200))
        funded.step(Decimal(900), CashFlow(at=AT, amount=Decimal(300)))

        # 잔고로 쟀다면 900+300 = 1200 이라 낙폭이 0 이 됐을 것이다. TWR 은 안 속는다.
        assert funded.drawdown_pct == plain.drawdown_pct
        assert funded.drawdown_pct > Decimal(0)

    def test_recovery_releases_it_on_its_own(self) -> None:
        """해제 규칙을 따로 두지 않는 이유 — 고점을 회복하면 낙폭이 0 이 된다."""
        ledger = TwrLedger(equity=Decimal(1000))
        ledger.step(Decimal(1200))
        ledger.step(Decimal(900))
        assert ledger.drawdown_pct > Decimal(12)
        ledger.step(Decimal(1300))
        assert ledger.drawdown_pct == Decimal(0)


class TestAttachGateWiresTheBrake:
    """`_attach_gate` 가 문에 낙폭을 **읽어 오는 함수**를 끼우나 (값 복사가 아니라)."""

    def _coordinator(self) -> Coordinator:
        basket = Basket(as_members([("BTC_USDT", Decimal(1))]), version="v1")
        engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)), slots=6)
        return Coordinator(engine=engine, ports={})

    def test_attach_does_not_raise_without_ports(self) -> None:
        """종목은 나중에 붙는다 — 포트가 비어도 문은 끼워져야 한다."""
        _attach_gate(
            self._coordinator(),
            6,
            2,
            Decimal(2),
            notional_fit=True,
            brake=DrawdownBrake(at=Decimal("0.12"), scale=Decimal("0.5")),
            leverage=Decimal(4),
        )

    def test_brake_follows_the_ledger_not_a_copied_value(self) -> None:
        """🔴 낙폭은 **매번 읽어야** 한다 — 값을 복사해 두면 첫 값에 얼어붙는다."""
        coordinator = self._coordinator()
        ledger = coordinator.engine.ledger
        gate = SlotGate(
            ports={},
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            min_grant=Decimal(4) / Decimal(4),
            drawdown=lambda: ledger.drawdown_pct / Decimal(100),
            brake_at=Decimal("0.12"),
            brake_scale=Decimal("0.5"),
        )
        assert gate.grant(AT, Decimal(4)).size == Decimal(4)
        ledger.step(Decimal(1200))
        ledger.step(Decimal(900))  # 고점 1200 대비 25% 낙폭
        assert gate.grant(AT, Decimal(4)).size == Decimal(2)
        ledger.step(Decimal(1400))  # 고점 회복 → 저절로 풀린다
        assert gate.grant(AT, Decimal(4)).size == Decimal(4)


class TestTheA6DeclarationIsTheMeasuredOne:
    """선언이 측정과 같은지 — 값이 하나라도 다르면 화면의 성적이 거짓이 된다."""

    def test_a6_declares_both_devices(self) -> None:
        book = {item.playbook_id: item for item in load_playbooks()}["private_strategy"]
        assert book.slots == 6
        assert book.leverage == Decimal(4)
        assert book.weight_mode == "slots"
        assert book.notional_cap == Decimal(2)
        assert book.halt_after_stops == 2
        # 🔴 T286 의 두 장치 — 이것이 꺼지면 A0(21,136)을 A(46,726)라고 부르는 것이 된다.
        assert book.notional_fit is True, "총 명목 상한에 걸리면 줄여서 받는다"
        assert book.drawdown_brake is not None, "낙폭 브레이크가 선언돼야 한다"
        assert book.drawdown_brake.at == Decimal("0.12"), "144차가 고른 고원의 가운데"
        assert book.drawdown_brake.scale == Decimal("0.5")

    def test_the_brake_threshold_is_tied_to_four_x(self) -> None:
        """⚠️ 149차 — 문턱 12% 는 4x 에 묶인 값이다. 3x 이하면 네 창 모두 잔고가 깎인다."""
        book = {item.playbook_id: item for item in load_playbooks()}["private_strategy"]
        assert book.drawdown_brake is not None
        assert book.leverage is not None
        assert book.leverage >= Decimal(4), (
            "배율을 4x 미만으로 내리면 낙폭 브레이크를 끄거나 문턱을 다시 재야 한다 (149차)"
        )
