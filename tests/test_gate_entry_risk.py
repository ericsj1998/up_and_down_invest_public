"""펀드 문의 진입별 위험 검사 칸 둘 · 세션의 신호 봉 건너뜀 칸 (T1004 · 2026-11-24).

🔴 못 박는 것:
  - 선언이 없으면 한 글자도 안 바뀐다(문 · 세션 · 저장본 꼴).
  - 청산 거리 문: 손절 폭 ≥ 100 ÷ 허용 크기 - 유지 증거금이면 막는다(경계 포함) ·
    크기를 다 정한 뒤 잰다.
  - 위험 합 상한: 창 안 n 번째 이상 진입만 · 넘으면 남은 몫까지 줄임 · 최소 진입 밑이면 막음 ·
    창 시작 포함 · 모르는 포트가 섞이면 막음(#8-1) · 불타기엔 안 건다.
  - 순서: 위험 합 상한(줄임) → 청산 거리 문(막음).
  - 신호 봉 건너뜀: 두 조건이 **다** 참일 때만 · 모르면 통과 · 조기 진입은 부분 봉으로 잰다.
  - 선언 변환기는 모르는 키 · 범위 밖 값을 거절한다.
"""

from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.analysis.indicators.atr import atr
from updown.analysis.playbook.select import (
    PlaybookConfigError,
    cluster_risk_cap,
    entry_risk_guard,
    weak_bar_skip,
)
from updown.analysis.playbook.types import (
    ClusterRiskCap,
    EntryRiskGuard,
    Family,
    Playbook,
    WeakBarSkip,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.decision.portfolio_rules import (
    Grant,
    cluster_risk_room,
    stop_distance_pct,
    stop_past_slot_margin,
)
from updown.orchestration.rebalancer import SessionBridge
from updown.orchestration.rebalancer.gate import LegGate, LegPort, SlotGate
from updown.orchestration.rebalancer.legs import FundLeg, leg_gate
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Outcome

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
LEG = "leg_a@0.1.0"
GUARD = EntryRiskGuard(maint_pct=Decimal("0.5"))
CAP = ClusterRiskCap(cap_pct=Decimal(12), min_n=3, window_minutes=30)
Risk = tuple[datetime, Decimal, Decimal]


class Port:
    """세션 하나인 척 — 진입 위험(시각 · 크기 · 손절 폭 %)을 준다."""

    def __init__(self, risks: list[Risk] | None = None) -> None:
        self._risks = risks

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return Decimal(0)

    def entry_risks(self) -> list[Risk] | None:
        return self._risks


class NoRiskPort:
    """진입 위험을 못 주는 옛 포트."""

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return Decimal(0)


def prior(minutes_ago: int, size: str = "6", stop: str = "5") -> Risk:
    """앞선 진입 한 건 — 자리 6 에서 크기 6 · 손절 5% 면 위험 5%."""
    return (AT - minutes_ago * MIN, Decimal(size), Decimal(stop))


class TestRules:
    def test_stop_past_slot_margin_boundary(self) -> None:
        # 크기 6 → 100 ÷ 6 - 0.5 = 16.1666…% 가 문턱(경계 포함).
        edge = Decimal(100) / Decimal(6) - Decimal("0.5")
        assert stop_past_slot_margin(Decimal(6), edge, Decimal("0.5"))
        assert not stop_past_slot_margin(Decimal(6), edge - Decimal("0.0001"), Decimal("0.5"))
        assert stop_past_slot_margin(Decimal(6), Decimal(20), Decimal("0.5"))
        assert not stop_past_slot_margin(Decimal(0), Decimal(99), Decimal("0.5"))

    def test_cluster_room(self) -> None:
        kw: dict[str, Any] = {"cap_pct": Decimal(12), "min_n": 3}
        # 앞 두 건 위험 10% · 이 건 5% → 넘는다 → (12 - 10) ÷ 5 x 6 = 2.4
        got = cluster_risk_room(Decimal(6), Decimal(5), 6, used=Decimal(10), count=2, **kw)
        assert got == Decimal("2.4")
        # 둘째 진입(count 1)은 넘어도 안 건다
        assert cluster_risk_room(Decimal(6), Decimal(5), 6, used=Decimal(10), count=1, **kw) is None
        # 안 넘으면 None(경계 = 딱 상한은 통과)
        assert (
            cluster_risk_room(Decimal("2.4"), Decimal(5), 6, used=Decimal(10), count=2, **kw)
            is None
        )
        # 이미 다 썼으면 0 이하
        got = cluster_risk_room(Decimal(6), Decimal(5), 6, used=Decimal(13), count=2, **kw)
        assert got is not None and got < 0

    def test_stop_distance_pct(self) -> None:
        assert stop_distance_pct(Decimal(100), Decimal(95)) == Decimal(5)
        assert stop_distance_pct(Decimal(100), Decimal(105)) == Decimal(5)
        assert stop_distance_pct(Decimal(0), Decimal(1)) is None


class TestDeclaration:
    def test_parses(self) -> None:
        assert entry_risk_guard({"maint_pct": "0.5"}, "t") == GUARD
        assert cluster_risk_cap({"cap_pct": "12", "min_n": 3, "window_minutes": 30}, "t") == CAP
        assert cluster_risk_cap({"cap_pct": "12"}, "t") == CAP  # 기본 3 건 · 30 분
        assert weak_bar_skip({"range_atr": "3", "ext_pct": "2"}, "t") == WeakBarSkip(
            range_atr=Decimal(3), ext_pct=Decimal(2)
        )

    @pytest.mark.parametrize(
        ("parse", "raw"),
        [
            (entry_risk_guard, {"maint_pct": "0.5", "extra": 1}),
            (entry_risk_guard, {}),
            (entry_risk_guard, {"maint_pct": "-1"}),
            (entry_risk_guard, {"maint_pct": "100"}),
            (cluster_risk_cap, {"cap_pct": "0"}),
            (cluster_risk_cap, {"cap_pct": "12", "min_n": 0}),
            (cluster_risk_cap, {"cap_pct": "12", "window_minutes": 0}),
            (cluster_risk_cap, {"cap_pct": "12", "hours": 1}),
            (cluster_risk_cap, {"min_n": 3}),
            (weak_bar_skip, {"range_atr": "0", "ext_pct": "1"}),
            (weak_bar_skip, {"range_atr": "3"}),
            (weak_bar_skip, {"range_atr": "3", "ext_pct": "1", "bars": 20}),
            (weak_bar_skip, "3"),
        ],
    )
    def test_rejects_nonsense(self, parse: Any, raw: Any) -> None:
        with pytest.raises(PlaybookConfigError):
            parse(raw, "t")


class TestOffByDefault:
    def test_stop_pct_alone_changes_nothing(self) -> None:
        gate = SlotGate(ports={"A": NoRiskPort()}, slots=6)  # type: ignore[dict-item]
        plain = gate.grant(AT, Decimal(6))
        assert gate.grant(AT, Decimal(6), stop_pct=Decimal(90)) == plain
        assert plain == Grant(Decimal(6), None)

    def test_leg_gate_without_rules_is_grant_for(self) -> None:
        gate = LegGate({LEG: SlotGate(ports={"A": NoRiskPort()}, slots=6)})  # type: ignore[dict-item]
        want = gate.grant_for(LEG, AT, Decimal(6))
        assert gate.grant_stop_for(LEG, AT, Decimal(6), Decimal(90)) == want
        assert gate.grant_stop_for(LEG, AT, Decimal(6), None) == want
        assert gate.grant_stop_for("nobody@0", AT, Decimal(6), Decimal(1)).blocked == "leg"


class TestRiskGuard:
    def gate(self, **kw: Any) -> SlotGate:
        return SlotGate(ports={"A": Port([])}, slots=6, risk_guard=GUARD, **kw)

    def test_just_below_passes_and_at_the_edge_blocks(self) -> None:
        edge = Decimal(100) / Decimal(6) - Decimal("0.5")
        gate = self.gate()
        got = gate.grant(AT, Decimal(6), stop_pct=edge - Decimal("0.01"))
        assert got == Grant(Decimal(6), None)
        got = gate.grant(AT, Decimal(6), stop_pct=edge)
        assert got.blocked == "risk_guard" and got.size == 0
        assert gate.risk_guard_blocks == 1

    def test_judged_on_the_granted_size_not_the_asked_one(self) -> None:
        # 한 건 상한이 6 → 3 으로 줄이면 청산 거리는 100 ÷ 3 - 0.5 = 32.8% — 손절 20% 는 통과한다.
        gate = self.gate(entry_cap=Decimal(3))
        got = gate.grant(AT, Decimal(6), stop_pct=Decimal(20))
        assert got.blocked is None and got.size == Decimal(3) and got.shrunk == "entry_cap"
        assert self.gate().grant(AT, Decimal(6), stop_pct=Decimal(20)).blocked == "risk_guard"

    def test_without_a_stop_it_is_not_judged_here(self) -> None:
        # `blocks` · 연구 걸음처럼 손절을 모르는 물음 — 예전과 같다.
        assert self.gate().grant(AT, Decimal(6)).blocked is None
        assert self.gate().blocks(AT, Decimal(6)) is None

    def test_add_on_is_untouched(self) -> None:
        assert self.gate().grant_add(AT, Decimal(6)) == Grant(Decimal(6), None)

    def test_leg_gate_holds_when_the_stop_is_unknown(self) -> None:
        # 🔴 지킬 수 없는 한도는 보류다(#8-1).
        gate = LegGate({LEG: self.gate()})
        assert gate.grant_stop_for(LEG, AT, Decimal(6), None).blocked == "risk_stop"
        assert gate.grant_stop_for(LEG, AT, Decimal(6), Decimal(5)).blocked is None


class TestClusterRisk:
    def gate(self, risks: list[Risk], **kw: Any) -> SlotGate:
        return SlotGate(
            ports={"A": Port(risks[:1]), "B": Port(risks[1:])}, slots=6, cluster_risk=CAP, **kw
        )

    def test_first_two_are_untouched(self) -> None:
        assert self.gate([]).grant(AT, Decimal(6), stop_pct=Decimal(15)) == Grant(Decimal(6), None)
        # 둘째 — 앞 한 건 위험 10% + 이 건 15% 로 상한을 넘지만 n 번째가 아니다.
        got = self.gate([prior(0, stop="10")]).grant(AT, Decimal(6), stop_pct=Decimal(15))
        assert got == Grant(Decimal(6), None)

    def test_third_is_shrunk_to_the_room_left(self) -> None:
        gate = self.gate([prior(0), prior(10)])
        got = gate.grant(AT, Decimal(6), stop_pct=Decimal(5))
        assert got == Grant(Decimal("2.4"), None, "cluster_risk")
        assert gate.cluster_risk_fits == 1

    def test_third_under_the_cap_is_untouched(self) -> None:
        gate = self.gate([prior(0, stop="3"), prior(10, stop="3")])
        assert gate.grant(AT, Decimal(6), stop_pct=Decimal(5)) == Grant(Decimal(6), None)

    def test_window_start_is_included_and_older_is_not(self) -> None:
        # 정확히 30 분 앞 = 창 안(측정과 같은 부등호) · 31 분 앞 = 창 밖.
        got = self.gate([prior(0), prior(30)]).grant(AT, Decimal(6), stop_pct=Decimal(5))
        assert got.size == Decimal("2.4")
        got = self.gate([prior(0), prior(31)]).grant(AT, Decimal(6), stop_pct=Decimal(5))
        assert got == Grant(Decimal(6), None)

    def test_later_entries_are_not_counted(self) -> None:
        later = (AT + MIN, Decimal(6), Decimal(5))
        got = self.gate([prior(0), later]).grant(AT, Decimal(6), stop_pct=Decimal(5))
        assert got == Grant(Decimal(6), None)

    def test_below_the_minimum_grant_is_blocked(self) -> None:
        gate = self.gate([prior(0), prior(10)], min_grant=Decimal("3.5"))
        got = gate.grant(AT, Decimal(6), stop_pct=Decimal(5))
        assert got.blocked == "cluster_risk" and gate.cluster_risk_blocks == 1
        # 이미 다 쓴 창 — 남은 몫 0 이하
        gate = self.gate([prior(0, stop="7"), prior(10, stop="7")])
        assert gate.grant(AT, Decimal(6), stop_pct=Decimal(5)).blocked == "cluster_risk"

    def test_unknown_port_blocks(self) -> None:
        gate = SlotGate(ports={"A": Port([]), "B": NoRiskPort()}, slots=6, cluster_risk=CAP)  # type: ignore[dict-item]
        assert gate.grant(AT, Decimal(6), stop_pct=Decimal(5)).blocked == "risk_log"
        gate = SlotGate(ports={"A": Port(None)}, slots=6, cluster_risk=CAP)
        assert gate.grant(AT, Decimal(6), stop_pct=Decimal(5)).blocked == "risk_log"

    def test_add_on_is_untouched(self) -> None:
        gate = self.gate([prior(0, stop="7"), prior(10, stop="7")])
        assert gate.grant_add(AT, Decimal(6)) == Grant(Decimal(6), None)

    def test_shrunk_label_joins_the_earlier_one(self) -> None:
        gate = self.gate([prior(0), prior(10)], entry_cap=Decimal(5))
        got = gate.grant(AT, Decimal(6), stop_pct=Decimal(5))
        assert got == Grant(Decimal("2.4"), None, "entry_cap+cluster_risk")

    def test_needs_slots(self) -> None:
        with pytest.raises(ValueError, match="slots"):
            SlotGate(ports={"A": Port([])}, cluster_risk=CAP)


class TestOrder:
    def test_cap_shrinks_first_then_the_guard_judges_the_smaller_size(self) -> None:
        # 손절 20% · 크기 6 → 청산 거리 문만 있으면 막힌다. 상한이 먼저 0.6 으로 줄이면
        # 청산 거리가 166.2% 로 멀어져 통과한다.
        risks = [prior(0), prior(10)]
        both = SlotGate(ports={"A": Port(risks)}, slots=6, cluster_risk=CAP, risk_guard=GUARD)
        got = both.grant(AT, Decimal(6), stop_pct=Decimal(20))
        assert got == Grant(Decimal("0.6"), None, "cluster_risk")
        only = SlotGate(ports={"A": Port(risks)}, slots=6, risk_guard=GUARD)
        assert only.grant(AT, Decimal(6), stop_pct=Decimal(20)).blocked == "risk_guard"

    def test_an_earlier_block_stays(self) -> None:
        class Held(Port):
            def open_count(self) -> int:
                return 1

        full = SlotGate(ports={"A": Held([])}, slots=1, risk_guard=GUARD)
        assert full.grant(AT, Decimal(6), stop_pct=Decimal(99)).blocked == "slots"


@dataclass
class _Rec:
    actor: Actor
    outcome: Outcome
    playbook: str
    placed_at: datetime
    entry: Decimal = Decimal(100)
    planned_stop: Decimal = Decimal(99)
    initial_stop: Decimal | None = Decimal(95)
    leverage: Decimal = Decimal(6)
    held_exposure: Decimal = Decimal(0)
    closed_at: datetime | None = None


class _Ledger:
    def __init__(self, records: list[_Rec]) -> None:
        self.records = records
        self.closed: list[_Rec] = []


class _Session:
    def __init__(self, records: list[_Rec]) -> None:
        self.ledger = _Ledger(records)


def _leg(**kw: Any) -> FundLeg:
    return FundLeg(
        playbook="leg_a",
        attribution=LEG,
        symbols=("AAA", "BBB", "CCC"),
        leverage=Decimal(6),
        exposure=Decimal(4),
        timeframe="1h",
        slots=6,
        **kw,
    )


class TestWiring:
    def test_bridge_reports_first_stop_risk_of_the_leg(self) -> None:
        recs = [
            _Rec(Actor.SYSTEM, Outcome.OPEN, LEG, AT),
            # 처음 손절이 없는 옛 기록은 지금 손절로 본다 · 숏도 폭은 양수
            _Rec(
                Actor.SYSTEM,
                Outcome.STOP_LOSS,
                LEG,
                AT - MIN,
                planned_stop=Decimal(102),
                initial_stop=None,
                leverage=Decimal(3),
            ),
            _Rec(Actor.SYSTEM, Outcome.CANCELLED, LEG, AT),
            _Rec(Actor.SYSTEM, Outcome.OPEN, "other@0.1.0", AT),
            _Rec(Actor.HUMAN, Outcome.OPEN, LEG, AT),
        ]
        bridge = SessionBridge(_Session(recs))  # type: ignore[arg-type]
        assert bridge.entry_risks_of(LEG) == [
            (AT, Decimal(6), Decimal(5)),
            (AT - MIN, Decimal(3), Decimal(2)),
        ]
        port = LegPort(bridge, LEG)
        assert port.entry_risks() == bridge.entry_risks_of(LEG)

    def test_saved_form_is_unchanged_without_declarations(self) -> None:
        plain = _leg().to_dict()
        assert "entry_risk_guard" not in plain and "cluster_risk_cap" not in plain
        back = FundLeg.from_dict(plain)
        assert back.risk_guard is None and back.cluster_risk is None and back == _leg()

    def test_saved_form_round_trip(self) -> None:
        leg = _leg(risk_guard=GUARD, cluster_risk=CAP)
        saved = leg.to_dict()
        assert saved["entry_risk_guard"] == {"maint_pct": "0.5"}
        assert saved["cluster_risk_cap"] == {"cap_pct": "12", "min_n": 3, "window_minutes": 30}
        assert FundLeg.from_dict(saved) == leg

    def test_leg_gate_carries_both_and_counts_across_symbols(self) -> None:
        def held(at: datetime) -> SessionBridge:
            return SessionBridge(_Session([_Rec(Actor.SYSTEM, Outcome.OPEN, LEG, at)]))  # type: ignore[arg-type]

        ports = {
            "AAA": held(AT),
            "BBB": held(AT - 30 * MIN),
            "CCC": SessionBridge(_Session([])),  # type: ignore[arg-type]
        }
        gate = leg_gate(ports, [_leg(risk_guard=GUARD, cluster_risk=CAP)], lambda: Decimal(0))  # type: ignore[arg-type]
        # 앞 두 건 위험 5% 씩 → 셋째(손절 5%)는 2.4 로
        got = gate.grant_stop_for(LEG, AT, Decimal(6), Decimal(5))
        assert got == Grant(Decimal("2.4"), None, "cluster_risk")
        # 선언 없는 다리는 그대로
        gate = leg_gate(ports, [_leg()], lambda: Decimal(0))  # type: ignore[arg-type]
        assert gate.grant_stop_for(LEG, AT, Decimal(6), Decimal(5)) == Grant(Decimal(6), None)


# ── 세션 쪽 ───────────────────────────────────────────────────────────────

COIN = Instrument(Market.GATE, "AAA_USDT", "시험 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
HOURS = 200


def _walk(seed: int) -> list[Candle]:
    rng = random.Random(seed)
    px = 100.0
    out: list[Candle] = []
    for h in range(HOURS):
        nxt = px * (1 + rng.gauss(0, 0.01))
        hi = max(px, nxt) * (1 + abs(rng.gauss(0, 0.003)))
        lo = min(px, nxt) * (1 - abs(rng.gauss(0, 0.003)))
        out.append(
            Candle(
                instrument=COIN,
                timeframe=Timeframe.H1,
                ts=START + timedelta(hours=h),
                open=Decimal(str(round(px, 4))),
                high=Decimal(str(round(hi, 4))),
                low=Decimal(str(round(lo, 4))),
                close=Decimal(str(round(nxt, 4))),
                volume=Decimal(10),
            )
        )
        px = nxt
    return out


def _book(rule: WeakBarSkip | None) -> Playbook:
    return dataclasses.replace(
        Playbook(
            playbook_id="wb",
            version="0",
            market_groups=(MarketGroup.COIN,),
            timeframe=Timeframe.H1,
            regimes=(),
            primary_family=Family.TREND,
            setups=(),
        ),
        weak_bar_skip=rule,
    )


def _session(seed: int) -> Session:
    end = START + timedelta(hours=HOURS - 1)
    session = Session(
        instrument=COIN,
        playbooks=(_book(None),),
        feed=SealedFeed({Timeframe.H1: _walk(seed)}, Seal(start=end - timedelta(hours=2), end=end)),
        ledger=Ledger(seed_cash=Decimal(1000)),
    )
    session.step_frame = Timeframe.H1
    while not session.finished:
        session.step()
    return session


def _measured(window: list[Candle]) -> tuple[Decimal, Decimal]:
    """시험이 따로 잰 두 값 — 범위 ÷ ATR14(직전 봉까지) · 직전 20 봉 최고가 위 거리 %."""
    spans = atr([c.high for c in window], [c.low for c in window], [c.close for c in window], 14)
    before = spans[-2]
    assert before is not None
    last = window[-1]
    top = max(c.high for c in window[-21:-1])
    return (last.high - last.low) / before, (last.close / top - 1) * 100


def _rule(rng: Decimal, ext: Decimal) -> WeakBarSkip:
    return WeakBarSkip(range_atr=rng, ext_pct=ext)


class TestSignalBarSkip:
    def test_measure_matches_the_definition(self) -> None:
        window = _walk(1)
        got = _rule(Decimal(1), Decimal(1)).measure(window)
        assert got == _measured(window)

    def test_too_few_bars_is_unknown(self) -> None:
        rule = _rule(Decimal(1), Decimal(1))
        assert rule.measure(_walk(1)[:20]) is None
        assert rule.weak(_walk(1)[:20]) is None
        assert rule.measure(_walk(1)[:21]) is not None

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_both_conditions_must_hold(self, seed: int) -> None:
        session = _session(seed)
        window = list(session.feed.judged(Timeframe.H1))
        bar = window[-1]
        rng, ext = _measured(window)
        eps = Decimal("0.0001")
        cases = (
            (_rule(rng + eps, ext + eps), True),  # 둘 다 문턱 아래
            (_rule(rng, ext + eps), False),  # 범위가 문턱과 같다(미만이 아니다)
            (_rule(rng + eps, ext), False),  # 거리가 문턱과 같다
            (_rule(rng + eps, ext - 1), False),
        )
        for rule, want in cases:
            assert session._weak_bar(_book(rule), bar) is want, rule  # pyright: ignore[reportPrivateUsage]
        assert session.funnel.get("weak_bar:in") == 1
        assert session.funnel.get("weak_bar:out") == 3

    def test_no_declaration_is_silent(self) -> None:
        session = _session(1)
        bar = session.feed.judged(Timeframe.H1)[-1]
        assert session._weak_bar(_book(None), bar) is False  # pyright: ignore[reportPrivateUsage]
        assert not any(key.startswith("weak_bar") for key in session.funnel)

    def test_a_bar_outside_the_window_is_unknown_and_passes(self) -> None:
        session = _session(1)
        last = session.feed.judged(Timeframe.H1)[-1]
        later = dataclasses.replace(last, ts=last.ts + timedelta(hours=5))
        finer = dataclasses.replace(last, timeframe=Timeframe.M15)
        rule = _rule(Decimal(999), Decimal(999))  # 잴 수 있으면 반드시 약한 봉
        for bar in (later, finer):
            assert session._weak_bar(_book(rule), bar) is False  # pyright: ignore[reportPrivateUsage]
        assert session.funnel.get("weak_bar:unknown") == 2

    def test_early_entry_is_judged_on_the_partial_bar(self) -> None:
        # 조기 진입 창 = 마감 봉들 + 그 시점까지의 부분 봉. 부분 봉은 범위가 작다 — 그 봉으로 잰다.
        session = _session(2)
        closed = list(session.feed.judged(Timeframe.H1))
        full = closed[-1]
        mid = (full.high + full.low) / 2
        partial = dataclasses.replace(full, high=mid, low=mid, close=mid)
        tick = dataclasses.replace(partial, timeframe=Timeframe.M15)
        window = [*closed[:-1], partial]
        rng, ext = _measured(window)
        assert rng == 0  # 범위 0 인 부분 봉
        rule = _rule(Decimal("0.0001"), ext + Decimal("0.0001"))
        assert _measured(closed)[0] > rule.range_atr  # 닫힌 봉으로 쟀다면 통과였다
        session.early_windows = {Timeframe.H1: window}
        assert session._weak_bar(_book(rule), tick) is True  # pyright: ignore[reportPrivateUsage]
        session.early_windows = {}
        assert session._weak_bar(_book(rule), tick) is False  # pyright: ignore[reportPrivateUsage]
        assert session.funnel.get("weak_bar:unknown") == 1


class TestTheGateHearsTheStop:
    def test_a_stop_aware_gate_gets_the_planned_stop_distance(self) -> None:
        heard: list[tuple[str, Decimal | None]] = []

        class Aware:
            def grant(self, at: datetime, exposure: Decimal) -> Grant:  # noqa: ARG002
                return Grant(Decimal(0), "leg")

            def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:  # noqa: ARG002
                return Grant(Decimal(0), "leg")

            def grant_stop_for(
                self,
                leg: str,
                at: datetime,  # noqa: ARG002
                exposure: Decimal,
                stop_pct: Decimal | None,
            ) -> Grant:
                heard.append((leg, stop_pct))
                return Grant(exposure, None)

        @dataclass
        class Setup:
            stop_loss: Decimal
            size_mult: Decimal = Decimal(1)

        session = _session(1)
        session.entry_gate = Aware()
        got = session._gate(  # pyright: ignore[reportPrivateUsage]
            AT,
            Decimal(4),
            LEG,
            entry=Decimal(200),
            setup=Setup(Decimal(190)),  # type: ignore[arg-type]
        )
        assert got == Decimal(4) and heard == [(LEG, Decimal(5))]
        # 손절을 모르면 None 을 넘긴다 — 문이 정한다
        session._gate(AT, Decimal(4), LEG)  # pyright: ignore[reportPrivateUsage]
        assert heard[-1] == (LEG, None)

    def test_a_blocked_risk_guard_is_counted_by_reason(self) -> None:
        @dataclass
        class Setup:
            stop_loss: Decimal
            size_mult: Decimal = Decimal(1)

        session = _session(1)
        session.entry_gate = LegGate(
            {LEG: SlotGate(ports={"A": Port([])}, slots=6, risk_guard=GUARD)}
        )
        got = session._gate(  # pyright: ignore[reportPrivateUsage]
            AT,
            Decimal(6),
            LEG,
            entry=Decimal(100),
            setup=Setup(Decimal(80)),  # type: ignore[arg-type]
        )
        assert got is None and session.funnel.get("gate:risk_guard") == 1


# ── 진입 경로 끝까지(예시 매매법) ─────────────────────────────────────────


def _walked(change: Any = None) -> Session:
    """예시 매매법 판을 끝까지 걷는다 — `change` 로 걷기 전에 판을 바꾼다."""
    from test_sample_end_to_end import _session as sample  # pyright: ignore[reportPrivateUsage]

    session = sample()
    if change is not None:
        change(session)
    for _ in range(20_000):
        if session.finished:
            break
        session.step()
    return session


def _entries(session: Session) -> list[tuple[datetime, Decimal, Decimal, Decimal]]:
    return [
        (item.placed_at, item.entry, item.planned_stop, item.leverage)
        for item in session.ledger.records
    ]


def _with_rule(rule: WeakBarSkip | None) -> Any:
    def change(session: Session) -> None:
        session.playbooks = tuple(
            dataclasses.replace(book, weak_bar_skip=rule) for book in session.playbooks
        )

    return change


def _with_gate(**kw: Any) -> Any:
    def change(session: Session) -> None:
        leg = session.playbooks[0].attribution
        session.entry_gate = LegGate({leg: SlotGate(ports={"A": Port([])}, slots=6, **kw)})

    return change


class TestTheEntryPathEndToEnd:
    def test_baseline_enters(self) -> None:
        assert _entries(_walked())

    def test_a_rule_that_never_fires_changes_nothing(self) -> None:
        base = _walked()
        never = _walked(_with_rule(_rule(Decimal("0.000001"), Decimal(-100))))
        assert _entries(never) == _entries(base)
        assert never.funnel.get("gate:weak_bar") is None

    def test_a_rule_that_always_fires_skips_every_measurable_entry(self) -> None:
        always = _walked(_with_rule(_rule(Decimal(10**6), Decimal(10**6))))
        skipped = always.funnel.get("gate:weak_bar", 0)
        assert skipped >= 1 and skipped == always.funnel.get("weak_bar:in")
        # 잴 수 있었던 진입은 전부 건너뛰었다 — 남은 진입은 "모름" 으로 지난 것뿐이다.
        assert len(always.ledger.records) <= always.funnel.get("weak_bar:unknown", 0)

    def test_a_leg_gate_without_rules_changes_nothing(self) -> None:
        assert _entries(_walked(_with_gate())) == _entries(_walked())

    def test_a_cap_with_no_room_blocks_every_entry(self) -> None:
        # 상한을 0 에 가깝게 · 첫 진입부터 걸면 남은 몫이 최소 진입 밑이라 전부 막힌다 —
        # 세션이 실제 계획 손절 폭을 문까지 넘긴다는 뜻이다(안 넘기면 `risk_stop` 으로 막힌다).
        none = ClusterRiskCap(cap_pct=Decimal("0.000001"), min_n=1)
        blocked = _walked(_with_gate(cluster_risk=none, min_grant=Decimal("0.01")))
        assert not blocked.ledger.records
        assert blocked.funnel.get("gate:cluster_risk", 0) >= 1
        assert blocked.funnel.get("gate:risk_stop") is None

    def test_a_cap_with_room_shrinks_and_still_enters(self) -> None:
        # 최소 진입이 없으면 남은 몫까지 줄여서 든다 — 깔때기에 장치 이름으로 남는다.
        none = ClusterRiskCap(cap_pct=Decimal("0.000001"), min_n=1)
        shrunk = _walked(_with_gate(cluster_risk=none))
        assert shrunk.funnel.get("gate:fit:cluster_risk", 0) >= 1
        base = _entries(_walked())
        got = _entries(shrunk)
        assert got and got[0][:3] == base[0][:3] and got[0][3] < base[0][3]

    def test_a_guard_with_room_changes_nothing(self) -> None:
        assert _entries(_walked(_with_gate(risk_guard=GUARD))) == _entries(_walked())
