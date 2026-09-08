"""재시작이 **무엇을 잊고 무엇을 지어내는가** (사용자 신고 2026-08-20).

Note:
    러너의 메모리는 프로세스와 함께 죽고 원장은 DB 에서 통째로 되살아난다. 그 둘의
    비대칭이 두 가지 사고를 냈다.

    🔴 **① 끝난 매매가 새 주문이 됐다.**

    ```
    live14ee7408  failures 10 · placed 10건 전부 contracts 0
                  그 10건은 14:30~17:09 에 손절·익절로 닫힌 매매였다
    ```

    `_sent` 는 빈 집합으로 뜨고 `_place` 는 `outcome` 을 안 봤다. 이번엔 지정가 판이라
    `_protect`(읽기만) 로 빠져 무해했지만, **시장가 판이었으면 몇 시간 전에 닫힌 매매
    10건을 다시 열었다.**

    🔴 **② 못 낸 주문을 잊었다.**

    SOL 판이 15:30 에 두 번 거절됐는데(예산이 1계약을 못 샀다) 19:27 재시작 뒤 화면은
    `failures 0 · last_error ""` 였다 — **한 건도 못 내는 판**과 **자리가 아직 안 난
    판**이 똑같이 보였고, 사용자가 7시간 뒤에야 물었다.
"""

from __future__ import annotations

import inspect
import uuid
from decimal import Decimal
from typing import Any

import pytest

from updown.orchestration.walkforward.live_runner import LiveRunner


class TestClosedTradesNeverBecomeOrders:
    """① 끝난 기록은 주문이 아니다."""

    def test_the_filter_looks_at_the_outcome(self) -> None:
        """🔴 `opened_at` 만 보면 **닫힌 매매도 "열린 적 있다"** 라 전부 통과한다."""
        source = inspect.getsource(LiveRunner._place)  # pyright: ignore[reportPrivateUsage]
        head = source[source.index("fresh = [") : source.index("if not fresh")]
        assert "item.opened_at is not None" in head
        assert "item.outcome is Outcome.OPEN" in head

    def test_open_records_still_pass(self) -> None:
        """⭐ 보유 중은 통과해야 한다 — 되찾은 포지션에 손절을 다시 거는 길이 그것이다.

        ⛔ 여기서 `is not Outcome.OPEN` 으로 뒤집으면 재시작 뒤 포지션이 무방비가 된다.
        """
        source = inspect.getsource(LiveRunner._place)  # pyright: ignore[reportPrivateUsage]
        assert "is not Outcome.OPEN" not in source

    def test_sent_starts_empty_which_is_why_the_filter_matters(self) -> None:
        """⚠️ 뿌리는 여기다 — `_sent` 는 저장되지 않는다.

        고치는 길이 둘이었다(기억을 저장한다 / 끝난 것을 거른다). **거르는 쪽이 옳다** —
        저장하면 "보낸 적 있다" 라는 새 사실을 하나 더 관리해야 하고, 그 사실이 틀리면
        이번과 반대 방향(진짜 주문을 안 냄)으로 조용히 실패한다.
        """
        source = inspect.getsource(LiveRunner.__init__)
        assert "self._sent: set[str] = set()" in source


class TestRejectionsSurviveARestart:
    """② 못 낸 주문을 화면이 잊지 않는다."""

    class _Store:
        """거절 둘을 기억하는 가짜 저장소."""

        def __init__(self, rows: tuple[tuple[int, str], ...] = ()) -> None:
            self.asked = 0
            self._rows = rows

        async def rejections(self, _run_id: uuid.UUID) -> tuple[int, str]:
            self.asked += 1
            return self._rows[0] if self._rows else (0, "")

    @staticmethod
    def _runner(store: Any) -> LiveRunner:
        """저장소만 꽂힌 껍데기 러너 — 거래소를 부르지 않는다."""
        runner = LiveRunner.__new__(LiveRunner)
        runner.failures = 0
        runner.last_error = ""
        runner._store = store  # pyright: ignore[reportPrivateUsage]
        runner._run_id = uuid.uuid4()  # pyright: ignore[reportPrivateUsage]
        return runner

    async def test_it_recalls_what_the_ledger_kept(self) -> None:
        """🔴 사실은 `wf_orders` 에 남아 있었다 — 한 번 읽어 되살린다."""
        store = self._Store(((2, "계약 수 0 가 최소 1 에 못 미친다"),))
        runner = self._runner(store)
        await runner._recall_rejections()  # pyright: ignore[reportPrivateUsage]
        assert runner.failures == 2
        assert "재시작 전 거절 2건" in runner.last_error
        assert "최소 1 에 못 미친다" in runner.last_error

    async def test_a_clean_run_stays_clean(self) -> None:
        """⛔ 없는 이상을 지어내지 않는다."""
        runner = self._runner(self._Store())
        await runner._recall_rejections()  # pyright: ignore[reportPrivateUsage]
        assert runner.failures == 0
        assert runner.last_error == ""

    async def test_a_broken_store_does_not_stop_the_run(self) -> None:
        """⛔ 표시용 값이 판을 못 뜨게 하면 안 된다 (§1.2.1)."""

        class _Broken:
            async def rejections(self, _run_id: uuid.UUID) -> tuple[int, str]:
                raise RuntimeError("DB 가 없다")

        runner = self._runner(_Broken())
        await runner._recall_rejections()  # pyright: ignore[reportPrivateUsage]
        assert runner.failures == 0

    async def test_no_store_is_not_an_error(self) -> None:
        runner = self._runner(None)
        runner._run_id = None  # pyright: ignore[reportPrivateUsage]
        await runner._recall_rejections()  # pyright: ignore[reportPrivateUsage]
        assert runner.failures == 0

    def test_it_runs_once_at_startup_not_every_step(self) -> None:
        """⚠️ 폴링 경로에 두면 판마다 몇 초 간격으로 DB 를 때린다."""
        source = inspect.getsource(LiveRunner.run)
        assert "await self._recall_rejections()" in source
        step = inspect.getsource(LiveRunner._loop)  # pyright: ignore[reportPrivateUsage]
        assert "_recall_rejections" not in step

    def test_it_recalls_before_it_trades(self) -> None:
        """🔴 배율·이어받기보다 **먼저** 읽는다 — 그 둘이 실패하면 새 이유가 옛 것을 덮는다."""
        source = inspect.getsource(LiveRunner.run)
        assert source.index("_recall_rejections") < source.index("_sync_leverage")


class TestTheStoreReadIsHonest:
    """저장소 쪽 계약."""

    def test_it_only_counts_rejections(self) -> None:
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.rejections)
        assert 'WalkforwardOrder.status == "rejected"' in source

    def test_it_returns_the_last_reason_not_the_first(self) -> None:
        """⭐ 마지막이 지금 상태에 가깝다 — 첫 거절은 이미 고쳐졌을 수 있다."""
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.rejections)
        assert "reversed(found)" in source

    def test_it_never_raises(self) -> None:
        """⛔ 되짚기용 값이 판을 막지 않는다 (절대 규칙 #8-1)."""
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.rejections)
        assert 'return 0, ""' in source


@pytest.mark.parametrize("value", [Decimal(0)])
def test_module_imports_cleanly(value: Decimal) -> None:
    """⚠️ 이 파일이 러너를 들여오는 것 자체가 순환 import 검사다."""
    assert value == 0
