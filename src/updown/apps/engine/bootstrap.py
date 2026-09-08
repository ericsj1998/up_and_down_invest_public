"""engine 부팅 시퀀스 (P0-9-4 · spec §7, §12.5).

## 순서가 안전이다

```
1) 락 획득 (없으면 대기 — 잡을 실행하지 않는다)
2) [열린 포지션 대사]   ← 훅 자리만. 실구현 P2-3
3) 감시 재개 = 스케줄러 시작
```

**대사가 감시보다 먼저인 이유** (§7 "서버 프로세스 다운 중 손절가 도달"): 프로세스가 죽어
있던 동안 가격이 손절가를 지났을 수 있다. 감시를 먼저 켜면 그 포지션을 "정상 보유"로 보고
지나간다 — 이미 나가야 했던 손절이 조용히 사라진다.

**락이 대사보다 먼저인 이유** (§12.5): 두 인스턴스가 동시에 대사하면 같은 포지션을 두 번
청산하려 든다. 리더가 아닌 인스턴스는 대사도 하지 않는다.

## Phase 0 에는 대사가 없다

포지션이 존재하지 않으므로(주문 경로 자체가 막혀 있다 — plan D-12) 대사할 것이 없다.
**훅 자리를 지금 만들어 두는 이유**는 P2-3 에서 순서를 다시 설계하지 않기 위해서다 —
그때 급하게 끼워 넣으면 감시가 먼저 켜지는 배치가 되기 쉽다.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from updown.apps.engine.scheduler import job_ids
from updown.common.lock.redis_lock import SingleInstanceLock
from updown.common.logging.setup import get_logger

_logger = get_logger("apps.engine.bootstrap")

ReconcileHook = Callable[[], Awaitable[None]]
"""열린 포지션 대사 훅 (spec §7, §4.10 정합성 루프). 실구현 P2-3."""


async def no_reconciliation_needed() -> None:
    """Phase 0 의 대사 — 할 일이 없다.

    Note:
        **빈 함수가 아니라 로그를 남긴다.** 조용히 통과하면 "대사가 돌았는지"와 "대사 단계가
        빠졌는지"를 구분할 수 없다. Phase 2 에서 이 자리가 교체되지 않았다면 그것 자체가
        사고이므로, 로그가 그 흔적을 남긴다 (spec §7).
    """
    _logger.info(
        "position_reconciliation_skipped",
        payload={
            "reason": "Phase 0 에는 포지션이 없다 (주문 경로 차단 — plan D-12)",
            "implement_at": "P2-3",
        },
    )


@dataclass(frozen=True, slots=True)
class BootResult:
    """부팅 결과.

    Attributes:
        became_leader: 락을 획득해 리더가 됐는가.
        scheduler_started: 스케줄러를 시작했는가.
    """

    became_leader: bool
    scheduler_started: bool


async def boot_engine(
    lock: SingleInstanceLock,
    scheduler: AsyncIOScheduler,
    *,
    reconcile: ReconcileHook = no_reconciliation_needed,
    wait_for_lock: bool = True,
    retry_interval_seconds: float = 5.0,
    max_lock_attempts: int | None = None,
) -> BootResult:
    """락 → 대사 → 감시 순서로 engine 을 띄운다.

    Args:
        lock: 단일 실행 락.
        scheduler: 잡이 등록된 스케줄러.
        reconcile: 열린 포지션 대사 훅. Phase 0 기본값은 로그만 남긴다.
        wait_for_lock: 락 획득까지 대기할지. False 면 1회만 시도한다 (테스트용).
        retry_interval_seconds: 락 재시도 간격.
        max_lock_attempts: 락 시도 상한. None 이면 무한.

    Returns:
        부팅 결과. 리더가 아니면 `scheduler_started=False` 다.

    Note:
        **리더가 아니면 스케줄러를 시작하지 않는다.** 두 번째 인스턴스는 대기만 하며,
        리더가 죽으면 TTL 만료 후 승격한다 (P0-9 DoD 3).

        하트비트는 스케줄러 시작 **전에** 띄운다 — 잡이 도는 동안 갱신이 멈춰 있으면
        TTL 이 만료되어 다른 인스턴스가 승격하고, 그 순간 둘이 동시에 일한다.
    """
    became_leader = (
        await lock.wait_until_acquired(
            retry_interval_seconds=retry_interval_seconds,
            max_attempts=max_lock_attempts,
        )
        if wait_for_lock
        else await lock.acquire()
    )

    if not became_leader:
        _logger.warning(
            "engine_boot_not_leader",
            payload={
                "note": "락을 얻지 못해 스케줄러를 시작하지 않는다 — 잡은 실행되지 않는다",
                "lock_key": lock.config.key,
            },
        )
        return BootResult(became_leader=False, scheduler_started=False)

    await lock.start_heartbeat()

    # 감시보다 대사가 먼저다 (§7) — 죽어 있던 동안 손절가를 지난 포지션을 먼저 정리한다.
    await reconcile()

    scheduler.start()
    _logger.info(
        "engine_boot_complete",
        payload={
            "lock_key": lock.config.key,
            "jobs": job_ids(scheduler),
        },
    )
    return BootResult(became_leader=True, scheduler_started=True)


async def shutdown_engine(lock: SingleInstanceLock, scheduler: AsyncIOScheduler) -> None:
    """부팅의 역순으로 정리한다 (P0-9-2 graceful shutdown).

    Args:
        lock: 단일 실행 락.
        scheduler: 스케줄러.

    Note:
        순서가 **스케줄러 정지 → 하트비트 정지 → 락 해제**다.

        락을 먼저 놓으면 다른 인스턴스가 즉시 승격하는데, 우리 잡은 아직 돌고 있다 —
        그 겹침이 락을 둔 목적을 어긴다. 진행 중인 잡을 기다린 뒤 놓는다.

        `wait=True` 로 진행 잡을 기다린다. 잡을 중간에 끊으면 부분 적재 상태가 남는데,
        캔들은 upsert 라 무해하지만 Phase 2 의 주문 경로에서는 그렇지 않다 — 지금부터
        기다리는 습관을 코드에 넣어 둔다.
    """
    if scheduler.running:
        scheduler.shutdown(wait=True)
        _logger.info("engine_scheduler_stopped", payload={})
    await lock.stop_heartbeat()
    await lock.release()
