"""펀드 매매법 전환은 원자적이다 (T563).

지키는 것:

- 아무것도 거두기 전에 모든 멤버의 시작 문을 잰다 — 하나라도 막히면 **아무것도 안 바꾼다**
  (옛 판 · 옛 매매법 · 옛 바스켓 · 틱도 없음) · 자동 대체는 경고 사건 하나를 남기고 다음 창에
  다시 잰다.
- 다 지나면 예전처럼 바꾼다 — 옛 판이 있는 종목 먼저 · 새 종목 나중 · 결과는 바스켓 순서.
- 중간에 실패하면 이미 바꾼 종목을 옛 매매법으로 되돌린다(저장 예산 그대로) · 못 되돌린 종목은
  오류로.
- 미리 재기의 예산 합은 띄우는 순서대로 걷는다 — 옛 판의 낡은 저장 예산이 새 종목을 막지 않게.
- 되살려 다시 묶는 판의 매매법이 펀드 선언과 다르면 경고한다(반쪽 전환의 흔적).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from updown.apps.api import exchange
from updown.apps.api import rebalancer as mod
from updown.apps.api import walkforward as wf
from updown.common.domain.instrument import Market
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
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
    """전환이 부르는 바깥(판 띄우기 · 거두기 · 틱 · 미리 재기)을 기록하는 대역."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events = _Events()
        self.spawned: list[dict[str, Any]] = []
        self.dropped: list[str] = []
        self.ticks = 0
        self.blocked: list[tuple[str, wf.StartBlock]] = []
        self.planned: list[wf.PlannedStart] = []
        self.fail_spawn: set[tuple[str, str]] = set()  # (종목, 매매법) — 띄우기 실패
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
            if (symbol, playbook) in self.fail_spawn:
                raise RuntimeError(f"{symbol} 못 띄움")
            return f"{playbook}-{symbol}-{len(self.spawned)}", _Port()

        async def drop(key: str) -> None:
            self.dropped.append(key)

        async def tick(fund: Any, flow: Any = None) -> None:
            _ = (fund, flow)
            self.ticks += 1

        async def blockers(
            plans: list[wf.PlannedStart], market: Market
        ) -> list[tuple[str, wf.StartBlock]]:
            _ = market
            self.planned = list(plans)
            return list(self.blocked)

        async def margins(market: Market) -> dict[str, Decimal]:
            _ = market
            return dict(self.margins)

        def no_records(handle: str | None) -> tuple[Any, ...]:
            _ = handle
            return ()

        def declared() -> list[Any]:
            return list(self.declared)

        monkeypatch.setattr(mod, "_events", self.events)
        monkeypatch.setattr(mod, "_spawn_session", spawn)
        monkeypatch.setattr(mod, "_drop_one", drop)
        monkeypatch.setattr(mod, "_tick", tick)
        monkeypatch.setattr(mod, "start_blockers", blockers)
        monkeypatch.setattr(mod, "run_margins", margins)
        monkeypatch.setattr(mod, "open_records_of", no_records)
        monkeypatch.setattr(mod, "run_store", lambda: None)
        monkeypatch.setattr(mod, "load_playbooks", declared)


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
            await mod._switch_playbook(fund, "new_pb", {})  # pyright: ignore[reportPrivateUsage]
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
            await mod._switch_playbook(fund, "new_pb", {})  # pyright: ignore[reportPrivateUsage]
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
        assert await mod._supersede_funds() == 0  # pyright: ignore[reportPrivateUsage]
        deferred = world.events.named("fund_supersede_deferred")
        assert len(deferred) == 1 and deferred[0][0] == "warning"
        assert deferred[0][2]["blocked"][0]["symbol"] == "CCC_USDT"
        assert world.events.named("fund_playbook_supersede_failed") == []
        assert "f1" in mod.SUPERSEDE_DEFERRED and fund.playbook == "old_pb"
        # 문이 열리면(입금 · 가격) 다음 시도가 바꾸고 미룸 표시를 지운다
        world.blocked = []
        assert await mod._supersede_funds() == 1  # pyright: ignore[reportPrivateUsage]
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
        await mod._supersede_later()  # pyright: ignore[reportPrivateUsage]
        assert len(calls) == 3

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
        await mod._switch_playbook(fund, "new_pb", {})  # pyright: ignore[reportPrivateUsage]
        assert world.dropped == ["old-A", "old-B"]
        order = [(c["symbol"], c["adopt_from"]) for c in world.spawned]
        assert order == [("AAA_USDT", "old-A"), ("BBB_USDT", "old-B"), ("CCC_USDT", None)]
        assert all(c["playbook"] == "new_pb" for c in world.spawned)
        assert all("capital" in c for c in world.spawned)
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
        await mod._switch_playbook(fund, "new_pb", {})  # pyright: ignore[reportPrivateUsage]
        assert [c["symbol"] for c in world.spawned] == ["AAA_USDT", "BBB_USDT", "CCC_USDT"]
        assert list(fund.handles) == ["CCC_USDT", "AAA_USDT", "BBB_USDT"]


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
            await mod._switch_playbook(fund, "new_pb", {})  # pyright: ignore[reportPrivateUsage]
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
        assert set(fund.handles) == {"AAA_USDT", "BBB_USDT"}
        assert all(handle.startswith("old_pb-") for handle in fund.handles.values())
        done = world.events.named("fund_playbook_switch_rolled_back")
        assert len(done) == 1 and done[0][0] == "warning"
        assert done[0][2]["restored"] == ["AAA_USDT", "BBB_USDT"]
        assert done[0][2]["unmanaged"] == []
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
            await mod._switch_playbook(fund, "new_pb", {})  # pyright: ignore[reportPrivateUsage]
        lost = world.events.named("fund_member_unmanaged")
        assert [item[2]["symbol"] for item in lost] == ["BBB_USDT"]
        done = world.events.named("fund_playbook_switch_rolled_back")
        assert done[0][0] == "error" and done[0][2]["unmanaged"] == ["BBB_USDT"]
        assert "BBB_USDT" not in fund.handles and "BBB_USDT" not in fund.coordinator.ports
        assert fund.handles["AAA_USDT"].startswith("old_pb-"), "나머지는 계속 되돌린다"


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


class TestStartBlocker:
    """순서와 무관한 시작 문 — `_live_start` 와 같은 함수로 잰다."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch, *, short: set[str]) -> None:
        book = SimpleNamespace(playbook_id="p", attribution="p@1", stop_mode="intrabar")

        def books(payload: Any) -> tuple[Any, ...]:
            _ = payload
            return (book,)

        def inside(found: Any, market: Any) -> list[str]:
            _ = (found, market)
            return []

        def nothing(*args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        def costs(path: Any) -> Any:
            _ = path
            return SimpleNamespace(for_market=nothing)

        def instrument(symbol: str, market: Any) -> Any:
            _ = (symbol, market)
            return SimpleNamespace(currency=None)

        def one_leg(catalog: Any, found: Any) -> Decimal:
            _ = (catalog, found)
            return Decimal(1)

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
        monkeypatch.setattr(wf, "require_stop_cap", nothing)
        monkeypatch.setattr(wf, "resolve_tick", nothing)
        monkeypatch.setattr(wf, "load_cost_table", costs)
        monkeypatch.setattr(wf, "instrument_of", instrument)
        monkeypatch.setattr(wf, "load_rules", dict)
        monkeypatch.setattr(wf, "thinnest_leg", one_leg)

        async def depth(*_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(ok=True, why="")

        async def fit(symbol: str, budget: Decimal, *_a: Any, **_k: Any) -> Any:
            why = "예산이 모자란다" if symbol in short else ""
            return SimpleNamespace(ok=not why, why=why, floor=budget * 2)

        monkeypatch.setattr(exchange, "liquidity_of", depth)
        monkeypatch.setattr(exchange, "sizing_of", fit)

    async def test_the_one_contract_gate_reports_its_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, short={"A"})
        monkeypatch.setattr(wf, "SESSIONS", {})

        class _Holder:
            async def holder_of(self, *_a: Any, **_k: Any) -> None:
                return None

        monkeypatch.setattr(wf, "_store", _Holder())
        got = await wf._start_blocker(  # pyright: ignore[reportPrivateUsage]
            {"symbol": "A", "market": "GATE", "margin": "2", "leverage": "4"}, "old-A"
        )
        assert got is not None and got.gate == "sizing" and got.floor == Decimal(4)
        ok = await wf._start_blocker(  # pyright: ignore[reportPrivateUsage]
            {"symbol": "B", "market": "GATE", "margin": "2", "leverage": "4"}, "old-B"
        )
        assert ok is None

    async def test_one_run_per_symbol_except_the_one_being_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(monkeypatch, short=set())
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
        got = await wf._start_blocker(payload, "old-A")  # pyright: ignore[reportPrivateUsage]
        assert got is not None and got.gate == "held" and "orphan" in got.why
        monkeypatch.setattr(wf, "LIVE_RUNNERS", {"old-A": object()})
        assert await wf._start_blocker(payload, "old-A") is None  # pyright: ignore[reportPrivateUsage]


class TestMirror:
    """미리 재기는 `_live_start` 와 **같은 식**을 쓴다 — 둘이 갈리면 문이 거짓말을 한다."""

    @staticmethod
    def _flat(fn: Any) -> str:
        return "".join(inspect.getsource(fn).split())

    def test_the_gates_are_the_same_calls(self) -> None:
        live = self._flat(wf._live_start)  # pyright: ignore[reportPrivateUsage]
        pre = self._flat(wf._start_blocker)  # pyright: ignore[reportPrivateUsage]
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
        spawn = self._flat(mod._spawn_session)  # pyright: ignore[reportPrivateUsage]
        planned = self._flat(mod._MemberStart.planned)  # pyright: ignore[reportPrivateUsage]
        assert "_member_payload(" in spawn and "_member_payload(" in planned

    def test_the_budget_gate_uses_the_shared_comparison(self) -> None:
        assert "room_exceeded(" in self._flat(wf._budget_room)  # pyright: ignore[reportPrivateUsage]
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
        mod._note_mismatch("f1", "AAA_USDT", session, "old_pb", Decimal(3), None, ())  # pyright: ignore[reportPrivateUsage]
        found = events.named("fund_member_playbook_mismatch")
        assert len(found) == 1 and found[0][0] == "warning"
        assert found[0][2]["session"] == ["new_pb"] and found[0][2]["fund"] == ["old_pb"]

    def test_the_same_playbook_is_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = self._setup(monkeypatch)
        session = SimpleNamespace(
            playbooks=[SimpleNamespace(playbook_id="b"), SimpleNamespace(playbook_id="a")]
        )
        mod._note_mismatch("f1", "AAA_USDT", session, "a+b", Decimal(3), None, ())  # pyright: ignore[reportPrivateUsage]
        assert events.seen == [], "다리 순서만 다른 것은 다른 매매법이 아니다"

    def test_restore_checks_the_sessions_it_reuses(self) -> None:
        source = inspect.getsource(mod._restore_one)  # pyright: ignore[reportPrivateUsage]
        assert source.index("_running_handle(") < source.index("_note_mismatch(")
