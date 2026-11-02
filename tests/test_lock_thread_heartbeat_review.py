"""T759 독립 검토 반영 — 스레드 심장박동의 네 보강 (1-a · 1-b · 1-c · 1-d).

막아야 하는 실패:
1-c 🟠 루프가 **영영** 멈춘 프로세스가 스레드 갱신으로 리더를 붙드는 것 — 한도 넘으면 갱신을 멈춘다.
1-a 🟡 세운 뒤(해제 경쟁)의 갱신 0 을 상실로 읽어 종료 중인 옛 슬롯이 승격 루프를 새로 만드는 것.
1-b 🟡 스레드가 예외로 죽어 "리더는 거래하는데 락은 아무도 갱신 안 하는" 상태.
1-d 🟡 타임아웃 한 번에 바로 상실 — 예외는 TTL 안에서 한 번 더.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from updown.common.lock.redis_lock import LockConfig, LockConfigError, SingleInstanceLock


class FakeAsyncRedis:
    async def set(self, *_: Any, **__: Any) -> bool:
        return True

    async def eval(self, *_: Any, **__: Any) -> int:
        return 1

    async def get(self, *_: Any) -> None:
        return None


class FakeSyncRedis:
    """갱신 결과 · 예외를 손으로 조종한다 · 호출 시각을 적는다."""

    def __init__(self) -> None:
        self.calls: list[float] = []
        self.results: list[Any] = []  # 앞에서부터 꺼내 쓴다 · 비면 1
        self.on_eval: Any = None

    def ping(self) -> bool:
        return True

    def eval(self, *_: Any) -> int:
        self.calls.append(time.monotonic())
        if self.on_eval is not None:
            self.on_eval()
        if self.results:
            got = self.results.pop(0)
            if isinstance(got, BaseException):
                raise got
            return int(got)
        return 1


def _lock(
    sync: FakeSyncRedis,
    *,
    on_lost: Any = None,
    ttl: float = 0.3,
    renew: float = 0.05,
    stall: float = 90.0,
) -> SingleInstanceLock:
    return SingleInstanceLock(
        FakeAsyncRedis(),  # type: ignore[arg-type]
        LockConfig(
            key="updown:test:thread-hb-review",
            ttl_seconds=ttl,
            renew_interval_seconds=renew,
            loop_stall_limit_seconds=stall,
        ),
        on_lost=on_lost,
        heartbeat_client=sync,  # type: ignore[arg-type]
    )


def test_stall_limit_must_exceed_ttl() -> None:
    with pytest.raises(LockConfigError):
        LockConfig(ttl_seconds=30.0, renew_interval_seconds=10.0, loop_stall_limit_seconds=30.0)


async def test_1c_renewal_stops_when_loop_is_stalled_past_limit() -> None:
    """1-c — 한도(0.4 s) 안의 막힘은 흡수하고, 넘으면 갱신을 멈춘다 · 도장이 돌아오면 재개."""
    sync = FakeSyncRedis()
    lock = _lock(sync, ttl=0.3, renew=0.05, stall=0.4)
    assert await lock.acquire()
    await lock.start_heartbeat()
    await asyncio.sleep(0.12)  # 도장 태스크가 한 번은 찍게
    blocked_at = time.monotonic()
    time.sleep(1.0)  # 루프를 한도(0.4) 넘게 막는다 — 교착의 축소판
    during = [t for t in sync.calls if t >= blocked_at]
    assert during, "한도 안(0.4 s) 에는 갱신이 돌아야 한다(43 ~ 50 초 막힘 흡수)"
    assert max(during) - blocked_at < 0.6, (
        "한도를 넘긴 뒤에도 갱신이 이어졌다 — 교착 프로세스가 리더를 붙든다"
    )
    assert lock.loop_stalls >= 1
    await asyncio.sleep(0.2)  # 루프가 돌아오면 도장이 찍히고 갱신이 재개된다
    assert any(t > blocked_at + 1.0 for t in sync.calls)
    await lock.stop_heartbeat()


async def test_1a_zero_after_stop_is_not_a_loss() -> None:
    """1-a — 해제가 세운 뒤 eval 에 걸쳐 0 을 받아도 on_lost 를 예약하지 않는다."""
    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    sync = FakeSyncRedis()
    lock = _lock(sync, on_lost=on_lost)

    def race() -> None:  # eval 도중에 해제가 끼어든 꼴
        lock._thread_stop.set()  # pyright: ignore[reportPrivateUsage]
        sync.results.append(0)

    sync.on_eval = race
    assert await lock.acquire()
    await lock.start_heartbeat()
    await asyncio.sleep(0.3)
    assert lost == []
    await lock.stop_heartbeat()


async def test_1d_one_exception_is_retried_within_ttl() -> None:
    """1-d — 타임아웃 한 번은 상실이 아니다 · 두 번 연속이면 상실."""
    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    sync = FakeSyncRedis()
    sync.results = [TimeoutError("socket timeout"), 1]
    lock = _lock(sync, on_lost=on_lost)
    assert await lock.acquire()
    await lock.start_heartbeat()
    await asyncio.sleep(0.3)
    assert lost == [] and lock.is_held
    assert len(sync.calls) >= 2
    sync.results = [TimeoutError("a"), ConnectionError("b")]
    for _ in range(20):
        await asyncio.sleep(0.05)
        if lost:
            break
    assert len(lost) == 1 and lost[0].startswith("Redis 갱신 호출 실패")
    assert not lock.is_held
    await lock.stop_heartbeat()


async def test_1b_thread_death_falls_back_to_loop_heartbeat() -> None:
    """1-b — 스레드가 예외로 죽으면 로그 뒤 루프 심장박동이 다시 뜬다."""
    sync = FakeSyncRedis()
    sync.results = [
        SystemExit("boom")
    ]  # Exception 이 아니라 재시도 · 상실 분기를 지나 스레드를 죽인다
    lock = _lock(sync)
    assert await lock.acquire()
    await lock.start_heartbeat()
    for _ in range(20):
        await asyncio.sleep(0.05)
        if lock.thread_deaths:
            break
    await asyncio.sleep(0.05)
    assert lock.thread_deaths == 1
    assert lock._heartbeat is not None and not lock._heartbeat.done()  # pyright: ignore[reportPrivateUsage]
    assert lock.is_held
    await lock.stop_heartbeat()
    assert all(
        not t.is_alive() for t in threading.enumerate() if t.name.startswith("lock-heartbeat:")
    )
