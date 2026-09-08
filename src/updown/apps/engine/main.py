"""engine 프로세스 진입점 (P0-9-2 · spec §2.1, §12.5).

api 와 **같은 이미지·다른 커맨드**로 돈다 (§2.1). 웹 재배포가 손절 감시를 죽이지 않게
프로세스를 분리한 것이 이 파일의 존재 이유다.

## SIGTERM 처리

컨테이너 종료는 SIGTERM 이다. 기본 동작(즉시 종료)으로 두면 진행 중인 잡이 중간에 끊기고
**락이 TTL 만료까지 남아** 다음 인스턴스의 승격이 최대 TTL 만큼 늦어진다.

```
SIGTERM → 진행 잡 대기 → 하트비트 정지 → 락 해제 → 종료
```

## 락 상실은 즉시 정지다

하트비트가 갱신에 실패하면 `on_lost` 콜백이 스케줄러를 멈춘다 (§12.5). 재시도하며 버티지
않는 이유는 그 사이에 다른 인스턴스가 이미 리더로 일하고 있을 수 있기 때문이다 —
둘이 동시에 도는 것이 락을 둔 목적을 정면으로 어긴다.

정지 후 프로세스는 **살아서 락을 다시 기다린다.** 죽으면 컨테이너 재시작 정책에 맡겨지는데,
재시작 루프가 로그를 덮어 원인 파악이 어려워진다.
"""

import asyncio
import contextlib
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from updown.apps.engine.bootstrap import boot_engine, shutdown_engine
from updown.apps.engine.liveness import LivenessBeacon
from updown.apps.engine.resource_beat import register_resource_beat
from updown.apps.engine.scheduler import (
    build_scheduler,
    register_candle_collection,
    register_daily_report,
)
from updown.common import paths as log_paths
from updown.common.config import Settings, load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import Market
from updown.common.lock.redis_lock import LockConfig, SingleInstanceLock
from updown.common.logging.context import trace_context
from updown.common.logging.setup import configure_logging, get_logger
from updown.marketdata.ingest.backfill import BackfillScope
from updown.marketdata.ingest.integrity import IntegrityThresholds
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.provider import MarketDataProvider

_logger = get_logger("apps.engine.main")

LOCK_RETRY_INTERVAL_SECONDS = 5.0
"""락 재시도 간격 (P0-9-4)."""


class EngineRunner:
    """engine 수명주기 (P0-9-2).

    Note:
        락 상실과 SIGTERM 을 **같은 종료 신호**로 모은다 (`_stop` 이벤트). 두 경로가
        각자 종료 로직을 갖게 하면 정리 순서가 갈라지고, 한쪽이 락 해제를 빠뜨린다.
    """

    def __init__(self, settings: Settings, *, lock_config: LockConfig | None = None) -> None:
        """러너를 만든다.

        Args:
            settings: 설정.
            lock_config: 락 설정. 기본값은 `LockConfig()`.
        """
        self._settings = settings
        self._redis: Redis = Redis.from_url(settings.redis_url)  # pyright: ignore[reportUnknownMemberType]
        self._scheduler: AsyncIOScheduler = build_scheduler()
        self._lock = SingleInstanceLock(self._redis, lock_config, on_lost=self._on_lock_lost)
        self._beacon = LivenessBeacon()
        self._stop = asyncio.Event()
        self._lost_reason: str | None = None

        # 캔들 수집 잡이 쓰는 자원 (P0-9-7).
        #
        # 어댑터를 **직접 만들지 않는다** — `MarketDataProvider` 가 조회 전용 획득 지점이다
        # (절대 규칙 #0 의 정적 검사가 구체 어댑터 import 를 두 파일로 제한한다).
        self._engine: AsyncEngine = create_engine(settings.database_url)
        self._repository = CandleRepository(create_session_factory(self._engine))
        self._market_data = MarketDataProvider()
        self._adapter = self._market_data.adapter_for(Market.UPBIT)
        self._scope = BackfillScope.load()
        self._thresholds = IntegrityThresholds.load()

    @property
    def lock(self) -> SingleInstanceLock:
        """단일 실행 락 (테스트·검증용)."""
        return self._lock

    async def _on_lock_lost(self, reason: str) -> None:
        """락 상실 → 스케줄러 즉시 정지 (spec §12.5).

        Args:
            reason: 상실 사유.

        Note:
            `wait=False` 다. 이미 다른 인스턴스가 리더일 수 있으므로 **지금 도는 잡을 빨리
            끊는 편**이 겹침을 줄인다. 종료(SIGTERM)와 반대 선택이며, 이유가 다르다 —
            종료는 우리가 유일한 리더인 상태에서 깔끔히 끝내는 것이고, 상실은 이미 겹쳤을
            가능성을 줄이는 것이다.
        """
        self._lost_reason = reason
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        _logger.error(
            "engine_scheduler_halted_on_lock_loss",
            payload={"reason": reason, "next": "락을 다시 기다린다 (프로세스는 유지)"},
        )
        self._stop.set()

    def _install_signal_handlers(self) -> None:
        """SIGTERM·SIGINT 를 종료 신호로 연결한다.

        Note:
            `add_signal_handler` 는 유닉스 전용이다. 실패하면 기본 동작으로 둔다 —
            graceful shutdown 이 없는 것보다 프로세스가 안 뜨는 것이 나쁘다.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, AttributeError):
                loop.add_signal_handler(sig, self._request_stop, sig.name)

    def _request_stop(self, signal_name: str) -> None:
        """종료를 요청한다.

        Args:
            signal_name: 받은 시그널 이름 (로그용).
        """
        _logger.info("engine_stop_requested", payload={"signal": signal_name})
        self._stop.set()

    async def run(self) -> None:
        """부팅 → 대기 → 정리.

        Note:
            락을 못 얻으면 `boot_engine` 안에서 계속 대기한다. 그 대기 중에도 SIGTERM 으로
            빠져나올 수 있어야 하므로, 부팅과 종료 신호를 **함께 기다린다.**
        """
        self._install_signal_handlers()
        # 락 대기 중에도 살아 있음을 알려야 한다 — 대기 인스턴스는 정상이며 페일오버
        # 수단이다 (P0-9-4). 그래서 부팅 **전에** 띄운다.
        await self._beacon.start()

        with trace_context() as trace_id:
            _logger.info(
                "engine_starting",
                payload={
                    "app_env": self._settings.app_env.value,
                    "trace_id": trace_id,
                    "lock_key": self._lock.config.key,
                },
            )
            register_candle_collection(
                self._scheduler,
                self._adapter,
                self._repository,
                self._scope,
                self._thresholds,
            )
            # 일간 이메일 리포트 (T35) — 설정이 없으면 스스로 등록을 건너뛴다.
            register_daily_report(
                self._scheduler,
                create_session_factory(self._engine),
                self._settings,
            )
            # 자원 비트 (T215) — api 가 engine 프로세스를 못 보므로 Redis 로 건넨다.
            register_resource_beat(self._scheduler, self._redis)

            boot = asyncio.create_task(
                boot_engine(
                    self._lock,
                    self._scheduler,
                    retry_interval_seconds=LOCK_RETRY_INTERVAL_SECONDS,
                )
            )
            stop_wait = asyncio.create_task(self._stop.wait())
            done, _ = await asyncio.wait({boot, stop_wait}, return_when=asyncio.FIRST_COMPLETED)

            if boot not in done:
                # 락 대기 중에 종료 신호를 받았다.
                boot.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await boot
                _logger.info("engine_stopped_while_waiting_for_lock", payload={})
            else:
                await boot
                await self._stop.wait()

            stop_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_wait

        await self._cleanup()

    async def _cleanup(self) -> None:
        """자원을 정리한다.

        Note:
            **스케줄러 정지가 먼저**다 (`shutdown_engine` 안에서 `wait=True`). 진행 중인
            수집 잡이 쓰는 DB·HTTP 연결을 그보다 먼저 닫으면 잡이 예외로 죽는다.
        """
        await shutdown_engine(self._lock, self._scheduler)
        await self._beacon.stop()
        await self._market_data.aclose()
        await self._engine.dispose()
        await self._redis.aclose()
        _logger.info(
            "engine_stopped",
            payload={"lock_lost_reason": self._lost_reason},
        )


async def main() -> None:
    """Engine 을 실행한다."""
    settings = load_settings()
    # T211 — stderr + `logs/app/engine-YYYY-MM-DD.jsonl`. api 와 **다른 proc 이름**이어야
    # 같은 볼륨에서 두 프로세스의 줄이 섞이지 않는다.
    configure_logging(settings.log_level, file_dir=log_paths.under("app"), proc="engine")
    await EngineRunner(settings).run()


if __name__ == "__main__":
    asyncio.run(main())
