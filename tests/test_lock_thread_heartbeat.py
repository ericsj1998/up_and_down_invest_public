"""T759 ③ — 락 심장박동을 별도 스레드로 (2026-11-02 실계좌 사고 재현).

막아야 하는 실패:
1. 🔴 이벤트 루프가 TTL 보다 오래 막혀도(펀드 복원 43 ~ 50 초) 락을 잃는 것 — 일하는 리더를
   죽은 것으로 읽는다.
2. 🔴 스레드에서 갱신이 실패했는데 `on_lost` 가 안 불리는 것(조용히 둘이 리더).
3. ⚠️ `stop_heartbeat` · `release` 뒤에도 스레드가 살아 지운 키를 갱신하거나 "상실" 을 외치는 것.
4. ⚠️ 동기 클라이언트가 안 붙을 때 조용히 심장박동 없이 도는 것 — 루프 심장박동으로 되돌아가야 한다.

Redis 없이 가짜 클라이언트로 **게이트 로직만** 본다(원자성은 `test_single_instance_lock`(db) 몫).
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from updown.common.lock.redis_lock import LockConfig, SingleInstanceLock


class FakeAsyncRedis:
    """`acquire` · `release` 가 쓰는 비동기 쪽 — 늘 잡힌다."""

    def __init__(self) -> None:
        self.deleted = 0

    async def set(self, *_: Any, **__: Any) -> bool:
        return True

    async def eval(self, *_: Any, **__: Any) -> int:
        self.deleted += 1
        return 1

    async def get(self, *_: Any) -> None:
        return None


class FakeSyncRedis:
    """스레드가 쓰는 동기 쪽 — 갱신 결과를 손으로 조종한다."""

    def __init__(self, *, ping_ok: bool = True) -> None:
        self.ping_ok = ping_ok
        self.renew_ok = True
        self.calls = 0
        self.threads: set[str] = set()

    def ping(self) -> bool:
        if not self.ping_ok:
            raise ConnectionError("redis down")
        return True

    def eval(self, *_: Any) -> int:
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        return 1 if self.renew_ok else 0


def _lock(
    sync: FakeSyncRedis | None, *, on_lost: Any = None, ttl: float = 0.3, renew: float = 0.05
) -> SingleInstanceLock:
    return SingleInstanceLock(
        FakeAsyncRedis(),  # type: ignore[arg-type]
        LockConfig(key="updown:test:thread-hb", ttl_seconds=ttl, renew_interval_seconds=renew),
        on_lost=on_lost,
        heartbeat_client=sync,  # type: ignore[arg-type]
    )


async def test_thread_heartbeat_keeps_renewing_while_loop_is_blocked() -> None:
    """실패 1 — 루프를 `time.sleep` 으로 막아도 스레드 갱신은 이어진다."""
    sync = FakeSyncRedis()
    lock = _lock(sync)
    assert await lock.acquire()
    await lock.start_heartbeat()
    time.sleep(0.4)  # 이벤트 루프를 TTL(0.3) 보다 오래 막는다 — 실계좌 복원 43 초의 축소판
    await lock.stop_heartbeat()
    assert sync.calls >= 3, f"막힌 동안 갱신이 {sync.calls} 번 — 스레드가 아니다"
    assert all(name.startswith("lock-heartbeat:") for name in sync.threads)
    assert lock.is_held


async def test_thread_heartbeat_failure_notifies_on_lost_once_on_the_loop() -> None:
    """실패 2 — 갱신 실패 → `on_lost` 가 루프에서 정확히 한 번 · 사유와 함께."""
    reasons: list[str] = []
    seen_thread: list[str] = []

    async def on_lost(reason: str) -> None:
        reasons.append(reason)
        seen_thread.append(threading.current_thread().name)

    sync = FakeSyncRedis()
    lock = _lock(sync, on_lost=on_lost)
    assert await lock.acquire()
    await lock.start_heartbeat()
    sync.renew_ok = False
    for _ in range(40):
        await asyncio.sleep(0.05)
        if reasons:
            break
    assert reasons == ["다른 인스턴스가 락을 가져갔거나 TTL 이 만료됐다"]
    assert seen_thread == ["MainThread"], "on_lost 는 루프(메인 스레드)에서 돌아야 한다"
    assert not lock.is_held
    await asyncio.sleep(0.2)
    assert len(reasons) == 1, "상실은 한 번만 통지한다"
    await lock.stop_heartbeat()


async def test_stop_and_release_halt_the_thread() -> None:
    """실패 3 — 멈춘 뒤에는 갱신도 상실 통지도 없다."""
    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    sync = FakeSyncRedis()
    lock = _lock(sync, on_lost=on_lost)
    assert await lock.acquire()
    await lock.start_heartbeat()
    await asyncio.sleep(0.12)
    before = sync.calls
    assert before >= 1
    await lock.release()  # 해제가 스레드를 세운다 — 지운 키 갱신 실패를 상실로 읽으면 안 된다
    sync.renew_ok = False
    await asyncio.sleep(0.2)
    assert lost == []
    assert sync.calls <= before + 1  # 해제 순간 한 번 겹칠 수는 있다
    await lock.stop_heartbeat()
    assert all(
        not t.is_alive() for t in threading.enumerate() if t.name.startswith("lock-heartbeat:")
    )


async def test_falls_back_to_loop_heartbeat_when_sync_client_unreachable() -> None:
    """실패 4 — 동기 클라이언트가 안 붙으면 루프 심장박동으로 돈다(심장박동 없음은 금지)."""
    sync = FakeSyncRedis(ping_ok=False)
    lock = _lock(sync)
    assert await lock.acquire()
    await lock.start_heartbeat()
    assert lock._heartbeat is not None and not lock._heartbeat.done()  # pyright: ignore[reportPrivateUsage]
    assert lock._thread is None  # pyright: ignore[reportPrivateUsage]
    await lock.stop_heartbeat()


async def test_without_sync_client_behaviour_is_unchanged() -> None:
    """기본(동기 클라이언트 없음) — 지금까지의 루프 심장박동 그대로."""
    lock = _lock(None)
    assert await lock.acquire()
    await lock.start_heartbeat()
    assert lock._heartbeat is not None  # pyright: ignore[reportPrivateUsage]
    await lock.stop_heartbeat()


@pytest.mark.parametrize("flag", ["0", "1"])
def test_env_switch_is_read_in_main(flag: str) -> None:
    """main.py 의 스위치 이름이 문서와 같다(오타로 조용히 꺼지지 않게)."""
    from pathlib import Path

    text = Path("src/updown/apps/api/main.py").read_text(encoding="utf-8")
    assert 'os.environ.get("TRADER_LOCK_HEARTBEAT_THREAD", "1")' in text
    assert flag in ("0", "1")
