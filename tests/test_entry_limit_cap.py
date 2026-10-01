"""펀드 문의 **신규 진입 수 상한** · **한 건 처음 노출 상한** (452차 · 2026-09-28).

452차 연구 원장: MACD 롱 다리를 24시간에 1건으로 묶고(L1) MACD 숏 한 건 처음 명목을 잔고 0.75배로
누르자(C75) 연 +570.5% · 평가 MDD 36.8% · 효율 15.52(기준 +554.3% · 41.7% · 13.29).
여기서 못 박는 것 — 연구 원장과 같은 창 · 같은 부등호 · 같은 순서인가, 그리고 모르면 막는가.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.analysis.playbook.select import (
    PlaybookConfigError,
    _entry_exposure_cap,  # pyright: ignore[reportPrivateUsage]
    entry_limit,
)
from updown.analysis.playbook.types import EntryLimit
from updown.orchestration.rebalancer import SessionBridge
from updown.orchestration.rebalancer.gate import LegPort, SlotGate
from updown.orchestration.rebalancer.legs import FundLeg, leg_gate
from updown.orchestration.walkforward.ledger import Actor, Outcome

AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)
LEG = "private_strategy@0.1.0"


class Port:
    """세션 하나인 척 — 열린 수 · 청산 · 노출 · 진입 시각."""

    def __init__(
        self, entries: list[datetime] | None = None, exposure: Decimal = Decimal(0)
    ) -> None:
        self._entries = entries
        self._exposure = exposure

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return self._exposure

    def entries(self) -> list[datetime] | None:
        return self._entries


class NoLogPort:
    """진입 시각을 못 주는 옛 포트."""

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return Decimal(0)


class TestDeclaration:
    def test_entry_limit_parses(self) -> None:
        assert entry_limit({"count": 1, "hours": 24}, "t") == EntryLimit(count=1, hours=24)

    @pytest.mark.parametrize(
        "raw", [{"count": 0, "hours": 24}, {"count": 1, "hours": 0}, {"count": 1}]
    )
    def test_entry_limit_rejects_nonsense(self, raw: dict[str, Any]) -> None:
        with pytest.raises(PlaybookConfigError):
            entry_limit(raw, "t")

    def test_entry_cap_parses_and_rejects_zero(self) -> None:
        assert _entry_exposure_cap({"entry_exposure_cap": "4.5"}, "t") == Decimal("4.5")
        assert _entry_exposure_cap({}, "t") is None
        with pytest.raises(PlaybookConfigError):
            _entry_exposure_cap({"entry_exposure_cap": "0"}, "t")


class TestEntryLimit:
    def test_first_entry_of_the_window_is_free(self) -> None:
        gate = SlotGate(ports={"A": Port([]), "B": Port([])}, entry_limit=1)
        got = gate.grant(AT, Decimal(2))
        assert got.blocked is None and got.size == Decimal(2)

    def test_second_entry_inside_24h_is_blocked_fund_wide(self) -> None:
        # 다른 종목(B)이 23시간 전에 들었다 — 펀드 전체로 센다.
        gate = SlotGate(ports={"A": Port([]), "B": Port([AT - 23 * H])}, entry_limit=1)
        assert gate.grant(AT, Decimal(2)).blocked == "entry_limit"

    def test_same_bar_entry_counts(self) -> None:
        # 같은 정시에 앞 종목이 이미 들었다(`placed_at` = 같은 봉) — 한 정시에 두 건이 새지 않는다.
        gate = SlotGate(ports={"A": Port([]), "B": Port([AT])}, entry_limit=1)
        assert gate.grant(AT, Decimal(2)).blocked == "entry_limit"

    def test_window_start_is_excluded(self) -> None:
        # 연구 원장: `o["in"] > when - 24h` — 정확히 24시간 전 진입은 창 밖이다.
        gate = SlotGate(ports={"B": Port([AT - 24 * H])}, entry_limit=1)
        assert gate.grant(AT, Decimal(2)).blocked is None

    def test_limit_two_allows_two(self) -> None:
        gate = SlotGate(ports={"B": Port([AT - 2 * H])}, entry_limit=2)
        assert gate.grant(AT, Decimal(2)).blocked is None
        gate = SlotGate(ports={"B": Port([AT - 2 * H, AT - H])}, entry_limit=2)
        assert gate.grant(AT, Decimal(2)).blocked == "entry_limit"

    def test_unknown_port_blocks(self) -> None:
        # 🔴 셀 수 없으면 막는다 — 신규 진입은 리스크 증가 · 분류 불명은 보류(#8-1).
        gate = SlotGate(ports={"A": Port([]), "B": NoLogPort()}, entry_limit=1)  # type: ignore[dict-item]
        assert gate.grant(AT, Decimal(2)).blocked == "entry_log"
        gate = SlotGate(ports={"A": Port(None)}, entry_limit=1)
        assert gate.grant(AT, Decimal(2)).blocked == "entry_log"

    def test_off_by_default(self) -> None:
        gate = SlotGate(ports={"B": NoLogPort()})  # type: ignore[dict-item]
        assert gate.grant(AT, Decimal(2)).blocked is None

    def test_add_on_is_not_counted_as_entry(self) -> None:
        # 불타기는 새 자리가 아니다 — 상한과 무관(연구 원장과 같다).
        gate = SlotGate(ports={"B": Port([AT - H])}, entry_limit=1)
        assert gate.grant_add(AT, Decimal(1)).blocked is None


class TestEntryCap:
    def test_caps_first_exposure(self) -> None:
        gate = SlotGate(ports={"A": Port([])}, entry_cap=Decimal("4.5"))
        got = gate.grant(AT, Decimal("6.75"))
        assert got.size == Decimal("4.5") and got.shrunk == "entry_cap" and got.blocked is None

    def test_below_cap_untouched(self) -> None:
        gate = SlotGate(ports={"A": Port([])}, entry_cap=Decimal("4.5"))
        got = gate.grant(AT, Decimal(3))
        assert got.size == Decimal(3) and got.shrunk is None

    def test_brake_first_then_cap(self) -> None:
        # 연구: 낙폭 줄임(x0.25) 뒤의 크기에 상한 — 줄어서 상한 아래면 상한은 일을 안 한다.
        gate = SlotGate(
            ports={"A": Port([])},
            entry_cap=Decimal("4.5"),
            drawdown=lambda: Decimal("0.2"),
            brake_at=Decimal("0.1"),
            brake_scale=Decimal("0.25"),
        )
        got = gate.grant(AT, Decimal(8))
        assert got.size == Decimal(2) and got.shrunk == "brake"

    def test_add_on_not_capped(self) -> None:
        gate = SlotGate(ports={"A": Port([])}, entry_cap=Decimal("4.5"))
        assert gate.grant_add(AT, Decimal(6)).size == Decimal(6)


@dataclass
class _Rec:
    actor: Actor
    outcome: Outcome
    playbook: str
    placed_at: datetime
    held_exposure: Decimal = Decimal(0)
    closed_at: datetime | None = None


class _Ledger:
    def __init__(self, records: list[_Rec]) -> None:
        self.records = records
        self.closed: list[_Rec] = []


class _Session:
    def __init__(self, records: list[_Rec]) -> None:
        self.ledger = _Ledger(records)


class TestWiring:
    def test_bridge_counts_system_non_cancelled_of_the_leg(self) -> None:
        recs = [
            _Rec(Actor.SYSTEM, Outcome.OPEN, LEG, AT - H),
            _Rec(Actor.SYSTEM, Outcome.STOP_LOSS, LEG, AT - 2 * H),
            _Rec(Actor.SYSTEM, Outcome.CANCELLED, LEG, AT - 3 * H),
            _Rec(Actor.SYSTEM, Outcome.OPEN, "other@0.1.0", AT - 4 * H),
        ]
        bridge = SessionBridge(_Session(recs))  # type: ignore[arg-type]
        assert bridge.entries_of(LEG) == [AT - H, AT - 2 * H]
        assert len(bridge.entries()) == 3

    def test_leg_port_reads_its_leg(self) -> None:
        recs = [_Rec(Actor.SYSTEM, Outcome.OPEN, LEG, AT - H)]
        port = LegPort(SessionBridge(_Session(recs)), LEG)  # type: ignore[arg-type]
        assert port.entries() == [AT - H]

    def test_fund_leg_round_trip_and_old_saves(self) -> None:
        leg = FundLeg(
            playbook="private_strategy",
            attribution=LEG,
            symbols=("BNB_USDT",),
            leverage=Decimal(4),
            exposure=Decimal("1.875"),
            timeframe="4h",
            slots=6,
            entry_limit=EntryLimit(count=1, hours=24),
            entry_cap=Decimal("4.5"),
        )
        assert FundLeg.from_dict(leg.to_dict()) == leg
        old = leg.to_dict()
        old.pop("entry_limit")
        old.pop("entry_cap")
        back = FundLeg.from_dict(old)
        assert back.entry_limit is None and back.entry_cap is None

    def test_leg_gate_carries_limit_and_cap(self) -> None:
        recs = [_Rec(Actor.SYSTEM, Outcome.OPEN, LEG, AT - H)]
        leg = FundLeg(
            playbook="private_strategy",
            attribution=LEG,
            symbols=("BNB_USDT",),
            leverage=Decimal(4),
            exposure=Decimal("1.875"),
            timeframe="4h",
            slots=6,
            entry_limit=EntryLimit(count=1, hours=24),
            entry_cap=Decimal("4.5"),
        )
        gate = leg_gate({"BNB_USDT": SessionBridge(_Session(recs))}, [leg], lambda: Decimal(0))  # type: ignore[dict-item]
        assert gate.grant_for(LEG, AT, Decimal(2)).blocked == "entry_limit"
        gate = leg_gate({"BNB_USDT": SessionBridge(_Session([]))}, [leg], lambda: Decimal(0))  # type: ignore[dict-item]
        got = gate.grant_for(LEG, AT, Decimal(6))
        assert got.size == Decimal("4.5") and got.shrunk == "entry_cap"
