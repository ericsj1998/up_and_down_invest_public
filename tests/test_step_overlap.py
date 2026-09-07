"""걸음이 **겹쳐 돌아 사다리가 두 번 나갔다** (사용자 신고 2026-08-21).

Note:
    자가 점검이 둘을 외쳤다:

    ```
    🔴 원장 +22.80 vs 거래소 -19.00 — 부호가 다르다
    🔴 원장 없음 / 거래소 보유중
    ```

    뿌리는 하나였다. `_one_step` 은 두 곳에서 불린다 — `_chase_trigger`(1초마다)와
    `_loop`(봉마다). 그것은 의도된 설계지만(원칙 P3), 안에서 네트워크를 여러 번
    기다리므로 **한쪽이 도는 중에 다른 쪽이 들어왔다.**

    우편함이 그것을 못 막는다 — `to_place()` 는 대기 목록을 **읽기만** 하고 지우는 것은
    `sent()` 다. 둘이 같은 목록을 읽고 둘 다 보낸다:

    ```
    02:15:05.763  숏 -599  en-1
    02:15:05.764  숏 -599  en-1   ← 1ms 뒤 같은 다리
    02:15:06.210  숏 -599  en-0
    02:15:06.210  숏 -599  en-0   ← 0.1ms 뒤 같은 다리
                  합계 -2396      (계획은 -1198)
    05:45:02      +1198 청산      ← 원장이 아는 만큼만 닫았다
                  -1198 남음      🔴 유령
    ```

    ⚠️ 실측: 진입 다리 114개 중 **10개**가 겹쳤다 (DOGE 4 · BTC 4 · ETH 1 · SOL 1).
    그중 넷은 간격이 **0~1ms** — 재시작도 재시도도 아니고 **동시 실행**이다.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from updown.orchestration.walkforward.live_runner import LiveRunner


class _Runner:
    """겹침 방어만 떼어 본 껍데기 — 네트워크를 안 탄다."""

    def __init__(self, delay: float = 0.02) -> None:
        self._stepping = asyncio.Lock()
        self.overlaps = 0
        self.walked = 0
        self._delay = delay

    async def _walk_once(self) -> None:
        # ⚠️ 진짜 걸음처럼 **중간에 기다린다** — 그 사이가 겹침이 나던 자리다.
        await asyncio.sleep(self._delay)
        self.walked += 1

    _one_step = LiveRunner._one_step  # pyright: ignore[reportPrivateUsage]


class TestTwoLoopsCannotOverlap:
    """① 같은 순간에 두 번 돌지 않는다."""

    async def test_a_second_call_is_skipped(self) -> None:
        made = _Runner()
        await asyncio.gather(made._one_step(), made._one_step())  # pyright: ignore[reportPrivateUsage]
        assert made.walked == 1, "겹쳐 들었는데 둘 다 돌았다 — 사다리가 두 번 나간다"
        assert made.overlaps == 1

    async def test_many_at_once(self) -> None:
        """🔴 실측은 둘이었지만 셋 이상도 같은 병이다."""
        made = _Runner()
        await asyncio.gather(*(made._one_step() for _ in range(8)))  # pyright: ignore[reportPrivateUsage]
        assert made.walked == 1
        assert made.overlaps == 7

    async def test_it_runs_again_after_the_first_finishes(self) -> None:
        """⛔ 막는 것이지 **멈추는 것이 아니다** — 다음 걸음은 정상으로 돈다."""
        made = _Runner()
        await made._one_step()  # pyright: ignore[reportPrivateUsage]
        await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert made.walked == 2
        assert made.overlaps == 0

    async def test_a_failure_releases_the_lock(self) -> None:
        """🔴 예외에 잠금이 남으면 판이 **영영 멈춘다** — 겹침보다 나쁘다."""
        made = _Runner()

        async def boom() -> None:
            raise RuntimeError("걸음이 터졌다")

        made._walk_once = boom  # type: ignore[assignment,method-assign]
        with pytest.raises(RuntimeError):
            await made._one_step()  # pyright: ignore[reportPrivateUsage]
        assert not made._stepping.locked(), "잠금이 안 풀렸다"  # pyright: ignore[reportPrivateUsage]


class TestItSkipsInsteadOfQueueing:
    """② 줄을 세우지 않는다."""

    def test_it_returns_rather_than_waits(self) -> None:
        """⛔ 밀린 걸음이 뒤늦게 **옛 값으로** 판정한다 — 방아쇠는 1초 뒤에 또 온다."""
        source = inspect.getsource(LiveRunner._one_step)  # pyright: ignore[reportPrivateUsage]
        cut = source.index("self._stepping.locked()")
        branch = source[cut : cut + 160]
        assert "return" in branch
        assert "await self._stepping.acquire()" not in branch

    def test_the_check_has_no_await_before_the_lock(self) -> None:
        """🔴 검사와 잠금 사이에 `await` 가 들어가면 **그 순간 다시 깨진다.**"""
        source = inspect.getsource(LiveRunner._one_step)  # pyright: ignore[reportPrivateUsage]
        cut = source.index("self._stepping.locked()")
        gap = source[cut : source.index("async with self._stepping")]
        assert "await" not in gap

    def test_it_counts_the_skips(self) -> None:
        """⭐ 새 규칙이 값을 만들면 분포를 싣는다 (§1-0s)."""
        source = inspect.getsource(LiveRunner._one_step)  # pyright: ignore[reportPrivateUsage]
        assert "self.overlaps += 1" in source

    def test_the_screen_can_see_it(self) -> None:
        """⚠️ 급증하면 **걸음이 너무 오래 걸린다**는 뜻이라 사람이 봐야 한다."""
        from updown.apps.api import walkforward as wf

        assert "runner.overlaps" in inspect.getsource(wf.live_health)


class TestBothLoopsStillShareOnePath:
    """③ 두 루프가 같은 함수를 쓰는 것은 **의도된 설계**다 (원칙 P3)."""

    def test_both_callers_remain(self) -> None:
        """⭐ 방아쇠(1초마다)와 **스트림**(봉마다) 둘 다 같은 함수를 부른다."""
        chase = inspect.getsource(
            LiveRunner._chase_trigger  # pyright: ignore[reportPrivateUsage]
        )
        stream = inspect.getsource(LiveRunner._consume)  # pyright: ignore[reportPrivateUsage]
        assert "await self._one_step()" in chase
        assert "await self._one_step()" in stream

    def test_nobody_calls_the_body_directly(self) -> None:
        """⛔ `_walk_once` 를 직접 부르면 방어를 통째로 건너뛴다."""
        source = inspect.getsource(LiveRunner)
        assert source.count("_walk_once()") == 1, "방어를 우회하는 호출이 있다"
