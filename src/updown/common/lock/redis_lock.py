"""engine 단일 실행 락 — 리더 선출 (P0-9-3 · spec §12.5, §2.1 / plan D-3).

## 왜 필요한가

engine 이 두 개 돌면 같은 캔들을 두 번 수집하고, Phase 2 부터는 **같은 주문을 두 번 낸다**.
§12.5 는 "단일 실행 보장"을 요구하고, 그 수단이 이 락이다.

## 구현의 세 지점

1. **획득**: `SET key token NX PX ttl` — 원자적이다. `SETNX` + `EXPIRE` 2단계로 쓰면
   그 사이에 죽었을 때 TTL 없는 락이 영구히 남는다
2. **갱신**: **토큰이 내 것일 때만** TTL 을 늘린다. 무조건 `PEXPIRE` 하면, 내 TTL 이 이미
   만료돼 다른 인스턴스가 잡은 락의 수명을 내가 늘려 주게 된다
3. **해제**: **토큰이 내 것일 때만** 지운다. 무조건 `DEL` 하면 내 락이 만료된 뒤 다른
   인스턴스가 잡은 락을 내가 지운다 — 그 순간 세 번째 인스턴스까지 들어올 수 있다

2·3 은 "확인 후 실행"이 원자적이어야 하므로 Lua 로 한다. Python 에서 `GET` 후 분기하면
그 사이에 만료될 수 있다.

## 락은 중복 주문의 **최종** 방어선이 아니다

Redis 락에는 이론적 취약점이 있다 — GC 정지나 네트워크 지연으로 우리가 만료를 눈치채기
전에 다른 인스턴스가 락을 잡는 창이 존재한다. 그래서:

- 갱신 실패를 감지하면 **스케줄러를 즉시 정지**한다 (§12.5) — 창을 좁힌다
- 잡마다 `max_instances=1` — 같은 프로세스 내 중복도 막는다
- **중복 주문의 최종 방어선은 멱등키**다 (절대 규칙 #6, §4.10). 락이 실패해도 브로커가
  같은 키의 두 번째 주문을 거부한다

락은 "낭비와 혼선을 줄이는 장치"이고, 돈이 걸린 안전은 멱등키가 담당한다. 이 구분을
흐리면 락을 과신하게 된다.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis.asyncio import Redis

from updown.common.logging.setup import get_logger

_logger = get_logger("common.lock.redis_lock")

DEFAULT_LOCK_KEY = "updown:engine:leader"
"""기본 락 키. 환경별로 갈라야 하면 설정으로 주입한다."""

DEFAULT_TTL_SECONDS = 30.0
"""락 TTL. 이 시간 동안 갱신이 없으면 다른 인스턴스가 승격할 수 있다.

너무 짧으면 일시적 지연에 락을 잃고, 너무 길면 죽은 인스턴스의 락이 오래 남아
페일오버가 느려진다. 30초는 "잡 하나가 도는 시간"보다 길고 사람이 기다릴 만한 값이다.
"""

DEFAULT_RENEW_INTERVAL_SECONDS = 10.0
"""갱신 주기. TTL 의 1/3 이하여야 한다 — 아래 `LockConfig` 검증 참조."""

MIN_RENEW_ATTEMPTS_PER_TTL = 3
"""TTL 안에 최소 몇 번 갱신을 시도해야 하는가.

2번이면 한 번의 일시 실패로 락을 잃는다. 3번이면 한 번 실패해도 다음 시도가 남는다.
"""

#: 토큰이 일치할 때만 TTL 을 늘린다.
_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

#: 토큰이 일치할 때만 지운다.
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class LockConfigError(ValueError):
    """락 설정이 잘못됐다."""


@dataclass(frozen=True, slots=True)
class LockConfig:
    """락 설정 (P0-9-3 — 설정값으로 노출).

    Attributes:
        key: Redis 키.
        ttl_seconds: 락 TTL.
        renew_interval_seconds: 갱신 주기.

    Raises:
        LockConfigError: 값이 0 이하이거나 갱신 주기가 TTL 대비 너무 길 때.

    Note:
        **갱신 주기가 TTL 의 1/3 을 넘으면 거부한다.** 예를 들어 TTL 30초에 갱신 25초면
        한 번의 일시 실패로 락을 잃는다 — 그러면 정상 인스턴스가 스스로 스케줄러를 멈추고
        수집이 끊긴다. 설정 실수로 그런 일이 나지 않게 경계에서 막는다 (spec §7).
    """

    key: str = DEFAULT_LOCK_KEY
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    renew_interval_seconds: float = DEFAULT_RENEW_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        """설정 불변식을 검증한다."""
        if not self.key:
            raise LockConfigError("락 키가 비었다")
        if self.ttl_seconds <= 0 or self.renew_interval_seconds <= 0:
            raise LockConfigError(
                f"TTL·갱신 주기는 0 보다 커야 한다: "
                f"ttl={self.ttl_seconds}, renew={self.renew_interval_seconds}"
            )
        max_interval = self.ttl_seconds / MIN_RENEW_ATTEMPTS_PER_TTL
        if self.renew_interval_seconds > max_interval:
            raise LockConfigError(
                f"갱신 주기가 너무 길다: renew={self.renew_interval_seconds}s > "
                f"ttl/{MIN_RENEW_ATTEMPTS_PER_TTL}={max_interval:.1f}s. "
                "TTL 안에 최소 3회 시도해야 한 번의 일시 실패를 흡수한다"
            )

    @property
    def ttl_ms(self) -> int:
        """TTL 을 밀리초 정수로 (Redis `PX`/`PEXPIRE` 인자)."""
        return int(self.ttl_seconds * 1000)


class SingleInstanceLock:
    """engine 리더 선출 락 (spec §12.5).

    Note:
        **하트비트를 이 객체가 소유한다.** 호출부가 갱신 주기를 직접 돌리게 하면
        프로세스마다 정책이 갈라지고, 갱신을 잊는 구현이 나온다.
    """

    def __init__(
        self,
        redis: Redis,
        config: LockConfig | None = None,
        *,
        on_lost: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """락을 만든다.

        Args:
            redis: `redis.asyncio` 클라이언트.
            config: 락 설정. 기본값은 `LockConfig()`.
            on_lost: **락 상실 콜백.** 갱신이 실패하면 호출된다 — engine 은 여기서
                스케줄러를 정지시킨다 (§12.5). 인자는 사유 문자열.
        """
        self._redis = redis
        self._config = config or LockConfig()
        self._on_lost = on_lost
        self._token = uuid.uuid4().hex
        self._held = False
        self._heartbeat: asyncio.Task[None] | None = None

    @property
    def token(self) -> str:
        """이 인스턴스의 락 토큰. 소유 판정의 근거다."""
        return self._token

    @property
    def config(self) -> LockConfig:
        """락 설정."""
        return self._config

    @property
    def is_held(self) -> bool:
        """지금 락을 들고 있다고 **믿는가**.

        Note:
            "믿는가"인 이유: 갱신 사이에 TTL 이 만료됐을 수 있다. 확정 판정은
            `verify()` 가 Redis 에 직접 묻는다.
        """
        return self._held

    async def acquire(self) -> bool:
        """락을 시도한다.

        Returns:
            획득했으면 True, 다른 인스턴스가 들고 있으면 False.

        Note:
            `SET NX PX` 한 방이다. `SETNX` 후 `EXPIRE` 로 나누면 그 사이에 프로세스가
            죽었을 때 **TTL 없는 락이 영구히 남아** 아무도 승격할 수 없다.
        """
        acquired = await self._redis.set(  # pyright: ignore[reportUnknownMemberType]
            self._config.key, self._token, nx=True, px=self._config.ttl_ms
        )
        self._held = bool(acquired)
        if self._held:
            _logger.info(
                "engine_lock_acquired",
                payload={
                    "key": self._config.key,
                    "token": self._token,
                    "ttl_seconds": self._config.ttl_seconds,
                },
            )
        return self._held

    async def renew(self) -> bool:
        """TTL 을 늘린다 — **내 토큰일 때만**.

        Returns:
            갱신했으면 True. 락이 내 것이 아니거나 사라졌으면 False.

        Note:
            토큰을 확인하지 않고 `PEXPIRE` 하면, 내 TTL 이 이미 만료돼 다른 인스턴스가
            잡은 락의 수명을 **내가 늘려 주게 된다.** 그 인스턴스는 갱신을 안 해도 살아남고,
            나는 내 락이 유효하다고 믿는다 — 둘 다 리더라고 생각하는 최악의 상태다.
        """
        renewed = await self._redis.eval(  # pyright: ignore[reportUnknownMemberType]
            _RENEW_SCRIPT, 1, self._config.key, self._token, str(self._config.ttl_ms)
        )
        return bool(renewed)

    async def verify(self) -> bool:
        """Redis 에 직접 물어 소유를 확인한다.

        Returns:
            락 값이 내 토큰이면 True.

        Note:
            `is_held` 는 마지막 갱신 시점의 믿음이고 이것은 현재 사실이다. 부팅 시퀀스나
            테스트처럼 확정이 필요한 곳에서 쓴다.
        """
        current = await self._redis.get(self._config.key)  # pyright: ignore[reportUnknownMemberType]
        if current is None:
            return False
        value = current.decode() if isinstance(current, bytes) else str(current)
        return value == self._token

    async def release(self) -> bool:
        """락을 놓는다 — **내 토큰일 때만**.

        Returns:
            실제로 지웠으면 True.

        Note:
            무조건 `DEL` 하면 내 락이 만료된 뒤 다른 인스턴스가 잡은 락을 내가 지운다.
            그 순간 락이 비어 **세 번째 인스턴스까지** 들어올 수 있다.

            해제는 **종료 경로**이므로 실패해도 예외를 올리지 않는다 — TTL 이 어차피
            정리한다. 종료를 막을 이유가 없다.
        """
        self._held = False
        try:
            deleted = await self._redis.eval(  # pyright: ignore[reportUnknownMemberType]
                _RELEASE_SCRIPT, 1, self._config.key, self._token
            )
        except Exception as exc:
            _logger.warning(
                "engine_lock_release_failed",
                payload={"key": self._config.key, "reason": str(exc), "note": "TTL 이 정리한다"},
            )
            return False
        released = bool(deleted)
        _logger.info(
            "engine_lock_released",
            payload={"key": self._config.key, "token": self._token, "deleted": released},
        )
        return released

    async def start_heartbeat(self) -> None:
        """갱신 루프를 백그라운드로 띄운다.

        Raises:
            RuntimeError: 락을 들고 있지 않은 상태에서 부른 경우.

        Note:
            락 없이 하트비트를 돌리면 "갱신은 계속 실패하는데 아무도 모르는" 상태가 된다.
        """
        if not self._held:
            raise RuntimeError("락을 획득하지 않은 상태에서 하트비트를 시작할 수 없다")
        if self._heartbeat is not None and not self._heartbeat.done():
            return
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        """갱신 루프를 멈춘다."""
        task = self._heartbeat
        self._heartbeat = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _heartbeat_loop(self) -> None:
        """주기적으로 갱신하고, 실패하면 상실을 통지한다.

        Note:
            **갱신 실패는 곧 "리더가 아니다"** 다. 재시도하며 버티지 않는다 — 그 사이에
            다른 인스턴스가 이미 리더로 일하고 있을 수 있고, 둘이 동시에 도는 것이
            락을 둔 목적을 정면으로 어긴다 (§12.5).

            Redis 장애로 갱신이 실패한 경우도 같게 취급한다. "락 상태를 모른다"와
            "락이 없다"를 구분하려면 분산 합의가 필요하고, 그 복잡도를 살 이유가 없다 —
            멈추는 쪽이 안전하다.
        """
        while True:
            await asyncio.sleep(self._config.renew_interval_seconds)
            try:
                renewed = await self.renew()
                reason = "다른 인스턴스가 락을 가져갔거나 TTL 이 만료됐다"
            except Exception as exc:
                renewed = False
                reason = f"Redis 갱신 호출 실패: {type(exc).__name__}: {exc}"

            if renewed:
                continue

            self._held = False
            _logger.error(
                "engine_lock_lost",
                payload={"key": self._config.key, "token": self._token, "reason": reason},
            )
            if self._on_lost is not None:
                with contextlib.suppress(Exception):
                    await self._on_lost(reason)
            return

    async def wait_until_acquired(
        self,
        *,
        retry_interval_seconds: float = 5.0,
        max_attempts: int | None = None,
    ) -> bool:
        """획득할 때까지 주기적으로 재시도한다 (P0-9-4).

        Args:
            retry_interval_seconds: 재시도 간격.
            max_attempts: 최대 시도 횟수. None 이면 무한 (운영 기본).

        Returns:
            획득했으면 True, 시도 횟수를 소진하면 False.

        Note:
            두 번째 인스턴스는 **잡을 실행하지 않고 대기**한다 — 죽는 것보다 낫다.
            리더가 죽으면 TTL 만료 후 자동 승격하므로, 대기 인스턴스가 곧 페일오버 수단이다
            (P0-9 DoD 3).

            **대기 사실을 로그로 남긴다.** 조용히 대기하면 "engine 이 떴는데 아무 일도
            안 한다"로 보이고, 그것이 정상인지 장애인지 알 수 없다 (spec §7).
        """
        attempt = 0
        while max_attempts is None or attempt < max_attempts:
            if await self.acquire():
                return True
            attempt += 1
            _logger.warning(
                "engine_lock_waiting",
                payload={
                    "key": self._config.key,
                    "attempt": attempt,
                    "retry_in_seconds": retry_interval_seconds,
                    "note": "다른 engine 인스턴스가 리더다 — 잡을 실행하지 않고 대기한다",
                },
            )
            await asyncio.sleep(retry_interval_seconds)
        return False
