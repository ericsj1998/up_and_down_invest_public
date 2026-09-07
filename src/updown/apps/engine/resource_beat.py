"""엔진 자원 비트 — 30초마다 스냅샷을 Redis 에 (T215 · 2026-09-04).

api 컨테이너는 engine 프로세스를 볼 수 없다 (PID 네임스페이스가 다르다 · live 는 `pid: host`
를 쓰지 않는다). 그래서 engine 이 **자기** 스냅샷을 Redis 에 TTL 로 남기고 api 가 읽는다.
TTL 이 지나면 화면에 "engine 스냅샷 없음" 이 뜬다 — 그것도 정보다 (죽었거나 락 대기 중).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from apscheduler.triggers.interval import IntervalTrigger

from updown.common import paths
from updown.common.logging.setup import get_logger
from updown.common.resources import snapshot

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from redis.asyncio import Redis

ENGINE_KEY = "updown:stats:engine"
EVERY_S = 30
JOB_ID = "resource_beat"

_logger = get_logger("apps.engine.resource_beat")


def register_resource_beat(
    scheduler: AsyncIOScheduler, redis: Redis, *, every_s: int = EVERY_S, proc: str = "engine"
) -> str:
    """스냅샷 잡을 등록한다. 실패해도 매매를 막지 않는다 (§1.2.1) — 경고만 남긴다.

    Args:
        scheduler: 잡을 얹을 스케줄러.
        redis: 비트를 쓸 Redis.
        every_s: 비트 주기(초). TTL 은 이 값의 3배다.
        proc: 스냅샷에 적히는 프로세스 이름. api 안에서 돌면(`apps/api/inproc_engine`)
            "engine(in api)" 로 넘겨 화면이 별도 프로세스가 아님을 말하게 한다.

    Returns:
        등록된 잡 id.
    """

    async def beat() -> None:
        """스냅샷 한 장을 Redis 에 쓴다 — 실패는 경고 로그 한 줄로 끝난다 (§1.2.1)."""
        try:
            snap = snapshot(proc, disks={"logs": paths.logs_root()})
            await redis.set(ENGINE_KEY, json.dumps(snap, default=str), ex=every_s * 3)
        except Exception as exc:
            _logger.warning("resource_beat_failed", payload={"error": repr(exc)})

    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        beat,
        trigger=IntervalTrigger(seconds=every_s),
        id=JOB_ID,
        name=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _logger.info("job_registered", payload={"job": JOB_ID, "every_s": every_s})
    return JOB_ID
