"""T293 — 펀드 멤버는 **펀드가 문·예산을 붙일 때까지** 신규 진입을 안 받는다 (2026-09-22).

🔴 v1.14.0 재기동에서 드러난 결함. 기동 순서가 `autostart_live()`(판 18개를 전부 되살린다) →
`restore_funds()` 라서, 먼저 뜬 판은 약 100초 동안 **그냥 단독 판**으로 걸었다:

    · 진입 문이 없다            자리 6 · 명목 상한 · 낙폭 브레이크를 아무도 안 본다
    · 다리 배율이 없다          숏 다리가 2x 가 아니라 4x 로 잡힌다
    · 원장이 격리 전이다        예산 = 장부값 20.72 (몫은 62.15) · equity = 계좌 전액

그 창에 1시간 봉이 닫혔고(17:00:06), 멀쩡한 포지션 넷이 *"계획의 3.0배 — 판이 겹쳐 쌓였다"* 로
찍혔으며, 잔액 대조는 *"판 11개 원장 합 4101.46 > 계정 372.86"* 을 외쳤다 (= 11 x 계좌).

여기서 못 박는 것:

    ① 스위치는 **진입만** 막는다 — 열린 포지션의 청산은 그대로 돈다 (§1.2.1)
    ② 되살아나는 판은 러너가 돌기 **전에** 스위치가 꺼진다 (펀드 파일에 그 종목이 있을 때만)
    ③ 사람이 새로 띄우는 단독 판은 안 막는다 — 막으면 아무도 안 풀어 준다
    ④ 푸는 자리는 `_attach_gate` 하나이고, 문을 끼운 **다음**이다 (문이 터지면 안 풀린다)
    ⑤ 기다리는 동안 겹침 경보는 미루고, 붙은 뒤 **같은 기록을 다시 잰다** (버리지 않는다)
    ⑥ 기다리는 판이 있으면 잔액 합계 대조를 미룬다 — 대신 `awaiting_fund` 가 말한다
    ⑦ 감시견이 되살린 멤버는 펀드에 다시 묶인다 (전에는 다음 재기동까지 문 없이 돌았다)
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from test_live_incident_20260820 import (  # pyright: ignore[reportPrivateUsage]
    Exchange,
    at,
    ledger_with,
    runner_for,
    trade,
)
from test_sample_end_to_end import _session  # pyright: ignore[reportPrivateUsage]
from updown.apps.api import rebalancer
from updown.apps.api import walkforward as api
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer.coordinator import Coordinator
from updown.orchestration.rebalancer.engine import RebalanceEngine
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.report.funds import fund_of_member
from updown.orchestration.walkforward import Session
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.live_runner import LiveRunner
from updown.portfolio.performance import TwrLedger


def _walk(session: Session) -> None:
    for _ in range(20_000):
        if session.finished:
            break
        session.step()


class TestTheSwitchBlocksEntriesOnly:
    def test_default_is_open(self) -> None:
        """단독 판 · 백테스트 · 페이퍼 워크는 예전 그대로 돈다."""
        assert _session().fund_ready is True

    def test_a_waiting_member_does_not_enter(self) -> None:
        session = _session()
        session.fund_ready = False
        _walk(session)
        assert not session.ledger.records, "펀드를 기다리는 동안은 한 건도 안 산다"
        assert session.funnel.get("blocked:awaiting_fund", 0) > 0, "막힌 걸음은 깔때기에 남는다"

    def test_released_member_enters_like_any_other(self) -> None:
        session = _session()
        session.fund_ready = False
        for _ in range(3):
            session.step()
        session.fund_ready = True
        _walk(session)
        assert session.ledger.records, "풀린 뒤에는 신호를 받는다"

    def test_an_open_position_still_exits(self) -> None:
        """🔴 나가는 길은 안 막는다 — 보유 중에 스위치가 꺼져도 청산은 돈다 (§1.2.1)."""
        session = _session()
        for _ in range(20_000):
            if session.finished or session.ledger.records:
                break
            session.step()
        assert session.ledger.records, "전제: 예시 매매법이 한 번 들어간다"
        assert session.ledger.records[0].outcome is Outcome.OPEN
        session.fund_ready = False
        _walk(session)
        assert session.ledger.records[0].outcome is not Outcome.OPEN, "청산이 막혔다"
        assert len(session.ledger.records) == 1, "그리고 새로 들어가지는 않는다"

    def test_the_switch_lives_in_the_single_entry_door(self) -> None:
        gate = inspect.getsource(Session._may_enter)  # pyright: ignore[reportPrivateUsage]
        assert "self.fund_ready" in gate
        flip = inspect.getsource(Session._open_reversal)  # pyright: ignore[reportPrivateUsage]
        assert "self.fund_ready" in flip, "뒤집기는 _may_enter 를 안 거친다 — 따로 막아야 한다"


class TestWhoWaits:
    def _fund_file(self, root: Path, *, market: str | None = "GATE") -> None:
        body: dict[str, Any] = {
            "fund_id": "fund1",
            "label": "시험 펀드",
            "basket": {"members": [{"symbol": "BTC_USDT", "weight": "1"}]},
            "twr": {"equity": "300"},
            "runs": {"BTC_USDT": "live-old-handle"},
        }
        if market is not None:
            body["market"] = market
        (root / "fund1.json").write_text(json.dumps(body), encoding="utf-8")

    def test_a_basket_symbol_belongs_to_the_fund(self, tmp_path: Path) -> None:
        self._fund_file(tmp_path)
        assert fund_of_member("BTC_USDT", "GATE", tmp_path) == "fund1"
        assert fund_of_member("ETH_USDT", "GATE", tmp_path) is None
        assert fund_of_member("BTC_USDT", "BINANCE", tmp_path) is None, "거래소가 다르면 남이다"

    def test_legacy_files_without_a_market_are_gate(self, tmp_path: Path) -> None:
        """복원(`_restore_one`)과 같은 기본값이어야 한다 — 다르면 막고 안 풀어 주는 판이 생긴다."""
        self._fund_file(tmp_path, market=None)
        assert fund_of_member("BTC_USDT", "GATE", tmp_path) == "fund1"

    def test_a_revived_run_waits_for_its_fund(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fund_file(tmp_path)
        monkeypatch.setenv("FUNDS_ROOT", str(tmp_path))
        found = api._awaited_fund({}, "BTC_USDT", "GATE", reviving=True)  # pyright: ignore[reportPrivateUsage]
        assert found == "fund1"

    def test_a_hand_started_run_is_never_held(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 펀드 바스켓에 있는 종목을 사람이 단독으로 띄웠다고 막으면 아무도 안 풀어 준다."""
        self._fund_file(tmp_path)
        monkeypatch.setenv("FUNDS_ROOT", str(tmp_path))
        found = api._awaited_fund({}, "BTC_USDT", "GATE", reviving=False)  # pyright: ignore[reportPrivateUsage]
        assert found is None

    def test_a_run_the_fund_spawns_says_so_itself(self) -> None:
        found = api._awaited_fund(  # pyright: ignore[reportPrivateUsage]
            {"fund_member": "fund"}, "BTC_USDT", "GATE", reviving=False
        )
        assert found == "fund"

    def test_an_unreadable_fund_dir_does_not_stop_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """여기서 던지면 판이 안 뜨고 되살아난 포지션의 손절 관리가 멈춘다 — 그쪽이 더 나쁘다."""

        def boom(*_: Any, **__: Any) -> str:
            raise OSError("디스크")

        monkeypatch.setattr(api, "fund_of_member", boom)
        assert api._awaited_fund({}, "BTC_USDT", "GATE", reviving=True) is None  # pyright: ignore[reportPrivateUsage]

    def test_the_flag_is_set_before_the_runner_starts(self) -> None:
        source = inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]
        assert source.index("session.fund_ready = False") < source.index(
            "asyncio.create_task(runner.run()"
        ), "러너가 먼저 돌면 그 사이 한 걸음이 문 없이 걷는다"

    def test_the_fund_marks_the_runs_it_spawns(self) -> None:
        source = inspect.getsource(rebalancer._spawn_session)  # pyright: ignore[reportPrivateUsage]
        assert source.index('payload["fund_member"]') < source.index("await _live_start(")


def _coordinator(*sessions: Session) -> Coordinator:
    names = [f"S{i}_USDT" for i in range(len(sessions))]
    basket = Basket(as_members([(name, Decimal(1)) for name in names]), version="v1")
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)), slots=6)
    ports = {name: SessionBridge(item) for name, item in zip(names, sessions, strict=True)}
    return Coordinator(engine=engine, ports=cast("Any", ports))


class TestRelease:
    def test_attaching_the_gate_releases_the_members(self) -> None:
        one, two = _session(), _session()
        one.fund_ready = two.fund_ready = False
        rebalancer._attach_gate(_coordinator(one, two), 6, 2, Decimal(2))  # pyright: ignore[reportPrivateUsage]
        assert one.fund_ready and two.fund_ready
        assert one.entry_gate is not None, "풀린 판에는 문이 끼워져 있어야 한다"

    def test_a_fund_without_rules_still_releases(self) -> None:
        """비중 배분 펀드는 문이 없다 — 그래도 원장 격리는 펀드가 붙어야 생기므로 기다린다."""
        one = _session()
        one.fund_ready = False
        rebalancer._attach_gate(_coordinator(one), 0, 0)  # pyright: ignore[reportPrivateUsage]
        assert one.fund_ready
        assert one.entry_gate is None

    def test_a_failed_wiring_releases_nobody(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """🔴 순서가 안전장치다 — 문이 터지면 *"문 없이 풀린 판"* 이 만들어지면 안 된다."""
        one = _session()
        one.fund_ready = False

        def boom(*_: Any, **__: Any) -> None:
            raise RuntimeError("문을 못 끼웠다")

        monkeypatch.setattr(rebalancer, "_wire_gate", boom)
        with pytest.raises(RuntimeError):
            rebalancer._attach_gate(_coordinator(one), 6, 2)  # pyright: ignore[reportPrivateUsage]
        assert one.fund_ready is False

    def test_release_is_the_only_writer_of_true(self) -> None:
        """참으로 되돌리는 자리가 늘면 그중 하나가 문보다 먼저 풀게 된다."""
        body = inspect.getsource(rebalancer)
        assert body.count("fund_ready = True") == 1
        assert "fund_ready = True" in inspect.getsource(rebalancer._release_members)  # pyright: ignore[reportPrivateUsage]
        assert "fund_ready = True" not in inspect.getsource(api)


class _Waiting:
    """세션 대역 — 러너가 보는 것은 원장·종목, 그리고 이제 `fund_ready` 다."""

    def __init__(self, ledger: Any) -> None:
        self.ledger = ledger
        self.instrument = SimpleNamespace(symbol="BTC_USDT")
        self.fund_ready = False


def _held() -> Any:
    return trade(
        opened_at=at("2026-08-20T00:01:00Z"),
        entry_fills=((Decimal("68508.1"), Decimal("1")),),
    )


class TestFalseAlarmsWait:
    async def test_the_stacked_check_waits_and_then_reruns(self) -> None:
        """⑤ 기다리는 동안은 분모가 틀렸다 — 미루되 버리지 않는다."""
        made = runner_for(ledger_with(_held()), Exchange(size=-2544))
        waiting = _Waiting(made._session.ledger)  # pyright: ignore[reportPrivateUsage]
        made._session = cast("Any", waiting)  # pyright: ignore[reportPrivateUsage]
        made._stack_deferred = {}  # pyright: ignore[reportPrivateUsage]

        await made._place()  # pyright: ignore[reportPrivateUsage]
        assert "stacked" not in made.guards, "격리 전 분모로 낸 경보는 거짓이다"
        assert made._stack_deferred, "그러나 버리지 않는다"  # pyright: ignore[reportPrivateUsage]

        waiting.fund_ready = True
        await made._place()  # pyright: ignore[reportPrivateUsage]
        assert "stacked" in made.guards, "펀드가 붙은 뒤에는 진짜 겹침을 말해야 한다"
        assert not made._stack_deferred  # pyright: ignore[reportPrivateUsage]

    async def test_a_standalone_run_is_checked_at_once(self) -> None:
        """단독 판(스위치 없음)은 지금까지와 똑같다 — 대역에 `fund_ready` 가 없어도 돈다."""
        made = runner_for(ledger_with(_held()), Exchange(size=-2544))
        await made._place()  # pyright: ignore[reportPrivateUsage]
        assert "stacked" in made.guards

    def test_wallet_drift_waits_for_every_member(self) -> None:
        source = inspect.getsource(LiveRunner.audit)
        assert "self._account_settled()" in source
        assert source.index("if not settled:") < source.index('"code": "wallet_drift"')
        assert '"code": "awaiting_fund"' in source, "미루는 동안 조용하면 안 된다 (규칙 #8)"
        wired = inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]
        assert "runner.account_settled = lambda" in wired

    def test_resize_does_not_run_on_the_booked_budget(self) -> None:
        """장부값(총자본 ÷ 종목 수)으로 되맞추면 멀쩡한 포지션을 1/3 로 팔아 내린다."""
        source = inspect.getsource(LiveRunner._resize_position)  # pyright: ignore[reportPrivateUsage]
        assert "if not self.fund_ready:" in source
        assert source.index("if not self.fund_ready:") < source.index("position_snapshot")


class TestWatchdogRebind:
    def test_revive_rebinds_the_member(self) -> None:
        source = inspect.getsource(api._revive)  # pyright: ignore[reportPrivateUsage]
        assert "rebind_member(handle)" in source

    def test_the_new_session_takes_the_dead_ones_place(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dead, fresh = _session(), _session()
        dead.ledger.seed_cash = Decimal("55.5")
        fresh.fund_ready = False
        coordinator = _coordinator(dead)
        symbol = next(iter(coordinator.ports))
        # 세션의 종목을 포트 이름에 맞춘다 — rebind 는 (거래소, 종목)으로 멤버를 찾는다.
        placed = SimpleNamespace(symbol=symbol, market=fresh.instrument.market)
        monkeypatch.setattr(fresh, "instrument", placed)
        fund = SimpleNamespace(
            fund_id="fund1",
            market=fresh.instrument.market.value,
            coordinator=coordinator,
            handles={symbol: "old"},
            slots=6,
            halt_after_stops=2,
            notional_cap=Decimal(2),
            notional_fit=False,
            drawdown_brake=None,
            breadth_cap=None,
            leverage=Decimal(4),
            legs=(),
            playbook="p",
        )
        saved: list[str] = []

        def save(item: Any) -> None:
            saved.append(str(item.fund_id))

        def quiet(_: Any) -> None:
            return None

        monkeypatch.setattr(rebalancer, "FUNDS", {"fund1": fund})
        monkeypatch.setattr(rebalancer, "SESSIONS", {"new": SimpleNamespace(session=fresh)})
        monkeypatch.setattr(rebalancer, "_save_fund", save)
        monkeypatch.setattr(rebalancer, "_log_gate", quiet)

        assert rebalancer.rebind_member("new") == "fund1"

        port = coordinator.ports[symbol]
        assert isinstance(port, SessionBridge)
        assert port.session is fresh, "펀드가 죽은 세션을 계속 붙들고 있으면 안 된다"
        assert fund.handles[symbol] == "new"
        assert fresh.fund_ready is True
        assert fresh.entry_gate is not None
        # 원장 격리 — 복원과 같은 식. seed 는 죽은 세션의 저장된 몫을 물려받는다 (T285).
        assert fresh.ledger.wallet_start == Decimal(0)
        assert fresh.ledger.refill is False
        assert fresh.ledger.seed_cash == Decimal("55.5")
        assert fresh.ledger.margin_budget == Decimal(1000) / Decimal(6)
        assert saved == ["fund1"]

    def test_a_standalone_run_is_nobody_s_member(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rebalancer, "FUNDS", {})
        monkeypatch.setattr(rebalancer, "SESSIONS", {"x": SimpleNamespace(session=_session())})
        assert rebalancer.rebind_member("x") is None
        assert rebalancer.rebind_member("없는 핸들") is None
