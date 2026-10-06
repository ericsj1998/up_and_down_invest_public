"""혼합 3.3 (T400 · 2026-10-07) — 돌파 롱 · 짝 다리(삼각 · MACD 숏)가 앞서 열려 있으면 x1.5.

🔴 못 박는 것:
  - 선언: 돌파 롱 다리에만 `entry_peer_open_boost`(삼각 숏 · MACD 숏 · 1개 · x1.5) ·
    묶음 legs_revision 5.
  - 문: 짝 다리 보유가 문턱 이상이면 신규 진입 x1.5 — 브레이크 · 총 명목 여유 **앞에서**
    곱한다(연구 원장 순서) · 불타기엔 안 건다 · 막힌 물음은 그대로 막는다 · 키운 것도
    장치로 적는다(`boost`).
  - 🔴 미래 참조: **같은 시각에 든 짝은 안 센다**(`opened_at < at`) — 연구 원장은 같은
    시각이면 롱을 먼저 처리했다.
  - 시각을 못 주는 포트는 0 으로 친다(키우기는 리스크 증가 — 모르면 안 키운다 · #8-1).
  - 저장본 왕복 · 옛 저장본(키 없음)은 끔.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks, peer_open_boost
from updown.analysis.playbook.types import PeerOpenBoost
from updown.orchestration.rebalancer.gate import SlotGate
from updown.orchestration.rebalancer.legs import FundLeg, leg_gate
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.walkforward.ledger import Outcome

AT = datetime(2026, 10, 7, 7, 55, tzinfo=UTC)
BRK = "private_strategy@0.1.0"
TRI = "private_strategy@0.1.0"
MACD = "private_strategy@0.1.0"


@dataclass
class _Port:
    """돌파 다리 포트 — 자리 · 노출만."""

    exposure: Decimal = Decimal(0)
    count: int = 0

    def open_count(self) -> int:
        return self.count

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return self.exposure


@dataclass
class _ShortPort(_Port):
    """짝 다리 포트 — `at` 앞서 연 보유 수를 준다."""

    before: int = 0

    def open_count_before(self, at: datetime) -> int:
        del at
        return self.before


def _gate(before: int, **kw: object) -> SlotGate:
    return SlotGate(
        ports={"BTC": _Port()},
        slots=6,
        boost_ports=({"SOL": _ShortPort(before=before)},),
        boost_open_min=1,
        boost_mult=Decimal("1.5"),
        **kw,  # type: ignore[arg-type]
    )


class TestDeclaration:
    def test_only_the_breakout_leg_is_boosted(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        got = books["private_strategy"].entry_peer_open_boost
        assert got == PeerOpenBoost(
            legs=("private_strategy", "private_strategy"),
            min=1,
            mult=Decimal("1.5"),
        )
        for name in (
            "private_strategy",
            "private_strategy",
            "private_strategy",
            "private_strategy",
            "private_strategy",
        ):
            assert books[name].entry_peer_open_boost is None, name
        wrapper = books["private_strategy"]
        assert wrapper.legs_revision == 5
        assert wrapper.label is not None and wrapper.label.startswith("혼합 3.3")

    def test_bad_values_are_refused(self) -> None:
        with pytest.raises(PlaybookConfigError):
            peer_open_boost({"legs": [], "min": 1, "mult": "1.5"}, "x")
        with pytest.raises(PlaybookConfigError):
            peer_open_boost({"legs": ["a"], "min": 0, "mult": "1.5"}, "x")
        with pytest.raises(PlaybookConfigError):
            peer_open_boost({"legs": ["a"], "min": 1, "mult": "0"}, "x")


class TestGate:
    def test_a_short_open_before_boosts(self) -> None:
        got = _gate(1).grant(AT, Decimal(4))
        assert got.size == Decimal(6)
        assert got.blocked is None
        assert got.shrunk == "boost"

    def test_no_short_no_boost(self) -> None:
        got = _gate(0).grant(AT, Decimal(4))
        assert got.size == Decimal(4)
        assert got.shrunk is None

    def test_brake_cuts_after_the_boost(self) -> None:
        """연구 원장 순서 — 크기(x1.5)를 정한 뒤 브레이크(x0.25)가 자른다."""
        got = _gate(
            1,
            drawdown=lambda: Decimal("0.2"),
            brake_at=Decimal("0.10"),
            brake_scale=Decimal("0.25"),
        ).grant(AT, Decimal(4))
        assert got.size == Decimal("1.5")
        assert got.shrunk == "boost+brake"

    def test_notional_room_cuts_after_the_boost(self) -> None:
        """총 명목 상한 3.6 x 자리 6 = 21.6 · 이미 18 이 열려 있으면 남은 3.6 으로 자른다."""
        gate = _gate(1, notional_cap=Decimal("3.6"), notional_fit=True)
        gate.ports = {"BTC": _Port(exposure=Decimal(18), count=3)}
        got = gate.grant(AT, Decimal(4))
        assert got.size == Decimal("3.6")
        assert got.shrunk == "boost+notional"

    def test_a_blocked_entry_stays_blocked(self) -> None:
        gate = _gate(1)
        gate.ports = {"BTC": _Port(count=6)}
        got = gate.grant(AT, Decimal(4))
        assert got.size == 0
        assert got.blocked == "slots"

    def test_adds_are_not_boosted(self) -> None:
        got = _gate(1).grant_add(AT, Decimal(2))
        assert got.size == Decimal(2)

    def test_a_port_without_timing_counts_zero(self) -> None:
        """시각을 못 주는 포트(보유 수만 아는 포트)는 키우지 않는다(#8-1)."""
        gate = SlotGate(
            ports={"BTC": _Port()},
            slots=6,
            boost_ports=({"SOL": _Port(count=3)},),
            boost_open_min=1,
            boost_mult=Decimal("1.5"),
        )
        assert gate.grant(AT, Decimal(4)).size == Decimal(4)


def _rec(leg: str, opened: datetime | None, outcome: Outcome = Outcome.OPEN) -> SimpleNamespace:
    return SimpleNamespace(playbook=leg, opened_at=opened, outcome=outcome)


@dataclass
class _Session:
    records: list[SimpleNamespace] = field(default_factory=lambda: list[SimpleNamespace]())

    @property
    def ledger(self) -> SimpleNamespace:
        return SimpleNamespace(records=self.records)


class TestBridgeTiming:
    def test_same_bar_short_is_not_counted(self) -> None:
        """🔴 같은 마감에 든 숏(같은 체결 봉 이름표)은 못 본다 — 연구 원장과 같다."""
        bridge = SessionBridge(session=_Session([_rec(TRI, AT)]))  # type: ignore[arg-type]
        assert bridge.open_count_before_of(TRI, AT) == 0

    def test_earlier_short_is_counted(self) -> None:
        bridge = SessionBridge(session=_Session([_rec(TRI, AT - timedelta(hours=4))]))  # type: ignore[arg-type]
        assert bridge.open_count_before_of(TRI, AT) == 1

    def test_closed_or_other_leg_is_not_counted(self) -> None:
        early = AT - timedelta(hours=4)
        bridge = SessionBridge(
            session=_Session(  # type: ignore[arg-type]
                [_rec(TRI, early, Outcome.STOP_LOSS), _rec(BRK, early), _rec(MACD, None)]
            )
        )
        assert bridge.open_count_before_of(TRI, AT) == 0
        assert bridge.open_count_before_of(MACD, AT) == 0


class _Source:
    """다리 보기가 묻는 세션 다리 — 기록 목록 하나."""

    def __init__(self, recs: list[SimpleNamespace]) -> None:
        self.recs = recs

    def open_count_of(self, leg: str) -> int:
        return sum(1 for r in self.recs if r.playbook == leg and r.outcome is Outcome.OPEN)

    def exits_of(self, leg: str) -> list[tuple[datetime, bool]]:
        del leg
        return []

    def open_exposure_of(self, leg: str) -> Decimal:
        del leg
        return Decimal(0)

    def band_breaks(self, at: datetime) -> int:
        del at
        return 0

    def open_count_before_of(self, leg: str, at: datetime) -> int:
        return sum(
            1
            for r in self.recs
            if r.playbook == leg and r.outcome is Outcome.OPEN and r.opened_at < at
        )


def _legs() -> tuple[FundLeg, ...]:
    def leg(attr: str, syms: tuple[str, ...], boost: bool = False) -> FundLeg:
        return FundLeg(
            playbook=attr.split("@")[0],
            attribution=attr,
            symbols=syms,
            leverage=Decimal(6),
            exposure=Decimal(4),
            timeframe="1h",
            slots=6,
            peer_open_boost=((TRI, MACD), 1, Decimal("1.5")) if boost else None,
        )

    return (leg(BRK, ("BTC",), boost=True), leg(TRI, ("SOL",)), leg(MACD, ("ETH",)))


class TestLegGate:
    def test_a_triangle_short_on_another_symbol_boosts_the_breakout(self) -> None:
        ports = {
            "BTC": _Source([]),
            "SOL": _Source([_rec(TRI, AT - timedelta(hours=4))]),
            "ETH": _Source([]),
        }
        gate = leg_gate(ports, _legs(), lambda: Decimal(0))
        assert gate.grant_for(BRK, AT, Decimal(4)).size == Decimal(6)
        # 짝 다리 자신은 안 키운다
        assert gate.grant_for(TRI, AT, Decimal(4)).size == Decimal(4)

    def test_same_time_short_does_not_boost(self) -> None:
        ports = {"BTC": _Source([]), "SOL": _Source([]), "ETH": _Source([_rec(MACD, AT)])}
        gate = leg_gate(ports, _legs(), lambda: Decimal(0))
        assert gate.grant_for(BRK, AT, Decimal(4)).size == Decimal(4)


class TestStore:
    def test_round_trip(self) -> None:
        leg = _legs()[0]
        back = FundLeg.from_dict(leg.to_dict())
        assert back == leg
        assert back.peer_open_boost == ((TRI, MACD), 1, Decimal("1.5"))

    def test_old_store_without_the_key_is_off(self) -> None:
        raw = _legs()[0].to_dict()
        raw.pop("peer_open_boost")
        assert FundLeg.from_dict(raw).peer_open_boost is None
