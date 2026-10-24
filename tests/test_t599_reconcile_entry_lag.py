"""T599 — 진입 직후 포지션 조회 지연에 진입 체결을 청산가로 잡지 않는다 (2026-10-24).

사고(10-23 01:00 KST · SUSHI_USDT 숏 74 계약): 진입 체결 0.5 초 뒤 포지션 조회가 아직 0 이었다.
몫 대조는 그 0 을 믿고, 이름으로 못 찾은 청산 체결 대신 "가장 최근 체결"(= 방금 낸 진입 체결 ·
매도)을 청산가로 잡아 원장을 진입가 "손절" 로 닫고 몫 손절을 거뒀다 — 거래소 포지션이 손절 없이
4 시간 남았다.

지키는 것:
- 진입과 같은 방향 체결은 청산 체결이 아니다(`closes_side`).
- 포지션 0 을 이름 없는 체결로 닫으려면 0 이 `ZERO_CONFIRM_SECONDS` 넘게 이어져야 한다.
- 이름 있는 체결(발동한 손절 `ao-{id}`)은 기다리지 않고 바로 닫는다.
- 0 이 아닌 크기를 한 번 읽으면 기다림을 처음부터 다시 센다.
- 고아 이어받기(`_retry_adopt`)는 30 초 점검 루프에서도 감사 뒤에 돈다(전엔 걸음 = 4h 판은 4 시간).
- 나가는 중(메이커 청산 대기 · 막힌 몫 청산 재시도)인 포지션은 이어받지 않는다.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from test_live_fixes_1281 import _orphan_runner, _Stops  # pyright: ignore[reportPrivateUsage]
from test_share_runner import (
    ShareExchange,
    _Runner,  # pyright: ignore[reportPrivateUsage]
    key,
    now_of,
    share,
)
from updown.orchestration.walkforward import live_runner as lr
from updown.orchestration.walkforward.ledger import Direction, Ledger, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import (
    ZERO_CONFIRM_SECONDS,
    LiveRunner,
    closes_side,
)


def one_share() -> tuple[_Runner, ShareExchange, TradeRecord]:
    led = Ledger()
    a = share("aaaaaaaa1111", stop="95", contracts=2, playbook="macd@0.1.0")
    led.add(a)
    exch = ShareExchange(size=2)
    run = _Runner(exch, led)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    return run, exch, a


ENTRY_FILL = {
    "text": "t-411bb0-aaaaaaaa-en-0",
    "fill_price": "100",
    "finish_as": "filled",
    "size": "2",
}


def reconcile(run: _Runner) -> bool:
    return asyncio.run(run.reconcile())


def age_zero(run: _Runner) -> None:
    """처음 본 0 을 기다림 문턱보다 오래전 일로 돌린다(시계를 흘리는 대신)."""
    run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=ZERO_CONFIRM_SECONDS + 5)  # pyright: ignore[reportPrivateUsage]


class TestClosesSide:
    def test_a_long_is_closed_only_by_a_sell(self) -> None:
        assert closes_side({"size": "-2"}, Direction.LONG)
        assert not closes_side({"size": "2"}, Direction.LONG)

    def test_a_short_is_closed_only_by_a_buy(self) -> None:
        assert closes_side({"size": "74"}, Direction.SHORT)
        assert not closes_side({"size": "-74"}, Direction.SHORT)

    def test_unknown_is_not_filtered(self) -> None:
        assert closes_side({"size": "2"}, None)
        assert closes_side({}, Direction.LONG)
        assert closes_side({"size": "0"}, Direction.SHORT)
        assert closes_side({"size": "n/a"}, Direction.SHORT)


class TestTheSushiCase:
    def test_a_zero_read_right_after_entry_closes_nothing(self) -> None:
        run, exch, a = one_share()
        stop_before = dict(exch.stops)
        exch.size = 0  # 조회 지연 — 체결을 아직 못 봤다
        exch.history = [ENTRY_FILL]
        assert reconcile(run) is False
        assert now_of(run, a).outcome is Outcome.OPEN
        assert exch.stops == stop_before, "손절을 거두지 않는다"
        assert exch.cancelled_stops == []

    def test_even_after_the_wait_the_entry_fill_is_not_an_exit(self) -> None:
        run, exch, a = one_share()
        exch.size = 0
        exch.history = [ENTRY_FILL]
        assert reconcile(run) is False
        age_zero(run)
        assert reconcile(run) is False, "진입과 같은 방향 체결뿐이면 청산가가 없다 — 닫지 않는다"
        assert now_of(run, a).outcome is Outcome.OPEN
        assert exch.cancelled_stops == []

    def test_a_returning_position_resets_the_wait(self) -> None:
        run, exch, _a = one_share()
        exch.size = 0
        exch.history = [ENTRY_FILL]
        assert reconcile(run) is False
        assert run._zero_seen_at is not None  # pyright: ignore[reportPrivateUsage]
        exch.size = 2  # 다음 조회에 체결이 보인다
        assert reconcile(run) is False
        assert run._zero_seen_at is None  # pyright: ignore[reportPrivateUsage]


class TestRealExitsAreNotDelayed:
    def test_a_fired_stop_is_closed_at_once(self) -> None:
        run, exch, a = one_share()
        exch.fire(key(a), 2, "94.9")
        assert reconcile(run) is True
        assert now_of(run, a).exit_price == Decimal("94.9")

    def test_an_unnamed_opposite_fill_waits_for_the_zero_to_hold(self) -> None:
        run, exch, a = one_share()
        exch.size = 0
        exch.history = [
            {"text": "ao-999", "fill_price": "94.0", "finish_as": "filled", "size": "-2"}
        ]
        assert reconcile(run) is False, "이름 없는 체결은 0 이 이어질 때까지 기다린다"
        age_zero(run)
        assert reconcile(run) is True
        assert now_of(run, a).exit_price == Decimal("94.0")
        assert run._zero_seen_at is None  # pyright: ignore[reportPrivateUsage]


class TestOrphansAreAdoptedBetweenBars:
    def test_the_probe_loop_retries_adoption_after_the_audit(self) -> None:
        source = inspect.getsource(
            LiveRunner._keep_probing  # pyright: ignore[reportPrivateUsage]
        )
        assert "await self._retry_adopt()" in source, "점검 루프가 고아를 안 이어받는다"
        # 감사가 센 연속 수(_mismatch_streak)가 조건이라 감사 뒤여야 한다
        assert source.index("_run_audit") < source.index("_retry_adopt")

    def test_the_walk_still_retries_too(self) -> None:
        source = inspect.getsource(LiveRunner._walk_once)  # pyright: ignore[reportPrivateUsage]
        assert "_retry_adopt" in source

    @pytest.mark.parametrize("exiting", ["maker", "share_retry"])
    def test_a_position_on_its_way_out_is_not_adopted(
        self, monkeypatch: pytest.MonkeyPatch, exiting: str
    ) -> None:
        monkeypatch.setattr(lr, "ADOPT_RETRY_S", 0.0)
        runner = _orphan_runner(_Stops([[]]), refused=None, streak=2)
        calls: list[int] = []

        async def adopt() -> bool:
            calls.append(1)
            return True

        runner.adopt = adopt  # type: ignore[method-assign]
        if exiting == "maker":
            runner.__dict__["_maker_exit"] = {"t1": (3, 2)}
        else:
            runner.__dict__["_exit_retry"] = {"t1": object()}
        asyncio.run(runner._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        assert calls == [], "나가는 중인 포지션을 이어받으면 마무리 경로와 다툰다"
        runner.__dict__["_maker_exit"] = {}
        runner.__dict__["_exit_retry"] = {}
        asyncio.run(runner._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        assert calls == [1], "다 나가면 다시 이어받는다"
