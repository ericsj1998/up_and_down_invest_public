"""거래(러너)를 **단일 인스턴스로 게이트**한다 — API 이중매매 방지 (리뷰 갭 1 · 2026-09-01).

## 왜 필요한가

거래 러너는 **API 프로세스**에서 돈다(`autostart_live`). 그런데 단일 인스턴스 락은
**엔진**(봉 수집)에만 있고, API lifespan 은 락 없이 거래를 시작했다. ⇒ API 인스턴스가
둘 뜨면(롤링 배포 겹침·실수로 두 컨테이너) **같은 거래소 포지션에 둘이 주문 → 이중 주문**.
Gate 는 계약당 포지션이 하나라, 둘이 서로의 포지션을 자기 것으로 여겨 싸운다.

## 무엇을 하나

- 시작 때 **트레이더 락**(엔진과 **다른 키**)을 잡는다.
- **리더**면 거래를 시작하고 하트비트로 락을 갱신한다.
- **팔로워**(다른 API 가 이미 리더)면 거래를 **안 하고** UI·조회만 제공하며, 리더가
  놓을 때까지 백그라운드로 승격을 시도한다 (배포 페일오버).
- 하트비트가 끊기면(`on_lost`) 즉시 거래를 멈추고 팔로워로 내려간다 — 둘이 동시에
  일하는 것이 락의 목적을 정면으로 어긴다.

## Redis 불통 정책

Redis 를 못 잡으면 **거래를 막지 않고 진행한다**(가용성 우선). 손절·청산 관리를 Redis
한 번 끊겼다고 멈추면 그게 더 위험하다 — 이 프로젝트가 실측으로 데인 그 사고다
(`ban_left` 주석). 대신 **크게 경고**하고, 그 창에서 이중매매 위험은 감수한다
(Redis 불통 + API 두 개 동시가 겹칠 확률은 낮다).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from updown.common.lock.redis_lock import LockConfig, SingleInstanceLock
from updown.common.logging.setup import get_logger

_logger = get_logger("api.leader")

#: 트레이더 락 키 — 엔진 리더 키(`updown:engine:leader`)와 **반드시 다르다**.
#:   엔진은 봉 수집, API 는 거래. 둘은 각자 하나씩 리더가 있어야 한다.
TRADER_LOCK_KEY = "updown:api:trader"

#: 팔로워가 승격을 재시도하는 간격(초).
PROMOTE_RETRY_S = 5.0


class TradingLeader:
    """거래 시작/중지를 락으로 게이트하는 조정자."""

    def __init__(
        self,
        redis: Redis,
        *,
        start: Callable[[], Awaitable[None]],
        stop: Callable[[], Awaitable[None]],
        config: LockConfig | None = None,
        promote_retry_s: float = PROMOTE_RETRY_S,
    ) -> None:
        """게이트를 만든다.

        Args:
            redis: `redis.asyncio` 클라이언트.
            start: 리더가 될 때 거래를 시작하는 코루틴 (autostart + 루프들).
            stop: 팔로워로 내려갈 때 거래를 멈추는 코루틴 (태스크 취소).
            config: 락 설정. 기본 키는 `TRADER_LOCK_KEY`.
            promote_retry_s: 팔로워의 승격 재시도 간격.
        """
        self._lock = SingleInstanceLock(
            redis, config or LockConfig(key=TRADER_LOCK_KEY), on_lost=self._on_lost
        )
        self._start = start
        self._stop = stop
        self._promote_retry_s = promote_retry_s
        self._promoter: asyncio.Task[None] | None = None
        self._is_leader = False
        self._holds_lock = False

    @property
    def is_leader(self) -> bool:
        """지금 이 인스턴스가 거래 중인가 (리더)."""
        return self._is_leader

    async def begin(self) -> bool:
        """시작 — 락을 잡아 리더면 거래 시작, 아니면 팔로워로 대기.

        Returns:
            리더로 시작했으면 True.
        """
        proceed, held = await self._try_acquire()
        if proceed:
            await self._promote(held=held)
            return True
        # 팔로워 — 거래 안 하고 승격만 노린다.
        _logger.warning(
            "trader_follower",
            payload={
                "note": "다른 API 가 이미 거래 리더다 — 여기서는 거래를 시작하지 않는다 "
                "(UI·조회만). 리더가 놓으면 승격한다",
                "key": self._lock.config.key,
            },
        )
        self._promoter = asyncio.create_task(self._await_promotion(), name="trader-promote")
        return False

    async def _try_acquire(self) -> tuple[bool, bool]:
        """(거래를 시작해도 되나, 락을 실제로 쥐었나).

        Redis 불통이면 `(True, False)` — 거래는 하되 락은 못 쥔 상태(하트비트 없음).
        """
        try:
            got = await self._lock.acquire()
        except Exception as exc:
            _logger.error(
                "trader_lock_redis_unreachable",
                payload={
                    "error": str(exc)[:160],
                    "note": "🔴 Redis 를 못 잡았다 — 거래를 막지 않고 진행한다(가용성). "
                    "이 창에서 API 가 둘이면 이중매매 위험 (드묾)",
                },
            )
            return True, False
        return got, got

    async def _promote(self, *, held: bool) -> None:
        """리더가 된다 — 하트비트 시작(락을 쥔 경우) + 거래 시작."""
        self._is_leader = True
        self._holds_lock = held
        if held:
            with contextlib.suppress(Exception):
                await self._lock.start_heartbeat()
        _logger.info(
            "trader_leader",
            payload={"held_lock": held, "note": "거래 리더로 시작한다"},
        )
        await self._start()

    async def _await_promotion(self) -> None:
        """팔로워 루프 — 락이 빌 때까지 재시도, 잡으면 승격한다."""
        while True:
            await asyncio.sleep(self._promote_retry_s)
            try:
                if await self._lock.acquire():
                    _logger.info(
                        "trader_promoted",
                        payload={"note": "이전 리더가 놓았다 — 이제 거래를 시작한다"},
                    )
                    await self._promote(held=True)
                    return
            except Exception:
                # Redis 불통 — 팔로워는 그대로 대기한다. 여기서 진행하면(begin 과 달리)
                # 이미 다른 리더가 돌던 판에 끼어들 수 있어 위험하다. 다음 주기에 다시.
                continue

    async def _on_lost(self, reason: str) -> None:
        """하트비트가 끊겼다 — 즉시 거래를 멈추고 팔로워로 내려간다.

        Note:
            🔴 갱신 실패 = 더는 리더가 아니다. 다른 인스턴스가 이미 리더로 일하고 있을
            수 있으므로, 버티지 않고 **거래를 멈춘다** — 둘이 동시에 도는 것이 락의
            목적을 정면으로 어긴다. 그동안 거래소 손절이 포지션을 지킨다.
        """
        _logger.error(
            "trader_lock_lost",
            payload={"reason": reason, "note": "🔴 리더십 상실 — 거래를 멈추고 팔로워로 내려간다"},
        )
        self._is_leader = False
        self._holds_lock = False
        with contextlib.suppress(Exception):
            await self._stop()
        if self._promoter is None or self._promoter.done():
            self._promoter = asyncio.create_task(self._await_promotion(), name="trader-promote")

    async def shutdown(self) -> None:
        """종료 — 승격 루프·하트비트를 멈추고 락을 놓고 거래를 정리한다."""
        if self._promoter is not None:
            self._promoter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._promoter
        with contextlib.suppress(Exception):
            await self._lock.stop_heartbeat()
        with contextlib.suppress(Exception):
            await self._lock.release()
        if self._is_leader:
            with contextlib.suppress(Exception):
                await self._stop()
        self._is_leader = False
