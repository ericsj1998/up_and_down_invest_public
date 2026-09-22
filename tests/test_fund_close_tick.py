"""멤버가 기록을 닫으면 펀드가 **그 순간** 지갑에 다시 앵커한다 (2026-09-22).

사용자 지적: *"경계 봉에서 익절이 나면, 그 실현 손익은 틱이 지갑을 읽은 뒤에 들어옵니다. 그래서
다음 틱(4시간 뒤)에야 크기에 반영됩니다."* → *"이것만 수정되면 되겠네."*

지키는 것:

    ① 러너는 걸음이 기록을 닫았을 때만 `on_closed` 를 부른다 (안 닫힌 걸음은 부르지 않는다)
    ② 훅이 던져도 걸음은 멀쩡하다
    ③ 펀드 모듈은 닫힌 판의 펀드만 앵커한다 (단독 판은 무시)
    ④ 앵커 = 4h 경계·수동 틱과 같은 `_tick`(지갑 읽기) — 시간 추측도 원장 증분 셈도 없다.
       한 걸음에 여럿이 닫혀도 지갑은 한 번(도는 중에 온 것은 끝난 뒤 한 번 더)만 읽는다
    ⑤ 훅은 등록으로 꽂힌다 — 러너·walkforward 는 펀드 모듈을 모른다 (import 방향)
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from test_live_incident_20260820 import (  # pyright: ignore[reportPrivateUsage]
    Book,
    Exchange,
    at,
    ledger_with,
    runner_for,
    trade,
)
from updown.apps.api import rebalancer
from updown.apps.api import walkforward as api
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.live_runner import LiveRunner


class _Quiet:
    """로거 대역 — 경고를 삼킨다."""

    def warning(self, *_a: object, **_k: object) -> None:
        return None


class TestRunnerHook:
    async def _runner(self, closes: bool) -> tuple[LiveRunner, list[int]]:
        record = trade(opened_at=at("2026-08-20T00:01:00Z"))
        made = runner_for(ledger_with(record), Exchange())
        calls: list[int] = []
        made.on_closed = lambda: calls.append(1)
        import asyncio

        made._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]

        async def walk() -> None:
            if closes:
                # `closed()` 는 새 기록을 돌려준다(불변) — 원장에 갈아 끼워야 "끝난 기록" 이 된다.
                made._session.ledger.replace(  # pyright: ignore[reportPrivateUsage]
                    record.closed(
                        at=at("2026-08-20T01:00:00Z"),
                        price=Decimal("68000"),
                        outcome=Outcome.TAKE_PROFIT,
                    )
                )

        made._walk_once = walk  # type: ignore[method-assign]
        return made, calls

    async def test_fires_only_when_a_record_closed(self) -> None:
        made, calls = await self._runner(closes=True)
        await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert calls == [1]
        await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert calls == [1], "이미 닫힌 기록은 다시 세지 않는다"

    async def test_quiet_step_does_not_fire(self) -> None:
        made, calls = await self._runner(closes=False)
        await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert calls == []

    async def test_a_broken_hook_does_not_break_the_step(self) -> None:
        made, _ = await self._runner(closes=True)

        def boom() -> None:
            raise RuntimeError("펀드 쪽 고장")

        made.on_closed = boom
        object.__setattr__(made, "_log", _Quiet())
        await made._one_step()  # pyright: ignore[reportPrivateUsage]  # 던지면 실패

    def test_default_hook_is_a_noop(self) -> None:
        made = runner_for(ledger_with(), Exchange())
        assert not hasattr(made, "on_closed") or callable(made.on_closed)
        assert "self.on_closed: Callable[[], None] = lambda: None" in inspect.getsource(
            LiveRunner.__init__
        )

    async def test_a_cancelled_plan_is_not_a_close(self) -> None:
        """못 채운 계획(취소)은 돈이 안 움직였다 — 펀드를 다시 앵커할 이유가 없다."""
        record = trade(opened_at=at("2026-08-20T00:01:00Z"))
        made = runner_for(ledger_with(record), Exchange())
        calls: list[int] = []
        made.on_closed = lambda: calls.append(1)
        import asyncio

        made._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]

        async def walk() -> None:
            made._session.ledger.replace(  # pyright: ignore[reportPrivateUsage]
                record.closed(
                    at=at("2026-08-20T01:00:00Z"),
                    price=record.entry,
                    outcome=Outcome.CANCELLED,
                )
            )

        made._walk_once = walk  # type: ignore[method-assign]
        await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert calls == []

    async def test_fires_even_when_the_walk_raises_after_closing(self) -> None:
        """걸음 뒤쪽(대조·저장)이 터져도 앞쪽의 청산은 청산이다."""
        record = trade(opened_at=at("2026-08-20T00:01:00Z"))
        made = runner_for(ledger_with(record), Exchange())
        calls: list[int] = []
        made.on_closed = lambda: calls.append(1)
        import asyncio

        made._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]

        async def walk() -> None:
            made._session.ledger.replace(  # pyright: ignore[reportPrivateUsage]
                record.closed(
                    at=at("2026-08-20T01:00:00Z"),
                    price=Decimal("68000"),
                    outcome=Outcome.STOP_LOSS,
                )
            )
            raise RuntimeError("저장 실패")

        made._walk_once = walk  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert calls == [1]

    def test_the_probe_reconcile_also_notices_closes(self) -> None:
        """거래소 조건부 손절(`stop_mode: touch`)은 60초 프로브의 대조가 닫는다 — 걸음이 아니다."""
        source = inspect.getsource(LiveRunner._keep_probing)  # pyright: ignore[reportPrivateUsage]
        assert source.index("before = _closed_count(self)") < source.index("await self.reconcile()")
        assert "_notice_closes(self, before)" in source

    def test_walk_still_owns_the_book(self) -> None:
        """대역 `Book` 이 원장만 있어도 `_closed_count` 가 돈다 — 가짜 세션 시험 여섯을 안 깬다."""
        made = runner_for(ledger_with(trade(opened_at=at("2026-08-20T00:01:00Z"))), Exchange())
        assert isinstance(made._session, Book)  # pyright: ignore[reportPrivateUsage]
        assert live_runner._closed_count(made) == 0  # pyright: ignore[reportPrivateUsage]
        assert live_runner._closed_count(object()) == 0  # pyright: ignore[reportPrivateUsage]


class TestFundSide:
    """닫힘 → 펀드를 **지금** 지갑에 다시 앵커한다 (같은 `_tick` · 시계도 원장 산술도 없다)."""

    @pytest.fixture(autouse=True)
    def clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fresh_dirty: set[str] = set()
        fresh_tasks: dict[str, Any] = {}
        monkeypatch.setattr(rebalancer, "FUNDS", {})
        monkeypatch.setattr(rebalancer, "_ANCHOR_DIRTY", fresh_dirty)
        monkeypatch.setattr(rebalancer, "_ANCHOR_TASKS", fresh_tasks)

    def _fund(self, fund_id: str, handles: dict[str, str]) -> Any:
        engine = SimpleNamespace(balance=Decimal(600))
        return SimpleNamespace(
            fund_id=fund_id, handles=handles, coordinator=SimpleNamespace(engine=engine)
        )

    def _ticker(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[str]:
        ticked: list[str] = []

        async def fake_tick(fund: Any, _flow: Any = None) -> Any:
            import asyncio

            await asyncio.sleep(0.01)  # 진짜처럼 지갑을 기다린다 — 그 사이 닫힘이 더 올 수 있다
            if fail:
                raise RuntimeError("거래소")
            ticked.append(fund.fund_id)
            return SimpleNamespace(balance=Decimal(660), budgets={"BTC_USDT": Decimal(110)})

        monkeypatch.setattr(rebalancer, "_tick", fake_tick)
        return ticked

    async def test_a_close_anchors_the_fund_now(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc"})
        ticked = self._ticker(monkeypatch)
        assert rebalancer.note_closed("h-btc") == "f1"
        await asyncio.sleep(0.05)
        assert ticked == ["f1"]

    async def test_a_burst_of_closes_anchors_once_or_twice_not_n_times(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """한 걸음에 멤버 셋이 닫혀도 지갑은 한 번(도는 중에 온 것은 끝난 뒤 한 번 더)만 읽는다."""
        import asyncio

        rebalancer.FUNDS["f1"] = self._fund(
            "f1", {"BTC_USDT": "h-btc", "ETH_USDT": "h-eth", "XRP_USDT": "h-xrp"}
        )
        ticked = self._ticker(monkeypatch)
        for handle in ("h-btc", "h-eth", "h-xrp"):
            rebalancer.note_closed(handle)
        await asyncio.sleep(0.08)
        assert 1 <= len(ticked) <= 2

    async def test_a_close_during_the_anchor_reads_the_wallet_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """지갑을 읽는 동안 또 닫히면 그 닫힘이 빠질 수 있다 — 끝난 뒤 한 번 더 읽는다."""
        import asyncio

        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc", "ETH_USDT": "h-eth"})
        ticked = self._ticker(monkeypatch)
        rebalancer.note_closed("h-btc")
        await asyncio.sleep(0.003)  # 첫 앵커가 지갑을 기다리는 중
        rebalancer.note_closed("h-eth")
        await asyncio.sleep(0.08)
        assert ticked == ["f1", "f1"]

    async def test_a_standalone_run_touches_no_fund(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc"})
        ticked = self._ticker(monkeypatch)
        assert rebalancer.note_closed("h-lone") is None
        await asyncio.sleep(0.03)
        assert ticked == []

    async def test_a_failed_anchor_is_logged_and_left_to_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc"})
        self._ticker(monkeypatch, fail=True)
        rebalancer.note_closed("h-btc")
        await asyncio.sleep(0.05)
        assert "f1" not in rebalancer._ANCHOR_DIRTY  # pyright: ignore[reportPrivateUsage]
        task = rebalancer._ANCHOR_TASKS["f1"]  # pyright: ignore[reportPrivateUsage]
        assert task.done() and task.exception() is None, "태스크가 조용히 죽지 않는다"

    def test_the_hook_is_registered_not_imported(self) -> None:
        assert rebalancer.note_closed in api.ON_TRADE_CLOSED
        assert "rebalancer" not in inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]

    def test_no_clock_and_no_ledger_arithmetic(self) -> None:
        """시간 추측(15초)도, 원장 증분 셈(대조보다 늦어 이중 계산)도 없다 — 지갑 앵커 하나."""
        source = inspect.getsource(rebalancer.note_closed) + inspect.getsource(
            rebalancer._anchor_on_close  # pyright: ignore[reportPrivateUsage]
        )
        assert "sleep" not in source
        assert "coordinator.tick()" not in source, "앵커 없는 증분 틱은 대조보다 늦어 두 번 센다"
        assert "await _tick(fund)" in source, "4h 경계·수동 틱과 같은 앵커 경로 하나"

    def test_the_boundary_loop_is_its_original_shape(self) -> None:
        source = inspect.getsource(rebalancer.rebalance_loop)
        assert "_tick_closed" not in source
        assert "await _tick(fund)" in source
