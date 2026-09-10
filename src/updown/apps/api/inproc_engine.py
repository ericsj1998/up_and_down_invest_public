"""engine 잡을 api 프로세스 안에서 — 1 GB 호스트용 (2026-09-04 · 사용자 결정).

engine 이 별도 프로세스였던 이유는 *"웹 재배포가 손절 감시를 죽이지 않게"* 였다 (spec §2.1).
그 전제는 두 번 무너졌다: ① 손절 감시·RUN 은 이제 **api 의 트레이딩 리더** 안에 있고 engine 은
Upbit 수집·자원 비트만 한다 ② api 재배포는 블루그린(T212)이 RUN 을 끊지 않고 넘긴다.
남은 것은 파이썬 골격 복제 **112 MiB** 였다 (T47 실측). Gate 전용 라이브에선 Upbit 수집이
필요 없으므로 자원 비트만 api 안에서 돌린다.

- `UPDOWN_ENGINE_INPROC=1` 일 때만 켜진다 (`compose.live.yml`). dev 는 engine 을 따로 둔다 —
  연구용 Upbit 수집이 거기 있다.
- **트레이딩 리더에서만** 돈다. 블루그린 겹침 구간에 두 api 가 같은 키를 번갈아 쓰지 않는다
  (engine 의 단일 실행 락과 같은 뜻을 리더 락이 대신한다).
- 캔들 수집은 **등록하지 않는다.** Gate 캔들은 RUN 이 직접 받는다.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from updown.apps.engine.resource_beat import register_resource_beat
from updown.apps.engine.scheduler import build_scheduler, job_ids
from updown.common.logging.setup import get_logger
from updown.common.resources import start_loop_lag

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

FLAG = "UPDOWN_ENGINE_INPROC"
PROC_LABEL = "engine(in api)"
"""자원 카드의 engine 행에 이 이름이 뜬다 — 별도 프로세스가 아님을 화면이 말해야 한다."""

_logger = get_logger("apps.api.inproc_engine")


def enabled(environ: dict[str, str] | None = None) -> bool:
    """플래그가 켜져 있나. 기본은 꺼짐 — dev·paper 는 engine 컨테이너가 따로 있다.

    Args:
        environ: 환경 사전. 시험이 넣는다. None 이면 `os.environ`.

    Returns:
        플래그 값이 정확히 `1` 이면 True.
    """
    env = os.environ if environ is None else environ
    return env.get(FLAG, "").strip() == "1"


class InprocEngine:
    """리더가 될 때 시작하고 내려갈 때 멈추는 작은 스케줄러."""

    def __init__(self, redis: Any) -> None:
        """비트를 쓸 Redis 를 받는다. 스케줄러는 `start` 에서 만든다."""
        self._redis = redis
        self._scheduler: AsyncIOScheduler | None = None

    @property
    def jobs(self) -> list[str]:
        """등록된 잡 id (시험·진단용)."""
        return job_ids(self._scheduler) if self._scheduler is not None else []

    def start(self) -> None:
        """스케줄러를 만들고 띄운다. 두 번 불러도 하나만 돈다."""
        if self._scheduler is not None:
            return
        scheduler = build_scheduler()
        register_resource_beat(scheduler, self._redis, proc=PROC_LABEL)
        start_loop_lag()  # T265 눈금 — 스냅샷의 `loop_lag_ms`
        scheduler.start()
        self._scheduler = scheduler
        _logger.info("inproc_engine_started", payload={"jobs": job_ids(scheduler)})

    def stop(self) -> None:
        """멈춘다. 진행 중 잡을 기다리지 않는다 — 리더를 놓는 순간엔 빨리 비키는 것이 맞다."""
        scheduler, self._scheduler = self._scheduler, None
        if scheduler is None:
            return
        if scheduler.running:
            scheduler.shutdown(wait=False)
        _logger.info("inproc_engine_stopped", payload={})
