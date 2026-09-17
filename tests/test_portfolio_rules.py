"""P3 포트폴리오 규칙 — 동시 보유 상한 · 연속 손절 정지 · 자리 예산 · 진입 문 (T279 83차).

막아야 하는 실패:
1. 🔴 규칙이 연구 엔진(`t279_sizing_combo.run`)과 다르게 세는 것 — 톱 3 표가 무효가 된다.
2. 🔴 손절 아닌 청산이 연속을 안 끊는 것 / 다른 날 손절이 오늘로 새는 것.
3. 🔴 자리 배분이 비중 배분으로 조용히 떨어지는 것 (예산 1/6 ≠ 1/3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.decision.allocation import Basket, BasketError, as_members, slot_budgets
from updown.decision.portfolio_rules import day_halted, notional_free, slot_free
from updown.orchestration.rebalancer import RebalanceEngine, SlotGate
from updown.portfolio.performance import TwrLedger

T0 = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


def _h(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


class TestDayHalted:
    def test_two_consecutive_stops_halt_the_rest_of_the_day(self) -> None:
        exits = [(_h(3), True), (_h(5), True)]
        assert day_halted(exits, _h(6), 2)
        assert day_halted(exits, _h(23.5), 2), "한 번 닿으면 그날 끝까지 정지다"

    def test_non_stop_exit_breaks_the_run(self) -> None:
        exits = [(_h(3), True), (_h(4), False), (_h(5), True)]
        assert not day_halted(exits, _h(6), 2)

    def test_order_is_by_time_not_by_listing(self) -> None:
        exits = [(_h(5), True), (_h(4), False), (_h(3), True)]  # 뒤섞인 순서
        assert not day_halted(exits, _h(6), 2)

    def test_yesterdays_stops_do_not_leak(self) -> None:
        exits = [(_h(-2), True), (_h(-1), True)]
        assert not day_halted(exits, _h(1), 2)

    def test_threshold_zero_means_no_rule(self) -> None:
        assert not day_halted([(_h(1), True), (_h(2), True)], _h(3), 0)

    def test_matches_research_loop(self) -> None:
        """연구 엔진 `consec[d] = consec[d] + 1 if stop else 0 · halted if >= 2` 와 같은 답."""
        seq = [True, True, False, True, True, False]
        exits = [(_h(i + 1), stop) for i, stop in enumerate(seq)]
        consec, halted = 0, False
        for _, stop in exits:
            consec = consec + 1 if stop else 0
            halted = halted or consec >= 2
        assert day_halted(exits, _h(7), 2) is halted


class TestSlotFree:
    def test_cap(self) -> None:
        assert slot_free(2, 3)
        assert not slot_free(3, 3)
        assert slot_free(99, 0), "0 은 상한 없음"


class TestSlotBudgets:
    def test_each_member_gets_total_over_slots(self) -> None:
        members = [("A", Decimal(1)), ("B", Decimal(2)), ("C", Decimal(1)), ("D", Decimal(1))]
        basket = Basket(as_members(members))
        budgets = slot_budgets(Decimal(300), basket, 3)
        assert budgets == {s: Decimal(100) for s in "ABCD"}
        assert sum(budgets.values()) > Decimal(300), "합이 총자본을 넘는 것이 자리 배분의 의도다"

    def test_rejects_bad_slots(self) -> None:
        basket = Basket(as_members([("A", Decimal(1))]))
        with pytest.raises(BasketError):
            slot_budgets(Decimal(300), basket, 0)

    def test_engine_uses_slots_when_declared(self) -> None:
        basket = Basket(as_members([("A", Decimal(1)), ("B", Decimal(3))]))
        eng = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(300)), slots=3)
        budgets = eng.rebalance({"A": Decimal(150), "B": Decimal(150)})
        assert budgets == {"A": Decimal(100), "B": Decimal(100)}
        plain = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(300)))
        by_weight = plain.rebalance({"A": Decimal(150), "B": Decimal(150)})
        assert by_weight["B"] == Decimal(225), "slots 0 은 비중 배분 그대로다"


class _Port:
    def __init__(
        self,
        open_count: int,
        exits: list[tuple[datetime, bool]] | None = None,
        exposure: Decimal = Decimal(0),
    ) -> None:
        self._open = open_count
        self._exits = exits or []
        self._exposure = exposure

    def open_exposure(self) -> Decimal:
        return self._exposure

    def open_count(self) -> int:
        return self._open

    def exits(self) -> list[tuple[datetime, bool]]:
        return list(self._exits)


class TestSlotGate:
    def test_blocks_when_slots_full(self) -> None:
        gate = SlotGate(ports={"A": _Port(1), "B": _Port(1), "C": _Port(1)}, slots=3)
        assert gate.blocks(_h(1)) == "slots"

    def test_opens_when_a_slot_frees(self) -> None:
        ports = {"A": _Port(1), "B": _Port(1), "C": _Port(0)}
        gate = SlotGate(ports=ports, slots=3)
        assert gate.blocks(_h(1)) is None

    def test_day_halt_counts_across_symbols(self) -> None:
        ports = {"A": _Port(0, [(_h(3), True)]), "B": _Port(0, [(_h(4), True)])}
        gate = SlotGate(ports=ports, slots=3, halt_after_stops=2)
        assert gate.blocks(_h(5)) == "day_halt"
        assert gate.blocks(_h(30)) is None, "다음 날은 다시 연다"

    def test_follows_the_live_mapping(self) -> None:
        """펀드가 종목을 더하면 같은 매핑을 보는 문도 따라간다."""
        ports: dict[str, _Port] = {"A": _Port(1)}
        gate = SlotGate(ports=ports, slots=2)
        assert gate.blocks(_h(1)) is None
        ports["B"] = _Port(1)
        assert gate.blocks(_h(1)) == "slots"


class TestNotionalCap:
    def test_rule(self) -> None:
        # 자리 3 · 상한 2x: 열린 노출 합 + 새 노출 ≤ 6
        assert notional_free(Decimal("4.5"), Decimal("1.5"), 3, Decimal(2))
        assert not notional_free(Decimal("4.5"), Decimal("3"), 3, Decimal(2))
        assert notional_free(Decimal(99), Decimal(99), 0, Decimal(2)), "자리 수 0 = 상한 없음"
        assert notional_free(Decimal(99), Decimal(99), 3, Decimal(0)), "상한 0 = 없음"

    def test_gate_notional_exact(self) -> None:
        ports = {"A": _Port(1, exposure=Decimal(3)), "B": _Port(1, exposure=Decimal(3))}
        gate = SlotGate(ports=ports, slots=3, notional_cap=Decimal(2))
        assert gate.blocks(_h(1), Decimal(0)) is None
        assert gate.blocks(_h(1), Decimal("0.01")) == "notional"
        free = SlotGate(ports=ports, slots=3)
        assert free.blocks(_h(1), Decimal(9)) is None, "상한 없으면 노출은 안 본다"
