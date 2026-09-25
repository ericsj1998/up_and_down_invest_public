"""T308 — 불타기 크기를 펀드 문이 정한다 (`SlotGate.grant_add` · `LegGate.grant_add_for`).

측정(368 ~ 371차 원장 `t296_wave62.run(long_adds=…)`)이 잰 그대로다 — 추가는 **자리를 안 쓰고
같은 날 정지도 안 본다.** 펀드 낙폭 끔 · 낙폭 브레이크 · 총 명목 여유(모자라면 줄이고 최소
미만이면 막음)는 진입과 같은 순서 · 같은 자다. 추가한 노출은 그 뒤 총 명목 상한의 입력이 된다
(`held_exposure`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.decision.portfolio_rules import Grant
from updown.orchestration.rebalancer import SessionBridge
from updown.orchestration.rebalancer.gate import LegGate, SlotGate
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class FakePort:
    """세션 한 개인 척 — 열린 수 · 노출 · 청산만 말한다."""

    def __init__(
        self,
        open_count: int = 0,
        exposure: Decimal = Decimal(0),
        exits: list[tuple[datetime, bool]] | None = None,
    ) -> None:
        self._open = open_count
        self._exposure = exposure
        self._exits = exits or []

    def open_count(self) -> int:
        return self._open

    def exits(self) -> list[tuple[datetime, bool]]:
        return self._exits

    def open_exposure(self) -> Decimal:
        return self._exposure


def _gate(port: FakePort, **kw: object) -> SlotGate:
    return SlotGate(ports={"A": port}, **kw)  # type: ignore[arg-type]


class TestGrantAdd:
    def test_full_slots_do_not_block_an_add(self) -> None:
        # 자리 6 이 다 찼어도 추가는 새 자리가 아니다 — 진입은 막히고 추가는 선다.
        gate = _gate(FakePort(open_count=6, exposure=Decimal(6)), slots=6)
        assert gate.grant(AT, Decimal(1)).blocked == "slots"
        assert gate.grant_add(AT, Decimal("0.5")) == Grant(Decimal("0.5"))

    def test_day_halt_does_not_block_an_add(self) -> None:
        stops = [(AT - timedelta(hours=2), True), (AT - timedelta(hours=1), True)]
        gate = _gate(FakePort(exits=stops), slots=6, halt_after_stops=2)
        assert gate.grant(AT, Decimal(1)).blocked == "day_halt"
        assert gate.grant_add(AT, Decimal("0.5")).blocked is None

    def test_brake_halves_the_add(self) -> None:
        gate = _gate(
            FakePort(),
            slots=6,
            drawdown=lambda: Decimal("0.15"),
            brake_at=Decimal("0.10"),
            brake_scale=Decimal("0.5"),
        )
        assert gate.grant_add(AT, Decimal(2)) == Grant(Decimal(1), None, "brake")

    def test_short_room_shrinks_the_add(self) -> None:
        # 상한 2 x 자리 6 = 12 · 들고 있는 11 → 여유 1 · 요청 2 → 1 로 줄인다.
        gate = _gate(
            FakePort(exposure=Decimal(11)),
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            min_grant=Decimal("0.5"),
        )
        assert gate.grant_add(AT, Decimal(2)) == Grant(Decimal(1), None, "notional")

    def test_room_below_the_floor_blocks_the_add(self) -> None:
        gate = _gate(
            FakePort(exposure=Decimal("11.8")),
            slots=6,
            notional_cap=Decimal(2),
            notional_fit=True,
            min_grant=Decimal("0.5"),
        )
        assert gate.grant_add(AT, Decimal(2)).blocked == "notional"

    def test_fund_drawdown_switch_blocks_the_add(self) -> None:
        gate = _gate(
            FakePort(), slots=6, drawdown=lambda: Decimal("0.2"), halt_dd_at=Decimal("0.1")
        )
        assert gate.grant_add(AT, Decimal(1)).blocked == "fund_dd"

    def test_nothing_asked_is_nothing_granted(self) -> None:
        assert _gate(FakePort()).grant_add(AT, Decimal(0)).blocked == "size"


class TestLegGateAdd:
    def test_routes_to_the_leg(self) -> None:
        full = _gate(FakePort(open_count=6), slots=6)
        gate = LegGate(legs={"long": full})
        assert gate.grant_add_for("long", AT, Decimal("0.5")) == Grant(Decimal("0.5"))

    def test_unknown_leg_and_legless_questions_are_held(self) -> None:
        gate = LegGate(legs={"long": _gate(FakePort())})
        assert gate.grant_add_for("short", AT, Decimal(1)).blocked == "leg"
        assert gate.grant_add(AT, Decimal(1)).blocked == "leg"


def _record(**kw: object) -> TradeRecord:
    base: dict[str, object] = {
        "trade_id": "t1",
        "playbook": "long",
        "actor": Actor.SYSTEM,
        "direction": Direction.LONG,
        "placed_at": AT,
        "opened_at": AT,
        "entry": Decimal(100),
        "planned_stop": Decimal(90),
        "planned_target": Decimal(200),
        "planned_first": Decimal(200),
        "outcome": Outcome.OPEN,
        "leverage": Decimal(4),
    }
    return TradeRecord(**(base | kw))  # type: ignore[arg-type]


class _Ledger:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.records = records


class _Session:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.ledger = _Ledger(records)


class TestHeldExposure:
    def test_add_counts_toward_the_cap(self) -> None:
        record = _record(add_exposure=Decimal(2))
        assert record.held_exposure == Decimal(6)
        port = SessionBridge(_Session([record]))  # type: ignore[arg-type]
        assert port.open_exposure() == Decimal(6)
        assert port.open_exposure_of("long") == Decimal(6)

    def test_fills_win_over_intent_on_both_parts(self) -> None:
        record = _record(
            filled_leverage=Decimal("4.2"), add_exposure=Decimal(2), add_filled=Decimal("1.9")
        )
        assert record.held_exposure == Decimal("6.1")

    def test_no_add_is_the_old_count(self) -> None:
        assert _record().held_exposure == Decimal(4)
        assert _record(filled_leverage=Decimal("3.5")).held_exposure == Decimal("3.5")

    def test_closing_keeps_the_add(self) -> None:
        record = _record(
            add_at=AT, add_price=Decimal(111), add_frac=Decimal("0.5"), add_exposure=Decimal(2)
        )
        done = record.closed(
            at=AT + timedelta(hours=5), price=Decimal(120), outcome=Outcome.STOP_LOSS
        )
        assert (done.add_at, done.add_price, done.add_exposure) == (AT, Decimal(111), Decimal(2))
