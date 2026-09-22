"""멤버가 기록을 닫으면 펀드가 **다음 4h 경계를 안 기다리고** 다시 앵커한다 (2026-09-22).

사용자 지적: *"경계 봉에서 익절이 나면, 그 실현 손익은 틱이 지갑을 읽은 뒤에 들어옵니다. 그래서
다음 틱(4시간 뒤)에야 크기에 반영됩니다."* → *"이것만 수정되면 되겠네."*

지키는 것:

    ① 러너는 걸음이 기록을 닫았을 때만 `on_closed` 를 부른다 (안 닫힌 걸음은 부르지 않는다)
    ② 훅이 던져도 걸음은 멀쩡하다
    ③ 펀드 모듈은 닫힌 판의 펀드만 표시한다 (단독 판은 무시)
    ④ 표시 뒤 `CLOSE_TICK_DELAY_S` 가 지나야 앵커하고, 냉각 안이면 미룬다 · 4h 경계 틱이 표시를
       겸한다
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
    @pytest.fixture(autouse=True)
    def clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rebalancer, "FUNDS", {})
        monkeypatch.setattr(rebalancer, "_CLOSED_AT", {})
        monkeypatch.setattr(rebalancer, "_LAST_TICK_AT", {})

    def _fund(self, fund_id: str, handles: dict[str, str]) -> Any:
        return SimpleNamespace(fund_id=fund_id, handles=handles)

    def test_marks_only_the_owning_fund(self) -> None:
        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc"})
        rebalancer.FUNDS["f2"] = self._fund("f2", {"ETH_USDT": "h-eth"})
        assert rebalancer.note_closed("h-eth") == "f2"
        assert rebalancer.note_closed("h-lone") is None
        assert set(rebalancer._CLOSED_AT) == {"f2"}  # pyright: ignore[reportPrivateUsage]

    def test_the_hook_is_registered_not_imported(self) -> None:
        assert rebalancer.note_closed in api.ON_TRADE_CLOSED
        assert "rebalancer" not in inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]

    async def test_waits_for_the_delay_then_ticks_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc"})
        ticked: list[str] = []

        async def fake_tick(fund: Any, _flow: Any = None) -> None:
            ticked.append(fund.fund_id)

        monkeypatch.setattr(rebalancer, "_tick", fake_tick)
        rebalancer._CLOSED_AT["f1"] = 1000.0  # pyright: ignore[reportPrivateUsage]
        assert await rebalancer._tick_closed(now=1000.0 + rebalancer.CLOSE_TICK_DELAY_S - 1) == []  # pyright: ignore[reportPrivateUsage]
        assert ticked == []
        assert await rebalancer._tick_closed(now=1000.0 + rebalancer.CLOSE_TICK_DELAY_S) == ["f1"]  # pyright: ignore[reportPrivateUsage]
        assert ticked == ["f1"]
        assert "f1" not in rebalancer._CLOSED_AT  # pyright: ignore[reportPrivateUsage]
        # 다시 표시가 와도 냉각 안이면 미룬다 — 손절이 연달아 나도 1분에 한 번.
        rebalancer._CLOSED_AT["f1"] = 1020.0  # pyright: ignore[reportPrivateUsage]
        assert await rebalancer._tick_closed(now=1040.0) == []  # pyright: ignore[reportPrivateUsage]
        later = 1000.0 + rebalancer.CLOSE_TICK_DELAY_S + rebalancer.CLOSE_TICK_COOLDOWN_S
        assert await rebalancer._tick_closed(now=later) == ["f1"]  # pyright: ignore[reportPrivateUsage]

    async def test_a_failed_tick_is_logged_and_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rebalancer.FUNDS["f1"] = self._fund("f1", {"BTC_USDT": "h-btc"})

        async def boom(_fund: Any, _flow: Any = None) -> None:
            raise RuntimeError("거래소")

        monkeypatch.setattr(rebalancer, "_tick", boom)
        rebalancer._CLOSED_AT["f1"] = 0.0  # pyright: ignore[reportPrivateUsage]
        assert await rebalancer._tick_closed(now=100.0) == []  # pyright: ignore[reportPrivateUsage]
        left = rebalancer._CLOSED_AT  # pyright: ignore[reportPrivateUsage]
        assert "f1" not in left, "다음 4h 경계가 어차피 앵커한다 — 영원히 재시도하지 않는다"

    def test_a_vanished_fund_is_forgotten(self) -> None:
        rebalancer._CLOSED_AT["gone"] = 0.0  # pyright: ignore[reportPrivateUsage]
        import asyncio

        assert asyncio.run(rebalancer._tick_closed(now=100.0)) == []  # pyright: ignore[reportPrivateUsage]
        assert "gone" not in rebalancer._CLOSED_AT  # pyright: ignore[reportPrivateUsage]

    def test_the_boundary_tick_clears_the_mark_before_it_reads_the_wallet(self) -> None:
        """틱이 지갑을 읽는 동안 들어온 닫힘이 지워지면 그 실현 손익은 4시간을 기다린다."""
        source = inspect.getsource(rebalancer.rebalance_loop)
        assert "await _tick_closed()" in source
        assert source.index("await _tick_closed()") < source.index(
            "for fund in list(FUNDS.values()):"
        )
        body = source[source.index("for fund in list(FUNDS.values()):") :]
        assert body.index("_CLOSED_AT.pop(fund.fund_id, None)") < body.index("await _tick(fund)")
        # 경계 직전의 닫힘은 경계 틱이 앵커한다 — 같은 지갑을 두 번 읽지 않는다.
        assert "if left > CLOSE_TICK_DELAY_S:" in source
