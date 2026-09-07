"""거래 단일 인스턴스 게이트 (`TradingLeader`) — API 이중매매 방지 (리뷰 갭 1).

막아야 하는 실패:
1. 🔴 두 API 가 동시에 거래를 시작하는 것 (이중 주문).
2. 🔴 리더십을 잃고도 계속 거래하는 것 (둘이 동시에 = 락 목적 위반).
3. ⚠️ 팔로워가 리더 사라진 뒤에도 영영 안 뜨는 것 (배포 페일오버 실패).
"""

from __future__ import annotations

import asyncio

import pytest

from updown.apps.api.leader import TradingLeader


class FakeLock:
    """acquire 가능 여부를 손으로 조종하는 락 — Redis 없이 게이트 로직만 시험한다."""

    def __init__(self, *, free: bool, raise_on_acquire: bool = False) -> None:
        self.free = free  # 지금 잡을 수 있나 (다른 리더가 없나)
        self.raise_on_acquire = raise_on_acquire
        self.heartbeat_started = False
        self.released = False
        self._on_lost = None

    async def acquire(self) -> bool:
        if self.raise_on_acquire:
            raise ConnectionError("redis down")
        return self.free

    async def start_heartbeat(self) -> None:
        self.heartbeat_started = True

    async def stop_heartbeat(self) -> None:
        self.heartbeat_started = False

    async def release(self) -> bool:
        self.released = True
        return True

    class _Cfg:
        key = "updown:api:trader"

    @property
    def config(self) -> _Cfg:
        return self._Cfg()


def _leader_with(lock: FakeLock, *, retry_s: float = 0.01) -> tuple[TradingLeader, dict[str, int]]:
    counts = {"start": 0, "stop": 0}

    async def start() -> None:
        counts["start"] += 1

    async def stop() -> None:
        counts["stop"] += 1

    made = TradingLeader.__new__(TradingLeader)
    # 최소 구성 — 락을 가짜로 갈아끼운다 (SingleInstanceLock 대신).
    made._lock = lock  # type: ignore[attr-defined,assignment]
    made._start = start  # type: ignore[attr-defined]
    made._stop = stop  # type: ignore[attr-defined]
    made._promote_retry_s = retry_s  # type: ignore[attr-defined]
    made._promoter = None  # type: ignore[attr-defined]
    made._is_leader = False  # type: ignore[attr-defined]
    made._holds_lock = False  # type: ignore[attr-defined]
    return made, counts


@pytest.mark.asyncio
async def test_leader_starts_trading_and_heartbeat() -> None:
    """🔴 락을 잡으면 리더 — 거래를 시작하고 하트비트를 켠다."""
    lock = FakeLock(free=True)
    leader, counts = _leader_with(lock)
    assert await leader.begin() is True
    assert leader.is_leader
    assert counts["start"] == 1
    assert lock.heartbeat_started


@pytest.mark.asyncio
async def test_follower_does_not_trade() -> None:
    """🔴 다른 리더가 있으면 거래를 시작하지 않는다 (이중매매 방지)."""
    lock = FakeLock(free=False)
    leader, counts = _leader_with(lock)
    assert await leader.begin() is False
    assert not leader.is_leader
    assert counts["start"] == 0
    await leader.shutdown()  # 승격 루프 정리


@pytest.mark.asyncio
async def test_follower_promotes_when_leader_releases() -> None:
    """⚠️ 리더가 사라지면(락이 비면) 팔로워가 승격해 거래를 시작한다 (배포 페일오버)."""
    lock = FakeLock(free=False)
    leader, counts = _leader_with(lock, retry_s=0.01)
    await leader.begin()
    assert counts["start"] == 0
    lock.free = True  # 이전 리더가 놓았다
    for _ in range(50):
        await asyncio.sleep(0.01)
        if leader.is_leader:
            break
    assert leader.is_leader
    assert counts["start"] == 1
    await leader.shutdown()


@pytest.mark.asyncio
async def test_redis_down_proceeds_as_leader_without_heartbeat() -> None:
    """⚠️ Redis 불통이면 거래를 막지 않고 진행한다(가용성) — 락은 못 쥐어 하트비트 없음."""
    lock = FakeLock(free=False, raise_on_acquire=True)
    leader, counts = _leader_with(lock)
    assert await leader.begin() is True
    assert leader.is_leader
    assert counts["start"] == 1
    assert not lock.heartbeat_started  # 락을 못 쥐었으니 갱신도 없다


@pytest.mark.asyncio
async def test_lock_lost_stops_trading() -> None:
    """🔴 하트비트가 끊기면 거래를 멈추고 팔로워로 내려간다 (둘이 동시에 도는 것 방지)."""
    lock = FakeLock(free=True)
    leader, counts = _leader_with(lock, retry_s=100.0)  # 승격 재시도는 안 돌게
    await leader.begin()
    assert leader.is_leader
    await leader._on_lost("heartbeat failed")  # type: ignore[attr-defined]
    assert not leader.is_leader
    assert counts["stop"] == 1
    await leader.shutdown()
