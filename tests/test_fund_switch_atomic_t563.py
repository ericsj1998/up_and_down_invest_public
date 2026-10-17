"""펀드 매매법 전환은 원자적이다 (T563).

지키는 것:

- 아무것도 거두기 전에 모든 멤버의 시작 문을 잰다 — 하나라도 막히면 **아무것도 안 바꾼다**
  (옛 판 · 옛 매매법 · 옛 바스켓 · 틱도 없음) · 자동 대체는 경고 사건 하나를 남기고 다음 창에
  다시 잰다.
- 다 지나면 예전처럼 바꾼다 — 옛 판이 있는 종목 먼저 · 새 종목 나중 · 결과는 바스켓 순서.
- 앵커 틱은 옛 계좌 층으로 한 번 · 총자본이 바뀌면(오르든 내리든) 다시 잰다 · 미뤄도 틱은 한 번.
- 중간에 실패하면 이미 바꾼 종목을 옛 매매법으로 되돌린다(저장 예산 그대로 · 포지션을 든 판은
  유동성 · 1계약 문이 막지 않는다) · 옛 판이 안 뜨면 방금 돌던 새 매매법 판으로 대피 · 일시 실패는
  기다렸다 다시 · 끝까지 못 띄운 종목만 오류로.
- 미리 재기의 예산 합은 띄우는 순서대로 걷는다 — 옛 판의 낡은 저장 예산이 새 종목을 막지 않게 ·
  1계약 바닥에는 여유를 둔다.
- 같은 펀드의 전환 · 편집 · 틱은 겹치지 않는다(펀드 락) · 리더가 아니면 자동 대체를 시도하지 않는다.
- 리더를 놓으면 정지가 도는 전환을 기다린다(시한 · 경고) · 다시 리더가 되어 도는 복원도 펀드 락
  안에서 파일을 다시 읽는다 — 같은 프로세스 재승격이 전환과 겹쳐도 같은 펀드를 두 곳에서 안 바꾼다.
- 되살려 다시 묶는 판의 매매법이 펀드 선언과 다르면 경고한다(반쪽 전환의 흔적).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from updown.apps.api import exchange
from updown.apps.api import rebalancer as mod
from updown.apps.api import walkforward as wf
from updown.common.domain.instrument import Market
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.orchestration.rebalancer.legs import LegError
from updown.orchestration.walkforward.ledger import Outcome
from updown.portfolio.performance import TwrLedger


class _Port:
    """조정자 포트 대역 — 틱 · 떼기가 읽는 것만."""

    def realized(self) -> Decimal:
        return Decimal(0)

    def set_budget(self, budget: Decimal) -> None:
        _ = budget


class _Events:
    """구조화 로그 대역 — (수준, 사건, 본문)을 모은다."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, Any]] = []

    def info(self, event: str, payload: Any = None, **_: Any) -> None:
        self.seen.append(("info", event, payload))

    def warning(self, event: str, payload: Any = None, **_: Any) -> None:
        self.seen.append(("warning", event, payload))

    def error(self, event: str, payload: Any = None, **_: Any) -> None:
        self.seen.append(("error", event, payload))

    def named(self, event: str) -> list[tuple[str, str, Any]]:
        return [item for item in self.seen if item[1] == event]


class _Rec:
    """열린 원장 기록 대역 — 포지션을 든 판."""

    def __init__(self, trade_id: str) -> None:
        self.trade_id = trade_id
        self.outcome = Outcome.OPEN


def _fund(symbols: list[str], handles: dict[str, str]) -> mod.Fund:
    basket = Basket(as_members([(name, Decimal(1)) for name in symbols]), version="v1")
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)), slots=0)
    ports: dict[str, Any] = {name: _Port() for name in handles}
    return mod.Fund(
        fund_id="f1",
        label="t",
        coordinator=Coordinator(engine=engine, ports=ports),  # type: ignore[arg-type]
        handles=dict(handles),
        leverage=Decimal(3),
        playbook="old_pb",
    )


class _World:
    """전환이 부르는 바깥(판 띄우기 · 거두기 · 틱 · 미리 재기)을 기록하는 대역.

    Attributes:
        fail_spawn: `(종목, 매매법)` — 늘 못 띄운다(일시 실패로 친다 · RuntimeError).
        gated: `(종목, 매매법)` — 1계약 문(409). 포지션을 든 되살리기(`keep_managing` + 열린 기록)
            면 지난다 — `_live_start` 의 규칙을 흉내 낸다.
        undo_errors: `(종목, 매매법)` → 되돌리기 시도(`keep_managing`)마다 차례로 낼 예외.
        fail_drop: 거두기가 던지는 판.
        records: 판 → 열린 기록(`open_records_of`).
        on_tick: 틱마다 부를 함수(총자본을 바꾸는 시험).
        blocked_after_tick: 틱 뒤의 미리 재기가 낼 막힘(다시 재기 시험). None 이면 `blocked` 그대로.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events = _Events()
        self.spawned: list[dict[str, Any]] = []
        self.dropped: list[str] = []
        self.ticks = 0
        self.blocked: list[tuple[str, wf.StartBlock]] = []
        self.blocked_after_tick: list[tuple[str, wf.StartBlock]] | None = None
        self.planned: list[wf.PlannedStart] = []
        self.planned_calls: list[list[wf.PlannedStart]] = []
        self.fail_spawn: set[tuple[str, str]] = set()
        self.gated: set[tuple[str, str]] = set()
        self.undo_errors: dict[tuple[str, str], list[Exception]] = {}
        self.fail_drop: set[str] = set()
        self.records: dict[str, tuple[Any, ...]] = {}
        self.on_tick: Callable[[mod.Fund], None] | None = None
        self.declared: list[Any] = []
        self.margins = {"old-A": Decimal("9"), "old-B": Decimal("7")}

        async def spawn(
            symbol: str,
            share: Decimal,
            leverage: Decimal,
            playbook: str,
            **kw: Any,
        ) -> tuple[str, _Port]:
            call = {"symbol": symbol, "playbook": playbook, "share": share, "lev": leverage, **kw}
            self.spawned.append(call)
            key = (symbol, playbook)
            if key in self.fail_spawn:
                raise RuntimeError(f"{symbol} 못 띄움")
            queued = self.undo_errors.get(key)
            if kw.get("keep_managing") and queued:
                raise queued.pop(0)
            if key in self.gated and not (kw.get("keep_managing") and kw.get("inherit")):
                raise HTTPException(409, f"{symbol} 는 이 예산으로 주문이 안 나간다")
            return f"{playbook}-{symbol}-{len(self.spawned)}", _Port()

        async def drop(key: str) -> None:
            self.dropped.append(key)
            if key in self.fail_drop:
                raise RuntimeError("판 행을 못 닫았다")

        async def tick(fund: Any, flow: Any = None) -> None:
            _ = flow
            self.ticks += 1
            if self.on_tick is not None:
                self.on_tick(fund)

        async def blockers(
            plans: list[wf.PlannedStart], market: Market
        ) -> list[tuple[str, wf.StartBlock]]:
            _ = market
            self.planned = list(plans)
            self.planned_calls.append(list(plans))
            if self.blocked_after_tick is not None and self.ticks > 0:
                return list(self.blocked_after_tick)
            return list(self.blocked)

        async def margins(market: Market) -> dict[str, Decimal]:
            _ = market
            return dict(self.margins)

        def records_of(handle: str | None) -> tuple[Any, ...]:
            return self.records.get(handle or "", ())

        def declared() -> list[Any]:
            return list(self.declared)

        monkeypatch.setattr(mod, "_events", self.events)
        monkeypatch.setattr(mod, "_spawn_session", spawn)
        monkeypatch.setattr(mod, "_drop_one", drop)
        monkeypatch.setattr(mod, "_tick", tick)
        monkeypatch.setattr(mod, "start_blockers", blockers)
        monkeypatch.setattr(mod, "run_margins", margins)
        monkeypatch.setattr(mod, "open_records_of", records_of)
        monkeypatch.setattr(mod, "run_store", lambda: None)
        monkeypatch.setattr(mod, "load_playbooks", declared)
        monkeypatch.setattr(mod, "UNDO_RETRY_WAITS_S", ())
        monkeypatch.setattr(mod, "_leader_check", None)
        monkeypatch.setattr(mod, "_FUND_LOCKS", {})

    def back(self, symbol: str, playbook: str) -> list[dict[str, Any]]:
        """그 종목 · 매매법으로 되돌리기가 띄운 시도들(`keep_managing`)."""
        return [
            call
            for call in self.spawned
            if call["symbol"] == symbol
            and call["playbook"] == playbook
            and call.get("keep_managing")
        ]


def _declared_new(**over: Any) -> Any:
    body: dict[str, Any] = {
        "playbook_id": "new_pb",
        "split_legs": False,
        "weight_mode": "slots",
        "slots": 3,
        "halt_after_stops": 2,
        "notional_cap": Decimal(2),
        "notional_fit": True,
        "drawdown_brake": None,
        "breadth_cap": None,
        "legs_revision": None,
    }
    body.update(over)
    return SimpleNamespace(**body)


SYMBOLS = ["AAA_USDT", "BBB_USDT", "CCC_USDT"]
OLD = {"AAA_USDT": "old-A", "BBB_USDT": "old-B"}  # CCC 는 판이 없다(이번 전환이 띄운다)


def _rolled_back(world: _World) -> tuple[str, str, Any]:
    done = world.events.named("fund_playbook_switch_rolled_back")
    assert len(done) == 1
    return done[0]


class TestDeferred:
    """(a) 멤버 하나가 막히면 아무것도 안 바꾼다."""

    async def test_one_blocked_member_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.declared = [_declared_new()]
        world.blocked = [
            ("CCC_USDT", wf.StartBlock("sizing", "1계약을 못 산다", floor=Decimal("3")))
        ]
        fund = _fund(SYMBOLS, OLD)
        before_basket = fund.coordinator.engine.basket
        with pytest.raises(mod.SwitchDeferredError) as got:
            await mod._switch_playbook(fund, "new_pb", {})
        assert got.value.status_code == 409
        assert world.dropped == [] and world.spawned == [], "옛 판을 하나도 안 거둔다"
        assert world.ticks == 0, "미룬 시도는 틱도 안 한다(예산 갱신 주기를 안 바꾼다)"
        assert fund.playbook == "old_pb" and fund.handles == OLD
        assert fund.coordinator.engine.basket is before_basket
        assert (fund.weight_mode, fund.slots, fund.coordinator.engine.slots) == ("static", 0, 0)
        body = got.value.payload
        assert [row["symbol"] for row in body["blocked"]] == ["CCC_USDT"]
        # 새 자리 3 → 예산 = 총자본 ÷ 3 · 바닥 3 에 닿는 총자본 = 3 x 1000 ÷ 333.33 = 9
        assert body["need_total"] == "9.00"
        assert "CCC_USDT(sizing)" in str(got.value.detail)

    async def test_the_preflight_sees_every_member_in_start_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.blocked = [("AAA_USDT", wf.StartBlock("liquidity", "얇다"))]
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(mod.SwitchDeferredError):
            await mod._switch_playbook(fund, "new_pb", {})
        got = [(p.payload["symbol"], p.replacing) for p in world.planned]
        assert got == [("AAA_USDT", "old-A"), ("BBB_USDT", "old-B"), ("CCC_USDT", None)]
        assert all(p.payload["playbook"] == "new_pb" for p in world.planned)

    async def test_auto_supersede_logs_one_warning_and_waits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.blocked = [("CCC_USDT", wf.StartBlock("sizing", "1계약", floor=Decimal("3")))]
        fund = _fund(SYMBOLS, OLD)
        monkeypatch.setattr(mod, "FUNDS", {"f1": fund})
        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", {})

        def target(now: str, books: Any) -> str | None:
            _ = books
            return "new_pb" if now == "old_pb" else None

        monkeypatch.setattr(mod, "superseding_playbook", target)
        assert await mod._supersede_funds() == 0
        deferred = world.events.named("fund_supersede_deferred")
        assert len(deferred) == 1 and deferred[0][0] == "warning"
        assert deferred[0][2]["blocked"][0]["symbol"] == "CCC_USDT"
        assert world.events.named("fund_playbook_supersede_failed") == []
        assert "f1" in mod.SUPERSEDE_DEFERRED and fund.playbook == "old_pb"
        # 문이 열리면(입금 · 가격) 다음 시도가 바꾸고 미룸 표시를 지운다
        world.blocked = []
        assert await mod._supersede_funds() == 1
        assert mod.SUPERSEDE_DEFERRED == {} and fund.playbook == "new_pb"

    async def test_the_retry_loop_runs_until_nothing_is_deferred(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deferred: dict[str, dict[str, Any]] = {}
        calls: list[int] = []

        async def fake_funds() -> int:
            calls.append(1)
            if len(calls) < 3:
                deferred["f1"] = {}
            else:
                deferred.clear()
            return 0

        def no_wait(now: datetime, base: int = 0) -> float:
            _ = (now, base)
            return 0.0

        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", deferred)
        monkeypatch.setattr(mod, "_supersede_funds", fake_funds)
        monkeypatch.setattr(mod, "supersede_delay_s", no_wait)
        monkeypatch.setattr(mod, "_leader_check", None)
        await mod._supersede_later()
        assert len(calls) == 3

    async def test_a_failed_round_does_not_kill_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """한 바퀴가 예외로 끝나도(선언 읽기 등) 고리는 오류를 남기고 다음 창에 다시 잰다."""
        events = _Events()
        deferred: dict[str, dict[str, Any]] = {"f1": {}}
        calls: list[int] = []

        async def flaky_funds() -> int:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("선언을 못 읽었다")
            deferred.clear()
            return 0

        def no_wait(now: datetime, base: int = 0) -> float:
            _ = (now, base)
            return 0.0

        monkeypatch.setattr(mod, "_events", events)
        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", deferred)
        monkeypatch.setattr(mod, "_supersede_funds", flaky_funds)
        monkeypatch.setattr(mod, "supersede_delay_s", no_wait)
        monkeypatch.setattr(mod, "_leader_check", None)
        await mod._supersede_later()
        assert len(calls) == 2
        assert events.named("fund_supersede_round_failed")[0][0] == "error"

    def test_retry_waits_land_in_the_window(self) -> None:
        now = datetime(2026, 10, 18, 13, 10, 0, tzinfo=UTC)  # + 1시간 = 14:10 (창 안)
        assert mod.supersede_delay_s(now, base=mod.SUPERSEDE_RETRY_S) == mod.SUPERSEDE_RETRY_S
        late = datetime(2026, 10, 18, 13, 50, 0, tzinfo=UTC)  # + 1시간 = 14:50 → 15:05
        wait = mod.supersede_delay_s(late, base=mod.SUPERSEDE_RETRY_S)
        landed = datetime.fromtimestamp(late.timestamp() + wait, tz=UTC)
        assert (landed.hour, landed.minute) == (15, 5)


class TestSwitch:
    """(b) 다 지나면 예전처럼 바꾼다."""

    async def test_all_pass_switches_every_member(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _World(monkeypatch)
        fund = _fund(SYMBOLS, OLD)
        await mod._switch_playbook(fund, "new_pb", {})
        assert world.dropped == ["old-A", "old-B"]
        order = [(c["symbol"], c["adopt_from"]) for c in world.spawned]
        assert order == [("AAA_USDT", "old-A"), ("BBB_USDT", "old-B"), ("CCC_USDT", None)]
        assert all(c["playbook"] == "new_pb" for c in world.spawned)
        assert all("capital" in c for c in world.spawned)
        assert not any(c.get("keep_managing") for c in world.spawned), "전환은 문을 낮추지 않는다"
        assert fund.playbook == "new_pb"
        assert list(fund.handles) == SYMBOLS, "바스켓 순서로 둔다"
        assert list(fund.coordinator.ports) == SYMBOLS
        assert fund.handles["CCC_USDT"].startswith("new_pb-CCC_USDT")
        assert not ({"old-A", "old-B"} & mod.REPLACING)

    async def test_fresh_members_start_after_the_replaced_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """새 종목이 바스켓 앞쪽이어도 옛 판이 있는 종목을 먼저 바꾼다(예산 합 문 · T563)."""
        world = _World(monkeypatch)
        fund = _fund(["CCC_USDT", "AAA_USDT", "BBB_USDT"], OLD)
        await mod._switch_playbook(fund, "new_pb", {})
        assert [c["symbol"] for c in world.spawned] == ["AAA_USDT", "BBB_USDT", "CCC_USDT"]
        assert list(fund.handles) == ["CCC_USDT", "AAA_USDT", "BBB_USDT"]


class TestRemeasure:
    """앵커 틱은 옛 계좌 층으로 한 번 · 총자본이 바뀌면 다시 잰다(리뷰 T563 ③)."""

    async def test_a_risen_total_is_measured_again_and_can_defer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """늘어난 총자본도 유동성 · 잔액 · 예산 합 문을 뒤집는다 — 전에는 줄 때만 다시 쟀다."""
        world = _World(monkeypatch)
        world.declared = [_declared_new()]
        world.blocked_after_tick = [("CCC_USDT", wf.StartBlock("liquidity", "얇다"))]

        def richer(fund: mod.Fund) -> None:
            fund.coordinator.engine.ledger = TwrLedger(equity=Decimal(1500))

        world.on_tick = richer
        fund = _fund(SYMBOLS, OLD)
        before = fund.coordinator.engine.basket
        with pytest.raises(mod.SwitchDeferredError) as got:
            await mod._switch_playbook(fund, "new_pb", {})
        assert world.ticks == 1, "앵커 틱 한 번 — 되돌리느라 또 틱하지 않는다"
        assert len(world.planned_calls) == 2
        assert world.dropped == [] and world.spawned == []
        assert fund.playbook == "old_pb" and fund.coordinator.engine.basket is before
        assert (fund.weight_mode, fund.slots, fund.coordinator.engine.slots) == ("static", 0, 0)
        # 두 번째는 새 총자본으로 쟀다 — 1500 ÷ 새 자리 3 = 500
        assert [p.payload["margin"] for p in world.planned_calls[1]] == ["500"] * 3
        assert got.value.payload["total"] == "1500.00"

    async def test_a_fallen_total_is_measured_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _World(monkeypatch)
        world.declared = [_declared_new()]

        def poorer(fund: mod.Fund) -> None:
            fund.coordinator.engine.ledger = TwrLedger(equity=Decimal(900))

        world.on_tick = poorer
        fund = _fund(SYMBOLS, OLD)
        await mod._switch_playbook(fund, "new_pb", {})
        assert len(world.planned_calls) == 2
        assert [c["share"] for c in world.spawned] == [Decimal(300)] * 3, "새 총자본으로 띄운다"

    async def test_an_unchanged_total_is_measured_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        fund = _fund(SYMBOLS, OLD)
        await mod._switch_playbook(fund, "new_pb", {})
        assert len(world.planned_calls) == 1

    async def test_the_anchor_tick_runs_on_the_old_rules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """계좌 층 · 바스켓은 앵커 틱 **뒤에** 바뀐다 — 미뤄도 펀드 파일에 새 구성이 안 남는다."""
        world = _World(monkeypatch)
        world.declared = [_declared_new()]
        seen: list[tuple[int, str]] = []
        world.on_tick = lambda fund: seen.append((fund.slots, fund.playbook))
        fund = _fund(SYMBOLS, OLD)
        await mod._switch_playbook(fund, "new_pb", {})
        assert seen[0] == (0, "old_pb"), "앵커 틱은 옛 계좌 층으로"
        assert seen[-1] == (3, "new_pb"), "끝의 틱은 새 구성으로"


class TestRollback:
    """(c) 중간에 실패하면 옛 매매법으로 되돌린다."""

    async def test_a_failure_midway_restores_the_old_playbook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.declared = [_declared_new()]
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        fund = _fund(SYMBOLS, OLD)
        before_basket = fund.coordinator.engine.basket
        with pytest.raises(HTTPException) as got:
            await mod._switch_playbook(fund, "new_pb", {})
        assert got.value.status_code == 400 and "되돌림" in str(got.value.detail)
        assert fund.playbook == "old_pb"
        assert fund.coordinator.engine.basket is before_basket
        assert (fund.weight_mode, fund.slots, fund.halt_after_stops) == ("static", 0, 0)
        assert fund.notional_cap is None and fund.coordinator.engine.slots == 0
        # 옛 A · 옛 B 를 거뒀고(전환) · 새 A 를 거뒀다(되돌림) — CCC 는 손대지 않았다
        assert world.dropped == ["old-A", "old-B", "new_pb-AAA_USDT-1"]
        back = [c for c in world.spawned if c["playbook"] == "old_pb"]
        assert [(c["symbol"], c["adopt_from"], c["booked"]) for c in back] == [
            ("AAA_USDT", "new_pb-AAA_USDT-1", Decimal("9")),  # 저장 예산 그대로
            ("BBB_USDT", "old-B", Decimal("7")),
        ]
        assert all(c["keep_managing"] is True for c in back)
        assert set(fund.handles) == {"AAA_USDT", "BBB_USDT"}
        assert all(handle.startswith("old_pb-") for handle in fund.handles.values())
        done = _rolled_back(world)
        assert done[0] == "warning"
        assert done[2]["restored"] == ["AAA_USDT", "BBB_USDT"]
        assert done[2]["unmanaged"] == [] and done[2]["kept_new"] == []
        assert world.events.named("fund_playbook_switch_rolling_back"), "로그 먼저(P5)"
        left = {"old-A", "old-B", "new_pb-AAA_USDT-1"} & mod.REPLACING
        assert not left, "되돌린 뒤 감시자 표시는 비운다"

    async def test_a_member_that_cannot_be_restored_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.fail_spawn = {("BBB_USDT", "new_pb"), ("BBB_USDT", "old_pb")}
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        lost = world.events.named("fund_member_unmanaged")
        assert [item[2]["symbol"] for item in lost] == ["BBB_USDT"]
        done = _rolled_back(world)
        assert done[0] == "error" and done[2]["unmanaged"] == ["BBB_USDT"]
        assert "BBB_USDT" not in fund.handles and "BBB_USDT" not in fund.coordinator.ports
        assert fund.handles["AAA_USDT"].startswith("old_pb-"), "나머지는 계속 되돌린다"


class TestRollbackKeepsPositionsManaged:
    """리뷰 T563 ① — 포지션을 든 종목이 되돌리기 끝에 관리 밖으로 남는 길이 없다."""

    async def test_the_held_records_go_to_the_restored_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        rec = _Rec("t-A")
        world.records = {"new_pb-AAA_USDT-1": (rec,)}
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        back = world.back("AAA_USDT", "old_pb")
        assert len(back) == 1
        assert back[0]["inherit"] == (rec,) and back[0]["adopt_from"] == "new_pb-AAA_USDT-1"

    async def test_the_one_contract_gate_does_not_strand_a_held_position(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """옛 판이 옛 저장 예산으로 1계약 문에 걸려도 포지션을 든 되살리기는 뜬다(경고만)."""
        world = _World(monkeypatch)
        world.records = {"new_pb-AAA_USDT-1": (_Rec("t-A"),)}
        world.gated = {("AAA_USDT", "old_pb")}
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert fund.handles["AAA_USDT"].startswith("old_pb-AAA_USDT")
        done = _rolled_back(world)
        assert "AAA_USDT" in done[2]["restored"] and done[2]["unmanaged"] == []

    async def test_an_old_run_that_cannot_start_falls_back_to_the_new_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """옛 판이 안 뜨면 방금 돌던 새 매매법 판을 다시 띄워 펀드에 묶는다(어긋남 경고)."""
        world = _World(monkeypatch)
        world.gated = {("AAA_USDT", "old_pb")}  # 열린 기록 없음 — 문이 그대로 막는다
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert fund.playbook == "old_pb"
        assert fund.handles["AAA_USDT"].startswith("new_pb-AAA_USDT"), "대피 — 새 매매법 판"
        evac = world.back("AAA_USDT", "new_pb")
        assert len(evac) == 1 and evac[0]["adopt_from"] == "new_pb-AAA_USDT-1"
        kept = world.events.named("fund_member_kept_new")
        assert [item[2]["symbol"] for item in kept] == ["AAA_USDT"] and kept[0][0] == "warning"
        done = _rolled_back(world)
        assert done[0] == "warning"
        assert done[2]["kept_new"] == ["AAA_USDT"] and done[2]["unmanaged"] == []

    async def test_a_rate_limited_restore_is_tried_again_after_a_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        monkeypatch.setattr(mod, "UNDO_RETRY_WAITS_S", (0.0,))
        world.records = {"new_pb-AAA_USDT-1": (_Rec("t-A"),)}
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        world.undo_errors = {
            ("AAA_USDT", "old_pb"): [HTTPException(503, "요율 제한")],
            ("AAA_USDT", "new_pb"): [HTTPException(503, "요율 제한")],
        }
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert len(world.back("AAA_USDT", "old_pb")) == 2, "한 바퀴 기다렸다 다시"
        assert fund.handles["AAA_USDT"].startswith("old_pb-AAA_USDT")
        assert world.dropped.count("new_pb-AAA_USDT-1") == 1, "다시 할 때 또 거두지 않는다"
        done = _rolled_back(world)
        assert done[2]["restored"] == ["AAA_USDT", "BBB_USDT"] and done[2]["unmanaged"] == []

    async def test_a_refused_restore_is_not_tried_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        monkeypatch.setattr(mod, "UNDO_RETRY_WAITS_S", (0.0, 0.0))
        world.gated = {("AAA_USDT", "old_pb")}
        world.undo_errors = {("AAA_USDT", "new_pb"): [HTTPException(409, "얇다")] * 3}
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert len(world.back("AAA_USDT", "old_pb")) == 1, "결정적 거절은 기다려도 같다"
        lost = world.events.named("fund_member_unmanaged")
        assert [item[2]["symbol"] for item in lost] == ["AAA_USDT"]
        assert lost[0][2]["holding"] is False

    async def test_an_unmanaged_member_says_it_held_a_position(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.records = {"new_pb-AAA_USDT-1": (_Rec("t-A"),)}
        world.fail_spawn = {
            ("BBB_USDT", "new_pb"),
            ("AAA_USDT", "old_pb"),
        }
        world.undo_errors = {("AAA_USDT", "new_pb"): [RuntimeError("네트워크")]}
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        lost = world.events.named("fund_member_unmanaged")
        assert lost[0][2]["symbol"] == "AAA_USDT" and lost[0][2]["holding"] is True
        assert "러너가 없다" in lost[0][2]["note"]

    async def test_a_new_run_that_cannot_be_dropped_stays_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """새 판을 거두지도 못했으면(러너가 그대로 돈다) 그 판을 묶는다 — 다시 띄우지 않는다."""
        world = _World(monkeypatch)
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        world.fail_drop = {"new_pb-AAA_USDT-1"}
        session = SimpleNamespace(fund_ready=False)
        monkeypatch.setattr(
            mod, "SESSIONS", {"new_pb-AAA_USDT-1": SimpleNamespace(session=session)}
        )
        monkeypatch.setattr(mod, "LIVE_RUNNERS", {"new_pb-AAA_USDT-1": object()})
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert fund.handles["AAA_USDT"] == "new_pb-AAA_USDT-1"
        assert world.back("AAA_USDT", "old_pb") == [] and world.back("AAA_USDT", "new_pb") == []
        assert session.fund_ready is True, "옛 문을 붙이고 진입 보류를 푼다"
        assert _rolled_back(world)[2]["kept_new"] == ["AAA_USDT"]

    async def test_an_added_member_holding_records_is_not_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        symbols = ["AAA_USDT", "BBB_USDT", "CCC_USDT", "DDD_USDT"]
        world.fail_spawn = {("DDD_USDT", "new_pb")}
        world.records = {"new_pb-CCC_USDT-3": (_Rec("t-C"),)}
        fund = _fund(symbols, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert "new_pb-CCC_USDT-3" not in world.dropped, "거두면 그 포지션이 관리 밖이 된다"
        left = world.events.named("fund_member_left_running")
        assert [item[2]["symbol"] for item in left] == ["CCC_USDT"] and left[0][0] == "error"
        done = _rolled_back(world)
        assert done[0] == "error" and done[2]["left_running"] == ["CCC_USDT"]


class _FailingStore:
    """이관이 실패하는 판 저장소."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], str]] = []

    async def transfer_open_trades(self, source: str, trades: list[str], *, to_key: str) -> None:
        self.calls.append((source, trades, to_key))
        raise RuntimeError("DB 끊김")


class TestTransfer:
    """리뷰 T563 ①-d — 기록 이관 실패는 떠 있는 판을 버리지 않는다(경고만)."""

    async def test_a_failed_transfer_keeps_the_restored_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.records = {"new_pb-AAA_USDT-1": (_Rec("t-A"),)}
        world.fail_spawn = {("BBB_USDT", "new_pb")}
        store = _FailingStore()
        monkeypatch.setattr(mod, "run_store", lambda: store)
        fund = _fund(SYMBOLS, OLD)
        with pytest.raises(HTTPException):
            await mod._switch_playbook(fund, "new_pb", {})
        assert store.calls and store.calls[0][0] == "new_pb-AAA_USDT-1"
        assert fund.handles["AAA_USDT"].startswith("old_pb-AAA_USDT"), "떠 있는 판을 묶는다"
        assert _rolled_back(world)[2]["unmanaged"] == []
        assert world.events.named("fund_trade_transfer_failed")[0][0] == "warning"

    async def test_a_failed_transfer_does_not_roll_back_the_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World(monkeypatch)
        world.records = {"old-A": (_Rec("t-A"),)}
        monkeypatch.setattr(mod, "run_store", _FailingStore)
        fund = _fund(SYMBOLS, OLD)
        await mod._switch_playbook(fund, "new_pb", {})
        assert fund.playbook == "new_pb"
        assert world.events.named("fund_trade_transfer_failed")
        assert world.events.named("fund_playbook_switch_rolling_back") == []


class TestFundLock:
    """리뷰 T563 ② — 같은 펀드의 전환 · 편집 · 틱은 겹치지 않는다."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> tuple[_World, mod.Fund, list[str]]:
        world = _World(monkeypatch)
        world.declared = [_declared_new()]
        fund = _fund(SYMBOLS, OLD)
        monkeypatch.setattr(mod, "FUNDS", {"f1": fund})
        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", {})
        order: list[str] = []

        async def slow_switch(target: mod.Fund, new_pb: str, payload: Any) -> None:
            _ = payload
            order.append(f"in:{new_pb}")
            await asyncio.sleep(0.01)
            target.playbook = new_pb
            order.append(f"out:{new_pb}")

        def supersede(now: str, books: Any) -> str | None:
            _ = books
            return "new_pb" if now == "old_pb" else None

        async def status(target: mod.Fund) -> dict[str, Any]:
            return {"playbook": target.playbook}

        def allow(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(mod, "_switch_playbook", slow_switch)
        monkeypatch.setattr(mod, "superseding_playbook", supersede)
        monkeypatch.setattr(mod, "require_playbook_trade", allow)
        monkeypatch.setattr(mod, "require_market_trade", allow)
        monkeypatch.setattr(mod, "_status", status)
        return world, fund, order

    async def test_the_screen_and_the_auto_switch_do_not_overlap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, fund, order = self._setup(monkeypatch)
        request = cast(Any, None)
        await asyncio.gather(
            mod._change_playbook(request, "f1", {"playbook": "new_pb"}),
            mod._supersede_one(fund),
        )
        assert order == ["in:new_pb", "out:new_pb"], "뒤에 온 쪽은 락 안에서 다시 보고 그만둔다"
        assert fund.playbook == "new_pb"

    async def test_a_tick_waits_for_a_running_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, fund, order = self._setup(monkeypatch)

        async def tick(target: mod.Fund, flow: Any = None) -> Any:
            _ = (target, flow)
            order.append("tick")
            return SimpleNamespace(
                budgets={},
                balance=Decimal(0),
                twr_return=Decimal(0),
                missing=(),
                winding_down=(),
            )

        monkeypatch.setattr(mod, "_tick", tick)
        await asyncio.gather(mod._supersede_one(fund), mod.tick("f1"))
        assert order == ["in:new_pb", "out:new_pb", "tick"]

    def test_every_writer_takes_the_fund_lock(self) -> None:
        for name in (
            "_change_playbook",
            "_edit_basket",
            "_supersede_one",
            "resync",
            "tick",
            "deposit",
            "drop",
            "rebalance_loop",
            "_anchor_on_close",
            "restore_funds",
        ):
            assert "_fund_lock" in inspect.getsource(getattr(mod, name)), name


class TestLeader:
    """리뷰 T563 ② — 리더가 아니면 자동 대체를 시도하지 않는다 · 리더를 놓으면 고리를 거둔다."""

    async def test_no_attempt_when_not_the_leader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _World(monkeypatch)
        fund = _fund(SYMBOLS, OLD)
        monkeypatch.setattr(mod, "FUNDS", {"f1": fund})
        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", {})

        def supersede(now: str, books: Any) -> str | None:
            _ = books
            return "new_pb" if now == "old_pb" else None

        monkeypatch.setattr(mod, "superseding_playbook", supersede)
        monkeypatch.setattr(mod, "_leader_check", lambda: False)
        assert await mod._supersede_funds() == 0
        assert world.planned_calls == [] and fund.playbook == "old_pb"
        assert world.events.named("fund_supersede_not_leader")

    def test_the_leader_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod, "_leader_check", None)
        assert mod._still_leader() is True
        mod.bind_leader(lambda: False)
        assert mod._still_leader() is False

        def broken() -> bool:
            raise RuntimeError("게이트를 못 읽었다")

        mod.bind_leader(broken)
        assert mod._still_leader() is False, "못 읽으면 리더가 아니라고 본다"
        mod.bind_leader(None)
        assert mod._still_leader() is True

    async def test_the_retry_loop_ends_when_leadership_is_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        leading = {"on": True}
        calls: list[int] = []

        async def fake_funds() -> int:
            calls.append(1)
            leading["on"] = False
            return 0

        def no_wait(now: datetime, base: int = 0) -> float:
            _ = (now, base)
            return 0.0

        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", {"f1": {}})
        monkeypatch.setattr(mod, "_supersede_funds", fake_funds)
        monkeypatch.setattr(mod, "supersede_delay_s", no_wait)
        monkeypatch.setattr(mod, "_leader_check", lambda: leading["on"])
        await asyncio.wait_for(mod._supersede_later(), timeout=2.0)
        assert len(calls) == 1, "미룬 펀드가 남아도 리더가 아니면 다시 재지 않는다"

    async def test_stop_cancels_a_waiting_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def forever() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(forever())
        monkeypatch.setattr(mod, "_supersede_task", task)
        await mod.stop_supersede()
        assert task.cancelled() and mod._supersede_task is None

    async def test_a_running_switch_finishes_when_the_loop_is_cancelled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """중간에 끊으면 반쪽 전환 — 도는 전환은 끝까지 가고 다음 시도만 없다(shield)."""
        _World(monkeypatch)
        fund = _fund(SYMBOLS, OLD)
        monkeypatch.setattr(mod, "FUNDS", {"f1": fund})
        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", {})
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_switch(target: mod.Fund, new_pb: str, payload: Any) -> None:
            _ = payload
            entered.set()
            await release.wait()
            target.playbook = new_pb

        def supersede(now: str, books: Any) -> str | None:
            _ = books
            return "new_pb" if now == "old_pb" else None

        monkeypatch.setattr(mod, "_switch_playbook", held_switch)
        monkeypatch.setattr(mod, "superseding_playbook", supersede)
        task = asyncio.create_task(mod._supersede_funds())
        monkeypatch.setattr(mod, "_supersede_task", task)
        await entered.wait()
        await mod.stop_supersede(wait=False)  # 종료 경로 — 고리만 거두고 바로 돌아온다
        assert task.cancelled()
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert fund.playbook == "new_pb", "도는 전환은 끝까지 간다"
        assert not mod._SUPERSEDE_INFLIGHT

    def test_the_trading_leader_owns_the_loop(self) -> None:
        source = (Path(mod.__file__).parent / "main.py").read_text(encoding="utf-8")
        start = source.split("async def _start_trading")[1].split("async def _stop_trading")[0]
        stop = source.split("async def _stop_trading")[1].split("# ⚠️ redis")[0]
        assert "bind_fund_leader(" in start
        assert "await stop_supersede(wait=not _closing" in stop
        assert stop.index("task.cancel()") < stop.index("await stop_supersede("), (
            "관리 루프를 먼저 거둔다 — 전환을 기다리는 동안 강등된 프로세스가 루프를 안 돌리게"
        )
        tail = source.split('_logger.info("api_stopping"')[1]
        assert tail.index('_closing["on"] = True') < tail.index("leader.shutdown()"), (
            "종료는 기다리지 않는다 — 락을 놓아 새 리더가 뜨는 중이다"
        )


class TestSameProcessRepromotion:
    """두 번째 리뷰 §4 — 전환이 도는 중 리더 정지 → 같은 프로세스 재승격 · 복원이 겹쳐도 같은
    펀드를 동시에 바꾸지 않는다(정지가 전환을 기다리고 · 복원은 펀드 락 안에서 파일을 다시
    읽는다)."""

    def _setup(
        self, monkeypatch: pytest.MonkeyPatch, root: Path
    ) -> tuple[_World, mod.Fund, list[str], asyncio.Event, asyncio.Event]:
        world = _World(monkeypatch)
        fund = _fund(SYMBOLS, OLD)
        monkeypatch.setattr(mod, "FUNDS", {"f1": fund})
        monkeypatch.setattr(mod, "SUPERSEDE_DEFERRED", {})
        monkeypatch.setattr(mod, "PENDING_FUNDS", set[str]())
        monkeypatch.setattr(mod, "FUNDS_ROOT", root)
        monkeypatch.setattr(mod, "_schedule_supersede", lambda: None)
        saved = root / "f1.json"
        saved.write_text(json.dumps({"fund_id": "f1", "playbook": "old_pb"}), encoding="utf-8")
        order: list[str] = []
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_switch(target: mod.Fund, new_pb: str, payload: Any) -> None:
            _ = payload
            order.append("switch:in")
            entered.set()
            await release.wait()
            target.playbook = new_pb
            saved.write_text(  # 전환 끝의 틱이 파일을 쓴다
                json.dumps({"fund_id": "f1", "playbook": new_pb}), encoding="utf-8"
            )
            order.append("switch:out")

        async def restore_one(data: dict[str, Any]) -> None:
            order.append(f"restore:{data['playbook']}")
            again = _fund(SYMBOLS, OLD)
            again.playbook = str(data["playbook"])
            mod.FUNDS["f1"] = again

        def supersede(now: str, books: Any) -> str | None:
            _ = books
            return "new_pb" if now == "old_pb" else None

        monkeypatch.setattr(mod, "_switch_playbook", held_switch)
        monkeypatch.setattr(mod, "_restore_one", restore_one)
        monkeypatch.setattr(mod, "superseding_playbook", supersede)
        return world, fund, order, entered, release

    async def test_stop_waits_and_restore_does_not_overlap(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, fund, order, entered, release = self._setup(monkeypatch, tmp_path)
        loop_task = asyncio.create_task(mod._supersede_funds())
        monkeypatch.setattr(mod, "_supersede_task", loop_task)
        await entered.wait()
        stopper = asyncio.create_task(mod.stop_supersede())  # 리더 상실 → 정지
        restorer = asyncio.create_task(mod.restore_funds())  # 재승격 → 복원(겹치게 일부러 바로)
        for _ in range(20):
            await asyncio.sleep(0)
        assert loop_task.cancelled(), "고리는 바로 거둔다"
        assert not stopper.done(), "정지는 도는 전환을 기다린다"
        assert order == ["switch:in"], "복원은 펀드 락을 기다린다 — 전환 중에 펀드를 안 바꾼다"
        assert mod.FUNDS["f1"] is fund
        release.set()
        await asyncio.wait_for(stopper, timeout=2.0)
        assert await asyncio.wait_for(restorer, timeout=2.0) == 1
        assert order == ["switch:in", "switch:out", "restore:new_pb"], (
            "복원은 전환이 끝난 뒤 락 안에서 파일을 다시 읽는다"
        )
        assert mod.FUNDS["f1"].playbook == "new_pb" and mod.FUNDS["f1"] is not fund
        assert not mod._SUPERSEDE_INFLIGHT and not mod.PENDING_FUNDS

    async def test_a_late_switch_warns_and_is_not_cut(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        world, fund, order, entered, release = self._setup(monkeypatch, tmp_path)
        loop_task = asyncio.create_task(mod._supersede_funds())
        monkeypatch.setattr(mod, "_supersede_task", loop_task)
        await entered.wait()
        await mod.stop_supersede(timeout=0.01)
        late = world.events.named("fund_supersede_stop_timeout")
        assert late and late[0][0] == "warning" and late[0][2]["inflight"] == 1
        assert order == ["switch:in"] and mod._SUPERSEDE_INFLIGHT, "시한이 지나도 끊지 않는다"
        restorer = asyncio.create_task(mod.restore_funds())
        for _ in range(20):
            await asyncio.sleep(0)
        assert order == ["switch:in"], "시한 뒤에는 펀드 락이 복원을 막는다"
        release.set()
        assert await asyncio.wait_for(restorer, timeout=2.0) == 1
        assert order == ["switch:in", "switch:out", "restore:new_pb"]
        assert fund.playbook == "new_pb"

    async def test_restore_skips_a_fund_dropped_while_waiting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, _, order, _, _ = self._setup(monkeypatch, tmp_path)
        lock = mod._fund_lock("f1")
        await lock.acquire()  # 접기(`drop`)가 락을 쥐고 있다
        restorer = asyncio.create_task(mod.restore_funds())
        for _ in range(10):
            await asyncio.sleep(0)
        mod._archive_fund_file("f1", root=tmp_path)
        lock.release()
        assert await asyncio.wait_for(restorer, timeout=2.0) == 0
        assert order == [] and not mod.PENDING_FUNDS, (
            "접힌 펀드는 되살리지도 · 못 붙인 것으로 치지도 않는다"
        )


class _Margin:
    """증거금 계좌 흉내 — `MarginAware`."""

    def __init__(self, cash: str, margin: str = "0") -> None:
        self._cash, self._margin = Decimal(cash), Decimal(margin)

    async def get_balance(self) -> Any:
        return SimpleNamespace(cash=self._cash)

    async def account_margin(self) -> Decimal:
        return self._margin


class _Runs:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def open_runs(self, *, live: bool) -> list[dict[str, Any]]:
        assert live
        return self.rows


def _plan(symbol: str, margin: str, replacing: str | None) -> wf.PlannedStart:
    payload = {"symbol": symbol, "market": "GATE", "margin": margin, "playbook": "p"}
    return wf.PlannedStart(payload, replacing=replacing)


class TestStartBlockers:
    """미리 재기의 계좌 쪽 문 — 순서대로 걸으며 거둘 판의 저장 예산을 빼고 새 예산을 더한다."""

    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cash: str,
        blocked: dict[str, wf.StartBlock] | None = None,
    ) -> None:
        rows = [
            {"key": "old-A", "market": "GATE", "margin": "9"},
            {"key": "old-B", "market": "GATE", "margin": "9"},
            {"key": "x", "market": "BINANCE", "margin": "500"},  # 다른 계좌 — 안 센다
        ]
        monkeypatch.setattr(wf, "_store", _Runs(rows))
        monkeypatch.setattr(exchange, "_orders_adapter", lambda _m="GATE": _Margin(cash))
        found = blocked or {}

        async def blocker(payload: Any, replacing: str | None) -> wf.StartBlock | None:
            _ = replacing
            return found.get(str(payload["symbol"]))

        monkeypatch.setattr(wf, "_start_blocker", blocker)

    async def test_replaced_runs_free_their_stored_budget_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, cash="20")
        plans = [_plan("A", "3", "old-A"), _plan("B", "3", "old-B"), _plan("C", "3", None)]
        assert await wf.start_blockers(plans, Market.GATE) == []

    async def test_a_fresh_run_first_meets_the_stale_budgets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """새 종목을 먼저 띄우면 옛 판의 낡은 예산 합(18)에 막힌다 — 순서가 답을 정한다."""
        self._setup(monkeypatch, cash="20")
        plans = [_plan("C", "3", None), _plan("A", "3", "old-A"), _plan("B", "3", "old-B")]
        got = await wf.start_blockers(plans, Market.GATE)
        assert [(symbol, block.gate) for symbol, block in got] == [("C", "budget")]

    async def test_cash_is_checked_only_for_fresh_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, cash="2")
        plans = [_plan("A", "3", "old-A"), _plan("C", "3", None)]
        got = await wf.start_blockers(plans, Market.GATE)
        assert [(symbol, block.gate) for symbol, block in got] == [("C", "cash")]

    async def test_every_blocked_run_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._setup(
            monkeypatch,
            cash="20",
            blocked={
                "A": wf.StartBlock("sizing", "1계약", floor=Decimal("3")),
                "B": wf.StartBlock("liquidity", "얇다"),
            },
        )
        plans = [_plan("A", "3", "old-A"), _plan("B", "3", "old-B"), _plan("C", "3", None)]
        got = await wf.start_blockers(plans, Market.GATE)
        assert [(symbol, block.gate) for symbol, block in got] == [
            ("A", "sizing"),
            ("B", "liquidity"),
        ]

    async def test_an_unreadable_account_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_m: str = "GATE") -> Any:
            raise HTTPException(503, "게이트가 막았다")

        monkeypatch.setattr(exchange, "_orders_adapter", boom)
        got = await wf.start_blockers([_plan("A", "3", "old-A")], Market.GATE)
        assert [(symbol, block.gate) for symbol, block in got] == [("A", "account")]


def _nothing(*args: Any, **kwargs: Any) -> None:
    _ = (args, kwargs)


def _costs(path: Any) -> Any:
    _ = path
    return SimpleNamespace(for_market=_nothing)


def _instrument(symbol: str, market: Any) -> Any:
    _ = (symbol, market)
    return SimpleNamespace(currency=None)


def _one_leg(catalog: Any, found: Any) -> Decimal:
    _ = (catalog, found)
    return Decimal(1)


class TestStartBlocker:
    """순서와 무관한 시작 문 — `_live_start` 와 같은 함수로 잰다."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch, *, floors: dict[str, Decimal]) -> None:
        book = SimpleNamespace(playbook_id="p", attribution="p@1", stop_mode="intrabar")

        def books(payload: Any) -> tuple[Any, ...]:
            _ = payload
            return (book,)

        def inside(found: Any, market: Any) -> list[str]:
            _ = (found, market)
            return []

        monkeypatch.setattr(wf, "start_books", books)
        monkeypatch.setattr(wf, "playbooks_outside", inside)

        class _Quotes:
            async def get_quote(self, _instrument: Any) -> Any:
                return SimpleNamespace(last_price=Decimal(1))

        class _Provider:
            def live_markets(self) -> tuple[str, ...]:
                return ("GATE",)

            def adapter_for(self, _market: Any) -> Any:
                return _Quotes()

        monkeypatch.setattr(wf, "MarketDataProvider", _Provider)
        monkeypatch.setattr(
            wf, "load_risk_settings", lambda: SimpleNamespace(stop_protect_ratio=Decimal(1))
        )
        monkeypatch.setattr(wf, "require_stop_cap", _nothing)
        monkeypatch.setattr(wf, "resolve_tick", _nothing)
        monkeypatch.setattr(wf, "load_cost_table", _costs)
        monkeypatch.setattr(wf, "instrument_of", _instrument)
        monkeypatch.setattr(wf, "load_rules", dict)
        monkeypatch.setattr(wf, "thinnest_leg", _one_leg)

        async def depth(*_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(ok=True, why="")

        async def fit(symbol: str, budget: Decimal, *_a: Any, **_k: Any) -> Any:
            floor = floors.get(symbol, Decimal(0))
            why = "예산이 모자란다" if budget < floor else ""
            return SimpleNamespace(ok=not why, why=why, floor=floor)

        monkeypatch.setattr(exchange, "liquidity_of", depth)
        monkeypatch.setattr(exchange, "sizing_of", fit)

    async def test_the_one_contract_gate_reports_its_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, floors={"A": Decimal(4), "B": Decimal(1)})
        monkeypatch.setattr(wf, "SESSIONS", {})

        class _Holder:
            async def holder_of(self, *_a: Any, **_k: Any) -> None:
                return None

        monkeypatch.setattr(wf, "_store", _Holder())
        got = await wf._start_blocker(
            {"symbol": "A", "market": "GATE", "margin": "2", "leverage": "4"}, "old-A"
        )
        # 바닥에 미리 재기 여유를 더해 알린다 — 필요 총자본도 여유까지 친다
        assert got is not None and got.gate == "sizing"
        assert got.floor == Decimal(4) * (1 + wf.PREFLIGHT_FLOOR_HEADROOM)
        ok = await wf._start_blocker(
            {"symbol": "B", "market": "GATE", "margin": "2", "leverage": "4"}, "old-B"
        )
        assert ok is None

    async def test_a_floor_without_headroom_is_deferred(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """바닥을 겨우 넘는 예산은 미룬다 — 잰 뒤 판이 뜨기까지 값이 움직인다(리뷰 T563 ③)."""
        self._setup(monkeypatch, floors={"C": Decimal("1.95")})
        monkeypatch.setattr(wf, "SESSIONS", {})

        class _Holder:
            async def holder_of(self, *_a: Any, **_k: Any) -> None:
                return None

        monkeypatch.setattr(wf, "_store", _Holder())
        got = await wf._start_blocker(
            {"symbol": "C", "market": "GATE", "margin": "2", "leverage": "4"}, "old-C"
        )
        assert got is not None and got.gate == "sizing" and "여유" in got.why
        assert got.floor == Decimal("1.95") * (1 + wf.PREFLIGHT_FLOOR_HEADROOM)
        assert Decimal("0.03") <= wf.PREFLIGHT_FLOOR_HEADROOM <= Decimal("0.05")

    async def test_one_run_per_symbol_except_the_one_being_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, floors={})
        live = SimpleNamespace(
            session=SimpleNamespace(instrument=SimpleNamespace(symbol="A", market=Market.GATE))
        )
        monkeypatch.setattr(wf, "SESSIONS", {"old-A": live, "orphan": live})
        monkeypatch.setattr(wf, "LIVE_RUNNERS", {"old-A": object(), "orphan": object()})

        class _Holder:
            async def holder_of(self, *_a: Any, **_k: Any) -> tuple[str, str]:
                return ("old-A", "anchor-of-old")

        monkeypatch.setattr(wf, "_store", _Holder())
        payload = {"symbol": "A", "market": "GATE", "margin": "2", "leverage": "4"}
        got = await wf._start_blocker(payload, "old-A")
        assert got is not None and got.gate == "held" and "orphan" in got.why
        monkeypatch.setattr(wf, "LIVE_RUNNERS", {"old-A": object()})
        assert await wf._start_blocker(payload, "old-A") is None


class _ReachedError(Exception):
    """시작 문을 다 지나 잔재 청소까지 왔다 — 시험은 여기서 멈춘다."""


def _soft_payload() -> dict[str, Any]:
    return {
        "playbook": "p",
        "symbol": "AAA_USDT",
        "market": "GATE",
        "margin": "2",
        "leverage": "4",
        "run_key": "new-A",
    }


class TestSoftGate:
    """포지션을 든 되살리기(`keep_managing` + 열린 기록)는 유동성 · 1계약 문이 막지 않는다."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch, *, depth_ok: bool, fit_ok: bool) -> _Events:
        book = SimpleNamespace(
            playbook_id="p",
            attribution="p@1",
            setups=("x",),
            timeframe=None,
            bar_tilts=(),
            primary_flags=(),
            stop_mode="intrabar",
        )

        def books(payload: Any) -> tuple[Any, ...]:
            _ = payload
            return (book,)

        class _Quotes:
            capabilities = frozenset({wf.Capability.WS})

            def supported_frames(self, frames: Any) -> list[Any]:
                _ = frames
                return []

            async def get_quote(self, _instrument: Any) -> Any:
                return SimpleNamespace(last_price=Decimal(1))

        class _Provider:
            def live_markets(self) -> tuple[str, ...]:
                return ("GATE",)

            def adapter_for(self, _market: Any) -> Any:
                return _Quotes()

        class _Orders:
            async def get_balance(self) -> Any:
                return SimpleNamespace(cash=Decimal(100))

        def orders(quotes: Any, user_id: str) -> Any:
            _ = (quotes, user_id)
            return _Orders()

        async def seed(*_a: Any, **_k: Any) -> tuple[Any, int]:
            return SimpleNamespace(), 0

        def no_frames(*_a: Any, **_k: Any) -> tuple[Any, ...]:
            return ()

        def none_frame(*_a: Any, **_k: Any) -> None:
            return None

        async def depth(*_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(ok=depth_ok, why="" if depth_ok else "호가가 얇다")

        async def fit(symbol: str, budget: Decimal, *_a: Any, **_k: Any) -> Any:
            _ = symbol
            why = "" if fit_ok else "1계약을 못 산다"
            return SimpleNamespace(ok=fit_ok, why=why, floor=budget * 2)

        async def reached(*_a: Any, **_k: Any) -> Any:
            raise _ReachedError

        def inside(found: Any, market: Any) -> list[str]:
            _ = (found, market)
            return []

        logged = _Events()
        monkeypatch.setattr(wf, "start_books", books)
        monkeypatch.setattr(wf, "load_rules", dict)
        monkeypatch.setattr(wf, "playbooks_outside", inside)
        monkeypatch.setattr(wf, "instrument_of", _instrument)
        monkeypatch.setattr(wf, "MarketDataProvider", _Provider)
        monkeypatch.setattr(wf, "order_adapter", orders)
        monkeypatch.setattr(wf, "QuoteAdapter", object)
        monkeypatch.setattr(wf, "trigger_frame", none_frame)
        monkeypatch.setattr(wf, "decision_frames", no_frames)
        monkeypatch.setattr(wf, "seed_within_budget", seed)
        monkeypatch.setattr(
            wf, "load_app_settings", lambda: SimpleNamespace(run_start_request_cap=0)
        )
        monkeypatch.setattr(wf, "_drawn_flags", no_frames)
        monkeypatch.setattr(wf, "expand", no_frames)
        monkeypatch.setattr(wf, "available_flags", no_frames)
        monkeypatch.setattr(wf, "rules_config", dict)
        monkeypatch.setattr(wf, "resolve_tick", _nothing)
        monkeypatch.setattr(wf, "load_cost_table", _costs)
        monkeypatch.setattr(wf, "thinnest_leg", _one_leg)
        monkeypatch.setattr(exchange, "liquidity_of", depth)
        monkeypatch.setattr(exchange, "sizing_of", fit)
        monkeypatch.setattr(wf, "sweep_leftovers", reached)
        monkeypatch.setattr(wf, "_logger", logged)
        return logged

    async def test_a_fresh_start_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._setup(monkeypatch, depth_ok=True, fit_ok=False)
        held = cast(Any, (_Rec("t-A"),))
        with pytest.raises(HTTPException) as got:
            await wf._live_start(_soft_payload(), reviving=True, inherit=held)
        assert got.value.status_code == 409, "되돌리기가 아니면 문은 그대로 막는다"

    async def test_keep_managing_without_a_position_is_still_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, depth_ok=True, fit_ok=False)
        with pytest.raises(HTTPException) as got:
            await wf._live_start(_soft_payload(), reviving=True, keep_managing=True)
        assert got.value.status_code == 409, "포지션이 없으면 낮출 이유가 없다"

    async def test_a_held_position_passes_the_one_contract_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logged = self._setup(monkeypatch, depth_ok=True, fit_ok=False)
        held = cast(Any, (_Rec("t-A"),))
        with pytest.raises(_ReachedError):
            await wf._live_start(_soft_payload(), reviving=True, inherit=held, keep_managing=True)
        soft = logged.named("live_start_gate_softened")
        assert [item[2]["gate"] for item in soft] == ["sizing"] and soft[0][0] == "warning"

    async def test_a_held_position_passes_the_liquidity_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logged = self._setup(monkeypatch, depth_ok=False, fit_ok=True)
        held = cast(Any, (_Rec("t-A"),))
        with pytest.raises(_ReachedError):
            await wf._live_start(_soft_payload(), reviving=True, inherit=held, keep_managing=True)
        assert [item[2]["gate"] for item in logged.named("live_start_gate_softened")] == [
            "liquidity"
        ]

    def test_the_undo_asks_for_it(self) -> None:
        assert "keep_managing=True" in inspect.getsource(mod._respawn)
        assert "keep_managing=keep_managing" in inspect.getsource(mod._spawn_session)
        assert "keep_managing" not in inspect.getsource(mod._switch_playbook), (
            "앞으로 가는 전환은 문을 낮추지 않는다"
        )


class TestRunRowHygiene:
    """리뷰 T563 ④ — 판 행을 연 뒤 실패해 러너 없는 열린 행이 남지 않게."""

    def test_reads_happen_before_the_row_opens(self) -> None:
        body = inspect.getsource(wf._live_start)
        opened = body.index("opened = await _store.open(")
        assert body.index("spec = await quotes.contract_spec(instrument)") < opened
        assert body.index("await _settings.get(HALT_KEY)") < opened

    def test_a_failed_sweep_closes_a_fresh_row(self) -> None:
        body = inspect.getsource(wf._live_start)
        cut = body.index("sweep_zombie_entries(")
        tail = body[cut : cut + 700]
        assert "if not opened.resumed:" in tail and "await _store.close(handle" in tail


class TestMirror:
    """미리 재기는 `_live_start` 와 **같은 식**을 쓴다 — 둘이 갈리면 문이 거짓말을 한다."""

    @staticmethod
    def _flat(fn: Any) -> str:
        return "".join(inspect.getsource(fn).split())

    def test_the_gates_are_the_same_calls(self) -> None:
        live = self._flat(wf._live_start)
        pre = self._flat(wf._start_blocker)
        for call in (
            "liquidity_of(symbol,margin*lever,market=market.value)",
            "sizing_of(symbol,margin,lever,leg=thinnest_leg(catalog,book),market=market.value)",
            "resolve_tick(load_cost_table(DEFAULT_CONFIG_PATH).for_market(market),symbol,",
            "holder_of(market.value,symbol,live=True)",
            "playbooks_outside(books,market)",
        ):
            assert call in live, call
            assert call in pre, call
        assert "books=start_books(payload)" in live
        assert "books=start_books(payload)" in pre

    def test_the_spawn_and_the_plan_build_the_same_request(self) -> None:
        spawn = self._flat(mod._spawn_session)
        planned = self._flat(mod._MemberStart.planned)
        assert "_member_payload(" in spawn and "_member_payload(" in planned

    def test_the_budget_gate_uses_the_shared_comparison(self) -> None:
        assert "room_exceeded(" in self._flat(wf._budget_room)
        assert "room_exceeded(" in self._flat(wf.start_blockers)
        assert wf.room_exceeded(Decimal("10"), Decimal("7"), Decimal("3.004")) is False
        assert wf.room_exceeded(Decimal("10"), Decimal("7"), Decimal("3.01")) is True


class TestRestoreMismatch:
    """되살려 다시 묶는 판의 매매법이 펀드 선언과 다르면 경고한다 (묶는 동작은 그대로)."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> _Events:
        events = _Events()

        def books(payload: dict[str, str]) -> tuple[Any, ...]:
            return tuple(
                SimpleNamespace(playbook_id=name) for name in payload["playbook"].split("+")
            )

        monkeypatch.setattr(mod, "_events", events)
        monkeypatch.setattr(mod, "start_books", books)
        return events

    def test_a_different_playbook_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = self._setup(monkeypatch)
        session = SimpleNamespace(playbooks=[SimpleNamespace(playbook_id="new_pb")])
        mod._note_mismatch("f1", "AAA_USDT", session, "old_pb", Decimal(3), None, ())
        found = events.named("fund_member_playbook_mismatch")
        assert len(found) == 1 and found[0][0] == "warning"
        assert found[0][2]["session"] == ["new_pb"] and found[0][2]["fund"] == ["old_pb"]

    def test_the_same_playbook_is_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = self._setup(monkeypatch)
        session = SimpleNamespace(
            playbooks=[SimpleNamespace(playbook_id="b"), SimpleNamespace(playbook_id="a")]
        )
        mod._note_mismatch("f1", "AAA_USDT", session, "a+b", Decimal(3), None, ())
        assert events.seen == [], "다리 순서만 다른 것은 다른 매매법이 아니다"

    def test_a_symbol_outside_every_leg_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """다리에 없는 종목의 판은 신규 진입이 다 막힌다 — 조용히 넘기지 않는다(리뷰 T563 ④)."""
        events = self._setup(monkeypatch)

        def no_leg(*_a: Any, **_k: Any) -> tuple[str, Decimal]:
            raise LegError("이 종목을 가진 다리가 없다")

        monkeypatch.setattr(mod, "_member_terms", no_leg)
        session = SimpleNamespace(playbooks=[SimpleNamespace(playbook_id="new_pb")])
        mod._note_mismatch("f1", "ZZZ_USDT", session, "old_pb", Decimal(3), None, ())
        found = events.named("fund_member_playbook_mismatch")
        assert len(found) == 1 and found[0][0] == "warning"
        assert found[0][2]["reason"] == "no_leg" and found[0][2]["fund"] == []

    def test_restore_checks_the_sessions_it_reuses(self) -> None:
        source = inspect.getsource(mod._restore_one)
        assert source.index("_running_handle(") < source.index("_note_mismatch(")
