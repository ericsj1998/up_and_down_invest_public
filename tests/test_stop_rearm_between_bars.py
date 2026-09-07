"""손절이 **봉 사이에** 사라졌을 때 누가 다시 거나 (2026-08-31 실측 사고).

Note:
    🔴 페이퍼 콘솔이 *"21 계약을 들고 있는데 조건부는 0 계약만 덮는다"* 를 계속
    띄웠고, Gate 를 직접 읽어 보니 **사실이었다.**

    ```
    08-31 00:00:00  조건부 등록          trigger 76504.4 · close_long · 만료 30일
    08-31 13:47:53  Gate 가 죽였다        finish_as=failed
                                          "Order price deviated too much from mark price"
    08-31 13:48:22  감사 stop_missing     ← 29초 만에 알았다
    08-31 16:00:00  다음 4h 걸음          ← 여기까지 2시간 12분 무방비
    ```

    같은 날 ETH 도 05:31 취소 → 08:00 걸음까지 **2시간 29분** 무방비였다. 저절로
    나으니 화면에서는 배너가 깜빡이는 것으로만 보인다.

    🔴 **원인은 탐지가 아니라 복구다.** 고치는 자리(`_guard_stop`)가 `_walk_once` 안
    `session.step()` 의 조기 반환 **뒤에** 있었고, 라이브 급전의 `advance()` 는
    `STEP_FRAME`(5m) 을 무시하고 **진입축** 기준으로 답한다 — 진입축 4h 인 판에서
    *"매 걸음 확인한다"* 는 docstring 이 실제로는 **4시간에 한 번**이었다.

    ⚠️ `test_naked_alarm.py` 의 *"걸음마다 돈다"* 시험은 이 사고 중에도 **통과했다**.
    `_walk_once` 안에 호출이 있는지만 봤고, 그 호출에 닿는지는 안 봤다 — 그래서
    여기서는 **분기 동작**을 잰다 (소스 문자열이 아니라).
"""

from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.live_runner import (
    STOP_GUARD_LIMIT,
    LiveRunner,
    StopBlownError,
)


def _hush(*args: Any, **kwargs: Any) -> None:
    """아무것도 안 하는 자리채움 — 로그와 능력 검사용."""
    del args, kwargs


class _Stub(LiveRunner):
    """`_guard_stop` 의 분기만 재는 최소 러너.

    Note:
        ⛔ `LiveRunner.__init__` 은 급전·스트림·브로커 어댑터를 붙든다. 여기서 재려는
        것은 *실패를 어떻게 세는가* 하나이므로 필요한 것만 세운다 — 진짜 초기화를
        부르면 시험이 네트워크에 의존하게 되고, 그러면 이 시험이 안 돌게 된다.
    """

    def __init__(self, *, blows: Exception | None = None, misses: int = 0) -> None:
        self._arming = asyncio.Lock()
        self.observe_only = False
        self._armed_for = ""
        self._stop_id = ""
        self.stop_misses = misses
        self.last_error = ""
        self._blows = blows
        self.arm_calls = 0
        self.panics: list[str] = []
        # ⚠️ 진짜 로거 타입이 아니다 — 이 시험이 재는 것은 **분기**이고, 로그의 모양은
        #    다른 시험의 몫이다.
        self._log = cast("Any", SimpleNamespace(error=_hush, info=_hush, warning=_hush))
        held = SimpleNamespace(
            trade_id="a3da5d8cccd1",
            outcome=Outcome.OPEN,
            planned_stop=Decimal("76504.4"),
            note="",
        )
        # ⚠️ `stops_for` 가 **있어야** `_guard_stop` 이 진행한다 (능력 검사).
        self._orders = SimpleNamespace(stops_for=_hush)
        self._session = SimpleNamespace(
            guarded=True, ledger=SimpleNamespace(records=[held]), auto=True
        )

    async def _arm_stop(self, held: Any) -> Exception | None:  # pyright: ignore[reportIncompatibleMethodOverride]
        del held
        self.arm_calls += 1
        return self._blows

    async def _note_order(self, *a: Any, **k: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        del a, k

    async def _panic_close(self, held: Any, why: str) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        del held
        self.panics.append(why)

    def _fired(self, name: str, detail: str) -> None:
        del name, detail


class TestTheProbeLoopMayArmButNeverDumps:
    """점검 루프(30초)는 **거는 일만** 한다."""

    def test_a_failure_from_the_probe_loop_is_not_counted(self) -> None:
        """⚠️ 문턱은 *걸음* 을 세도록 골라진 값이다 — 30초 루프가 같이 세면 90초 만에
        시장가 청산이 난다.
        """
        run = _Stub(blows=RuntimeError("rate limited"))
        asyncio.run(run._guard_stop(escalate=False))  # pyright: ignore[reportPrivateUsage]
        assert run.arm_calls == 1, "걸어 보지도 않았다"
        assert run.stop_misses == 0
        assert run.panics == []
        # 🔴 그래도 **새 진입은 막는다** — 무방비인데 또 사면 무방비가 하나 더 는다.
        assert run._session.guarded is False  # pyright: ignore[reportPrivateUsage]

    def test_the_probe_loop_never_dumps_even_at_the_limit(self) -> None:
        """⛔ `_panic_close` 는 되돌릴 수 없다. 30초 루프가 그 방아쇠를 쥐면 청산이
        실패했을 때 **30초마다 다시 던진다.**
        """
        run = _Stub(blows=RuntimeError("여전히 못 건다"), misses=STOP_GUARD_LIMIT)
        asyncio.run(run._guard_stop(escalate=False))  # pyright: ignore[reportPrivateUsage]
        assert run.panics == []

    def test_a_step_still_counts_and_dumps(self) -> None:
        """⭐ 걸음 경로의 뜻은 **하나도 안 바뀐다** — 이 시험이 그 경계다."""
        run = _Stub(blows=RuntimeError("못 건다"), misses=STOP_GUARD_LIMIT - 1)
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert run.stop_misses == STOP_GUARD_LIMIT
        assert len(run.panics) == 1

    def test_an_already_breached_stop_is_dumped_from_either_path(self) -> None:
        """🔴 *"이미 지났다"* 는 4시간을 기다릴 일이 아니다 (2026-08-20 실측 33건).

        `escalate` 는 **연속 실패 계수**의 스위치다 — 손절선을 이미 통과한 것은
        세는 문제가 아니라 지금 나가야 하는 문제다.
        """
        run = _Stub(blows=StopBlownError("TRIGGER_PRICE_LESS_LAST"))
        asyncio.run(run._guard_stop(escalate=False))  # pyright: ignore[reportPrivateUsage]
        assert len(run.panics) == 1

    def test_success_reopens_the_gate(self) -> None:
        run = _Stub(misses=2)
        run._session.guarded = False  # pyright: ignore[reportPrivateUsage]
        asyncio.run(run._guard_stop(escalate=False))  # pyright: ignore[reportPrivateUsage]
        assert run.stop_misses == 0
        assert run._session.guarded is True  # pyright: ignore[reportPrivateUsage]


class TestArmingDoesNotOverlap:
    """`stops_for` 는 *목록 → 취소 → 등록* 이라 겹치면 조건부가 두 개 남는다."""

    def test_a_second_caller_skips_instead_of_queueing(self) -> None:
        """⚠️ 줄을 세우면 옛 값으로 뒤늦게 건다 — 30초 뒤에 어차피 다시 확인한다."""
        run = _Stub()
        gate = asyncio.Event()

        async def slow(held: Any) -> Exception | None:
            del held
            run.arm_calls += 1
            await gate.wait()
            return None

        run._arm_stop = slow  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

        async def both() -> None:
            first = asyncio.create_task(
                run._guard_stop()  # pyright: ignore[reportPrivateUsage]
            )
            await asyncio.sleep(0)  # 첫 호출이 잠금을 쥐게 한다
            await run._guard_stop(escalate=False)  # pyright: ignore[reportPrivateUsage]
            gate.set()
            await first

        asyncio.run(both())
        assert run.arm_calls == 1, "겹쳐 돌았다 — 조건부가 두 개 남는다"


class TestTheRepairRunsAsOftenAsTheDetection:
    """🔴 30초 만에 알고 4시간을 기다리는 것은 아는 것이 아니다."""

    def test_the_probe_loop_repairs_what_the_audit_found(self) -> None:
        source = inspect.getsource(
            LiveRunner._keep_probing  # pyright: ignore[reportPrivateUsage]
        )
        assert '"stop_missing"' in source, "점검 루프가 무방비를 안 고친다"
        assert "_guard_stop(escalate=False)" in source
        # 🔴 **감사 뒤**여야 한다 — 감사가 목록을 읽어 놓아야 조건이 성립한다.
        assert source.index("_run_audit") < source.index("_guard_stop")

    def test_fixing_is_the_loops_job_not_the_audits(self) -> None:
        """⛔ 감사가 상태를 바꾸면 다음에 무엇이 원인이었는지 알 수 없게 된다."""
        source = inspect.getsource(LiveRunner.audit)
        # ⚠️ 이름을 **언급**하는 것은 괜찮다 — 감사 ⑤ 는 `_guard_stop` 이 돌았는지를
        #    기준으로 쓴다. 막는 것은 감사가 직접 **부르는** 것이다.
        assert "await self._guard_stop" not in source
        assert "stops_for(" not in source

    @pytest.mark.parametrize("name", ["_walk_once", "_keep_probing"])
    def test_both_paths_still_call_it(self, name: str) -> None:
        """⭐ 걸음 경로를 **대체하는 것이 아니다** — 값 갱신(본절 상향)은 거기서 돈다."""
        source = inspect.getsource(getattr(LiveRunner, name))
        assert "_guard_stop" in source
