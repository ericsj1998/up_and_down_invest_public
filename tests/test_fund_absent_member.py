"""재시작 때 판이 못 뜬 펀드 멤버를 **잊지 않는다** (T852 F1).

예전 사슬: 판 하나가 재시작 때 한 번 못 뜸 → 펀드 복원이 그 종목을 바스켓에서 뺌 → 그 구성이
저장됨(일시 실패가 영구 편집) → 감시견이 나중에 그 판을 되살리면 "펀드 종목이 아니다" 로 읽혀
자리 · 브레이크 문이 없는 단독 판이 됨.

지키는 것:

- 못 뜬 멤버는 저장본 값(비중 · 몫 · 정산 mark · 진입 범위)째로 적어 두고, 저장할 때 파일에
  도로 넣는다.
- 그 판이 돌아오면 펀드에 다시 묶인다(문이 붙고 · 몫과 mark 가 저장본 값으로 · 적어 둔 칸은 지움).
- 평소(못 뜬 멤버 없음)에는 저장 내용이 한 글자도 안 바뀐다.
- 이미 있던 판을 같은 키로 다시 띄울 때는 시작 문(호가 · 1 계약)이 막지 않고 경고만 한다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from test_sample_end_to_end import _session  # pyright: ignore[reportPrivateUsage]
from updown.apps.api import rebalancer
from updown.apps.api import walkforward as api
from updown.apps.api.rebalancer import Fund, absent_entry
from updown.decision.allocation import Basket, BasketMember, as_members
from updown.orchestration.rebalancer.coordinator import Coordinator
from updown.orchestration.rebalancer.engine import RebalanceEngine
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.walkforward import Session
from updown.portfolio.performance import TwrLedger

GONE = "S1_USDT"


def fund_with(session: Session, absent: dict[str, dict[str, Any]]) -> Fund:
    basket = Basket(as_members([("S0_USDT", Decimal(1))]), version="v1")
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)), slots=6)
    coordinator = Coordinator(engine=engine, ports=cast("Any", {"S0_USDT": SessionBridge(session)}))
    return Fund(
        fund_id="fund1",
        label="펀드",
        coordinator=coordinator,
        handles={"S0_USDT": "h0"},
        leverage=Decimal(4),
        playbook="p",
        market=session.instrument.market.value,
        slots=6,
        halt_after_stops=2,
        notional_cap=Decimal(2),
        absent=absent,
    )


def test_the_entry_carries_only_what_the_file_had() -> None:
    data = {
        "seeds": {GONE: "55.5", "S0_USDT": "60"},
        "marks": {GONE: "3.25"},
        "entry_legs": {GONE: ["a@1", "b@1"]},
    }
    got = absent_entry(data, BasketMember(symbol=GONE, weight=Decimal(1)))
    assert got == {"weight": "1", "seed": "55.5", "mark": "3.25", "entry": ["a@1", "b@1"]}
    bare = absent_entry({}, BasketMember(symbol=GONE, weight=Decimal(2)))
    assert bare == {"weight": "2"}, "저장본에 없던 값을 지어내지 않는다"


def test_the_file_keeps_the_member_that_did_not_come_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = _session()
    monkeypatch.setattr(rebalancer, "SESSIONS", {"h0": SimpleNamespace(session=held)})
    gone = {"weight": "1", "seed": "55.5", "mark": "3.25", "leg_mark": "1.5", "entry": ["a@1"]}
    data = rebalancer._fund_data(fund_with(held, {GONE: gone}))  # pyright: ignore[reportPrivateUsage]
    assert [m["symbol"] for m in data["basket"]["members"]] == ["S0_USDT", GONE]
    assert data["seeds"][GONE] == "55.5" and data["marks"][GONE] == "3.25"
    assert data["leg_marks"][GONE] == "1.5" and data["entry_legs"][GONE] == ["a@1"]


def test_nothing_changes_when_every_member_came_up(monkeypatch: pytest.MonkeyPatch) -> None:
    held = _session()
    monkeypatch.setattr(rebalancer, "SESSIONS", {"h0": SimpleNamespace(session=held)})
    plain = rebalancer._fund_data(fund_with(held, {}))  # pyright: ignore[reportPrivateUsage]
    assert [m["symbol"] for m in plain["basket"]["members"]] == ["S0_USDT"]
    assert set(plain["seeds"]) == {"S0_USDT"} and plain["marks"] == {} and plain["leg_marks"] == {}
    # 이미 다시 묶인 종목이 적어 둔 칸에 남아 있어도 두 번 쓰지 않는다.
    stale = rebalancer._fund_data(fund_with(held, {"S0_USDT": {"weight": "9"}}))  # pyright: ignore[reportPrivateUsage]
    assert stale["basket"] == plain["basket"]


def test_the_board_that_comes_back_is_bound_to_the_fund_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held, back = _session(), _session()
    back.fund_ready = False  # 되살아난 판은 펀드 파일에 있으므로 문을 기다린다
    monkeypatch.setattr(
        back, "instrument", SimpleNamespace(symbol=GONE, market=back.instrument.market)
    )
    fund = fund_with(held, {GONE: {"weight": "1", "seed": "55.5", "mark": "3.25"}})
    saved: list[str] = []

    def save(item: Any) -> None:
        saved.append(str(item.fund_id))

    def quiet(_: Any) -> None:
        return None

    monkeypatch.setattr(rebalancer, "FUNDS", {"fund1": fund})
    monkeypatch.setattr(rebalancer, "SESSIONS", {"new": SimpleNamespace(session=back)})
    monkeypatch.setattr(rebalancer, "_save_fund", save)
    monkeypatch.setattr(rebalancer, "_log_gate", quiet)

    assert rebalancer.rebind_member("new") == "fund1"

    engine = fund.coordinator.engine
    assert list(engine.basket.symbols) == ["S0_USDT", GONE]
    port = fund.coordinator.ports[GONE]
    assert isinstance(port, SessionBridge) and port.session is back
    assert fund.handles[GONE] == "new" and fund.absent == {}
    assert back.fund_ready is True and back.entry_gate is not None, "문이 붙은 뒤에만 풀린다"
    assert back.ledger.refill is False and back.ledger.wallet_start == Decimal(0)
    assert back.ledger.seed_cash == Decimal("55.5"), "몫은 저장본 값"
    assert back.ledger.margin_budget == Decimal(1000) / Decimal(6)
    assert fund.coordinator.marks[GONE] == Decimal("3.25"), (
        "빠져 있던 동안의 실현 손익이 다음 틱에 펀드로 들어온다"
    )
    assert saved == ["fund1"]


def test_a_stranger_is_still_nobody_s_member(monkeypatch: pytest.MonkeyPatch) -> None:
    held, other = _session(), _session()
    monkeypatch.setattr(
        other, "instrument", SimpleNamespace(symbol="ZZ_USDT", market=other.instrument.market)
    )
    fund = fund_with(held, {GONE: {"weight": "1"}})
    monkeypatch.setattr(rebalancer, "FUNDS", {"fund1": fund})
    monkeypatch.setattr(rebalancer, "SESSIONS", {"x": SimpleNamespace(session=other)})
    assert rebalancer.rebind_member("x") is None
    assert GONE in fund.absent and "ZZ_USDT" not in fund.coordinator.ports


def test_restore_notes_the_member_it_could_not_start() -> None:
    source = inspect.getsource(rebalancer._restore_one)  # pyright: ignore[reportPrivateUsage]
    assert "absent[member.symbol] = absent_entry(data, member)" in source
    assert "absent=absent," in source


def test_a_board_coming_back_under_its_own_key_passes_the_start_gates() -> None:
    """시작 문은 '새 판을 열어도 되나' 를 묻는다 — 다시 띄우기에는 경고만(진입은 실행 중 스위치)."""
    source = inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]
    assert 'resuming = reviving and bool(payload.get("session_id"))' in source
    assert "holding = resuming or (" in source
    assert source.index("holding = resuming or (") < source.index("if not depth.ok:")


def test_a_board_that_passed_the_depth_gate_on_a_warning_starts_with_entries_shut() -> None:
    """호가 점검은 판이 뜬 지 5 분 뒤에야 처음 잰다 — 그 사이 마른 호가에 새로 들면 안 된다."""
    source = inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]
    assert "dry_start = not depth.ok" in source
    shut = source.index("if dry_start:")
    assert source.index("session = Session(") < shut < source.index("runner.run()")
    assert "session.liquid = False" in source[shut : shut + 400]
    # 불타기 · 크기 늘림은 러너의 "말랐다" 표식을 본다 — 그것도 같이 세운다.
    assert 'runner.dry = {"why": depth.why}' in source
    assert source.index('runner.dry = {"why": depth.why}') < source.index("runner.run()")
