"""engine 단일 실행 락 검증 (P0-9-3·4·6 · spec §12.5, §2.1).

DoD 1 — engine 중복 기동 시 **두 번째 인스턴스가 락 대기**
DoD 3 — 락 보유 프로세스 강제 종료 → TTL 만료 후 **대기 인스턴스 승격**

실제 Redis 를 쓴다 (`db` 마커). 락의 정확성은 `SET NX PX` 와 Lua 의 원자성에 달려 있어
가짜 클라이언트로는 검증할 수 없다 — "확인 후 실행"이 원자적인지가 핵심이다.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from updown.apps.engine.bootstrap import boot_engine, shutdown_engine
from updown.apps.engine.scheduler import build_scheduler
from updown.common.lock.redis_lock import (
    MIN_RENEW_ATTEMPTS_PER_TTL,
    LockConfig,
    LockConfigError,
    SingleInstanceLock,
)

pytestmark = pytest.mark.db


@pytest.fixture
def redis_url() -> str:
    """dev Redis URL."""
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL 이 없다 — `make up` 후 `.env.dev` 를 로드하면 실행된다")
    return url


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    """테스트 전용 키를 쓰는 Redis 클라이언트."""
    client: Redis = Redis.from_url(redis_url)  # pyright: ignore[reportUnknownMemberType]
    yield client
    await client.aclose()


@pytest.fixture
async def lock_key(redis: Redis) -> AsyncIterator[str]:
    """테스트마다 고유한 락 키.

    Note:
        고정 키를 쓰면 테스트가 서로의 락을 보게 되어 순서에 의존한다 — P0-6·P0-8 에서
        같은 부류로 두 번 깨졌다.
    """
    key = f"updown:test:lock:{os.urandom(8).hex()}"
    yield key
    await redis.delete(key)


def make_lock(
    redis: Redis,
    key: str,
    *,
    ttl: float = 1.0,
    renew: float | None = None,
    on_lost: object = None,
) -> SingleInstanceLock:
    """짧은 TTL 의 락 (테스트가 만료를 기다릴 수 있게).

    Note:
        `renew` 기본값을 **TTL 에서 파생**시킨다. 고정값(0.2s)으로 뒀더니 `ttl=0.3` 인
        테스트가 `renew <= ttl/3` 검증에 걸려 깨졌다 — 검증이 맞고 헬퍼가 틀렸다.
    """
    return SingleInstanceLock(
        redis,
        LockConfig(
            key=key,
            ttl_seconds=ttl,
            renew_interval_seconds=renew if renew is not None else ttl / 4,
        ),
        on_lost=on_lost,  # pyright: ignore[reportArgumentType]
    )


# ---------------------------------------------------------------------------
# 1. 설정 검증
# ---------------------------------------------------------------------------


def test_renew_interval_must_be_well_under_ttl() -> None:
    """갱신 주기가 TTL 대비 길면 한 번의 일시 실패로 락을 잃는다."""
    with pytest.raises(LockConfigError, match="갱신 주기가 너무 길다"):
        LockConfig(ttl_seconds=30, renew_interval_seconds=25)


def test_renew_interval_at_the_boundary_is_allowed() -> None:
    """TTL/3 은 허용된다 — 경계에서 거부되면 기본값을 못 쓴다."""
    ttl = 30.0
    LockConfig(ttl_seconds=ttl, renew_interval_seconds=ttl / MIN_RENEW_ATTEMPTS_PER_TTL)


@pytest.mark.parametrize(
    ("ttl", "renew"),
    [(0, 1), (10, 0), (-1, 1), (10, -1)],
)
def test_non_positive_values_are_rejected(ttl: float, renew: float) -> None:
    with pytest.raises(LockConfigError):
        LockConfig(ttl_seconds=ttl, renew_interval_seconds=renew)


def test_empty_key_is_rejected() -> None:
    with pytest.raises(LockConfigError, match="락 키가 비었다"):
        LockConfig(key="")


def test_default_config_is_valid() -> None:
    """기본값이 자기 검증을 통과해야 한다."""
    config = LockConfig()
    assert config.renew_interval_seconds <= config.ttl_seconds / MIN_RENEW_ATTEMPTS_PER_TTL
    assert config.ttl_ms == int(config.ttl_seconds * 1000)


# ---------------------------------------------------------------------------
# 2. 획득 — 상호 배제 (DoD 1)
# ---------------------------------------------------------------------------


async def test_first_instance_acquires(redis: Redis, lock_key: str) -> None:
    lock = make_lock(redis, lock_key)
    assert await lock.acquire() is True
    assert lock.is_held is True
    assert await lock.verify() is True


async def test_second_instance_is_refused(redis: Redis, lock_key: str) -> None:
    """DoD 1 — 두 번째 인스턴스는 락을 얻지 못한다."""
    leader = make_lock(redis, lock_key)
    follower = make_lock(redis, lock_key)

    assert await leader.acquire() is True
    assert await follower.acquire() is False
    assert follower.is_held is False
    assert await follower.verify() is False


async def test_tokens_differ_between_instances(redis: Redis, lock_key: str) -> None:
    """토큰이 같으면 소유 판정이 무의미해진다 — 남의 락을 내 것으로 본다."""
    assert make_lock(redis, lock_key).token != make_lock(redis, lock_key).token


async def test_waiting_instance_logs_and_gives_up_at_limit(redis: Redis, lock_key: str) -> None:
    """DoD 1 — 두 번째 인스턴스는 **대기**한다 (죽지 않는다)."""
    leader = make_lock(redis, lock_key)
    await leader.acquire()

    follower = make_lock(redis, lock_key)
    acquired = await follower.wait_until_acquired(retry_interval_seconds=0.05, max_attempts=2)
    assert acquired is False, "리더가 살아 있는데 승격했다"


# ---------------------------------------------------------------------------
# 3. 갱신 — 남의 락을 건드리지 않는다
# ---------------------------------------------------------------------------


async def test_renew_extends_own_lock(redis: Redis, lock_key: str) -> None:
    lock = make_lock(redis, lock_key, ttl=1.0)
    await lock.acquire()
    await asyncio.sleep(0.5)
    assert await lock.renew() is True
    await asyncio.sleep(0.7)  # 갱신이 없었다면 이미 만료됐을 시점
    assert await lock.verify() is True


async def test_renew_fails_for_a_lock_we_do_not_own(redis: Redis, lock_key: str) -> None:
    """**남의 락 수명을 늘려 주지 않는다.**

    토큰 확인 없이 `PEXPIRE` 하면, 내 TTL 이 만료돼 다른 인스턴스가 잡은 락을 내가 계속
    살려 준다 — 그 인스턴스는 갱신을 안 해도 살아남고 나는 내 락이 유효하다고 믿는다.
    """
    leader = make_lock(redis, lock_key)
    intruder = make_lock(redis, lock_key)
    await leader.acquire()

    assert await intruder.renew() is False
    assert await leader.verify() is True, "침입자의 갱신이 리더 락을 건드렸다"


async def test_renew_fails_after_expiry(redis: Redis, lock_key: str) -> None:
    """만료된 뒤의 갱신은 실패해야 한다 — 부활시키면 안 된다."""
    lock = make_lock(redis, lock_key, ttl=0.3)
    await lock.acquire()
    await asyncio.sleep(0.5)
    assert await lock.renew() is False


# ---------------------------------------------------------------------------
# 4. 해제 — 남의 락을 지우지 않는다
# ---------------------------------------------------------------------------


async def test_release_deletes_own_lock(redis: Redis, lock_key: str) -> None:
    lock = make_lock(redis, lock_key)
    await lock.acquire()
    assert await lock.release() is True
    assert await redis.get(lock_key) is None


async def test_release_does_not_delete_someone_elses_lock(redis: Redis, lock_key: str) -> None:
    """무조건 DEL 이면 **세 번째 인스턴스까지 들어온다.**

    내 락이 만료된 뒤 다른 인스턴스가 잡았는데 내가 그것을 지우면, 락이 비어 또 다른
    인스턴스가 들어올 수 있다.
    """
    expired = make_lock(redis, lock_key, ttl=0.3)
    await expired.acquire()
    await asyncio.sleep(0.5)

    successor = make_lock(redis, lock_key, ttl=5.0)
    assert await successor.acquire() is True

    assert await expired.release() is False, "만료된 인스턴스가 후임자의 락을 지웠다"
    assert await successor.verify() is True


async def test_release_is_idempotent(redis: Redis, lock_key: str) -> None:
    """종료 경로는 두 번 불려도 안전해야 한다."""
    lock = make_lock(redis, lock_key)
    await lock.acquire()
    assert await lock.release() is True
    assert await lock.release() is False


async def test_release_without_acquire_is_harmless(redis: Redis, lock_key: str) -> None:
    assert await make_lock(redis, lock_key).release() is False


# ---------------------------------------------------------------------------
# 5. 하트비트와 상실 감지 (spec §12.5)
# ---------------------------------------------------------------------------


async def test_heartbeat_keeps_the_lock_alive(redis: Redis, lock_key: str) -> None:
    lock = make_lock(redis, lock_key, ttl=0.6, renew=0.15)
    await lock.acquire()
    await lock.start_heartbeat()
    try:
        await asyncio.sleep(1.2)  # TTL 의 두 배 — 갱신이 없으면 만료된다
        assert await lock.verify() is True
    finally:
        await lock.stop_heartbeat()
        await lock.release()


async def test_lock_loss_invokes_the_callback(redis: Redis, lock_key: str) -> None:
    """§12.5 — 상실을 감지해야 스케줄러를 멈출 수 있다."""
    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    lock = make_lock(redis, lock_key, ttl=0.6, renew=0.15, on_lost=on_lost)
    await lock.acquire()
    await lock.start_heartbeat()
    try:
        # 락을 밖에서 지운다 — 다른 인스턴스가 가져간 상황과 동등하다.
        await redis.delete(lock_key)
        await asyncio.sleep(0.5)
        assert lost, "락을 잃었는데 콜백이 불리지 않았다"
        assert lock.is_held is False
    finally:
        await lock.stop_heartbeat()


async def test_heartbeat_requires_the_lock(redis: Redis, lock_key: str) -> None:
    """락 없이 하트비트를 돌리면 "계속 실패하는데 아무도 모르는" 상태가 된다."""
    with pytest.raises(RuntimeError, match="락을 획득하지 않은"):
        await make_lock(redis, lock_key).start_heartbeat()


async def test_stop_heartbeat_is_safe_when_not_started(redis: Redis, lock_key: str) -> None:
    await make_lock(redis, lock_key).stop_heartbeat()


# ---------------------------------------------------------------------------
# 6. 페일오버 (DoD 3)
# ---------------------------------------------------------------------------


async def test_waiting_instance_is_promoted_after_ttl(redis: Redis, lock_key: str) -> None:
    """DoD 3 — 리더가 **강제 종료**되면 TTL 만료 후 대기 인스턴스가 승격한다.

    "강제 종료"를 하트비트를 띄우지 않은 락으로 재현한다 — 프로세스가 SIGKILL 로 죽으면
    락 해제도 갱신도 못 하고 TTL 만료만 남는다. 이것이 그 상태와 동등하다.
    """
    killed = make_lock(redis, lock_key, ttl=0.4)
    assert await killed.acquire() is True

    follower = make_lock(redis, lock_key, ttl=5.0)
    promoted = await follower.wait_until_acquired(retry_interval_seconds=0.1, max_attempts=20)

    assert promoted is True, "TTL 이 만료됐는데 승격하지 못했다"
    assert await follower.verify() is True
    assert await killed.verify() is False
    await follower.release()


async def test_graceful_release_promotes_immediately(redis: Redis, lock_key: str) -> None:
    """정상 종료는 TTL 을 기다리지 않는다 — 그래서 락 해제를 종료 경로에 둔다."""
    leader = make_lock(redis, lock_key, ttl=30.0)
    await leader.acquire()
    follower = make_lock(redis, lock_key, ttl=30.0)
    assert await follower.acquire() is False

    await leader.release()
    assert await follower.acquire() is True
    await follower.release()


# ---------------------------------------------------------------------------
# 7. 부팅 시퀀스 (P0-9-4)
# ---------------------------------------------------------------------------


async def test_boot_order_is_lock_then_reconcile_then_watch(redis: Redis, lock_key: str) -> None:
    """§7 — **대사가 감시보다 먼저**다.

    감시를 먼저 켜면 죽어 있던 동안 손절가를 지난 포지션을 "정상 보유"로 보고 지나간다.
    """
    order: list[str] = []
    lock = make_lock(redis, lock_key, ttl=5.0)
    scheduler = build_scheduler()

    async def reconcile() -> None:
        order.append("reconcile")
        assert not scheduler.running, "대사보다 감시가 먼저 켜졌다"

    result = await boot_engine(lock, scheduler, reconcile=reconcile, wait_for_lock=False)
    try:
        assert result.became_leader is True
        assert result.scheduler_started is True
        assert order == ["reconcile"]
        assert scheduler.running is True
    finally:
        await shutdown_engine(lock, scheduler)


async def test_follower_does_not_start_the_scheduler(redis: Redis, lock_key: str) -> None:
    """DoD 1 — 리더가 아니면 잡을 실행하지 않는다."""
    leader = make_lock(redis, lock_key, ttl=30.0)
    await leader.acquire()

    follower = make_lock(redis, lock_key, ttl=30.0)
    scheduler = build_scheduler()
    reconciled: list[str] = []

    async def reconcile() -> None:
        reconciled.append("x")

    result = await boot_engine(follower, scheduler, reconcile=reconcile, wait_for_lock=False)

    assert result.became_leader is False
    assert result.scheduler_started is False
    assert scheduler.running is False
    assert reconciled == [], "리더가 아닌데 대사를 돌렸다 — 같은 포지션을 두 번 만진다"
    await leader.release()


async def test_shutdown_releases_the_lock(redis: Redis, lock_key: str) -> None:
    """종료가 락을 놓지 않으면 다음 인스턴스가 TTL 만큼 기다린다."""
    lock = make_lock(redis, lock_key, ttl=30.0)
    scheduler = build_scheduler()
    await boot_engine(lock, scheduler, wait_for_lock=False)

    await shutdown_engine(lock, scheduler)

    assert scheduler.running is False
    assert await redis.get(lock_key) is None


async def test_shutdown_is_safe_for_a_follower(redis: Redis, lock_key: str) -> None:
    """리더가 아닌 인스턴스의 종료가 **리더의 락을 지우면 안 된다.**"""
    leader = make_lock(redis, lock_key, ttl=30.0)
    await leader.acquire()

    follower = make_lock(redis, lock_key, ttl=30.0)
    scheduler = build_scheduler()
    await boot_engine(follower, scheduler, wait_for_lock=False)

    await shutdown_engine(follower, scheduler)

    assert await leader.verify() is True, "팔로워 종료가 리더의 락을 지웠다"
    await leader.release()
